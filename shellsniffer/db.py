"""SQLite storage: one shared connection, the schema and its migrations.

Tables (see docs/architecture.md):
    events      one row per command (time, shell, host, session, cwd, command, exit code, output, ...)
    blocks      sessions: consecutive commands of one shell, later assigned to a topic
    topics      documentation topics, each in one ontology domain
    outputs     hook output reports waiting to be matched to an Atuin entry
    sources     Atuin sources with their import watermark
    meta        small key/value store (settings, one-off migrations)
    embeddings  semantic vectors per command and model (hybrid search, see embeddings.py)
    doc_embeddings  semantic vectors of the documentation (session title + summary)
    docs_fts    FTS5 index over the documentation (session title + summary)
    events_fts  FTS5 full-text index over events, used for the BM25 search

The connection is shared between the event loop and worker threads; every access goes
through ``lock``. Use :func:`rows` / :func:`one` for reads and ``with lock:`` + ``conn`` for writes.
"""
import sqlite3
import threading

from .config import DB_PATH

lock = threading.RLock()
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row

_SCHEMA = """
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
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS embeddings(event_id INTEGER, model TEXT, vec BLOB, PRIMARY KEY(event_id, model));
CREATE TABLE IF NOT EXISTS doc_embeddings(block_id INTEGER, model TEXT, vec BLOB, text_hash TEXT, PRIMARY KEY(block_id, model));
"""

# FTS5 index for the BM25 search, kept in sync with `events` by triggers ("external content" table).
_FTS = """
CREATE VIRTUAL TABLE events_fts USING fts5(
    command, output, cwd, shell, content='events', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER events_fts_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid,command,output,cwd,shell) VALUES(new.id,new.command,new.output,new.cwd,new.shell);
END;
CREATE TRIGGER events_fts_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts,rowid,command,output,cwd,shell) VALUES('delete',old.id,old.command,old.output,old.cwd,old.shell);
END;
CREATE TRIGGER events_fts_au AFTER UPDATE OF command,output,cwd,shell ON events BEGIN
    INSERT INTO events_fts(events_fts,rowid,command,output,cwd,shell) VALUES('delete',old.id,old.command,old.output,old.cwd,old.shell);
    INSERT INTO events_fts(rowid,command,output,cwd,shell) VALUES(new.id,new.command,new.output,new.cwd,new.shell);
END;
INSERT INTO events_fts(events_fts) VALUES('rebuild');
"""

# FTS5 index over the documentation SniffMagic writes (session title + summary), for the BM25 search.
_DOCS_FTS = """
CREATE VIRTUAL TABLE docs_fts USING fts5(
    title, summary, content='blocks', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER docs_fts_ai AFTER INSERT ON blocks BEGIN
    INSERT INTO docs_fts(rowid,title,summary) VALUES(new.id,coalesce(new.title,''),coalesce(new.summary,''));
END;
CREATE TRIGGER docs_fts_ad AFTER DELETE ON blocks BEGIN
    INSERT INTO docs_fts(docs_fts,rowid,title,summary) VALUES('delete',old.id,coalesce(old.title,''),coalesce(old.summary,''));
END;
CREATE TRIGGER docs_fts_au AFTER UPDATE OF title,summary ON blocks BEGIN
    INSERT INTO docs_fts(docs_fts,rowid,title,summary) VALUES('delete',old.id,coalesce(old.title,''),coalesce(old.summary,''));
    INSERT INTO docs_fts(rowid,title,summary) VALUES(new.id,coalesce(new.title,''),coalesce(new.summary,''));
END;
INSERT INTO docs_fts(docs_fts) VALUES('rebuild');
"""


def _columns(table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate() -> None:
    """Creates missing tables and adds columns introduced by later versions (idempotent)."""
    conn.executescript(_SCHEMA)
    if "atuin_uuid" not in _columns("events"):
        conn.execute("ALTER TABLE events ADD COLUMN atuin_uuid TEXT")
        conn.execute("ALTER TABLE events ADD COLUMN source TEXT DEFAULT 'hook'")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ev_atuin ON events(atuin_uuid)")
    if "payload" not in _columns("outputs"):
        conn.execute("ALTER TABLE outputs ADD COLUMN payload TEXT")
    if "domain" not in _columns("topics"):                 # ontology: domain per topic
        conn.execute("ALTER TABLE topics ADD COLUMN domain TEXT")
    if "project" not in _columns("blocks"):                # ontology: project + tools per session
        conn.execute("ALTER TABLE blocks ADD COLUMN project TEXT")
        conn.execute("ALTER TABLE blocks ADD COLUMN tools TEXT")
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='events_fts'").fetchone():
        conn.executescript(_FTS)                           # also indexes all existing commands once
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='docs_fts'").fetchone():
        conn.executescript(_DOCS_FTS)                      # also indexes all existing documentation once
    conn.commit()


def rows(sql: str, args: tuple = ()) -> list[dict]:
    """Runs a query and returns all rows as plain dicts."""
    with lock:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def one(sql: str, args: tuple = ()) -> dict | None:
    """Runs a query and returns the first row as a dict, or None."""
    r = rows(sql, args)
    return r[0] if r else None


def execute(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    """Runs one write statement and commits it."""
    with lock:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur


def meta_get(key: str) -> str | None:
    r = one("SELECT value FROM meta WHERE key=?", (key,))
    return None if r is None else r["value"]


def meta_set(key: str, value: str) -> None:
    execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))


_migrate()
