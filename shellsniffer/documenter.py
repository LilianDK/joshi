"""Turning sessions into documentation – with Ollama, but only on request.

The model never runs in the background. A click on **SniffMagic** calls
:func:`request_clustering`; the :func:`cluster_worker` then sends every waiting session to
:func:`cluster.classify` (Ollama, or a heuristic if Ollama is not reachable), which returns a domain,
a topic (existing or new), a title and a Markdown summary in ASD-STE100 style whose steps cite the
commands ([n]). The summary is then vectorized right away (:func:`embeddings.embed_doc_now`) and is
indexed for BM25 by the ``docs_fts`` triggers, so the documentation itself is searchable.

Also here: the lightweight ontology housekeeping (:func:`migrate_ontology`, runs at every start
without the model) and the model-based tidy-up behind **Tidy up topics** (:func:`consolidate_with_llm`).
"""
import asyncio
import json
import time

from . import cluster, db, embeddings
from . import ontology as onto
from .config import IDLE_SECONDS
from .ingest import block_info
from .state import broadcast, broadcast_status, status

_wake = asyncio.Event()


def block_meta(events: list[dict]) -> dict:
    """Project (from the working directories) and tools (from the commands) of a session."""
    return {"project": onto.project_of_block([e["cwd"] for e in events]),
            "tools": onto.tools_of([e["command"] for e in events])}


# ---------------------------------------------------------------- Clustering on request
_queue: set[int] = set()   # ids of the sessions SniffMagic was asked to document


def _range_filter(start: float | None, end: float | None) -> tuple[str, list]:
    """SQL condition for sessions that overlap the time range [start, end] (Unix seconds)."""
    sql, args = "", []
    if start is not None:
        sql += " AND end_ts >= ?"
        args.append(start)
    if end is not None:
        sql += " AND start_ts <= ?"
        args.append(end)
    return sql, args


def magic_preview(start: float | None = None, end: float | None = None) -> dict:
    """How many sessions a SniffMagic run on this time range would touch."""
    cond, args = _range_filter(start, end)
    c = db.one(f"SELECT sum(status IN ('open','ready')) AS waiting, sum(status='done') AS documented,"
               f" coalesce(sum(CASE WHEN status IN ('open','ready') THEN n END),0) AS commands"
               f" FROM blocks WHERE 1=1{cond}", tuple(args))
    return {"waiting": c["waiting"] or 0, "documented": c["documented"] or 0, "commands": c["commands"] or 0}


def request_clustering(start: float | None = None, end: float | None = None,
                       include_done: bool = False, block_ids: list[int] | None = None) -> int:
    """Queues sessions for the model and returns how many were queued.

    SniffMagic: all not yet documented sessions overlapping the time range [start, end] (no range =
    all of them); sessions still running are closed and included. With ``include_done``, sessions
    in the range that are already documented are documented again. ``block_ids`` queues exactly
    these sessions (button "re-cluster").
    """
    with db.lock:
        if block_ids is None:
            cond, args = _range_filter(start, end)
            states = "('open','ready','done')" if include_done else "('open','ready')"
            block_ids = [r[0] for r in db.conn.execute(f"SELECT id FROM blocks WHERE status IN {states}{cond}", args)]
        db.conn.executemany("UPDATE blocks SET status='ready' WHERE id=?", [(i,) for i in block_ids])
        db.conn.commit()
    _queue.update(block_ids)
    status["cluster_requested"] = bool(_queue)
    status["queued"] = len(_queue)
    _wake.set()
    return len(block_ids)


def _store_result(b: dict, res: dict, meta: dict, now: float) -> int:
    """Saves the model's answer for session ``b``; returns the topic id."""
    with db.lock:
        tid = res["topic_id"]
        if tid is None:
            nt = res["new_topic"]
            tid = db.conn.execute("INSERT INTO topics(name,description,created,updated,domain) VALUES(?,?,?,?,?)",
                                  (nt["name"], nt["description"], now, now, res["domain"])).lastrowid
        db.conn.execute("UPDATE topics SET updated=max(coalesce(updated,0),?), domain=coalesce(domain,?) WHERE id=?",
                        (b["end_ts"], res["domain"], tid))
        db.conn.execute("UPDATE blocks SET status='done', topic_id=?, title=?, summary=?, method=?, project=?, tools=? WHERE id=?",
                        (tid, res["title"], res["summary"], res["method"], meta["project"], json.dumps(meta["tools"]), b["id"]))
        db.conn.commit()
    return tid


async def cluster_worker() -> None:
    """Works through the queue after :func:`request_clustering`; idle otherwise."""
    status["model"] = await asyncio.to_thread(cluster.pick_model)   # only lists the models, runs nothing
    status["ollama"] = bool(status["model"])
    while True:
        try:
            now = time.time()
            # sessions without commands for IDLE_SECONDS count as finished ("waiting")
            db.execute("UPDATE blocks SET status='ready' WHERE status='open' AND end_ts < ?", (now - IDLE_SECONDS,))
            b = None
            if _queue:
                b = next((r for r in db.rows("SELECT * FROM blocks WHERE status='ready' ORDER BY start_ts")
                          if r["id"] in _queue), None)
            if not b:
                if status["cluster_requested"]:          # queue done
                    _queue.clear()
                    status["cluster_requested"] = False
                    status["queued"] = 0
                    await broadcast_status()
                _wake.clear()
                try:
                    await asyncio.wait_for(_wake.wait(), timeout=3)
                except asyncio.TimeoutError:
                    pass
                continue

            events = db.rows("SELECT * FROM events WHERE block_id=? ORDER BY ts", (b["id"],))
            topics = db.rows("SELECT id,name,description,domain FROM topics ORDER BY updated DESC")
            meta = block_meta(events)
            status["clustering"] = b["id"]
            await broadcast_status()
            res = await asyncio.to_thread(cluster.classify, events, topics, meta)
            status["model"] = cluster.current_model()
            status["ollama"] = bool(status["model"])
            tid = _store_result(b, res, meta, now)
            await asyncio.to_thread(embeddings.embed_doc_now, b["id"])   # make the new documentation searchable by meaning
            _queue.discard(b["id"])
            status["queued"] = len(_queue)
            status["clustering"] = None
            await broadcast({"type": "block", "block": block_info(b["id"])})
            await broadcast({"type": "topics_changed", "topic_id": tid})
            await broadcast_status()
        except asyncio.CancelledError:
            raise
        except Exception as e:   # the worker must never die
            status["last_error"] = repr(e)
            status["clustering"] = None
            await asyncio.sleep(5)


async def model_probe() -> None:
    """Checks every minute whether Ollama is reachable (lists models only, never runs one)."""
    while True:
        await asyncio.sleep(60)
        if not cluster.enabled():
            continue
        if not status["clustering"]:
            status["model"] = await asyncio.to_thread(cluster.pick_model)
            status["ollama"] = bool(status["model"])
            await broadcast_status()


# ---------------------------------------------------------------- Ontology housekeeping
def merge_topics(keep: int, others: list[int]) -> None:
    """Moves all sessions of ``others`` into topic ``keep`` and deletes the others."""
    with db.lock:
        for o in others:
            if o == keep:
                continue
            db.conn.execute("UPDATE blocks SET topic_id=? WHERE topic_id=?", (keep, o))
            db.conn.execute("UPDATE topics SET updated=max(coalesce(updated,0),coalesce((SELECT updated FROM topics WHERE id=?),0)) WHERE id=?", (o, keep))
            db.conn.execute("DELETE FROM topics WHERE id=?", (o,))
        db.conn.commit()


def migrate_ontology() -> int:
    """Brings old data into the ontology without the model: fills in project/tools of sessions,
    assigns missing domains and merges topics with near-identical names. Idempotent; returns the
    number of changes."""
    changed = 0
    for b in db.rows("SELECT id FROM blocks WHERE status='done' AND tools IS NULL"):
        meta = block_meta(db.rows("SELECT cwd,command FROM events WHERE block_id=?", (b["id"],)))
        with db.lock:
            db.conn.execute("UPDATE blocks SET project=?, tools=? WHERE id=?", (meta["project"], json.dumps(meta["tools"]), b["id"]))
        changed += 1
    for t in db.rows("SELECT * FROM topics WHERE domain IS NULL OR domain NOT IN (%s)" % ",".join("?" * len(onto.DOMAIN_KEYS)),
                     tuple(onto.DOMAIN_KEYS)):
        tools = []
        for b in db.rows("SELECT tools FROM blocks WHERE topic_id=?", (t["id"],)):
            tools += json.loads(b["tools"] or "[]")
        dom = onto.domain_for_tools(tools)                 # tools are more reliable than the name
        if dom == "sonstiges":
            dom = onto.domain_for_name(t["name"], t["description"] or "") or "sonstiges"
        with db.lock:
            db.conn.execute("UPDATE topics SET domain=? WHERE id=?", (dom, t["id"]))
        changed += 1
    with db.lock:
        db.conn.commit()

    topics = db.rows("SELECT id,name,domain FROM topics ORDER BY id")
    gone: set[int] = set()
    for i, a in enumerate(topics):
        if a["id"] in gone:
            continue
        dup = [b["id"] for b in topics[i + 1:]
               if b["id"] not in gone and b["domain"] == a["domain"] and onto.similar(a["name"], b["name"])]
        if dup:
            merge_topics(a["id"], dup)
            gone.update(dup)
            changed += len(dup)
    return changed


async def consolidate_with_llm() -> dict:
    """**Tidy up topics**: the model reassigns all topics to domains and proposes merges."""
    topics = db.rows("SELECT id,name,description,domain FROM topics ORDER BY id")
    res = await asyncio.to_thread(cluster.consolidate_llm, topics)
    if not res:
        return {"ok": False, "reason": "Ollama not reachable or the answer was unusable"}
    ids = {t["id"] for t in topics}
    n_domains = n_merged = 0
    for k, v in (res.get("domains") or {}).items():
        try:
            tid = int(k)
        except (TypeError, ValueError):
            continue
        if tid in ids and v in onto.DOMAIN_KEYS:
            with db.lock:
                db.conn.execute("UPDATE topics SET domain=? WHERE id=?", (v, tid))
            n_domains += 1
    with db.lock:
        db.conn.commit()
    for group in res.get("merge") or []:
        try:
            g = [int(x) for x in group if int(x) in ids]
        except (TypeError, ValueError):
            continue
        if len(g) >= 2:
            merge_topics(g[0], g[1:])
            ids -= set(g[1:])
            n_merged += len(g) - 1
    db.meta_set("llm_consolidated", str(time.time()))
    return {"ok": True, "domains": n_domains, "merged": n_merged}


async def ontology_startup() -> None:
    """At server start: heuristic housekeeping only – the model is never started automatically."""
    if await asyncio.to_thread(migrate_ontology):
        await broadcast({"type": "topics_changed", "topic_id": None})
