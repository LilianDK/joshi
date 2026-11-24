"""Ask the shroom: an answer from the local chat model, grounded in your own logs (RAG).

When you press Enter in "Sniff logs with shroom", the hybrid search (:func:`search.hybrid_search`)
first finds the most relevant documented sessions and commands. They are numbered and handed to
the Ollama chat model as the only source of truth; the model answers the question and cites the
sources as [n]. The answer is streamed to the browser as NDJSON lines:

    {"type": "sources", "sources": [{"n": 1, "kind": "doc"|"command", ...}, ...]}
    {"type": "token", "text": "..."}            (many)
    {"type": "done"}   or   {"type": "error", "detail": "..."}

Only runs on an explicit question – never in the background, never while Ollama is disabled.
"""
import json
import time
import urllib.request

from . import cluster, search

MAX_DOCS = 6
MAX_COMMANDS = 14
OUTPUT_CHARS = 400

SYSTEM_PROMPT = """You are "the shroom", the assistant of shellSniffer, a tool that records a user's shell work.
Answer the user's question ONLY from the numbered sources below: documented work sessions and shell
commands with their output, all from the user's own machine.

Rules:
- Cite every statement with the source numbers in square brackets, e.g. [2] or [3][5].
- If the sources do not contain the answer, say so in one sentence. Do not invent commands, paths or results.
- When the user asks how to do something, give the exact commands from the sources in a code block.
- Be short and concrete: a few sentences or a short list. Use Markdown.
- Answer in the language of the question."""


def _fmt_time(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "?"


def build_context(q: str, start: float | None, end: float | None, semantic_weight: float) -> tuple[list[dict], str]:
    """Runs the hybrid search and turns the best hits into numbered sources + prompt text."""
    from . import db
    res = search.hybrid_search(q, MAX_COMMANDS * 2, start, end, semantic_weight)
    sources, lines = [], []
    for d in (res.get("docs") or [])[:MAX_DOCS]:
        full = db.one("SELECT summary FROM blocks WHERE id=?", (d["id"],)) or {}
        n = len(sources) + 1
        title = (d.get("title_hl") or "").replace("\x01", "").replace("\x02", "")
        sources.append({"n": n, "kind": "doc", "id": d["id"], "topic_id": d.get("topic_id"),
                        "title": title, "topic": d.get("topic_name"), "ts": d.get("start_ts")})
        lines.append(f"[{n}] Documented session \"{title}\" (topic: {d.get('topic_name') or '-'}, {_fmt_time(d.get('start_ts'))}):\n"
                     f"{(full.get('summary') or '').strip()}")
    ids = [h["id"] for h in (res.get("hits") or [])[:MAX_COMMANDS]]
    if ids:
        rows = {r["id"]: r for r in db.rows(
            f"SELECT id, ts, shell, cwd, command, exit_code, output FROM events WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))}
        for h in res["hits"][:MAX_COMMANDS]:
            e = rows.get(h["id"])
            if not e:
                continue
            n = len(sources) + 1
            sources.append({"n": n, "kind": "command", "id": e["id"], "topic_id": h.get("topic_id"),
                            "command": e["command"], "ts": e["ts"], "shell": e["shell"]})
            out = (e["output"] or "").strip()
            if len(out) > OUTPUT_CHARS:
                out = out[:OUTPUT_CHARS] + " …"
            lines.append(f"[{n}] Command ({e['shell']}, {_fmt_time(e['ts'])}, in {e['cwd'] or '?'}, exit {e['exit_code']}):\n"
                         f"$ {e['command']}" + (f"\nOutput:\n{out}" if out else ""))
    return sources, "\n\n".join(lines)


def stream_answer(q: str, start: float | None = None, end: float | None = None, semantic_weight: float = 0.5):
    """Generator of NDJSON lines (bytes) – see the module docstring."""
    def line(obj) -> bytes:
        return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")

    try:
        if not cluster.enabled():
            raise cluster.OllamaDisabled("Ollama is disabled – enable it to ask the shroom")
        sources, context = build_context(q, start, end, semantic_weight)
        yield line({"type": "sources", "sources": sources})
        if not sources:
            yield line({"type": "token", "text": "The shroom found nothing in your logs for this question."})
            yield line({"type": "done"})
            return
        model = cluster.current_model() or cluster.pick_model()
        if not model:
            raise RuntimeError("no Ollama chat model available – run e.g.: ollama pull llama3.1")
        yield line({"type": "model", "model": model})
        payload = {"model": model, "stream": True, "options": {"temperature": 0.2},
                   "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": f"Sources:\n\n{context}\n\nQuestion: {q}"}]}
        if not cluster.enabled():
            raise cluster.OllamaDisabled("Ollama is disabled")
        req = urllib.request.Request(f"{cluster.OLLAMA_URL}/api/chat", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            for raw in r:
                if not raw.strip():
                    continue
                chunk = json.loads(raw)
                text = (chunk.get("message") or {}).get("content") or ""
                if text:
                    yield line({"type": "token", "text": text})
                if chunk.get("done"):
                    break
        yield line({"type": "done"})
    except Exception as e:   # shown in the answer box
        yield line({"type": "error", "detail": str(e)})
