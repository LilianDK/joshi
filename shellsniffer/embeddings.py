"""Semantic vectors for the hybrid search ("Sniff logs with shroom") and "ask the shroom".

Two kinds of vectors, both computed by a local Ollama embedding model and stored normalised
(float32) per model, so the cosine similarity is a plain dot product:

- ``embeddings``      one per command (command + directory + start of the output)
- ``doc_embeddings``  one per documented session (SniffMagic title + topic + summary); a hash of
                      the text is stored, so re-documented sessions are embedded again

They are computed

- on demand: every hybrid search first embeds a limited number of missing vectors
  (EMBED_PER_SEARCH), so the index grows with use;
- right after SniffMagic documented a session (:func:`embed_doc_now`);
- continuously, only while **Continuous shrooming** is on in ⚙ Settings (setting ``vectorize``):
  :func:`vectorize_worker` embeds every new command the moment it arrives and works off the backlog.

Model choice: SHELLSNIFFER_EMBED_MODEL, else the first installed embedding model, else the chat
model as a fallback (works, but slower and less precise – the UI then suggests
``ollama pull nomic-embed-text``).
"""
import array
import asyncio
import hashlib
import math
import os

from . import db, ollama
from .state import broadcast_status, settings, status, vector_wake

EMBED_MODEL = os.environ.get("SHELLSNIFFER_EMBED_MODEL", "")
EMBED_PER_SEARCH = int(os.environ.get("SHELLSNIFFER_EMBED_PER_SEARCH", 200))   # new vectors per search, at most
VECTORIZE_CHUNK = int(os.environ.get("SHELLSNIFFER_VECTORIZE_CHUNK", 64))      # per background round
BATCH = 32                                                                     # texts per Ollama request

_query_cache: dict[tuple[str, str], list[float]] = {}


class EmbeddingError(RuntimeError):
    """Ollama disabled / not reachable or no usable model (shown to the user)."""


# ---------------------------------------------------------------- model + vectors
def pick_model() -> tuple[str, bool]:
    """Returns (model name, is a dedicated embedding model)."""
    if not ollama.enabled():
        raise EmbeddingError("Ollama is disabled – enable it to sniff with shroom")
    try:
        names = ollama.list_models()
    except Exception as e:
        raise EmbeddingError("Ollama is not reachable – start Ollama to sniff with shroom") from e
    if EMBED_MODEL:
        for n in names:
            if n == EMBED_MODEL or n.split(":")[0] == EMBED_MODEL:
                return n, True
    for n in names:
        if ollama.is_embedding_model(n):
            return n, True
    chat = ollama.chat_model()
    if chat:
        return chat, False
    raise EmbeddingError("no Ollama model installed – run: ollama pull nomic-embed-text")


def _embed(model: str, texts: list[str]) -> list[list[float]]:
    try:
        return ollama.embed(model, texts)
    except Exception as e:
        raise EmbeddingError(f"Ollama could not compute embeddings with {model}: {e}") from e


def _normalise(v: list[float]) -> bytes:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return array.array("f", (x / n for x in v)).tobytes()


def query_vector(model: str, q: str) -> list[float]:
    """The normalised vector of a search query (cached)."""
    key = (model, q)
    if key not in _query_cache:
        if len(_query_cache) > 256:
            _query_cache.clear()
        vec = array.array("f")
        vec.frombytes(_normalise(_embed(model, [q])[0]))
        _query_cache[key] = list(vec)
    return _query_cache[key]


def _top_k(rows: list[dict], key: str, qvec: list[float], k: int) -> list[tuple[int, float]]:
    """Ranks stored vectors by cosine similarity with ``qvec``: [(row[key], similarity)], best first."""
    scored = []
    for r in rows:
        vec = array.array("f")
        vec.frombytes(r["vec"])
        if len(vec) == len(qvec):
            scored.append((r[key], sum(a * b for a, b in zip(qvec, vec))))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


# ---------------------------------------------------------------- commands
def _command_text(ev: dict) -> str:
    """What gets embedded for a command: the command, its directory and the start of its output."""
    return f"{ev['command']}\n(in {ev['cwd']})\n{(ev['output'] or '')[:800]}"


def index_missing(model: str, where: str = "", args: tuple = (), limit: int = EMBED_PER_SEARCH) -> dict:
    """Embeds up to ``limit`` commands (matching the SQL condition ``where`` on events ``e``) that have
    no vector for ``model`` yet. Returns {"new": n, "missing": still missing afterwards}."""
    base = (f"FROM events e LEFT JOIN embeddings v ON v.event_id = e.id AND v.model = ?"
            f" WHERE v.event_id IS NULL{where}")
    todo = db.rows(f"SELECT e.id, e.command, e.cwd, e.output {base} ORDER BY e.ts DESC LIMIT ?", (model, *args, limit))
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        vecs = _embed(model, [_command_text(e) for e in chunk])
        with db.lock:
            db.conn.executemany("INSERT OR REPLACE INTO embeddings(event_id, model, vec) VALUES(?,?,?)",
                                [(e["id"], model, _normalise(v)) for e, v in zip(chunk, vecs)])
            db.conn.commit()
    missing = db.one(f"SELECT count(*) AS n {base}", (model, *args))["n"]
    return {"new": len(todo), "missing": missing}


def nearest(model: str, qvec: list[float], where: str = "", args: tuple = (), k: int = 100) -> list[tuple[int, float]]:
    """The ``k`` commands most similar to ``qvec``, as [(event_id, similarity)]."""
    rows = db.rows(f"SELECT v.event_id, v.vec FROM embeddings v JOIN events e ON e.id = v.event_id"
                   f" WHERE v.model = ?{where}", (model, *args))
    return _top_k(rows, "event_id", qvec, k)


# ---------------------------------------------------------------- documentation (SniffMagic summaries)
def _doc_text(b: dict) -> str:
    return f"{b['title'] or ''}\nTopic: {b['topic'] or ''}\n{b['summary'] or ''}"


def index_docs(model: str, block_ids: list[int] | None = None, limit: int = EMBED_PER_SEARCH) -> dict:
    """Embeds the documentation of documented sessions that has no vector for ``model`` yet – or whose
    text changed since (re-documented). ``block_ids`` restricts it to these sessions.
    Returns {"new": n, "missing": still missing afterwards}."""
    cond = f" AND b.id IN ({','.join('?' * len(block_ids))})" if block_ids else ""
    rows = db.rows(f"SELECT b.id, b.title, b.summary, t.name AS topic, v.text_hash FROM blocks b"
                   f" LEFT JOIN topics t ON t.id = b.topic_id"
                   f" LEFT JOIN doc_embeddings v ON v.block_id = b.id AND v.model = ?"
                   f" WHERE b.status = 'done'{cond}", (model, *(block_ids or [])))
    todo = [(b, h) for b in rows if b["text_hash"] != (h := hashlib.sha1(_doc_text(b).encode("utf-8")).hexdigest())]
    todo_now = todo[:limit]
    for i in range(0, len(todo_now), BATCH):
        chunk = todo_now[i:i + BATCH]
        vecs = _embed(model, [_doc_text(b) for b, _ in chunk])
        with db.lock:
            db.conn.executemany("INSERT OR REPLACE INTO doc_embeddings(block_id, model, vec, text_hash) VALUES(?,?,?,?)",
                                [(b["id"], model, _normalise(v), h) for (b, h), v in zip(chunk, vecs)])
            db.conn.commit()
    return {"new": len(todo_now), "missing": len(todo) - len(todo_now)}


def embed_doc_now(block_id: int) -> None:
    """Called right after SniffMagic documented a session: vectorize its documentation at once.
    Best effort – without an embedding model the next hybrid search catches up."""
    try:
        model, _ = pick_model()
        index_docs(model, [block_id])
    except Exception:
        pass


def nearest_docs(model: str, qvec: list[float], where: str = "", args: tuple = (), k: int = 10) -> list[tuple[int, float]]:
    """The ``k`` documented sessions whose documentation is most similar to ``qvec``."""
    rows = db.rows(f"SELECT v.block_id, v.vec FROM doc_embeddings v JOIN blocks b ON b.id = v.block_id"
                   f" WHERE v.model = ? AND b.status = 'done'{where}", (model, *args))
    return _top_k(rows, "block_id", qvec, k)


# ---------------------------------------------------------------- continuous shrooming
async def vectorize_worker() -> None:
    """Background vectorization – runs only while ``vectorize`` is on and Ollama is enabled.

    Works off the backlog in chunks of VECTORIZE_CHUNK; when everything is indexed it sleeps until
    :func:`ingest.ingest` signals a new command (``state.vector_wake``) and vectorizes it right away.
    Progress and errors go to ``state.status["vectors"]``.
    """
    idle_before = True
    while True:
        try:
            if not settings.get("vectorize") or not settings.get("ollama", True):
                if status["vectors"]["running"]:
                    status["vectors"]["running"] = False
                    await broadcast_status()
                await asyncio.sleep(3)
                continue
            model, dedicated = await asyncio.to_thread(pick_model)
            res = await asyncio.to_thread(index_missing, model, "", (), VECTORIZE_CHUNK)
            docs = await asyncio.to_thread(index_docs, model)
            status["vectors"].update(model=model, dedicated=dedicated, running=True, error=None)
            busy = res["new"] + docs["new"] > 0
            if busy or idle_before:       # tell the UI about progress (and once when it gets idle)
                await broadcast_status()
            idle_before = busy
            if res["missing"] or docs["missing"]:
                await asyncio.sleep(0.2)  # backlog: keep going
                continue
            vector_wake.clear()           # up to date: sleep until a new command arrives
            try:
                await asyncio.wait_for(vector_wake.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            raise
        except Exception as e:            # Ollama gone etc.: report and retry later, never die
            status["vectors"].update(running=False, error=str(e))
            await broadcast_status()
            await asyncio.sleep(30)
