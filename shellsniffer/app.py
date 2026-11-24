"""shellSniffer web server: HTTP API, WebSocket live updates and the web UI.

Start:  python -m uvicorn shellsniffer.app:app --port 8765     (or simply start.ps1 / start.sh)

Module overview
    app           this file: HTTP API, WebSocket, background workers' lifecycle
    config        paths, environment variables, access token, data-dir migration
    db            SQLite connection, schema, migrations, FTS5 indexes
    state         UI settings, worker status, live updates (broadcast)
    ingest        storing commands and outputs reported by hooks / Atuin / import
    atuin         reading Atuin's history (Windows + WSL);  atuin_import: the import worker
    honeypot      per-shell plain-text logs (mode without Atuin)
    importer      batch import of collected shell dumps (format detection)
    ollama        the only Ollama client (chat, streaming, embeddings; honours "Ollama disabled")
    cluster       one session -> domain, topic, title, ASD-STE100 summary (model or heuristic)
    documenter    SniffMagic queue worker, ontology housekeeping
    embeddings    vectors for commands and documentation, continuous shrooming worker
    search        BM25 search and hybrid search (weighted RRF)
    ask           "ask the shroom": LLM answer grounded in the search hits (RAG)
    timerange     time-range validation and SQL conditions
    ontology      domains, projects, tools;   redact: secret filter
"""
import asyncio
import json
import time
import urllib.parse
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db, documenter, embeddings, timerange
from . import ontology as onto
from . import search as search_mod
from .atuin_import import atuin_worker, skip_history_until_now
from .config import HONEYPOT_DIR, STATIC_DIR, TOKEN
from .ingest import attach_output, ingest
from .state import (SETTING_KEYS, broadcast, broadcast_status, clients, honeypot_active, save_setting,
                    settings, status, status_info)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Starts the background workers with the server and stops them with it."""
    tasks = [asyncio.create_task(documenter.ontology_startup()),
             asyncio.create_task(documenter.cluster_worker()),
             asyncio.create_task(documenter.model_probe()),
             asyncio.create_task(atuin_worker()),
             asyncio.create_task(embeddings.vectorize_worker())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="shellSniffer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------- Access control
def _is_local(host: str | None) -> bool:
    return host in ("127.0.0.1", "::1", "localhost", "testclient", None)


@app.middleware("http")
async def auth(request: Request, call_next):
    """Requests to /api from other machines (e.g. WSL) need the token from ~/.shellsniffer/token,
    as header X-ShellSniffer-Token or query parameter ?token=."""
    if request.url.path.startswith("/api"):
        host = request.client.host if request.client else None
        token = request.headers.get("x-shellsniffer-token") or request.query_params.get("token")
        if not _is_local(host) and token != TOKEN:
            return JSONResponse({"detail": "Token missing or wrong"}, status_code=401)
    return await call_next(request)


async def read_payload(request: Request) -> dict:
    """JSON (PowerShell hook, UI) or application/x-www-form-urlencoded (curl in bash/zsh)."""
    raw = (await request.body()).decode("utf-8-sig", "replace")
    if "json" in request.headers.get("content-type", ""):
        return json.loads(raw)
    return {k: v[-1] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}


# ---------------------------------------------------------------- Capture (called by the shell hooks)
@app.post("/api/events")
async def post_event(request: Request):
    """A complete command from a hook (shell without Atuin)."""
    try:
        ev = await ingest(await read_payload(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": ev["id"], "block_id": ev["block_id"]}


@app.post("/api/outputs")
async def post_output(request: Request):
    """The output of a command from a hook in a shell with Atuin."""
    return await attach_output(await read_payload(request))


# ---------------------------------------------------------------- Batch import of collected dumps
MAX_IMPORT_BYTES = 50 * 1024 * 1024


@app.post("/api/import")
async def post_import(request: Request, name: str = "upload.txt", mtime: float | None = None, host: str = ""):
    """Imports one uploaded dump file (raw text body). The format is detected automatically
    (see :mod:`shellsniffer.importer`); ``mtime`` (Unix ms, the file's date) dates commands without
    a timestamp. Re-uploading the same file skips what is already there."""
    from . import importer
    raw = await request.body()
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(413, f"file too large (max {MAX_IMPORT_BYTES // 1024 // 1024} MB)")
    if b"\x00" in raw[:8192]:
        raise HTTPException(415, "this looks like a binary file, not a text dump")
    fmt, entries = importer.parse(name, raw.decode("utf-8", "replace"))
    reports = importer.to_reports(name, entries, (mtime / 1000) if mtime else time.time(), host)
    imported = 0
    for d in reports:
        try:
            if await ingest(d, source="import", quiet=True):
                imported += 1
        except ValueError:
            pass
    if imported:
        await broadcast({"type": "reload"})
    await broadcast_status()
    with_ts = sum(1 for e in entries if e["ts"] is not None)
    with_out = sum(1 for e in entries if e["output"])
    return {"file": name, "format": fmt, "found": len(entries), "imported": imported,
            "duplicates": len(entries) - imported, "with_timestamp": with_ts, "with_output": with_out}


# ---------------------------------------------------------------- Reading
@app.get("/api/events")
def get_events(limit: int = 200):
    """The latest commands, oldest first."""
    return list(reversed(db.rows("SELECT * FROM events ORDER BY id DESC LIMIT ?", (min(limit, 2000),))))


@app.get("/api/search")
def get_search(q: str = "", limit: int = 50, start: float | None = None, end: float | None = None,
               mode: str = "bm25", semantic_weight: float = 0.5):
    """Search the logs. ``mode=bm25`` (default): full-text search; ``mode=hybrid`` ("Sniff logs with shroom"):
    BM25 + semantic similarity via Ollama, weighted by ``semantic_weight`` (0 = BM25 only,
    1 = cosine only). See :mod:`shellsniffer.search`."""
    from .embeddings import EmbeddingError
    try:
        if mode == "hybrid":
            return search_mod.hybrid_search(q, limit, start, end, semantic_weight)
        return search_mod.search(q, limit, start, end)
    except (search_mod.SearchError, timerange.RangeError) as e:
        raise HTTPException(400, str(e))
    except EmbeddingError as e:
        raise HTTPException(503, str(e))


@app.get("/api/ask")
def get_ask(q: str, start: float | None = None, end: float | None = None, semantic_weight: float = 0.5):
    """Ask the shroom: an Ollama answer grounded in the best hybrid-search hits, streamed as NDJSON
    (see :mod:`shellsniffer.ask`)."""
    from . import ask
    if not q.strip():
        raise HTTPException(400, "question missing")
    return StreamingResponse(ask.stream_answer(q, start, end, semantic_weight), media_type="application/x-ndjson")


@app.get("/api/blocks/open")
def open_blocks():
    """Sessions that are not documented yet."""
    return db.rows("SELECT id,skey,status,start_ts,end_ts,n FROM blocks WHERE status IN ('open','ready') ORDER BY start_ts")


@app.get("/api/topics")
def get_topics():
    """All topics with their documented sessions and each session's commands."""
    topics = db.rows("SELECT * FROM topics ORDER BY updated DESC")
    blocks = db.rows("SELECT id,topic_id,start_ts,end_ts,n,title,summary,method,project,tools FROM blocks"
                     " WHERE status='done' ORDER BY start_ts DESC")
    evs = db.rows("SELECT e.id,e.block_id,e.ts,e.shell,e.cwd,e.command,e.exit_code FROM events e"
                  " JOIN blocks b ON b.id=e.block_id WHERE b.status='done' ORDER BY e.ts")
    by_block: dict[int, list] = {}
    for e in evs:
        by_block.setdefault(e["block_id"], []).append(e)
    by_topic: dict[int, list] = {}
    for b in blocks:
        b["events"] = by_block.get(b["id"], [])
        b["tools"] = json.loads(b["tools"] or "[]")
        by_topic.setdefault(b["topic_id"], []).append(b)
    for t in topics:
        t["blocks"] = by_topic.get(t["id"], [])
    return topics


@app.get("/api/ontology")
def get_ontology():
    """The fixed domains with their colour slots."""
    return {"domains": [{"key": k, "name": n, "description": d, "slot": i + 1} for i, (k, n, d) in enumerate(onto.DOMAINS)]}


@app.get("/api/status")
def get_status():
    return status_info()


@app.get("/api/export.md", response_class=PlainTextResponse)
def export_md():
    """The whole documentation as one Markdown file: domain > topic > session."""
    out = ["# Shell documentation\n"]
    topics = get_topics()
    for key, name, desc in onto.DOMAINS:
        ts = [t for t in topics if (t.get("domain") or "sonstiges") == key and t["blocks"]]
        if not ts:
            continue
        out.append(f"## {name}\n\n_{desc}_\n")
        for t in ts:
            out.append(f"### {t['name']}\n\n{t['description'] or ''}\n")
            for b in t["blocks"]:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(b["start_ts"]))
                extra = " · ".join(x for x in [b.get("project") and f"Project {b['project']}", ", ".join(b["tools"][:4])] if x)
                out.append(f"#### {b['title'] or 'Session'} ({when}{' · ' + extra if extra else ''})\n\n{b['summary'] or ''}\n")
    return "\n".join(out)


# ---------------------------------------------------------------- Documentation (Ollama, on request only)
def _check_range(start: float | None, end: float | None) -> None:
    try:
        timerange.check(start, end)
    except timerange.RangeError as e:
        raise HTTPException(400, str(e))


@app.get("/api/cluster/preview")
def cluster_preview(start: float | None = None, end: float | None = None):
    """How many sessions SniffMagic would document in this time range (shown before starting)."""
    _check_range(start, end)
    return documenter.magic_preview(start, end)


@app.post("/api/cluster")
async def post_cluster(request: Request):
    """Button "SniffMagic": lets Ollama document the sessions of a time range.

    Body (all optional): ``{"start": unix_s, "end": unix_s, "include_done": bool}``.
    Without a range, all sessions that are not documented yet are queued.
    """
    if not settings.get("ollama", True):
        raise HTTPException(409, "Ollama is disabled – enable it to use SniffMagic")
    raw = (await request.body()).strip()
    d = json.loads(raw) if raw else {}
    start, end = d.get("start"), d.get("end")
    _check_range(start, end)
    n = documenter.request_clustering(start, end, bool(d.get("include_done")))
    await broadcast({"type": "reload"})
    await broadcast_status()
    return {"ok": True, "queued": n, "pending": status_info()["pending"]}


@app.post("/api/blocks/{bid}/recluster")
async def recluster(bid: int):
    """Sends one session through the model again, right away."""
    if not settings.get("ollama", True):
        raise HTTPException(409, "Ollama is disabled – enable it to re-document sessions")
    documenter.request_clustering(block_ids=[bid])
    return {"ok": True}


@app.post("/api/ontology/consolidate")
async def post_consolidate():
    """Button "Tidy up topics": heuristic housekeeping, then the model reviews all topics."""
    n = await asyncio.to_thread(documenter.migrate_ontology)
    r = await documenter.consolidate_with_llm()
    await broadcast({"type": "topics_changed", "topic_id": None})
    return {**r, "heuristic_changes": n}


# ---------------------------------------------------------------- Settings (UI switches)
def _ollama_switched(on: bool) -> None:
    """Ollama enabled/disabled: update the model status at once (disabled = no request at all)."""
    from . import ollama
    if on:
        status["model"] = ollama.chat_model()
    else:
        status["model"] = None
        status["vectors"]["running"] = False
    status["ollama"] = bool(status["model"])


@app.get("/api/settings")
def get_settings():
    return {**settings, "honeypot_active": honeypot_active(), "honeypot_dir": str(HONEYPOT_DIR)}


@app.post("/api/settings")
async def post_settings(request: Request):
    """``{"atuin", "honeypot", "vectorize", "dog", "ollama"}`` (booleans) – any key may be left out."""
    d = await read_payload(request)
    for key in SETTING_KEYS:
        if key in d:
            value = d[key] in (True, 1, "1", "true", "on")
            if key == "atuin" and value and not settings["atuin"]:
                skip_history_until_now()
            save_setting(key, value)
            if key == "ollama":
                await asyncio.to_thread(_ollama_switched, value)
            if key == "vectorize" and not value:
                status["vectors"]["running"] = False   # the worker notices within seconds; show it at once
    await broadcast_status()
    return get_settings()


# ---------------------------------------------------------------- Live updates + UI
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """Pushes new commands, session changes and status to the browser (see state.broadcast)."""
    host = ws.client.host if ws.client else None
    if not _is_local(host) and ws.query_params.get("token") != TOKEN:
        await ws.close(code=4401)
        return
    await ws.accept()
    clients.add(ws)
    await ws.send_text(json.dumps({"type": "status", "status": status_info()}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/logo.svg")
def logo():
    return FileResponse(STATIC_DIR / "logo.svg", media_type="image/svg+xml")
