"""joshi (Journal Shell Intelligence) – observes shell input and documents it by topic.

Start:  python -m uvicorn joshi.app:app --port 8765
"""
import asyncio
import json
import re
import os
import secrets
import sqlite3
import threading
import time
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from . import atuin, cluster
from . import ontology as onto
from .redact import redact

DATA_DIR = Path(os.environ.get("JOSHI_DIR", Path.home() / ".joshi"))
# Carry over data from before the rename (shelldoc -> docuduck -> joshi)
for _old in ("docuduck", "shelldoc"):
    _od = Path.home() / f".{_old}"
    if not DATA_DIR.exists() and (_od / f"{_old}.db").exists():
        import shutil
        DATA_DIR.mkdir(parents=True)
        for suffix in (".db", ".db-wal", ".db-shm"):
            if (_od / f"{_old}{suffix}").exists():
                shutil.copy2(_od / f"{_old}{suffix}", DATA_DIR / f"joshi{suffix}")
        if (_od / "token").exists():
            shutil.copy2(_od / "token", DATA_DIR / "token")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "joshi.db"
TOKEN_PATH = DATA_DIR / "token"
STATIC = Path(__file__).parent / "static"

GAP_SECONDS = float(os.environ.get("JOSHI_GAP", 300))      # pause after which a new session starts
IDLE_SECONDS = float(os.environ.get("JOSHI_IDLE", 45))     # idle time after which a session is clustered
MAX_BLOCK = int(os.environ.get("JOSHI_MAX_BLOCK", 25))     # max. commands per session
MAX_OUTPUT = int(os.environ.get("JOSHI_MAX_OUTPUT", 16000))
ATUIN_ENABLED = os.environ.get("JOSHI_ATUIN", "1") != "0"
ATUIN_BACKFILL_DAYS = float(os.environ.get("JOSHI_ATUIN_DAYS", 14))   # how far back the first import goes
ATUIN_POLL = float(os.environ.get("JOSHI_ATUIN_POLL", 2))

if not TOKEN_PATH.exists():
    TOKEN_PATH.write_text(secrets.token_urlsafe(24))
TOKEN = TOKEN_PATH.read_text().strip()

# ---------------------------------------------------------------- Database
_lock = threading.RLock()
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript(
    """
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS topics(
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT,
        created REAL, updated REAL);
    CREATE TABLE IF NOT EXISTS blocks(
        id INTEGER PRIMARY KEY, skey TEXT, status TEXT DEFAULT 'open',
        start_ts REAL, end_ts REAL, n INTEGER DEFAULT 0,
        topic_id INTEGER REFERENCES topics(id), title TEXT, summary TEXT, method TEXT);
    CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY, ts REAL, received REAL, shell TEXT, host TEXT, session TEXT,
        cwd TEXT, command TEXT, exit_code INTEGER, duration_ms INTEGER, output TEXT,
        block_id INTEGER REFERENCES blocks(id));
    CREATE INDEX IF NOT EXISTS ev_block ON events(block_id);
    CREATE TABLE IF NOT EXISTS outputs(
        id INTEGER PRIMARY KEY, received REAL, session TEXT, command TEXT, ts REAL, output TEXT,
        event_id INTEGER, payload TEXT);
    CREATE TABLE IF NOT EXISTS sources(
        name TEXT PRIMARY KEY, watermark INTEGER, db TEXT, status TEXT, last_ok REAL, imported INTEGER DEFAULT 0);
    CREATE INDEX IF NOT EXISTS bl_status ON blocks(status);
    """
)


_cols = {r[1] for r in db.execute("PRAGMA table_info(events)")}
if "atuin_uuid" not in _cols:
    db.execute("ALTER TABLE events ADD COLUMN atuin_uuid TEXT")
    db.execute("ALTER TABLE events ADD COLUMN source TEXT DEFAULT 'hook'")
db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ev_atuin ON events(atuin_uuid)")
if "payload" not in {r[1] for r in db.execute("PRAGMA table_info(outputs)")}:
    db.execute("ALTER TABLE outputs ADD COLUMN payload TEXT")
# Ontology: domain per topic, project + tools per session
if "domain" not in {r[1] for r in db.execute("PRAGMA table_info(topics)")}:
    db.execute("ALTER TABLE topics ADD COLUMN domain TEXT")
_bcols = {r[1] for r in db.execute("PRAGMA table_info(blocks)")}
if "project" not in _bcols:
    db.execute("ALTER TABLE blocks ADD COLUMN project TEXT")
    db.execute("ALTER TABLE blocks ADD COLUMN tools TEXT")
db.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
db.commit()


def rows(sql, args=()):
    with _lock:
        return [dict(r) for r in db.execute(sql, args).fetchall()]


def one(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None


# ---------------------------------------------------------------- Live connections
clients: set[WebSocket] = set()
status = {"clustering": None, "model": None, "ollama": False, "last_error": None}


async def broadcast(msg: dict):
    dead = []
    data = json.dumps(msg, default=str)
    for ws in list(clients):
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


def _is_local(host: str | None) -> bool:
    return host in ("127.0.0.1", "::1", "localhost", "testclient", None)


def check_access(host: str | None, token: str | None):
    if not _is_local(host) and token != TOKEN:
        raise HTTPException(401, "Token missing or wrong")


# ---------------------------------------------------------------- Ingest
def _assign_block(skey: str, ts: float) -> tuple[int, int | None]:
    """Returns (block_id, id of a block that was closed, if any)."""
    with _lock:
        b = db.execute("SELECT * FROM blocks WHERE skey=? AND status='open' ORDER BY id DESC LIMIT 1", (skey,)).fetchone()
        closed = None
        if b and (ts - b["end_ts"] <= GAP_SECONDS) and b["n"] < MAX_BLOCK:
            db.execute("UPDATE blocks SET end_ts=max(end_ts,?), n=n+1 WHERE id=?", (ts, b["id"]))
            return b["id"], None
        if b:
            db.execute("UPDATE blocks SET status='ready' WHERE id=?", (b["id"],))
            closed = b["id"]
        cur = db.execute("INSERT INTO blocks(skey,status,start_ts,end_ts,n) VALUES(?,?,?,?,1)", (skey, "open", ts, ts))
        return cur.lastrowid, closed


def _clip_output(out: str | None) -> str | None:
    if not out:
        return None
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", out)          # remaining control characters
    lines = [ln.rstrip() for ln in out.split("\n")]
    while lines and re.fullmatch(r"[#%]?", lines[-1]):           # zsh PROMPT_SP leftovers at the end
        lines.pop()
    out = "\n".join(lines).strip("\n")
    if len(out) > MAX_OUTPUT:
        h = MAX_OUTPUT * 2 // 3
        out = out[:h] + f"\n… ({len(out) - MAX_OUTPUT} characters cut) …\n" + out[-(MAX_OUTPUT - h):]
    return out or None


async def ingest(d: dict, source: str = "hook", quiet: bool = False) -> dict | None:
    command = (d.get("command") or "").strip()
    if not command:
        raise HTTPException(400, "command missing")
    now = time.time()
    ts = d.get("started_at")
    try:
        ts = float(ts) / 1000.0 if ts not in (None, "") else now
    except ValueError:
        ts = now
    if source == "hook" and abs(ts - now) > 86400:  # broken clock / time zone -> server time
        ts = now

    def _int(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    ev = {
        "ts": ts, "received": now,
        "shell": (d.get("shell") or "?")[:30],
        "host": (d.get("host") or "")[:80],
        "session": (str(d.get("session") or ""))[:80],
        "cwd": d.get("cwd") or "",
        "command": redact(command),
        "exit_code": _int(d.get("exit_code")),
        "duration_ms": _int(d.get("duration_ms")),
        "output": redact(_clip_output(d.get("output"))),
        "atuin_uuid": d.get("atuin_uuid"),
        "source": source,
    }
    if ev["atuin_uuid"] and one("SELECT id FROM events WHERE atuin_uuid=?", (ev["atuin_uuid"],)):
        return None   # already imported (e.g. via Atuin sync from another source)
    if source == "atuin":
        # Did the hook already create this command as a fallback entry? Then just link it.
        for e in rows("SELECT id,command FROM events WHERE session=? AND atuin_uuid IS NULL AND abs(ts-?) < ?",
                      (ev["session"], ev["ts"], MATCH_WINDOW)):
            if _norm(e["command"]) == _norm(ev["command"]):
                with _lock:
                    db.execute("UPDATE events SET atuin_uuid=?, source='atuin' WHERE id=?", (ev["atuin_uuid"], e["id"]))
                    db.commit()
                return None
        if not ev["output"]:
            ev["output"] = _take_pending_output(ev)
    skey = f'{ev["host"]}|{ev["shell"]}|{ev["session"]}'
    block_id, closed = _assign_block(skey, ts)
    ev["block_id"] = block_id
    with _lock:
        cur = db.execute(
            "INSERT INTO events(ts,received,shell,host,session,cwd,command,exit_code,duration_ms,output,block_id,atuin_uuid,source)"
            " VALUES(:ts,:received,:shell,:host,:session,:cwd,:command,:exit_code,:duration_ms,:output,:block_id,:atuin_uuid,:source)",
            ev,
        )
        db.commit()
        ev["id"] = cur.lastrowid
    if quiet:
        return ev
    await broadcast({"type": "event", "event": ev})
    await broadcast({"type": "block", "block": block_info(block_id)})
    if closed:
        await broadcast({"type": "block", "block": block_info(closed)})
    return ev


def block_info(bid: int) -> dict | None:
    return one("SELECT id,skey,status,start_ts,end_ts,n,topic_id,title,method FROM blocks WHERE id=?", (bid,))


# ---------------------------------------------------------------- Outputs for Atuin entries
MATCH_WINDOW = 60.0   # seconds of tolerance between hook and Atuin start time


def _norm(cmd: str | None) -> str:
    return " ".join((cmd or "").split())


def _take_pending_output(ev: dict) -> str | None:
    """Finds an output reported by the hook that belongs to this Atuin entry."""
    cands = rows("SELECT * FROM outputs WHERE event_id IS NULL AND session=? AND abs(ts-?) < ? ORDER BY abs(ts-?)",
                 (ev["session"], ev["ts"], MATCH_WINDOW, ev["ts"]))
    for o in cands:
        if _norm(o["command"]) == _norm(ev["command"]):
            with _lock:
                db.execute("UPDATE outputs SET event_id=-1 WHERE id=?", (o["id"],))
            return o["output"]
    return None


async def attach_output(d: dict) -> dict:
    """Output from the hook (Atuin mode): attach it to an existing event right away, or park it."""
    out = redact(_clip_output(d.get("output")))
    session = str(d.get("session") or "")
    command = redact((d.get("command") or "").strip())
    try:
        ts = float(d.get("started_at")) / 1000.0
    except (TypeError, ValueError):
        ts = time.time()
    if not session:
        return {"matched": False}
    cands = rows("SELECT id,command FROM events WHERE session=? AND output IS NULL AND abs(ts-?) < ? ORDER BY abs(ts-?)",
                 (session, ts, MATCH_WINDOW, ts))
    for e in cands:
        if _norm(e["command"]) == _norm(command):
            if not out:
                return {"matched": True, "event_id": e["id"]}
            with _lock:
                db.execute("UPDATE events SET output=? WHERE id=?", (out, e["id"]))
                db.commit()
            ev = one("SELECT * FROM events WHERE id=?", (e["id"],))
            await broadcast({"type": "event_update", "event": ev})
            return {"matched": True, "event_id": e["id"]}
    with _lock:
        db.execute("DELETE FROM outputs WHERE received < ?", (time.time() - 3600,))
        db.execute("INSERT INTO outputs(received,session,command,ts,output,payload) VALUES(?,?,?,?,?,?)",
                   (time.time(), session, command, ts, out, json.dumps(d, default=str)))
        db.commit()
    return {"matched": False, "pending": True}


# ---------------------------------------------------------------- Atuin import
atuin_status: dict[str, dict] = {}


def _source(name: str) -> dict:
    s = one("SELECT * FROM sources WHERE name=?", (name,))
    if not s:
        wm = int((time.time() - ATUIN_BACKFILL_DAYS * 86400) * 1e9)
        with _lock:
            db.execute("INSERT INTO sources(name,watermark) VALUES(?,?)", (name, wm))
            db.commit()
        s = one("SELECT * FROM sources WHERE name=?", (name,))
    return s


_retry_at: dict[str, float] = {}


async def import_atuin(name: str, shell: str, reader) -> int:
    if time.time() < _retry_at.get(name, 0):
        return 0
    src = _source(name)
    try:
        res = await asyncio.to_thread(reader, src["watermark"], src["db"])
    except Exception as e:
        res = {"error": repr(e)}
    if "error" in res:
        atuin_status[name] = {"ok": False, "error": res["error"]}
        _retry_at[name] = time.time() + 60   # e.g. Atuin not installed: retry less often
        return 0
    rws = res["rows"]
    finished = [r for r in rws if r["duration_ns"] is not None and r["duration_ns"] >= 0]
    running = [r["ts_ns"] for r in rws if r not in finished]
    backlog = len(rws) > 50
    n = 0
    for r in finished:
        ev = await ingest({
            "command": r["command"], "cwd": r["cwd"], "session": r["session"], "host": r["host"],
            "shell": shell, "exit_code": r["exit"], "duration_ms": r["duration_ns"] // 1_000_000,
            "started_at": r["ts_ns"] / 1e6, "atuin_uuid": r["uuid"],
        }, source="atuin", quiet=backlog)
        n += 1 if ev else 0
    if rws:
        wm = (min(running) - 1) if running else rws[-1]["ts_ns"]
        # never go back: already imported entries are recognised by their UUID
        wm = max(wm, src["watermark"]) if not running else wm
        with _lock:
            db.execute("UPDATE sources SET watermark=?, db=?, imported=imported+?, last_ok=? WHERE name=?",
                       (wm, res.get("db"), n, time.time(), name))
            db.commit()
    elif res.get("db") != src["db"]:
        with _lock:
            db.execute("UPDATE sources SET db=?, last_ok=? WHERE name=?", (res.get("db"), time.time(), name))
            db.commit()
    atuin_status[name] = {"ok": True, "db": res.get("db"), "new": n}
    if backlog and n:
        await broadcast({"type": "reload"})
    return n


FALLBACK_AFTER = float(os.environ.get("JOSHI_FALLBACK_AFTER", 20))


async def flush_unmatched_outputs():
    """If no Atuin entry shows up for a hook report within FALLBACK_AFTER seconds (Atuin does not record
    in this shell, or cannot be read), joshi creates the command itself, so nothing is lost."""
    for o in rows("SELECT * FROM outputs WHERE event_id IS NULL AND received < ?", (time.time() - FALLBACK_AFTER,)):
        with _lock:
            db.execute("UPDATE outputs SET event_id=-2 WHERE id=?", (o["id"],))
            db.commit()
        try:
            d = json.loads(o["payload"] or "{}")
        except ValueError:
            d = {}
        if d.get("command"):
            await ingest(d, source="hook")


async def atuin_worker():
    local_shell = "pwsh" if os.name == "nt" else (os.path.basename(os.environ.get("SHELL", "")) or "bash")
    tick = 0
    while True:
        try:
            await import_atuin("local", local_shell, atuin.read_local)
            await flush_unmatched_outputs()
            if tick % 2 == 0:
                for d in await asyncio.to_thread(atuin.running_wsl_distros):
                    await import_atuin(f"wsl:{d}", "wsl", lambda wm, dbp, d=d: atuin.read_wsl(d, wm, dbp))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            atuin_status["worker"] = {"ok": False, "error": repr(e)}
        tick += 1
        await asyncio.sleep(ATUIN_POLL)


# ---------------------------------------------------------------- Clustering worker
async def cluster_worker():
    status["model"] = await asyncio.to_thread(cluster.pick_model)
    status["ollama"] = bool(status["model"])
    while True:
        try:
            now = time.time()
            with _lock:
                db.execute("UPDATE blocks SET status='ready' WHERE status='open' AND end_ts < ?", (now - IDLE_SECONDS,))
                db.commit()
            b = one("SELECT * FROM blocks WHERE status='ready' ORDER BY start_ts LIMIT 1")
            if not b:
                await asyncio.sleep(3)
                continue
            events = rows("SELECT * FROM events WHERE block_id=? ORDER BY ts", (b["id"],))
            topics = rows("SELECT id,name,description,domain FROM topics ORDER BY updated DESC")
            meta = block_meta(events)
            status["clustering"] = b["id"]
            await broadcast({"type": "status", "status": status_info()})
            res = await asyncio.to_thread(cluster.classify, events, topics, meta)
            status["model"] = cluster.current_model()
            status["ollama"] = bool(status["model"])
            with _lock:
                tid = res["topic_id"]
                if tid is None:
                    nt = res["new_topic"]
                    tid = db.execute(
                        "INSERT INTO topics(name,description,created,updated,domain) VALUES(?,?,?,?,?)",
                        (nt["name"], nt["description"], now, now, res["domain"]),
                    ).lastrowid
                db.execute("UPDATE topics SET updated=max(coalesce(updated,0),?), domain=coalesce(domain,?) WHERE id=?",
                           (b["end_ts"], res["domain"], tid))
                db.execute(
                    "UPDATE blocks SET status='done', topic_id=?, title=?, summary=?, method=?, project=?, tools=? WHERE id=?",
                    (tid, res["title"], res["summary"], res["method"], meta["project"], json.dumps(meta["tools"]), b["id"]),
                )
                db.commit()
            status["clustering"] = None
            await broadcast({"type": "block", "block": block_info(b["id"])})
            await broadcast({"type": "topics_changed", "topic_id": tid})
            await broadcast({"type": "status", "status": status_info()})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # the worker must never die
            status["last_error"] = repr(e)
            status["clustering"] = None
            await asyncio.sleep(5)


def block_meta(events: list[dict]) -> dict:
    return {"project": onto.project_of_block([e["cwd"] for e in events]),
            "tools": onto.tools_of([e["command"] for e in events])}


# ---------------------------------------------------------------- Ontology: migration & tidy-up
def merge_topics(keep: int, others: list[int]):
    with _lock:
        for o in others:
            if o == keep:
                continue
            db.execute("UPDATE blocks SET topic_id=? WHERE topic_id=?", (keep, o))
            db.execute("UPDATE topics SET updated=max(coalesce(updated,0),coalesce((SELECT updated FROM topics WHERE id=?),0)) WHERE id=?", (o, keep))
            db.execute("DELETE FROM topics WHERE id=?", (o,))
        db.commit()


def migrate_ontology() -> int:
    """Moves old data into the ontology: assigns domains, fills in project/tools and merges
    obvious duplicates. Runs at every start and is idempotent."""
    changed = 0
    for b in rows("SELECT id FROM blocks WHERE status='done' AND tools IS NULL"):
        meta = block_meta(rows("SELECT cwd,command FROM events WHERE block_id=?", (b["id"],)))
        with _lock:
            db.execute("UPDATE blocks SET project=?, tools=? WHERE id=?", (meta["project"], json.dumps(meta["tools"]), b["id"]))
        changed += 1
    for t in rows("SELECT * FROM topics WHERE domain IS NULL OR domain NOT IN (%s)" % ",".join("?" * len(onto.DOMAIN_KEYS)),
                  tuple(onto.DOMAIN_KEYS)):
        tools = []
        for b in rows("SELECT tools FROM blocks WHERE topic_id=?", (t["id"],)):
            tools += json.loads(b["tools"] or "[]")
        dom = onto.domain_for_tools(tools)                 # tools are more reliable than the name
        if dom == "sonstiges":
            dom = onto.domain_for_name(t["name"], t["description"] or "") or "sonstiges"
        with _lock:
            db.execute("UPDATE topics SET domain=? WHERE id=?", (dom, t["id"]))
        changed += 1
    with _lock:
        db.commit()
    topics = rows("SELECT id,name,domain FROM topics ORDER BY id")
    gone: set[int] = set()
    for i, a in enumerate(topics):
        if a["id"] in gone:
            continue
        dup = [b["id"] for b in topics[i + 1:] if b["id"] not in gone and b["domain"] == a["domain"] and onto.similar(a["name"], b["name"])]
        if dup:
            merge_topics(a["id"], dup)
            gone.update(dup)
            changed += len(dup)
    return changed


async def consolidate_with_llm() -> dict:
    """The model assigns all topics to domains and proposes merges."""
    topics = rows("SELECT id,name,description,domain FROM topics ORDER BY id")
    res = await asyncio.to_thread(cluster.consolidate_llm, topics)
    if not res:
        return {"ok": False, "reason": "Ollama not reachable or the answer was unusable"}
    ids = {t["id"] for t in topics}
    nd = nm = 0
    for k, v in (res.get("domains") or {}).items():
        try:
            tid = int(k)
        except (TypeError, ValueError):
            continue
        if tid in ids and v in onto.DOMAIN_KEYS:
            with _lock:
                db.execute("UPDATE topics SET domain=? WHERE id=?", (v, tid))
            nd += 1
    with _lock:
        db.commit()
    for group in res.get("merge") or []:
        try:
            g = [int(x) for x in group if int(x) in ids]
        except (TypeError, ValueError):
            continue
        if len(g) >= 2:
            merge_topics(g[0], g[1:])
            ids -= set(g[1:])
            nm += len(g) - 1
    with _lock:
        db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('llm_consolidated', ?)", (str(time.time()),))
        db.commit()
    return {"ok": True, "domains": nd, "merged": nm}


async def ontology_startup():
    n = await asyncio.to_thread(migrate_ontology)
    if n:
        await broadcast({"type": "topics_changed", "topic_id": None})
    # once: the model tidies up the migrated topics (if Ollama is running and there are topics)
    if not one("SELECT value FROM meta WHERE key='llm_consolidated'") and one("SELECT id FROM topics LIMIT 1"):
        if await asyncio.to_thread(cluster.pick_model):
            r = await consolidate_with_llm()
            if r.get("ok"):
                await broadcast({"type": "topics_changed", "topic_id": None})


async def model_probe():
    while True:
        await asyncio.sleep(60)
        if not status["clustering"]:
            status["model"] = await asyncio.to_thread(cluster.pick_model)
            status["ollama"] = bool(status["model"])
            await broadcast({"type": "status", "status": status_info()})


def status_info() -> dict:
    c = one("SELECT (SELECT count(*) FROM events) ev, (SELECT count(*) FROM topics) tp,"
            " (SELECT count(*) FROM blocks WHERE status IN ('open','ready')) pending")
    return {**status, "atuin": atuin_status, "events": c["ev"], "topics": c["tp"], "pending": c["pending"],
            "idle_seconds": IDLE_SECONDS, "gap_seconds": GAP_SECONDS}


@asynccontextmanager
async def lifespan(app):
    tasks = [asyncio.create_task(ontology_startup()), asyncio.create_task(cluster_worker()), asyncio.create_task(model_probe())]
    if ATUIN_ENABLED:
        tasks.append(asyncio.create_task(atuin_worker()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="joshi", lifespan=lifespan)


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path.startswith("/api"):
        tok = request.headers.get("x-joshi-token") or request.query_params.get("token")
        try:
            check_access(request.client.host if request.client else None, tok)
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    return await call_next(request)


async def read_payload(request: Request) -> dict:
    """JSON (PowerShell hook) or application/x-www-form-urlencoded (curl in bash/zsh)."""
    raw = (await request.body()).decode("utf-8-sig", "replace")
    if "json" in request.headers.get("content-type", ""):
        return json.loads(raw)
    return {k: v[-1] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}


@app.post("/api/events")
async def post_event(request: Request):
    d = await read_payload(request)
    ev = await ingest(d)
    return {"id": ev["id"], "block_id": ev["block_id"]}


@app.post("/api/outputs")
async def post_output(request: Request):
    return await attach_output(await read_payload(request))


@app.get("/api/events")
def get_events(limit: int = 200):
    evs = rows("SELECT * FROM events ORDER BY id DESC LIMIT ?", (min(limit, 2000),))
    return list(reversed(evs))


@app.get("/api/blocks/open")
def open_blocks():
    return rows("SELECT id,skey,status,start_ts,end_ts,n FROM blocks WHERE status IN ('open','ready') ORDER BY start_ts")


@app.get("/api/topics")
def get_topics():
    topics = rows("SELECT * FROM topics ORDER BY updated DESC")
    blocks = rows("SELECT id,topic_id,start_ts,end_ts,n,title,summary,method,project,tools FROM blocks WHERE status='done' ORDER BY start_ts DESC")
    evs = rows("SELECT e.id,e.block_id,e.ts,e.shell,e.cwd,e.command,e.exit_code FROM events e"
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
    return {"domains": [{"key": k, "name": n, "description": d, "slot": i + 1} for i, (k, n, d) in enumerate(onto.DOMAINS)]}


@app.post("/api/ontology/consolidate")
async def post_consolidate():
    n = await asyncio.to_thread(migrate_ontology)
    r = await consolidate_with_llm()
    await broadcast({"type": "topics_changed", "topic_id": None})
    return {**r, "heuristic_changes": n}


@app.get("/api/status")
def get_status():
    return status_info()


@app.post("/api/blocks/{bid}/recluster")
async def recluster(bid: int):
    with _lock:
        db.execute("UPDATE blocks SET status='ready' WHERE id=?", (bid,))
        db.commit()
    return {"ok": True}


@app.get("/api/export.md", response_class=PlainTextResponse)
def export_md():
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


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
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
    return FileResponse(STATIC / "index.html")
