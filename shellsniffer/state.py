"""Runtime state shared by the API and the background workers.

- ``settings``: user switches from the UI (capture mode, honeypot), persisted in the ``meta`` table
- ``status``: what the workers are doing right now (clustering, Ollama model, last error)
- ``atuin_status``: health of every Atuin source
- live updates: the set of connected browsers and :func:`broadcast`
"""
import asyncio
import json

from fastapi import WebSocket

from . import db
from .config import (ATUIN_DEFAULT, DATA_DIR, DB_PATH, GAP_SECONDS, HONEYPOT_DEFAULT,
                     HONEYPOT_DIR, IDLE_SECONDS, VECTORIZE_DEFAULT)

# ---------------------------------------------------------------- Settings (switchable in the UI)


def _load_setting(key: str, default: bool) -> bool:
    v = db.meta_get(f"setting_{key}")
    return default if v is None else v == "1"


settings: dict[str, bool] = {
    "atuin": _load_setting("atuin", ATUIN_DEFAULT),           # True: read Atuin; False: hooks only
    "honeypot": _load_setting("honeypot", HONEYPOT_DEFAULT),  # write per-shell log files (only without Atuin)
    "vectorize": _load_setting("vectorize", VECTORIZE_DEFAULT),  # keep embedding new commands in the background
    "dog": _load_setting("dog", True),                        # the mascot roams the web UI
    "ollama": _load_setting("ollama", True),                  # False: never contact Ollama (no LLM features)
}


SETTING_KEYS = tuple(settings)   # the switches the UI may change via POST /api/settings


def save_setting(key: str, value: bool) -> None:
    settings[key] = value
    db.meta_set(f"setting_{key}", "1" if value else "0")


def honeypot_active() -> bool:
    """Honeypot logging only runs in the mode without Atuin and when switched on."""
    return settings["honeypot"] and not settings["atuin"]


# ---------------------------------------------------------------- Worker status
status: dict = {
    "clustering": None,          # id of the session Ollama is working on right now
    "cluster_requested": False,  # True after a click on "SniffMagic", until the queue is empty
    "queued": 0,                 # sessions still in the SniffMagic queue
    "model": None,               # Ollama model in use (None = not reachable, heuristic fallback)
    "ollama": False,
    "last_error": None,
    # background vectorization for the hybrid search (embeddings.vectorize_worker)
    "vectors": {"model": None, "dedicated": None, "running": False, "error": None, "done": 0, "total": 0},
}
vector_wake = asyncio.Event()   # set for every new command; wakes the continuous shrooming worker
atuin_status: dict[str, dict] = {}   # source name ("local", "wsl:<distro>", "worker") -> {"ok": ..., ...}


def status_info() -> dict:
    """Everything the UI's header and info dialog show; sent on connect and after every change."""
    c = db.one("SELECT (SELECT count(*) FROM events) ev, (SELECT count(*) FROM topics) tp,"
               " (SELECT count(*) FROM blocks WHERE status IN ('open','ready')) pending")
    return {**status,
            "atuin": atuin_status if settings["atuin"] else {},
            "events": c["ev"], "topics": c["tp"], "pending": c["pending"],
            "idle_seconds": IDLE_SECONDS, "gap_seconds": GAP_SECONDS,
            "settings": dict(settings), "honeypot_active": honeypot_active(),
            "honeypot_dir": str(HONEYPOT_DIR), "data_dir": str(DATA_DIR), "db_path": str(DB_PATH),
            "vectors": {**status["vectors"], **_vector_coverage(status["vectors"]["model"])}}


def _vector_coverage(model: str | None) -> dict:
    """How many commands have a vector; without a known model, the model with the most vectors counts."""
    if not model:
        m = db.one("SELECT model, count(*) AS n FROM embeddings GROUP BY model ORDER BY n DESC LIMIT 1")
        model = m["model"] if m else None
    total = db.one("SELECT count(*) AS n FROM events")["n"]
    done = db.one("SELECT count(*) AS n FROM embeddings WHERE model=?", (model,))["n"] if model else 0
    return {"done": min(done, total), "total": total, "model": model}


# ---------------------------------------------------------------- Live updates to the browser
clients: set[WebSocket] = set()


async def broadcast(msg: dict) -> None:
    """Sends a message to every connected browser; drops connections that are gone.

    Message types: event, event_update, block, status, topics_changed, reload.
    """
    data = json.dumps(msg, default=str)
    for ws in list(clients):
        try:
            await ws.send_text(data)
        except Exception:
            clients.discard(ws)


async def broadcast_status() -> None:
    await broadcast({"type": "status", "status": status_info()})
