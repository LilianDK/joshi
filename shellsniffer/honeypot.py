"""Honeypot log: one plain-text file per shell session with every command and its output.

Active only in the mode *without Atuin* and when the "Honeypot log" switch is on
(see :func:`state.honeypot_active`). Files go to ``HONEYPOT_DIR`` (default: ``<project>/honeypot``)
and are named ``<date>_<shell>_<host>_<session>.log``. Entries look like::

    ===== 2026-11-24 14:03:12 | pwsh | MYPC | D:\\proj | exit 0 | 1200 ms
    $ docker compose up -d
    Container web Started

The event passed in has already gone through the secret filter.
"""
import re
import time

from . import db
from .config import HONEYPOT_DIR
from .state import status


def _safe(s: str) -> str:
    """Turns a shell/host/session name into something usable in a file name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s or "").strip("-")[:60] or "nosession"


def file_name(ev: dict) -> str:
    day = time.strftime("%Y-%m-%d", time.localtime(ev["ts"]))
    return f"{day}_{_safe(ev['shell'])}_{_safe(ev['host'])}_{_safe(ev['session'])}.log"


def format_entry(ev: dict) -> str:
    head = " | ".join(x for x in [
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ev["ts"])), ev["shell"], ev["host"], ev["cwd"],
        f"exit {ev['exit_code']}" if ev["exit_code"] is not None else "",
        f"{ev['duration_ms']} ms" if ev["duration_ms"] is not None else ""] if x)
    return f"===== {head}\n$ {ev['command']}\n" + (ev["output"] + "\n" if ev["output"] else "") + "\n"


def write(ev: dict) -> None:
    """Appends one command to its session's log file. Errors are reported in the status, never raised."""
    try:
        HONEYPOT_DIR.mkdir(parents=True, exist_ok=True)
        with db.lock, open(HONEYPOT_DIR / file_name(ev), "a", encoding="utf-8", newline="\n") as f:
            f.write(format_entry(ev))
    except OSError as e:
        status["last_error"] = f"honeypot: {e!r}"
