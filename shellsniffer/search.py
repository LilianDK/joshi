"""Sniffing the logs: BM25 full-text search and the hybrid search "Sniff logs with shroom".

BM25 (:func:`search`, no language model)
    SQLite FTS5 indexes (see :mod:`db`): ``events_fts`` over command, output, cwd, shell and
    ``docs_fts`` over the SniffMagic documentation (session title + summary). Every word of the query
    becomes a prefix term (``dock`` finds ``docker``), combined with OR, so commands containing more and
    rarer words rank higher. Column weights make a hit in the command (or title) count most.

Hybrid (:func:`hybrid_search`, Ollama)
    BM25 plus cosine similarity of Ollama embeddings (:mod:`embeddings`), merged with weighted
    Reciprocal Rank Fusion: ``score = (1-w)/(k + rank_bm25) + w/(k + rank_cosine)``, where ``w`` is the
    "Hybrid balance" from SniffOptions. So "remove container" also finds ``docker rm``.

Both return ``{"query", "ranked", "hits": [commands], "docs": [documented sessions]}``; a time range
(``start`` / ``end``, Unix seconds) restricts both. With a time range but no search words, all
commands in the range are listed, newest first. Hits are marked with ``\\x01 … \\x02`` in
``command_hl`` / ``output_snip`` / ``title_hl`` / ``summary_snip``; the UI turns them into ``<mark>``.
"""
import re
import sqlite3

from . import db, timerange

BM25_WEIGHTS = (4.0, 1.0, 0.5, 0.3)   # commands: command, output, cwd, shell
DOC_WEIGHTS = (3.0, 1.0)              # documentation: title, summary
MAX_TERMS = 12
DOC_LIMIT = 10

RRF_K = 60          # Reciprocal Rank Fusion constant (standard value)
CANDIDATES = 100    # BM25 hits considered before fusion
SEMANTIC_K = 20     # the shroom adds only its most similar commands (cosine always finds *something*)

_CMD_COLS = ("e.id, e.ts, e.shell, e.host, e.session, e.cwd, e.exit_code, e.block_id,"
             " b.topic_id, b.title AS block_title, t.name AS topic_name")
_CMD_JOINS = (" LEFT JOIN blocks b ON b.id = e.block_id AND b.status = 'done'"
              " LEFT JOIN topics t ON t.id = b.topic_id")
_DOC_COLS = "b.id, b.title, b.start_ts, b.end_ts, b.n, b.topic_id, b.project, t.name AS topic_name, t.domain"


class SearchError(ValueError):
    """Invalid search input (shown to the user)."""


def fts_query(q: str) -> str | None:
    """User input -> FTS5 query string, e.g. ``git push`` -> ``"git"* OR "push"*``."""
    terms = re.findall(r"\w+", q, flags=re.UNICODE)
    return " OR ".join(f'"{t}"*' for t in terms[:MAX_TERMS]) or None


def _bm25_scores(hits: list[dict]) -> list[dict]:
    for h in hits:
        h["score"] = round(-h["score"], 3)   # FTS5 returns negative BM25 scores; higher = better here
    return hits


# ---------------------------------------------------------------- BM25
def search_docs(fq: str, start: float | None, end: float | None, limit: int = DOC_LIMIT) -> list[dict]:
    """BM25 over the SniffMagic documentation of sessions overlapping the time range."""
    cond, args = timerange.sessions_sql(start, end, "b")
    w = ", ".join(str(x) for x in DOC_WEIGHTS)
    return _bm25_scores(db.rows(
        f"SELECT {_DOC_COLS}, bm25(docs_fts, {w}) AS score,"
        f" highlight(docs_fts, 0, char(1), char(2)) AS title_hl,"
        f" snippet(docs_fts, 1, char(1), char(2), ' … ', 24) AS summary_snip"
        f" FROM docs_fts JOIN blocks b ON b.id = docs_fts.rowid LEFT JOIN topics t ON t.id = b.topic_id"
        f" WHERE docs_fts MATCH ? AND b.status = 'done'{cond} ORDER BY score LIMIT ?", (fq, *args, limit)))


def search(q: str = "", limit: int = 50, start: float | None = None, end: float | None = None) -> dict:
    """BM25 search over commands and documentation; ``score`` is the BM25 score."""
    timerange.check(start, end)
    limit = max(1, min(limit, 500))
    cond, args = timerange.commands_sql(start, end)
    fq = fts_query(q)
    if not fq:
        if not cond:
            return {"query": q, "hits": [], "docs": []}
        hits = db.rows(f"SELECT {_CMD_COLS}, NULL AS score, e.command AS command_hl,"
                       f" substr(e.output, 1, 200) AS output_snip FROM events e {_CMD_JOINS}"
                       f" WHERE 1=1{cond} ORDER BY e.ts DESC LIMIT ?", (*args, limit))
        return {"query": q, "ranked": False, "hits": hits, "docs": []}
    w = ", ".join(str(x) for x in BM25_WEIGHTS)
    try:
        hits = db.rows(
            f"SELECT {_CMD_COLS}, bm25(events_fts, {w}) AS score,"
            f" highlight(events_fts, 0, char(1), char(2)) AS command_hl,"
            f" snippet(events_fts, 1, char(1), char(2), ' … ', 16) AS output_snip"
            f" FROM events_fts JOIN events e ON e.id = events_fts.rowid {_CMD_JOINS}"
            f" WHERE events_fts MATCH ?{cond} ORDER BY score LIMIT ?", (fq, *args, limit))
        docs = search_docs(fq, start, end)
    except sqlite3.OperationalError as e:
        raise SearchError(f"bad search query: {e}") from e
    return {"query": q, "ranked": True, "hits": _bm25_scores(hits), "docs": docs}


# ---------------------------------------------------------------- hybrid
def _fuse(lexical: list[dict], semantic: list[tuple[int, float]], w_sem: float, limit: int) -> list[tuple[int, dict]]:
    """Weighted Reciprocal Rank Fusion of a BM25 list (dicts with "id") and a cosine list
    [(id, similarity)]. Returns [(id, {"score", "bm25_rank", "similarity", "hit"})], best first;
    at weight 0 / 1 the other side drops out completely."""
    fused: dict[int, dict] = {}
    for rank, h in enumerate(lexical, 1):
        fused[h["id"]] = {"score": (1 - w_sem) / (RRF_K + rank), "bm25_rank": rank, "similarity": None, "hit": h}
    for rank, (key, sim) in enumerate(semantic, 1):
        f = fused.setdefault(key, {"score": 0.0, "bm25_rank": None, "similarity": None, "hit": None})
        f["score"] += w_sem / (RRF_K + rank)
        f["similarity"] = round(sim, 3)
    return sorted(((k, f) for k, f in fused.items() if f["score"] > 0), key=lambda kv: -kv[1]["score"])[:limit]


def _materialise(best: list[tuple[int, dict]], load_plain) -> list[dict]:
    """Fused entries -> hit dicts; semantic-only hits (no BM25 highlight) are loaded via ``load_plain(ids)``."""
    missing = [k for k, f in best if f["hit"] is None]
    plain = {h["id"]: h for h in load_plain(missing)} if missing else {}
    out = []
    for k, f in best:
        h = dict(f["hit"] or plain.get(k) or {})
        if h:
            h.update(score=round(f["score"] * 1000, 2), bm25_rank=f["bm25_rank"], similarity=f["similarity"])
            out.append(h)
    return out


def _plain_commands(ids: list[int]) -> list[dict]:
    return db.rows(f"SELECT {_CMD_COLS}, e.command AS command_hl, substr(e.output, 1, 200) AS output_snip"
                   f" FROM events e {_CMD_JOINS} WHERE e.id IN ({','.join('?' * len(ids))})", tuple(ids))


def _plain_docs(ids: list[int]) -> list[dict]:
    return db.rows(f"SELECT {_DOC_COLS}, b.title AS title_hl, substr(b.summary, 1, 240) AS summary_snip"
                   f" FROM blocks b LEFT JOIN topics t ON t.id = b.topic_id WHERE b.id IN ({','.join('?' * len(ids))})",
                   tuple(ids))


def hybrid_search(q: str = "", limit: int = 50, start: float | None = None, end: float | None = None,
                  semantic_weight: float = 0.5) -> dict:
    """BM25 + semantic similarity, merged with weighted RRF (see the module docstring).

    Each hit carries ``score`` (fused), ``bm25_rank`` and ``similarity`` (cosine) so the UI can show
    where it came from. Missing vectors are computed first (a limited number per search).
    Raises SearchError / timerange.RangeError for bad input and embeddings.EmbeddingError if Ollama
    cannot help.
    """
    from . import embeddings   # imported lazily: only needed for the shroom

    if not q.strip():
        return search(q, limit, start, end)          # no words: plain time-range listing
    lexical = search(q, CANDIDATES, start, end)       # validates the range, too
    w_sem = min(1.0, max(0.0, semantic_weight))
    model, dedicated = embeddings.pick_model()
    qvec = embeddings.query_vector(model, q)

    cmd_cond, cmd_args = timerange.commands_sql(start, end)
    index = embeddings.index_missing(model, cmd_cond, tuple(cmd_args))
    semantic = embeddings.nearest(model, qvec, cmd_cond, tuple(cmd_args), SEMANTIC_K)
    hits = _materialise(_fuse(lexical["hits"], semantic, w_sem, max(1, min(limit, 500))), _plain_commands)

    doc_cond, doc_args = timerange.sessions_sql(start, end, "b")
    embeddings.index_docs(model)                      # catch up on documentation without a vector
    sem_docs = embeddings.nearest_docs(model, qvec, doc_cond, tuple(doc_args), DOC_LIMIT)
    docs = _materialise(_fuse(lexical["docs"], sem_docs, w_sem, DOC_LIMIT), _plain_docs)

    return {"query": q, "ranked": True, "mode": "hybrid", "model": model, "embedding_model": dedicated,
            "semantic_weight": w_sem, "indexed_now": index["new"], "not_indexed": index["missing"],
            "hits": hits, "docs": docs}
