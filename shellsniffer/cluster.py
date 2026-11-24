"""Documenting a work session: domain, topic, title and an ASD-STE100-style summary.

:func:`classify` asks the Ollama chat model (:func:`classify_llm`) and falls back to a simple
heuristic (:func:`classify_heuristic`) when Ollama is disabled, not reachable or answers nonsense.
All requests go through :mod:`ollama`.
"""
import os

from . import ollama
from . import ontology as onto


DOMAIN_TEXT = "\n".join(f'- {k}: {n} ({d})' for k, n, d in onto.DOMAINS)

# Language of titles, summaries and new topic names (the UI itself is English)
DOC_LANGUAGE = os.environ.get("SHELLSNIFFER_DOC_LANGUAGE", "English")

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
- Write a title and a summary of the session in {DOC_LANGUAGE}. The summary is technical documentation
  in the style of ASD-STE100 Simplified Technical English (follow the rules closely, about 80 %;
  command names, flags, paths and product names are always allowed as they are):
  * Short sentences: procedures max. 20 words, descriptions max. 25 words. One instruction per sentence.
  * Procedures in the imperative ("Install the package."), active voice, simple present tense.
  * Simple, common words with one meaning each. Use articles. Avoid "-ing" forms and long noun clusters.
  * Use exactly this Markdown structure:
      **Purpose:** <one sentence: what the work was for>
      **Procedure:**
      1. <step> [n]
      2. <step> [n][m]
      **Result:** <what worked; what failed and why, if visible>
    Each step ends with the number(s) of the command(s) it is based on, e.g. [3] or [4][5],
    using the numbers [n] of the work session below. Combine trivial commands (cd, ls) into one step.
  * Add "**Note:**" only for an important warning or follow-up.
  Do not invent anything that does not follow from the commands and their output.

Reply ONLY with JSON in this format:
{{"domain": "<domain key>",
 "topic_id": <id of an existing topic or null>,
 "new_topic": {{"name": "...", "description": "One sentence on what it is about"}} or null,
 "title": "Short title of the session",
 "summary": "Markdown summary in the structure above"}}"""


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
    for i, e in enumerate(events, 1):   # [n] = the number the summary refers to (same order as "All commands" in the UI)
        lines.append(f'[{i}] ({e["shell"]}) {e["cwd"] or "?"} $ {e["command"]}   (exit {e["exit_code"]})')
        out = _clip(e.get("output"), 600)
        if out:
            lines.append("    Output: " + out.replace("\n", "\n    "))
    ctx = f'Project (from the directory): {meta.get("project") or "unknown"}\nTools: {", ".join(meta.get("tools") or []) or "-"}'
    return ("Existing topics:\n" + ("\n".join(tl) or "(no topics yet)") +
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
    """The model's answer, validated; None if there is no usable answer."""
    model = ollama.current_chat_model() or ollama.chat_model()
    if not model:
        return None
    try:
        data = ollama.chat_json(model, SYSTEM_PROMPT, build_user_prompt(events, topics, meta))
    except Exception:
        ollama.forget_chat_model()     # look for a model again next time
        return None
    if not data:
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
    """Without a model: topic from the main tool (or project), one procedure step per command."""
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
    failed = [i for i, e in enumerate(events, 1) if e.get("exit_code") not in (0, None)]
    steps = "\n".join(f"{i}. Run `{e['command'].splitlines()[0][:120]}`. [{i}]" for i, e in enumerate(events, 1))
    summary = (f"**Purpose:** Work with {name}.\n\n**Procedure:**\n{steps}\n\n**Result:** {len(events)} commands"
               + (f", {len(failed)} failed: " + "".join(f"[{i}]" for i in failed) if failed else ", all successful") + ".")
    return {"topic_id": tid, "new_topic": nt, "domain": domain,
            "title": (events[0]["command"][:60] if events else ""), "summary": summary, "method": "heuristic"}


def classify(events: list[dict], topics: list[dict], meta: dict) -> dict:
    """Returns {topic_id | new_topic, domain, title, summary, method}."""
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
    """Model-based tidy-up of the topic list (API only, no button in the UI at the moment)."""
    model = ollama.current_chat_model() or ollama.chat_model()
    if not model or not topics:
        return None
    listing = "\n".join(f'- id={t["id"]}: {t["name"]} — {t["description"] or ""}' for t in topics)
    try:
        return ollama.chat_json(model, CONSOLIDATE_PROMPT, "Topics:\n" + listing, temperature=0.1)
    except Exception:
        return None
