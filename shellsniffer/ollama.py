"""The Ollama client – the only module that talks to the local language model.

Every request goes through :func:`request_json` / :func:`stream_chat`, which refuse with
:class:`OllamaDisabled` while Ollama is switched off in the UI (setting ``ollama``). So "disabled"
really means: not a single request is sent.

Models:
- chat model (SniffMagic, ask the shroom): ``SHELLSNIFFER_OLLAMA_MODEL`` or the first installed
  model that is not an embedding model; cached in :func:`current_chat_model`.
- embedding model (hybrid search): chosen in :mod:`embeddings` from :func:`list_models`.
"""
import json
import os
import urllib.request

URL = os.environ.get("SHELLSNIFFER_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
PREFERRED_CHAT_MODEL = os.environ.get("SHELLSNIFFER_OLLAMA_MODEL", "")
EMBED_HINTS = ("embed", "bge", "minilm", "e5", "gte")   # model names that are embedding models

_chat_model: dict = {"name": None}


class OllamaDisabled(RuntimeError):
    """Ollama is switched off in shellSniffer: no request may be sent."""


def enabled() -> bool:
    from .state import settings   # imported lazily: state imports db, which must exist first
    return settings.get("ollama", True)


def _guard() -> None:
    if not enabled():
        raise OllamaDisabled("Ollama is disabled in shellSniffer – enable it at the Ollama chip or in ⚙ Settings")


def request_json(path: str, payload: dict | None = None, timeout: float = 120) -> dict:
    """GET (no payload) or POST JSON to ``URL + path`` and return the JSON answer."""
    _guard()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(URL + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def list_models() -> list[str]:
    """Names of all installed models (raises if Ollama is disabled or not reachable)."""
    return [m["name"] for m in request_json("/api/tags", timeout=3).get("models", [])]


def is_embedding_model(name: str) -> bool:
    return any(h in name.lower() for h in EMBED_HINTS)


def chat_model() -> str | None:
    """Picks the configured chat model, or the first installed one; None if Ollama is not usable."""
    try:
        names = [n for n in list_models() if not is_embedding_model(n)]
    except Exception:
        names = []
    pick = None
    if names:
        pick = next((n for n in names if PREFERRED_CHAT_MODEL and
                     (n == PREFERRED_CHAT_MODEL or n.split(":")[0] == PREFERRED_CHAT_MODEL)), names[0])
    _chat_model["name"] = pick
    return pick


def current_chat_model() -> str | None:
    """The chat model found by the last :func:`chat_model` call (no request)."""
    return _chat_model["name"]


def forget_chat_model() -> None:
    _chat_model["name"] = None


def chat_json(model: str, system: str, user: str, temperature: float = 0.2, timeout: float = 300) -> dict | None:
    """One chat turn in JSON mode; returns the parsed JSON object or None."""
    res = request_json("/api/chat", {"model": model, "stream": False, "format": "json", "options": {"temperature": temperature},
                                     "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                       timeout=timeout)
    data = json.loads(res.get("message", {}).get("content", ""))
    return data if isinstance(data, dict) else None


def stream_chat(model: str, system: str, user: str, temperature: float = 0.2, timeout: float = 600):
    """One chat turn, streamed: yields the text pieces as the model produces them."""
    _guard()
    payload = {"model": model, "stream": True, "options": {"temperature": temperature},
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    req = urllib.request.Request(URL + "/api/chat", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            if not raw.strip():
                continue
            chunk = json.loads(raw)
            text = (chunk.get("message") or {}).get("content") or ""
            if text:
                yield text
            if chunk.get("done"):
                break


def embed(model: str, texts: list[str], timeout: float = 300) -> list[list[float]]:
    """Vectors for ``texts`` (raises ValueError if the model returned none)."""
    vecs = request_json("/api/embed", {"model": model, "input": texts}, timeout=timeout).get("embeddings")
    if not vecs or len(vecs) != len(texts):
        raise ValueError(f"{model} returned no embeddings")
    return vecs
