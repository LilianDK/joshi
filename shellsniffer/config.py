"""Configuration: paths, tunables and the access token.

Everything is read once from environment variables at import time. See docs/configuration.md
for the user-facing description of every variable.
"""
import os
import secrets
import shutil
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


# ---------------------------------------------------------------- Paths
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
STATIC_DIR = PACKAGE_DIR / "static"

DATA_DIR = Path(os.environ.get("SHELLSNIFFER_DIR", Path.home() / ".shellsniffer"))
DB_PATH = DATA_DIR / "shellsniffer.db"
TOKEN_PATH = DATA_DIR / "token"
HONEYPOT_DIR = Path(os.environ.get("SHELLSNIFFER_HONEYPOT_DIR", PROJECT_DIR / "honeypot"))

# ---------------------------------------------------------------- Sessions
GAP_SECONDS = _env_float("SHELLSNIFFER_GAP", 300)          # pause after which a new session starts
IDLE_SECONDS = _env_float("SHELLSNIFFER_IDLE", 45)         # pause after which a session counts as finished
MAX_BLOCK = int(os.environ.get("SHELLSNIFFER_MAX_BLOCK", 25))      # max. commands per session
MAX_OUTPUT = int(os.environ.get("SHELLSNIFFER_MAX_OUTPUT", 16000))  # max. stored characters of output per command

# ---------------------------------------------------------------- Capture modes (initial values; switchable in the UI)
ATUIN_DEFAULT = os.environ.get("SHELLSNIFFER_ATUIN", "1") != "0"
HONEYPOT_DEFAULT = os.environ.get("SHELLSNIFFER_HONEYPOT", "0") == "1"
VECTORIZE_DEFAULT = os.environ.get("SHELLSNIFFER_VECTORIZE", "0") == "1"   # background vectorization (hybrid search)

# ---------------------------------------------------------------- Atuin import
ATUIN_BACKFILL_DAYS = _env_float("SHELLSNIFFER_ATUIN_DAYS", 14)   # how far back the very first import goes
ATUIN_POLL = _env_float("SHELLSNIFFER_ATUIN_POLL", 2)             # seconds between two reads
FALLBACK_AFTER = _env_float("SHELLSNIFFER_FALLBACK_AFTER", 20)    # wait this long for an Atuin entry before the hook report counts
MATCH_WINDOW = 60.0                                                # tolerance between hook and Atuin start time (seconds)


def _migrate_old_data_dir() -> None:
    """Copies the database from before a rename (shelldoc -> docuduck -> joshi -> shellSniffer).

    Decided by the database file, not the folder: the shell hooks create the data folder early
    for their output buffers, before the server ever ran. The old folder is left untouched.
    """
    for old in ("joshi", "docuduck", "shelldoc"):
        old_dir = Path.home() / f".{old}"
        if DB_PATH.exists() or not (old_dir / f"{old}.db").exists():
            continue
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for suffix in (".db", ".db-wal", ".db-shm"):
            if (old_dir / f"{old}{suffix}").exists():
                shutil.copy2(old_dir / f"{old}{suffix}", DATA_DIR / f"shellsniffer{suffix}")
        if (old_dir / "token").exists() and not TOKEN_PATH.exists():
            shutil.copy2(old_dir / "token", TOKEN_PATH)


def _load_token() -> str:
    """Random token that clients not on localhost (e.g. WSL) must send. Created on first start."""
    if not TOKEN_PATH.exists():
        TOKEN_PATH.write_text(secrets.token_urlsafe(24))
    return TOKEN_PATH.read_text().strip()


_migrate_old_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOKEN = _load_token()
