"""Batch import of shell dumps someone collected before shellSniffer was running.

The format is detected per file (:func:`parse`). Supported:

==================  ====================================================================
format              looks like
==================  ====================================================================
jsonl / json        objects with ``command`` (or ``cmd``), optional ``output``, ``cwd``,
                    ``timestamp``/``ts``/``time``, ``exit_code``, ``shell``, ``host``
csv                 header row with a ``command`` (or ``cmd``) column, same optional columns
honeypot            shellSniffer's own honeypot logs (``===== time | shell | host | cwd ...``)
pwsh-transcript     PowerShell ``Start-Transcript`` files (``PS C:\\path> command`` + output)
terminal-log        copied terminal sessions / ``script`` logs: prompt lines
                    (``user@host:~/dir$ cmd``, ``$ cmd``, ``PS C:\\> cmd``) followed by output
zsh-history         ``: 1700000000:0;command``
bash-history        optional ``#1700000000`` timestamp lines before each command
history             anything else: one command per line (e.g. PSReadLine
                    ``ConsoleHost_history.txt``)
==================  ====================================================================

Commands without a timestamp get the file's date (``fallback_ts``), one second apart, so their
order is kept. Every entry gets a stable id (hash of file name, position, command), so uploading
the same file twice does not create duplicates. Imported commands go through the same secret
filter as live ones and form sessions per file; document them with SniffMagic afterwards.
"""
import csv
import hashlib
import io
import json
import re
from datetime import datetime

# ---------------------------------------------------------------- helpers
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]")

_PROMPTS = [
    ("pwsh", re.compile(r"^PS (?P<cwd>[A-Za-z]:\\[^>]*|/[^>]*)> (?P<cmd>.+)$")),
    ("bash", re.compile(r"^(?:\([^)]*\) )?(?P<user>[\w.-]+)@(?P<host>[\w.-]+)[: ](?P<cwd>[~/][^$#\n]*?)\s*[$#] (?P<cmd>.+)$")),
    ("zsh", re.compile(r"^(?:\([^)]*\) )?(?P<user>[\w.-]+)@(?P<host>[\w.-]+) (?P<cwd>[~/]\S*) ?[%>] (?P<cmd>.+)$")),
    ("cmd", re.compile(r"^(?P<cwd>[A-Za-z]:\\[^>]*)>(?P<cmd>\S.*)$")),
    ("sh", re.compile(r"^[$#] (?P<cmd>.+)$")),
]


def _parse_time(v) -> float | None:
    """Unix seconds/milliseconds or an ISO-like date string -> Unix seconds."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) or re.fullmatch(r"\d+(\.\d+)?", str(v).strip()):
        x = float(v)
        return x / 1000 if x > 1e11 else x
    s = str(v).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return (datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)).timestamp()
        except ValueError:
            continue
    return None


def _entry(command, ts=None, output=None, cwd="", shell="", host="", exit_code=None, duration_ms=None) -> dict:
    return {"command": command.rstrip(), "ts": ts, "output": (output or "").strip("\n") or None, "cwd": cwd or "",
            "shell": shell, "host": host, "exit_code": exit_code, "duration_ms": duration_ms}


def _first(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _from_record(d: dict) -> dict | None:
    """One JSON object / CSV row -> entry (field names are matched loosely)."""
    low = {str(k).lower(): v for k, v in d.items()}
    cmd = _first(low, "command", "cmd", "commandline", "command_line", "input")
    if not cmd:
        return None
    ts = _parse_time(_first(low, "timestamp", "ts", "time", "started_at", "date", "start_time"))
    ec = _first(low, "exit_code", "exit", "exitcode", "status", "return_code")
    try:
        ec = int(float(ec)) if ec is not None else None
    except (TypeError, ValueError):
        ec = None
    return _entry(str(cmd), ts, _first(low, "output", "stdout", "result"), _first(low, "cwd", "dir", "directory", "pwd", "path") or "",
                  _first(low, "shell") or "", _first(low, "host", "hostname", "machine") or "", ec, _first(low, "duration_ms"))


# ---------------------------------------------------------------- format parsers
def _parse_jsonl(text: str) -> list[dict] | None:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or not all(ln.lstrip().startswith("{") for ln in lines[:5]):
        return None
    out = []
    for ln in lines:
        try:
            e = _from_record(json.loads(ln))
        except ValueError:
            continue
        if e:
            out.append(e)
    return out or None


def _parse_json(text: str) -> list[dict] | None:
    if not text.lstrip().startswith(("[", "{")):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, dict):   # {"history": [...]} / {"commands": [...]}
        data = next((v for v in data.values() if isinstance(v, list)), [data])
    out = [e for e in (_from_record(d) for d in data if isinstance(d, dict)) if e]
    return out or None


def _parse_csv(text: str) -> list[dict] | None:
    head = text.split("\n", 1)[0].lower()
    if not re.search(r"(^|[,;\t\"])(command|cmd|commandline)([,;\t\"]|$)", head):
        return None
    dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    out = [e for e in (_from_record(r) for r in csv.DictReader(io.StringIO(text), dialect=dialect)) if e]
    return out or None


def _parse_honeypot(text: str) -> list[dict] | None:
    if not text.startswith("===== "):
        return None
    out = []
    for chunk in re.split(r"^===== ", text, flags=re.M)[1:]:
        head, _, body = chunk.partition("\n")
        parts = [p.strip() for p in head.split(" | ")]
        if not body.startswith("$ "):
            continue
        cmd_line, _, output = body[2:].partition("\n")
        ec = next((int(p[5:]) for p in parts if re.fullmatch(r"exit -?\d+", p)), None)
        dur = next((int(p[:-3]) for p in parts if re.fullmatch(r"\d+ ms", p)), None)
        out.append(_entry(cmd_line, _parse_time(parts[0]), output, parts[3] if len(parts) > 3 else "",
                          parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "", ec, dur))
    return out or None


def _parse_prompted(text: str, fmt: str) -> list[dict] | None:
    """Terminal logs and PowerShell transcripts: a prompt line starts a command, following lines are output."""
    is_transcript = "Windows PowerShell transcript start" in text[:2000] or "PowerShell transcript start" in text[:2000]
    base_ts = None
    if is_transcript:
        m = re.search(r"Start time: (\d{14})", text[:2000])
        base_ts = _parse_time(m.group(1)) if m else None
    out, cur, hits = [], None, 0
    for raw in text.splitlines():
        line = raw.rstrip()
        if is_transcript and (re.fullmatch(r"\*{10,}", line) or re.match(r"(Windows )?PowerShell transcript (start|end)|"
                                                                          r"(Start|End) time: |Username: |RunAs User: |"
                                                                          r"Configuration Name: |Machine: |Host Application: |"
                                                                          r"Process ID: |PS(Version|Edition|Compatible|Remoting)|"
                                                                          r"(Build|CLR|WSMan|Serialization)Version", line)):
            continue
        shell = m = None
        for shell, rx in _PROMPTS:
            m = rx.match(line)
            if m:
                break
        if m and m.group("cmd").strip():
            if cur:
                out.append(cur)
            g = m.groupdict()
            cur = _entry(m.group("cmd"), base_ts, None, g.get("cwd") or "", "pwsh" if is_transcript else shell, g.get("host") or "")
            cur["output"] = ""
            hits += 1
        elif cur is not None:
            cur["output"] += line + "\n"
    if cur:
        out.append(cur)
    if hits < 1 or (not is_transcript and hits < 2 and fmt != "force"):
        return None
    for e in out:
        e["output"] = (e["output"] or "").strip("\n") or None
    return out


def _parse_zsh(text: str) -> list[dict] | None:
    if not re.match(r": \d{9,11}:\d+;", text):
        return None
    out = []
    # a command continues on the next line while its line ends with a backslash
    for m in re.finditer(r"^: (\d{9,11}):(\d+);((?:[^\n]*\\\n)*[^\n]*)", text, flags=re.M):
        out.append(_entry(m.group(3).replace("\\\n", "\n"), float(m.group(1)), shell="zsh",
                          duration_ms=int(m.group(2)) * 1000))
    return out or None


def _parse_bash(text: str) -> list[dict] | None:
    if not re.search(r"^#\d{9,11}$", text, flags=re.M):
        return None
    out, ts = [], None
    for line in text.splitlines():
        m = re.fullmatch(r"#(\d{9,11})", line.strip())
        if m:
            ts = float(m.group(1))
        elif line.strip():
            out.append(_entry(line, ts, shell="bash"))
    return out or None


def _parse_history(text: str, name: str) -> list[dict]:
    shell = "pwsh" if "consolehost" in name.lower() or name.lower().endswith(".ps1") else ""
    out, buf = [], ""
    for line in text.splitlines():
        if shell == "pwsh" and line.endswith("`"):     # PSReadLine stores multi-line commands with a backtick
            buf += line[:-1] + "\n"
            continue
        cmd = (buf + line).strip()
        buf = ""
        if cmd and not cmd.startswith("#"):
            out.append(_entry(cmd, shell=shell))
    return out


def parse(name: str, text: str) -> tuple[str, list[dict]]:
    """Detects the format of one file and returns (format name, entries)."""
    text = _ANSI.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).lstrip("\ufeff")
    for fmt, fn in (("jsonl", _parse_jsonl), ("json", _parse_json), ("honeypot", _parse_honeypot),
                    ("zsh-history", _parse_zsh), ("bash-history", _parse_bash), ("csv", _parse_csv)):
        r = fn(text)
        if r:
            return fmt, r
    r = _parse_prompted(text, "auto")
    if r:
        return ("pwsh-transcript" if "transcript start" in text[:2000] else "terminal-log"), r
    return "history", _parse_history(text, name)


def to_reports(name: str, entries: list[dict], fallback_ts: float, host: str = "") -> list[dict]:
    """Entries -> payloads for :func:`ingest.ingest` (same field names as the hook reports)."""
    shell_guess = "pwsh" if name.lower().endswith((".ps1", ".txt")) and "consolehost" in name.lower() else ""
    t = fallback_ts - len(entries)          # undated commands end at the file date, one second apart
    reports = []
    for i, e in enumerate(entries):
        ts = e["ts"] if e["ts"] is not None else t + i
        uid = hashlib.sha1(f"{name}|{i}|{e['command']}".encode("utf-8", "replace")).hexdigest()
        reports.append({
            "command": e["command"], "output": e["output"], "cwd": e["cwd"],
            "shell": e["shell"] or shell_guess or "import", "host": e["host"] or host or "import",
            "session": f"import:{name}"[:80], "exit_code": e["exit_code"], "duration_ms": e["duration_ms"],
            "started_at": ts * 1000, "atuin_uuid": f"import:{uid}",
        })
    return reports
