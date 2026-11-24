"""Full-text search over the logs, ranked with BM25 (SQLite FTS5, no language model).

The index ``events_fts`` (see :mod:`db`) covers the columns command, output, cwd and shell.
Every word of the query becomes a prefix term (``dock`` finds ``docker``); the terms are combined
with OR, so BM25 ranks commands containing more and rarer words higher. Column weights make a hit
in the command count most.

Search options: a time range (``start`` / ``end``, Unix seconds, inclusive). With a time range but no
search words, all commands in the range are listed, newest first.

Besides commands, every search also returns ``docs``: sessions whose SniffMagic documentation (title +
summary) matches – via BM25, and in the hybrid search also via their vectors.
"""
import re
import sqlite3

from . import db

BM25_WEIGHTS = (4.0, 1.0, 0.5, 0.3)   # command, output, cwd, shell
MAX_TERMS = 12

# Hits are marked with \x01 … \x02 in `command_hl` / `output_snip`; the UI turns them into <mark>.
_COLS = ("e.id, e.ts, e.shell, e.host, e.session, e.cwd, e.exit_code, e.block_id,"
         " b.topic_id, b.title AS block_title, t.name AS topic_name")
_JOINS = (" LEFT JOIN blocks b ON b.id = e.block_id AND b.status = 'done'"
          " LEFT JOIN topics t ON t.id = b.topic_id")


# Documentation hits: sessions documented by SniffMagic (title + summary), see db.docs_fts
DOC_WEIGHTS = (3.0, 1.0)   # title, summary
DOC_LIMIT = 10
_DOC_COLS = ("b.id, b.title, b.start_ts, b.end_ts, b.n, b.topic_id, b.project, t.name AS topic_name, t.domain")


def _doc_range(start: float | None, end: float | None) -> tuple[str, list]:
    """Sessions overlapping the time range."""
    sql, args = "", []
    if start is not None:
        sql += " AND b.end_ts >= ?"
        args.append(start)
    if end is not None:
        sql += " AND b.start_ts <= ?"
        args.append(end)
    return sql, args


def search_docs(fq: str, start: float | None, end: float | None, limit: int = DOC_LIMIT) -> list[dict]:
    """BM25 over the documentation; ``title_hl`` / ``summary_snip`` carry \x01…\x02 marks."""
    cond, args = _doc_range(start, end)
    w = ", ".join(str(x) for x in DOC_WEIGHTS)
    hits = db.rows(
        f"SELECT {_DOC_COLS}, bm25(docs_fts, {w}) AS score,"
        f" highlight(docs_fts, 0, char(1), char(2)) AS title_hl,"
        f" snippet(docs_fts, 1, char(1), char(2), ' … ', 24) AS summary_snip"
        f" FROM docs_fts JOIN blocks b ON b.id = docs_fts.rowid LEFT JOIN topics t ON t.id = b.topic_id"
        f" WHERE docs_fts MATCH ? AND b.status = 'done'{cond} ORDER BY score LIMIT ?", (fq, *args, limit))
    for h in hits:
        h["score"] = round(-h["score"], 3)
    return hits


class SearchError(ValueError):
    """Invalid search input (shown to the user)."""


def fts_query(q: str) -> str | None:
    """User input -> FTS5 query string, e.g. ``git push`` -> ``"git"* OR "push"*``."""
    terms = re.findall(r"\w+", q, flags=re.UNICODE)
    return " OR ".join(f'"{t}"*' for t in terms[:MAX_TERMS]) or None


def search(q: str = "", limit: int = 50, start: float | None = None, end: float | None = None) -> dict:
    """Returns ``{"query", "ranked", "hits": [...]}``; ``score`` is the BM25 score (higher = better)."""
    if start is not None and end is not None and start > end:
        raise SearchError("time range: start is after end")
    limit = max(1, min(limit, 500))
    where, args = [], []
    if start is not None:
        where.append("e.ts >= ?")
        args.append(start)
    if end is not None:
        where.append("e.ts <= ?")
        args.append(end)

    fq = fts_query(q)
    if not fq:
        if not where:
            return {"query": q, "hits": []}
        hits = db.rows(f"SELECT {_COLS}, NULL AS score, e.command AS command_hl,"
                       f" substr(e.output, 1, 200) AS output_snip FROM events e {_JOINS}"
                       f" WHERE {' AND '.join(where)} ORDER BY e.ts DESC LIMIT ?", (*args, limit))
        return {"query": q, "ranked": False, "hits": hits}

    weights = ", ".join(str(x) for x in BM25_WEIGHTS)
    try:
        hits = db.rows(
            f"SELECT {_COLS}, bm25(events_fts, {weights}) AS score,"
            f" highlight(events_fts, 0, char(1), char(2)) AS command_hl,"
            f" snippet(events_fts, 1, char(1), char(2), ' … ', 16) AS output_snip"
            f" FROM events_fts JOIN events e ON e.id = events_fts.rowid {_JOINS}"
            f" WHERE events_fts MATCH ? {''.join(' AND ' + c for c in where)} ORDER BY score LIMIT ?",
            (fq, *args, limit))
    except sqlite3.OperationalError as e:
        raise SearchError(f"bad search query: {e}") from e
    for h in hits:
        h["score"] = round(-h["score"], 3)   # FTS5 returns negative BM25 scores
    return {"query": q, "ranked": True, "hits": hits, "docs": search_docs(fq, start, end)}


# ---------------------------------------------------------------- Hybrid search ("Sniff logs with shroom")
RRF_K = 60          # Reciprocal Rank Fusion constant (standard value)
CANDIDATES = 100    # BM25 hits considered before fusion
SEMANTIC_K = 20     # the shroom adds only its most similar commands (it always finds *something*)


def hybrid_search(q: str = "", limit: int = 50, start: float | None = None, end: float | None = None,
                  semantic_weight: float = 0.5) -> dict:
    """BM25 + semantic similarity (Ollama embeddings), merged with Reciprocal Rank Fusion.

    BM25 finds exact words; the embeddings also find commands that *mean* the query
    ("remove container" finds ``docker rm``). Each hit gets ``score`` (fused, higher = better),
    ``bm25_rank`` and ``similarity`` (cosine, -1..1) so the UI can show where it came from.
    Commands without a vector yet are embedded first (a limited number per search).
    ``semantic_weight`` (0..1, SniffOptions "Hybrid balance") weights the two rankings in the fusion:
    0 = BM25 only, 1 = cosine similarity only, 0.5 = equal.
    Raises SearchError for bad input and embeddings.EmbeddingError if Ollama cannot help.
    """
    from . import embeddings   # imported lazily: only needed when the shroom is on

    if not q.strip():
        return search(q, limit, start, end)          # no words: plain time-range listing
    lexical = search(q, CANDIDATES, start, end)       # validates the range, too
    where, args = "", []
    if start is not None:
        where += " AND e.ts >= ?"
        args.append(start)
    if end is not None:
        where += " AND e.ts <= ?"
        args.append(end)

    model, dedicated = embeddings.pick_model()
    index = embeddings.index_missing(model, where, tuple(args))
    semantic = embeddings.nearest(model, embeddings.query_vector(model, q), where, tuple(args), SEMANTIC_K)

    w_sem = min(1.0, max(0.0, semantic_weight))
    w_lex = 1.0 - w_sem
    fused: dict[int, dict] = {}
    for rank, h in enumerate(lexical["hits"], 1):
        fused[h["id"]] = {"score": w_lex / (RRF_K + rank), "bm25_rank": rank, "similarity": None, "hit": h}
    for rank, (eid, sim) in enumerate(semantic, 1):
        f = fused.setdefault(eid, {"score": 0.0, "bm25_rank": None, "hit": None})
        f["score"] += w_sem / (RRF_K + rank)
        f["similarity"] = round(sim, 3)
    fused = {k: f for k, f in fused.items() if f["score"] > 0}     # at 0 % / 100 % one side drops out
    best = sorted(fused.items(), key=lambda kv: -kv[1]["score"])[:max(1, min(limit, 500))]

    # semantic-only hits have no BM25 highlight yet: load them plainly
    plain_ids = [eid for eid, f in best if f["hit"] is None]
    plain = {}
    if plain_ids:
        marks = ",".join("?" * len(plain_ids))
        for h in db.rows(f"SELECT {_COLS}, e.command AS command_hl, substr(e.output, 1, 200) AS output_snip"
                         f" FROM events e {_JOINS} WHERE e.id IN ({marks})", tuple(plain_ids)):
            plain[h["id"]] = h
    hits = []
    for eid, f in best:
        h = dict(f["hit"] or plain.get(eid) or {})
        if not h:
            continue
        h.update(score=round(f["score"] * 1000, 2), bm25_rank=f["bm25_rank"], similarity=f["similarity"])
        hits.append(h)
    docs = _hybrid_docs(embeddings, model, q, lexical.get("docs") or [], start, end, w_sem)
    return {"query": q, "ranked": True, "mode": "hybrid", "model": model, "embedding_model": dedicated,
            "semantic_weight": w_sem, "docs": docs,
            "indexed_now": index["new"], "not_indexed": index["missing"], "hits": hits}


def _hybrid_docs(embeddings, model: str, q: str, lexical: list[dict], start, end, w_sem: float) -> list[dict]:
    """Documentation hits for the hybrid search: BM25 docs + most similar docs, weighted RRF."""
    cond, args = _doc_range(start, end)
    embeddings.index_docs(model)                          # catch up on documentation without a vector
    semantic = embeddings.nearest_docs(model, embeddings.query_vector(model, q), cond, tuple(args), DOC_LIMIT)
    w_lex = 1.0 - w_sem
    fused: dict[int, dict] = {}
    for rank, h in enumerate(lexical, 1):
        fused[h["id"]] = {"score": w_lex / (RRF_K + rank), "bm25_rank": rank, "similarity": None, "hit": h}
    for rank, (bid, sim) in enumerate(semantic, 1):
        f = fused.setdefault(bid, {"score": 0.0, "bm25_rank": None, "hit": None})
        f["score"] += w_sem / (RRF_K + rank)
        f["similarity"] = round(sim, 3)
    best = sorted(((k, f) for k, f in fused.items() if f["score"] > 0), key=lambda kv: -kv[1]["score"])[:DOC_LIMIT]
    missing = [k for k, f in best if f["hit"] is None]
    plain = {}
    if missing:
        for h in db.rows(f"SELECT {_DOC_COLS}, b.title AS title_hl, substr(b.summary, 1, 240) AS summary_snip"
                         f" FROM blocks b LEFT JOIN topics t ON t.id = b.topic_id"
                         f" WHERE b.id IN ({','.join('?' * len(missing))})", tuple(missing)):
            plain[h["id"]] = h
    out = []
    for k, f in best:
        h = dict(f["hit"] or plain.get(k) or {})
        if h:
            h.update(score=round(f["score"] * 1000, 2), bm25_rank=f["bm25_rank"], similarity=f["similarity"])
            out.append(h)
    return out
