"""Topic clustering: assigns a work session (a block of commands) to a domain and topic
and writes short documentation for it. Uses a local Ollama model, with a heuristic
fallback when Ollama is not reachable."""
import json
import os
import re
import urllib.request

from . import ontology as onto

OLLAMA_URL = os.environ.get("JOSHI_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
PREFERRED_MODEL = os.environ.get("JOSHI_OLLAMA_MODEL", "")

_model_cache: dict = {"name": None}


def _http_json(url: str, payload: dict | None = None, timeout: float = 120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def pick_model() -> str | None:
    """Picks the configured model, or the first installed chat model."""
    try:
        tags = _http_json(f"{OLLAMA_URL}/api/tags", timeout=3)
    except Exception:
        _model_cache["name"] = None
        return None
    names = [m["name"] for m in tags.get("models", [])]
    names = [n for n in names if "embed" not in n.lower()]
    if not names:
        _model_cache["name"] = None
        return None
    if PREFERRED_MODEL:
        for n in names:
            if n == PREFERRED_MODEL or n.split(":")[0] == PREFERRED_MODEL:
                _model_cache["name"] = n
                return n
    _model_cache["name"] = names[0]
    return names[0]


def current_model() -> str | None:
    return _model_cache["name"]


DOMAIN_TEXT = "\n".join(f'- {k}: {n} ({d})' for k, n, d in onto.DOMAINS)

# Language of titles, summaries and new topic names (the UI itself is English)
DOC_LANGUAGE = os.environ.get("JOSHI_DOC_LANGUAGE", "English")

SYSTEM_PROMPT = f"""You are an assistant that documents shell work and files it into a fixed structure.

The structure has two levels:
1. DOMAIN – exactly one from this fixed list (use the key only):
{DOMAIN_TEXT}
2. TOPIC – a more specific topic inside the domain (e.g. "Docker Compose setup", "Python venv for research-rag").

Rules:
- Use an EXISTING topic whenever it fits (same task, same tool in the same context, or same project).
  Create a new topic only if none fits. Prefer a slightly more general existing topic over a duplicate
  with a different name.
- New topic names: short (2-4 words, in {DOC_LANGUAGE}), without repeating the domain name.
- Write a title and a summary of the session in {DOC_LANGUAGE}, in Markdown: what was done, probably why,
  what worked / what failed, and the most important commands as a code block.
  Do not invent anything that does not follow from the commands.

Reply ONLY with JSON in this format:
{{"domain": "<domain key>",
 "topic_id": <id of an existing topic or null>,
 "new_topic": {{"name": "...", "description": "One sentence on what it is about"}} or null,
 "title": "Short title of the session",
 "summary": "Markdown summary"}}"""


def _clip(s: str | None, n: int) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= n else s[: n // 2] + "\n…\n" + s[-n // 2 :]


def build_user_prompt(events: list[dict], topics: list[dict], meta: dict) -> str:
    by_dom: dict[str, list] = {}
    for t in topics:
        by_dom.setdefault(t.get("domain") or "sonstiges", []).append(t)
    tl = []
    for k, n, _ in onto.DOMAINS:
        for t in by_dom.get(k, []):
            tl.append(f'- id={t["id"]} [{k}] {t["name"]} — {t["description"] or ""}')
    lines = []
    for e in events:
        lines.append(f'[{e["shell"]}] {e["cwd"] or "?"} $ {e["command"]}   (exit {e["exit_code"]})')
        out = _clip(e.get("output"), 600)
        if out:
            lines.append("    Output: " + out.replace("\n", "\n    "))
    ctx = f'Project (from the directory): {meta.get("project") or "unknown"}\nTools: {", ".join(meta.get("tools") or []) or "-"}'
    return (f"Existing topics:\n" + ("\n".join(tl) or "(no topics yet)") +
            f"\n\n{ctx}\n\nWork session:\n" + "\n".join(lines))


def _resolve(topic_id, new_topic, domain: str, topics: list[dict]) -> tuple[int | None, dict | None, str]:
    """Prefer an existing topic; create a new one only if no similar name exists."""
    valid = {t["id"]: t for t in topics}
    if isinstance(topic_id, str) and topic_id.isdigit():
        topic_id = int(topic_id)
    if topic_id in valid:
        return topic_id, None, valid[topic_id].get("domain") or domain
    if not (isinstance(new_topic, dict) and new_topic.get("name")):
        return None, None, domain
    name = str(new_topic["name"]).strip()[:60]
    for t in topics:                       # catch duplicates
        if onto.similar(t["name"], name):
            return t["id"], None, t.get("domain") or domain
    return None, {"name": name, "description": str(new_topic.get("description") or "")[:300]}, domain


def classify_llm(events: list[dict], topics: list[dict], meta: dict) -> dict | None:
    model = current_model() or pick_model()
    if not model:
        return None
    payload = {
        "model": model, "stream": False, "format": "json", "options": {"temperature": 0.2},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(events, topics, meta)},
        ],
    }
    try:
        res = _http_json(f"{OLLAMA_URL}/api/chat", payload, timeout=300)
        data = json.loads(res.get("message", {}).get("content", ""))
    except Exception:
        _model_cache["name"] = None
        return None
    if not isinstance(data, dict):
        return None
    domain = str(data.get("domain") or "").strip().lower()
    if domain not in onto.DOMAIN_KEYS:
        domain = onto.domain_for_tools(meta.get("tools") or [])
    tid, nt, domain = _resolve(data.get("topic_id"), data.get("new_topic"), domain, topics)
    if tid is None and nt is None:
        return None
    return {"topic_id": tid, "new_topic": nt, "domain": domain,
            "title": str(data.get("title") or "Session")[:120], "summary": str(data.get("summary") or ""),
            "method": f"ollama:{model}"}


# ---------- Heuristic fallback ----------

TOOL_TOPIC_NAMES = {
    "git": "Git work", "gh": "GitHub", "docker": "Docker", "docker-compose": "Docker Compose", "kubectl": "Kubernetes",
    "helm": "Kubernetes", "pip": "Python packages", "venv": "Python environments", "uv": "Python packages",
    "conda": "Conda environments", "npm": "Node packages", "yarn": "Node packages", "pnpm": "Node packages",
    "apt": "System packages", "apt-get": "System packages", "winget": "Windows packages", "ssh": "SSH connections",
    "curl": "HTTP calls", "ollama": "Ollama models", "python": "Python scripts", "python3": "Python scripts",
    "py": "Python scripts", "pytest": "Tests", "node": "Node scripts", "terraform": "Terraform", "az": "Azure",
    "aws": "AWS", "gcloud": "Google Cloud", "systemctl": "System services",
}


def classify_heuristic(events: list[dict], topics: list[dict], meta: dict) -> dict:
    tools = meta.get("tools") or []
    domain = onto.domain_for_tools(tools)
    main = next((t for t in tools if t in onto.TOOL_DOMAIN), tools[0] if tools else None)
    project = meta.get("project")
    if main:
        name = TOOL_TOPIC_NAMES.get(main, main.capitalize())
    elif project:
        name = f"Project {project}"
    else:
        name = "General"
    tid, nt, domain = _resolve(None, {"name": name, "description": f"Sessions about {name} (detected heuristically)."},
                               domain, topics)
    cmds = "\n".join(e["command"] for e in events)
    failed = sum(1 for e in events if e.get("exit_code") not in (0, None))
    summary = f"{len(events)} commands" + (f", {failed} failed" if failed else "") + f".\n\n```\n{cmds}\n```"
    return {"topic_id": tid, "new_topic": nt, "domain": domain,
            "title": (events[0]["command"][:60] if events else ""), "summary": summary, "method": "heuristic"}


def classify(events: list[dict], topics: list[dict], meta: dict) -> dict:
    return classify_llm(events, topics, meta) or classify_heuristic(events, topics, meta)


# ---------- Tidy up existing topics (migration / merging) ----------

CONSOLIDATE_PROMPT = f"""You are tidying up the topic list of a shell documentation tool.
Assign every topic to exactly one DOMAIN from this fixed list (use the key):
{DOMAIN_TEXT}
And find duplicates: topics that mean the same thing (e.g. "Docker setup" and "Setting up containers").
Reply ONLY with JSON:
{{"domains": {{"<topic_id>": "<domain>", ...}},
 "merge": [[<id_to_keep>, <id_merged_into_it>, ...], ...]}}
Only merge when you are sure."""


def consolidate_llm(topics: list[dict]) -> dict | None:
    model = current_model() or pick_model()
    if not model or not topics:
        return None
    listing = "\n".join(f'- id={t["id"]}: {t["name"]} — {t["description"] or ""}' for t in topics)
    payload = {"model": model, "stream": False, "format": "json", "options": {"temperature": 0.1},
               "messages": [{"role": "system", "content": CONSOLIDATE_PROMPT},
                            {"role": "user", "content": "Topics:\n" + listing}]}
    try:
        res = _http_json(f"{OLLAMA_URL}/api/chat", payload, timeout=300)
        data = json.loads(res.get("message", {}).get("content", ""))
        return data if isinstance(data, dict) else None
    except Exception:
        return None
