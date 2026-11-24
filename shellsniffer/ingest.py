"""Turning hook reports and Atuin entries into stored commands ("events").

Two ways in:
- :func:`ingest` – a complete command (from a hook without Atuin, from the Atuin import,
  or a hook report nobody else claimed).
- :func:`attach_output` – a hook in a shell with Atuin reports only the *output*; it is linked
  to the matching Atuin entry, or parked until that entry arrives (see :func:`flush_unmatched_outputs`).

Every command passes the secret filter (:func:`redact.redact`) before it is stored, sent to the
browser, written to the honeypot log or ever shown to the model.
"""
import json
import re
import time

from . import db, honeypot
from .config import FALLBACK_AFTER, GAP_SECONDS, MATCH_WINDOW, MAX_BLOCK, MAX_OUTPUT
from .redact import redact
from .state import broadcast, honeypot_active, settings, vector_wake


# ---------------------------------------------------------------- Helpers
def _norm(cmd: str | None) -> str:
    """Whitespace-normalised command text, for matching hook reports against Atuin entries."""
    return " ".join((cmd or "").split())


def _to_int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def clip_output(out: str | None) -> str | None:
    """Cleans captured output (line endings, control characters, zsh prompt leftovers) and limits it
    to MAX_OUTPUT characters, keeping the start and the end."""
    if not out:
        return None
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", out)
    lines = [ln.rstrip() for ln in out.split("\n")]
    while lines and re.fullmatch(r"[#%]?", lines[-1]):           # zsh PROMPT_SP leftovers at the end
        lines.pop()
    out = "\n".join(lines).strip("\n")
    if len(out) > MAX_OUTPUT:
        head = MAX_OUTPUT * 2 // 3
        out = out[:head] + f"\n… ({len(out) - MAX_OUTPUT} characters cut) …\n" + out[-(MAX_OUTPUT - head):]
    return out or None


def block_info(bid: int) -> dict | None:
    """The fields of a session the browser needs to show its state."""
    return db.one("SELECT id,skey,status,start_ts,end_ts,n,topic_id,title,method FROM blocks WHERE id=?", (bid,))


def _assign_block(skey: str, ts: float) -> tuple[int, int | None]:
    """Adds a command at time ``ts`` to the open session of shell ``skey`` or starts a new one.

    A new session starts after GAP_SECONDS without commands or after MAX_BLOCK commands.
    Returns (session id, id of the session that was closed by this, if any).
    """
    with db.lock:
        b = db.conn.execute("SELECT * FROM blocks WHERE skey=? AND status='open' ORDER BY id DESC LIMIT 1", (skey,)).fetchone()
        if b and (ts - b["end_ts"] <= GAP_SECONDS) and b["n"] < MAX_BLOCK:
            db.conn.execute("UPDATE blocks SET end_ts=max(end_ts,?), n=n+1 WHERE id=?", (ts, b["id"]))
            return b["id"], None
        closed = None
        if b:
            db.conn.execute("UPDATE blocks SET status='ready' WHERE id=?", (b["id"],))
            closed = b["id"]
        cur = db.conn.execute("INSERT INTO blocks(skey,status,start_ts,end_ts,n) VALUES(?,?,?,?,1)", (skey, "open", ts, ts))
        return cur.lastrowid, closed


def _take_pending_output(ev: dict) -> str | None:
    """Finds a parked hook output that belongs to this Atuin entry and claims it."""
    cands = db.rows("SELECT * FROM outputs WHERE event_id IS NULL AND session=? AND abs(ts-?) < ? ORDER BY abs(ts-?)",
                    (ev["session"], ev["ts"], MATCH_WINDOW, ev["ts"]))
    for o in cands:
        if _norm(o["command"]) == _norm(ev["command"]):
            with db.lock:
                db.conn.execute("UPDATE outputs SET event_id=-1 WHERE id=?", (o["id"],))
            return o["output"]
    return None


# ---------------------------------------------------------------- Commands
async def ingest(d: dict, source: str = "hook", quiet: bool = False) -> dict | None:
    """Stores one command and tells the browsers about it.

    ``d`` uses the hook's field names: command, shell, host, session, cwd, exit_code, duration_ms,
    started_at (ms since epoch), output, atuin_uuid. ``source`` is "hook", "atuin" or "import"; ``quiet``
    suppresses the live updates (bulk imports send one "reload" instead). ``source`` "import" is
    used for uploaded dumps (see :mod:`importer`); ``atuin_uuid`` then holds a stable import id.

    Returns the stored event, or None if it was a duplicate or only linked to an existing one.
    Raises ValueError if there is no command.
    """
    command = (d.get("command") or "").strip()
    if not command:
        raise ValueError("command missing")
    now = time.time()
    try:
        ts = float(d["started_at"]) / 1000.0 if d.get("started_at") not in (None, "") else now
    except ValueError:
        ts = now
    if source == "hook" and abs(ts - now) > 86400:   # broken clock / time zone -> server time
        ts = now

    ev = {
        "ts": ts, "received": now,
        "shell": (d.get("shell") or "?")[:30],
        "host": (d.get("host") or "")[:80],
        "session": str(d.get("session") or "")[:80],
        "cwd": d.get("cwd") or "",
        "command": redact(command),
        "exit_code": _to_int(d.get("exit_code")),
        "duration_ms": _to_int(d.get("duration_ms")),
        "output": redact(clip_output(d.get("output"))),
        "atuin_uuid": d.get("atuin_uuid"),
        "source": source,
    }
    if ev["atuin_uuid"] and db.one("SELECT id FROM events WHERE atuin_uuid=?", (ev["atuin_uuid"],)):
        return None   # already imported (e.g. via Atuin sync from another source)
    if source == "atuin":
        # Did the hook already create this command as a fallback entry? Then just link it.
        for e in db.rows("SELECT id,command FROM events WHERE session=? AND atuin_uuid IS NULL AND abs(ts-?) < ?",
                         (ev["session"], ev["ts"], MATCH_WINDOW)):
            if _norm(e["command"]) == _norm(ev["command"]):
                db.execute("UPDATE events SET atuin_uuid=?, source='atuin' WHERE id=?", (ev["atuin_uuid"], e["id"]))
                return None
        if not ev["output"]:
            ev["output"] = _take_pending_output(ev)

    block_id, closed = _assign_block(f'{ev["host"]}|{ev["shell"]}|{ev["session"]}', ts)
    ev["block_id"] = block_id
    ev["id"] = db.execute(
        "INSERT INTO events(ts,received,shell,host,session,cwd,command,exit_code,duration_ms,output,block_id,atuin_uuid,source)"
        " VALUES(:ts,:received,:shell,:host,:session,:cwd,:command,:exit_code,:duration_ms,:output,:block_id,:atuin_uuid,:source)",
        ev).lastrowid
    if settings.get("vectorize"):
        vector_wake.set()   # continuous shrooming: vectorize this command right away
    if honeypot_active() and source != "import":   # the honeypot logs live shells only
        honeypot.write(ev)
    if not quiet:
        await broadcast({"type": "event", "event": ev})
        await broadcast({"type": "block", "block": block_info(block_id)})
        if closed:
            await broadcast({"type": "block", "block": block_info(closed)})
    return ev


async def attach_output(d: dict) -> dict:
    """Output report from a hook in a shell that has Atuin loaded.

    With Atuin: attach it to the matching Atuin entry right away, or park it in ``outputs``.
    Without Atuin: the report (it contains the full command) becomes the command itself.
    """
    if not settings["atuin"]:
        if not (d.get("command") or "").strip():
            return {"matched": False}
        ev = await ingest(d, source="hook")
        return {"matched": True, "event_id": ev["id"] if ev else None}

    out = redact(clip_output(d.get("output")))
    session = str(d.get("session") or "")
    command = redact((d.get("command") or "").strip())
    try:
        ts = float(d.get("started_at")) / 1000.0
    except (TypeError, ValueError):
        ts = time.time()
    if not session:
        return {"matched": False}
    for e in db.rows("SELECT id,command FROM events WHERE session=? AND output IS NULL AND abs(ts-?) < ? ORDER BY abs(ts-?)",
                     (session, ts, MATCH_WINDOW, ts)):
        if _norm(e["command"]) == _norm(command):
            if out:
                db.execute("UPDATE events SET output=? WHERE id=?", (out, e["id"]))
                await broadcast({"type": "event_update", "event": db.one("SELECT * FROM events WHERE id=?", (e["id"],))})
            return {"matched": True, "event_id": e["id"]}
    with db.lock:
        db.conn.execute("DELETE FROM outputs WHERE received < ?", (time.time() - 3600,))
        db.conn.execute("INSERT INTO outputs(received,session,command,ts,output,payload) VALUES(?,?,?,?,?,?)",
                        (time.time(), session, command, ts, out, json.dumps(d, default=str)))
        db.conn.commit()
    return {"matched": False, "pending": True}


async def flush_unmatched_outputs() -> None:
    """Parked hook reports without an Atuin entry after FALLBACK_AFTER seconds (Atuin does not record
    in this shell, or cannot be read) become commands themselves, so nothing is lost."""
    for o in db.rows("SELECT * FROM outputs WHERE event_id IS NULL AND received < ?", (time.time() - FALLBACK_AFTER,)):
        db.execute("UPDATE outputs SET event_id=-2 WHERE id=?", (o["id"],))
        try:
            d = json.loads(o["payload"] or "{}")
        except ValueError:
            d = {}
        if d.get("command"):
            await ingest(d, source="hook")
