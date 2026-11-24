"""Reads commands from Atuin (https://atuin.sh) – directly from the local Atuin
database, and in WSL via `wsl.exe ... python3` for every running distribution.

Atuin creates an entry when a command starts (exit/duration = -1) and completes it
when the command ends. We only import finished entries; the watermark stays just
before the oldest command that is still running.
"""
import base64
import json
import os
import shutil
import subprocess
import sys

# This code runs both locally and (as a string) inside WSL – hence self-contained.
READER = r'''
import json, os, re, sqlite3, subprocess, sys
def find_db():
    env = os.environ.get("ATUIN_DB_PATH")
    if env and os.path.exists(env):
        return env
    home = os.path.expanduser("~")
    exe = None
    for c in ("atuin", os.path.join(home, ".atuin", "bin", "atuin"), os.path.join(home, ".cargo", "bin", "atuin")):
        try:
            out = subprocess.run([c, "info"], capture_output=True, text=True, timeout=8).stdout
            m = re.search(r"""([A-Za-z]:\\[^"'\n]*?history\.db|/[^"'\s]*history\.db)""", out)
            if m and os.path.exists(m.group(1)):
                return m.group(1)
            exe = c
            break
        except Exception:
            pass
    cands = [os.path.join(home, ".local", "share", "atuin", "history.db"),
             os.path.join(home, ".atuin", "data", "history.db")]
    for v in ("APPDATA", "LOCALAPPDATA"):
        if os.environ.get(v):
            cands.append(os.path.join(os.environ[v], "atuin", "history.db"))
    for c in cands:
        if os.path.exists(c):
            return c
    return None
def main(watermark, limit, db=None):
    if not db or not os.path.exists(db):
        db = find_db()
    if not db:
        print(json.dumps({"error": "no Atuin database found"})); return
    con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=5)
    rows = con.execute(
        "SELECT id, timestamp, duration, exit, command, cwd, session, hostname FROM history "
        "WHERE deleted_at IS NULL AND timestamp > ? ORDER BY timestamp LIMIT ?", (watermark, limit)).fetchall()
    con.close()
    out = []
    for r in rows:
        out.append({"uuid": r[0], "ts_ns": r[1], "duration_ns": r[2], "exit": r[3], "command": r[4],
                    "cwd": r[5], "session": r[6], "host": r[7]})
    print(json.dumps({"db": db, "rows": out}))
main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else None)
'''

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _run(args: list[str], timeout: float = 20) -> str:
    return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW).stdout.decode("utf-8", "replace")


def read_local(watermark: int, db: str | None = None, limit: int = 5000) -> dict:
    """Atuin on this machine (on Windows: PowerShell)."""
    out = _run([sys.executable, "-I", "-c", READER, str(watermark), str(limit), db or ""])
    return json.loads(out.strip().splitlines()[-1]) if out.strip() else {"error": "no output"}


def running_wsl_distros() -> list[str]:
    """Only running distributions – we do not want to keep booting WSL ourselves."""
    if sys.platform != "win32" or not shutil.which("wsl.exe"):
        return []
    env = dict(os.environ, WSL_UTF8="1")
    try:
        raw = subprocess.run(["wsl.exe", "-l", "--running", "-q"], capture_output=True, timeout=10,
                             env=env, creationflags=_NO_WINDOW).stdout
    except Exception:
        return []
    text = raw.decode("utf-16-le", "replace") if raw[1:2] == b"\x00" else raw.decode("utf-8", "replace")
    names = [n.strip().replace("\x00", "") for n in text.splitlines()]
    return [n for n in names if n and " " not in n and not n.startswith("docker-desktop")]


def read_wsl(distro: str, watermark: int, db: str | None = None, limit: int = 5000) -> dict:
    b64 = base64.b64encode(READER.encode()).decode()
    code = f"import base64;exec(base64.b64decode('{b64}').decode())"
    out = _run(["wsl.exe", "-d", distro, "--", "python3", "-c", code, str(watermark), str(limit), db or ""], timeout=30)
    if not out.strip():
        return {"error": "no answer (is python3 installed in WSL?)"}
    return json.loads(out.strip().splitlines()[-1])
