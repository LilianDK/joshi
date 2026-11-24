"""Background worker that imports new commands from Atuin's history.

Every ATUIN_POLL seconds it reads the local Atuin database (and, every second round, the one of
every running WSL distribution – see :mod:`atuin` for the readers). Only finished commands are
imported; a per-source watermark (``sources`` table) remembers how far the import got.

In the mode *without Atuin* (UI switch) the reader pauses and only parked hook reports are flushed.
"""
import asyncio
import os
import time

from . import atuin, db
from .config import ATUIN_BACKFILL_DAYS, ATUIN_POLL
from .ingest import flush_unmatched_outputs, ingest
from .state import atuin_status, broadcast, settings

_retry_at: dict[str, float] = {}   # source -> time before which a failing source is not read again


def _source(name: str) -> dict:
    """The ``sources`` row of an Atuin source; created with a watermark ATUIN_BACKFILL_DAYS back."""
    s = db.one("SELECT * FROM sources WHERE name=?", (name,))
    if not s:
        wm = int((time.time() - ATUIN_BACKFILL_DAYS * 86400) * 1e9)
        db.execute("INSERT INTO sources(name,watermark) VALUES(?,?)", (name, wm))
        s = db.one("SELECT * FROM sources WHERE name=?", (name,))
    return s


async def import_source(name: str, shell: str, reader) -> int:
    """Imports new finished commands of one source. ``reader(watermark_ns, db_path)`` returns
    ``{"rows": [...], "db": path}`` or ``{"error": ...}``. Returns the number of new commands."""
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
    backlog = len(rws) > 50            # bulk import: no live update per command, one reload at the end
    n = 0
    for r in finished:
        ev = await ingest({
            "command": r["command"], "cwd": r["cwd"], "session": r["session"], "host": r["host"],
            "shell": shell, "exit_code": r["exit"], "duration_ms": r["duration_ns"] // 1_000_000,
            "started_at": r["ts_ns"] / 1e6, "atuin_uuid": r["uuid"],
        }, source="atuin", quiet=backlog)
        n += 1 if ev else 0

    if rws:
        # stop just before the oldest still-running command, so it is picked up once it finishes;
        # otherwise never go back (already imported entries are recognised by their UUID anyway)
        wm = (min(running) - 1) if running else max(rws[-1]["ts_ns"], src["watermark"])
        db.execute("UPDATE sources SET watermark=?, db=?, imported=imported+?, last_ok=? WHERE name=?",
                   (wm, res.get("db"), n, time.time(), name))
    elif res.get("db") != src["db"]:
        db.execute("UPDATE sources SET db=?, last_ok=? WHERE name=?", (res.get("db"), time.time(), name))
    atuin_status[name] = {"ok": True, "db": res.get("db"), "new": n}
    if backlog and n:
        await broadcast({"type": "reload"})
    return n


def skip_history_until_now() -> None:
    """Moves all watermarks to now. Used when switching back to Atuin: the hooks already reported
    everything typed in the meantime, so it must not be imported a second time."""
    db.execute("UPDATE sources SET watermark=?", (int(time.time() * 1e9),))


async def atuin_worker() -> None:
    local_shell = "pwsh" if os.name == "nt" else (os.path.basename(os.environ.get("SHELL", "")) or "bash")
    tick = 0
    while True:
        if not settings["atuin"]:
            await flush_unmatched_outputs()   # reports parked before the switch
            await asyncio.sleep(ATUIN_POLL)
            continue
        try:
            await import_source("local", local_shell, atuin.read_local)
            await flush_unmatched_outputs()
            if tick % 2 == 0:
                for d in await asyncio.to_thread(atuin.running_wsl_distros):
                    await import_source(f"wsl:{d}", "wsl", lambda wm, dbp, d=d: atuin.read_wsl(d, wm, dbp))
        except asyncio.CancelledError:
            raise
        except Exception as e:   # the worker must never die
            atuin_status["worker"] = {"ok": False, "error": repr(e)}
        tick += 1
        await asyncio.sleep(ATUIN_POLL)
