"""Removes secrets (passwords, tokens, keys) from commands and output
before anything is stored or handed to an LLM."""
import re

MASK = "‹REDACTED›"

_PATTERNS = [
    # key=value / key: value with a suspicious key name
    re.compile(r"(?i)\b([\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|client[_-]?secret|auth)[\w.-]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"),
    # --password foo, --token foo, -Password foo (PowerShell)
    re.compile(r"(?i)(--?(?:password|passwd|pass|token|secret|api-?key|access-?key|client-?secret|auth)\b(?:\s+|=))(\"[^\"]*\"|'[^']*'|\S+)"),
    # Authorization-Header
    re.compile(r"(?i)(authorization:\s*(?:bearer|basic|token)\s+)(\S+)"),
    re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/=-]{12,})"),
    # URLs with credentials: https://user:pass@host
    re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(@)"),
]

_TOKENS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),            # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),                  # OpenAI/Anthropic-artig
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                        # AWS Access Key
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),            # Slack
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]


def redact(text: str | None) -> str | None:
    if not text:
        return text
    t = _PATTERNS[0].sub(lambda m: m.group(1) + m.group(2) + MASK, text)
    t = _PATTERNS[1].sub(lambda m: m.group(1) + MASK, t)
    t = _PATTERNS[2].sub(lambda m: m.group(1) + MASK, t)
    t = _PATTERNS[3].sub(lambda m: m.group(1) + MASK, t)
    t = _PATTERNS[4].sub(lambda m: m.group(1) + MASK + m.group(3), t)
    for p in _TOKENS:
        t = p.sub(MASK, t)
    return t
