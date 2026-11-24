"""Lightweight ontology for shellSniffer.

Two levels:   domain (fixed)  ->  topic (grows freely, assigned by the model or the heuristic)
Per session:  project (derived from the working directory) and tools (first word of each command)

Deliberately no OWL/RDF – just enough structure to avoid duplicate topics and to let the
journal be filtered by domain, project and tool.
"""
import re
from collections import Counter

# Fixed domains. The order defines the colour (slot 1–8); "other" is neutral grey.
DOMAINS = [
    ("git",         "Version control",       "Git, GitHub, branches, commits, merges, pull requests"),
    ("container",   "Containers",            "Docker, Podman, Compose, Kubernetes, images"),
    ("netzwerk",    "Network & remote",      "SSH, HTTP calls, DNS, ports, proxies, file transfer"),
    ("pakete",      "Packages & environments", "Package managers, virtual environments, installs, versions"),
    ("dateien",     "Files & system",        "Files, folders, permissions, processes, services, shell configuration"),
    ("entwicklung", "Development & build",   "Running, testing, building and debugging code, editors"),
    ("daten",       "Data & cloud",          "Databases, data processing, cloud services, infrastructure"),
    ("ki",          "AI & models",           "LLMs, Ollama, machine learning, training, inference"),
    ("sonstiges",   "Other",                 "Anything that fits no other domain"),
]
DOMAIN_KEYS = [d[0] for d in DOMAINS]

TOOL_DOMAIN = {
    # version control
    "git": "git", "gh": "git", "glab": "git", "tig": "git", "lazygit": "git",
    # container
    "docker": "container", "docker-compose": "container", "podman": "container", "kubectl": "container",
    "helm": "container", "k9s": "container", "minikube": "container", "kind": "container", "nerdctl": "container",
    # network
    "ssh": "netzwerk", "scp": "netzwerk", "rsync": "netzwerk", "curl": "netzwerk", "wget": "netzwerk",
    "ping": "netzwerk", "dig": "netzwerk", "nslookup": "netzwerk", "netstat": "netzwerk", "ss": "netzwerk",
    "nc": "netzwerk", "ip": "netzwerk", "ifconfig": "netzwerk", "ipconfig": "netzwerk", "traceroute": "netzwerk",
    "tracert": "netzwerk", "invoke-webrequest": "netzwerk", "invoke-restmethod": "netzwerk", "test-netconnection": "netzwerk",
    # packages
    "pip": "pakete", "pip3": "pakete", "uv": "pakete", "poetry": "pakete", "conda": "pakete", "mamba": "pakete",
    "npm": "pakete", "npx": "pakete", "yarn": "pakete", "pnpm": "pakete", "apt": "pakete", "apt-get": "pakete",
    "dnf": "pakete", "yum": "pakete", "pacman": "pakete", "brew": "pakete", "winget": "pakete", "choco": "pakete",
    "scoop": "pakete", "snap": "pakete", "flatpak": "pakete", "pyenv": "pakete", "nvm": "pakete", "virtualenv": "pakete",
    "install-module": "pakete", "dpkg": "pakete", "rpm": "pakete",
    # files & system
    "cp": "dateien", "mv": "dateien", "rm": "dateien", "mkdir": "dateien", "chmod": "dateien", "chown": "dateien",
    "find": "dateien", "grep": "dateien", "rg": "dateien", "sed": "dateien", "awk": "dateien", "tar": "dateien",
    "zip": "dateien", "unzip": "dateien", "du": "dateien", "df": "dateien", "ps": "dateien", "kill": "dateien",
    "top": "dateien", "htop": "dateien", "systemctl": "dateien", "journalctl": "dateien", "service": "dateien",
    "copy-item": "dateien", "move-item": "dateien", "remove-item": "dateien", "new-item": "dateien",
    "get-process": "dateien", "stop-process": "dateien", "get-service": "dateien", "set-executionpolicy": "dateien",
    "robocopy": "dateien", "xcopy": "dateien", "touch": "dateien", "ln": "dateien", "wsl": "dateien", "sudo": "dateien",
    # development
    "python": "entwicklung", "python3": "entwicklung", "py": "entwicklung", "node": "entwicklung", "pytest": "entwicklung",
    "make": "entwicklung", "cmake": "entwicklung", "cargo": "entwicklung", "go": "entwicklung", "dotnet": "entwicklung",
    "java": "entwicklung", "mvn": "entwicklung", "gradle": "entwicklung", "gcc": "entwicklung", "code": "entwicklung",
    "vim": "entwicklung", "nvim": "entwicklung", "nano": "entwicklung", "jupyter": "entwicklung", "uvicorn": "entwicklung",
    "deno": "entwicklung", "bun": "entwicklung", "ruby": "entwicklung", "rustc": "entwicklung", "tsc": "entwicklung",
    # data & cloud
    "psql": "daten", "mysql": "daten", "sqlite3": "daten", "mongosh": "daten", "redis-cli": "daten", "duckdb": "daten",
    "az": "daten", "aws": "daten", "gcloud": "daten", "gsutil": "daten", "bq": "daten", "terraform": "daten",
    "ansible": "daten", "pulumi": "daten", "wrangler": "daten", "jq": "daten", "csvkit": "daten",
    # AI
    "ollama": "ki", "huggingface-cli": "ki", "llm": "ki", "claude": "ki", "nvidia-smi": "ki", "tensorboard": "ki",
}

# Navigation/display commands say nothing about the topic
NAV = {"cd", "ls", "ll", "la", "dir", "pwd", "cls", "clear", "set-location", "get-childitem", "cat", "type",
       "echo", "explorer", "exit", "history", "man", "help", "less", "more", "head", "tail", "which", "where",
       "get-content", "write-host", "start", "open", "xdg-open", "z", "true", "false", ":"}

# Keywords (English and German) to place existing topics that have no domain yet
NAME_HINTS = [
    (r"git|branch|commit|merge|rebase|pull.?request|repo", "git"),
    (r"docker|container|compose|kubern|k8s|podman|image|helm", "container"),
    (r"ssh|netz|network|http|dns|proxy|port|remote|curl", "netzwerk"),
    (r"paket|package|pip|npm|venv|umgebung|environment|install|conda|winget|apt", "pakete"),
    (r"datei|file|ordner|folder|rechte|permission|prozess|process|dienst|service|system|shell|profil", "dateien"),
    (r"python|node|build|test|debug|code|entwickl|compile|script", "entwicklung"),
    (r"daten|data|sql|datenbank|database|cloud|azure|aws|gcp|terraform|infra", "daten"),
    (r"ollama|llm|modell|model|\bki\b|\bai\b|machine.?learning|rag|embedding|gpu|cuda", "ki"),
]


def tool_of(cmd: str) -> str:
    c = (cmd or "").strip()
    c = re.sub(r"^(sudo|&|\.|time|nohup|env)\s+", "", c)
    c = re.sub(r"^(\w+=\S+\s+)+", "", c)                     # VAR=x command
    tok = re.split(r"[\s|;&()]+", c, maxsplit=1)[0] if c else ""
    tok = tok.strip("'\"").replace("\\", "/").split("/")[-1].lower()
    tok = re.sub(r"\.(exe|cmd|bat|ps1|sh|py)$", "", tok)
    if tok in ("python", "python3", "py") and re.search(r"\s-m\s+pip\b", c):
        return "pip"
    if tok in ("python", "python3", "py") and re.search(r"\s-m\s+venv\b", c):
        return "venv"
    return tok


def tools_of(commands: list[str]) -> list[str]:
    """Tools of a session, most frequent first, without navigation commands."""
    cnt = Counter(t for t in (tool_of(c) for c in commands) if t and t not in NAV)
    return [t for t, _ in cnt.most_common(6)]


_ROOT_DIRS = re.compile(r"^(programming projects|projects|projekte|project|code|src|source|repos|repositories|"
                        r"dev|develop|workspace|workspaces|git|github|work|arbeit)$", re.I)


def project_of(cwd: str | None) -> str | None:
    """Project from the working directory: the folder below a typical projects root
    (e.g. 'D:\\Programming Projects\\shellsniffer\\src' -> 'shellsniffer'), otherwise the first folder below home."""
    if not cwd:
        return None
    parts = [p for p in re.split(r"[\\/]+", cwd.strip()) if p]
    if not parts:
        return None
    for i, p in enumerate(parts[:-1]):
        if _ROOT_DIRS.match(p):
            return parts[i + 1]
    low = [p.lower() for p in parts]
    for marker in ("home", "users"):                         # /home/<user>/<x>, C:\Users\<user>\<x>
        if marker in low:
            i = low.index(marker)
            rest = parts[i + 2:]
            if rest and rest[0].lower() not in ("documents", "dokumente", "desktop", "downloads", "appdata"):
                return rest[0] if not rest[0].startswith(".") else None
            if len(rest) > 1:
                return rest[1]
            return None
    if re.fullmatch(r"[a-zA-Z]:?", parts[0]) and len(parts) > 1:   # D:\foo\..., /mnt/d/foo, /d/foo
        return parts[1] if len(parts) > 1 else None
    if parts[0] == "mnt" and len(parts) > 2:
        return parts[2]
    return parts[-1] if parts[-1] not in ("root", "tmp") else None


def project_of_block(cwds: list[str]) -> str | None:
    cnt = Counter(p for p in (project_of(c) for c in cwds) if p)
    return cnt.most_common(1)[0][0] if cnt else None


def domain_for_tools(tools: list[str]) -> str:
    cnt = Counter(TOOL_DOMAIN[t] for t in tools if t in TOOL_DOMAIN)
    return cnt.most_common(1)[0][0] if cnt else "sonstiges"


def domain_for_name(name: str, desc: str = "") -> str | None:
    text = f"{name} {desc}".lower()
    for pat, key in NAME_HINTS:
        if re.search(pat, text):
            return key
    return None


_STOP = {"und", "der", "die", "das", "mit", "von", "für", "im", "in", "the", "and", "of", "for", "with", "to", "a", "-", "&", "/"}


def _tokens(name: str) -> set[str]:
    toks = re.findall(r"[a-zäöüß0-9]+", name.lower())
    out = set()
    for t in toks:
        if t in _STOP:
            continue
        t = re.sub(r"(ungen|ung|ing|en|er|es|e|s)$", "", t) if len(t) > 5 else t   # crude stemming
        out.add(t)
    return out


def similar(a: str, b: str) -> bool:
    """Rough duplicate detection for topic names ('Docker-Setup' ~ 'Docker setup', 'Git workflow' ~ 'Git workflows')."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    inter = len(ta & tb)
    return inter / len(ta | tb) >= 0.67 or (inter >= 1 and (ta <= tb or tb <= ta) and min(len(ta), len(tb)) >= 2)
