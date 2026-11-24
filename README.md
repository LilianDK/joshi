<img src="shellsniffer/static/logo.svg" alt="shellSniffer – a sniffing pixel dog" width="190">

# shellSniffer

**shellSniffer sniffs through your shells.** It records every command you type in PowerShell, Git Bash, WSL, bash or zsh – **together with its output** – and turns it into a live view, a searchable log, a documentation of your work and a journal. You can even ask it questions about what you did.

Everything runs on your own machine: commands are stored in a local SQLite database, the language model is a local [Ollama](https://ollama.com) model, and no cloud service is involved.

| | |
|---|---|
| 👀 **Live** | every command of every open shell, the moment it finishes – with output, exit code and duration |
| 🔎 **Sniff logs** | full-text search over commands, output and directories (BM25), with a time-range filter |
| 🍄 **Sniff logs with shroom** | hybrid search that also understands *meaning* – and **asks the shroom**: an answer to your question, written from your own logs, with sources |
| ✨ **SniffMagic** | a local model groups your sessions into topics and documents them step by step (ASD-STE100 style) – only when you click it |
| 📅 **Journal** | which topics you worked on, day by day |
| 📥 **Import** | load shell history, transcripts and logs someone collected before |

---

## Contents

- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Setup in detail](#setup-in-detail)
- [Using shellSniffer](#using-shellsniffer)
- [Where your data is stored](#where-your-data-is-stored)
- [Configuration](#configuration)
- [Privacy](#privacy)
- [Uninstalling](#uninstalling)
- [Development](#development)
- [Further documentation](#further-documentation)

---

## Quick start

**Windows** (PowerShell):

```powershell
winget install -e --id Python.Python.3.12        # 1. Python 3.10+
winget install -e --id Ollama.Ollama             # 2. Ollama (recommended)
ollama pull llama3.1                             #    a chat model
ollama pull nomic-embed-text                     #    an embedding model for the hybrid search
git clone https://github.com/LilianDK/shellSniffer.git
cd shellSniffer
.\start.ps1                                      # 3. start the server – keep this window open
```

In a **second** PowerShell window, in the same folder:

```powershell
.\install.ps1                                    # 4. add the hooks to all your shells
```

**5. Open new shell windows** and type a few commands – they appear at <http://127.0.0.1:8765> right away.

**Linux / macOS:**

```bash
git clone https://github.com/LilianDK/shellSniffer.git && cd shellSniffer
ollama pull llama3.1 && ollama pull nomic-embed-text     # optional, recommended
bash start.sh              # terminal 1: the server (http://127.0.0.1:8765)
bash install.sh            # terminal 2: hooks for bash and zsh – then open a new terminal
```

---

## How it works

```
 your shells                    shellSniffer server  (http://127.0.0.1:8765)                 browser
 ───────────                    ────────────────────────────────────────────                 ───────
 PowerShell ─┐  hook ─────────▶  secret filter ─▶ SQLite ──▶ live updates (WebSocket) ───▶  web UI
 Git Bash   ─┤                       ▲              │
 WSL bash   ─┤  Atuin (optional) ────┘              ├──▶ BM25 index ─────────┐
 bash / zsh ─┘                                      ├──▶ honeypot files      ├──▶ Sniff logs / with shroom
                upload (📥 Import) ─────────────────┘                        │
                                                    Ollama (only on request) ┴──▶ SniffMagic · ask the shroom
```

1. **Hooks** – the installer adds one line to each shell profile. After every command the hook sends the command, its directory, exit code, duration and output to the local server, in the background – your prompt is never slowed down.
2. **Atuin (optional)** – with [Atuin](https://atuin.sh), Atuin records the commands and shellSniffer adds the output. You can switch between *with* and *without Atuin* at any time.
3. **Server** – a small Python (FastAPI) app masks passwords, tokens and keys, then stores everything in SQLite and indexes it for the search.
4. **Language model on request** – Ollama only runs when you start SniffMagic, search with the shroom, or turn on continuous shrooming. You can also disable it completely.

---

## Requirements

| What | Needed for | Required? |
|---|---|---|
| **Python 3.10+** | the server | yes |
| **Ollama** + a chat model (e.g. `llama3.1`, `qwen2.5:7b`) | SniffMagic, ask the shroom | recommended – without it, a simple heuristic names the topics |
| an Ollama **embedding model** (`nomic-embed-text`) | *Sniff logs with shroom* | recommended – otherwise the chat model is used (slower, less precise) |
| **Atuin** | more reliable capture, Ctrl+R history search | optional – installed by the installer unless you pass `-NoAtuin` / `--no-atuin` |
| `curl`, `script` (util-linux) | bash/zsh hooks on Linux and WSL | usually preinstalled |

The Python packages (`fastapi`, `uvicorn`, see `requirements.txt`) are installed by the start scripts into a local virtual environment.

---

## Setup in detail

### Windows

1. **Start the server:** `.\start.ps1`. The first start creates `.venv`, installs the packages and opens the web UI. If PowerShell refuses to run scripts, allow local scripts once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
2. **Install the hooks:** `.\install.ps1` (or `.\install.ps1 -NoAtuin`). It sets up Windows PowerShell, PowerShell 7 (console and VS Code), Git Bash and every WSL distribution it finds. Running it again is safe – it replaces its own lines.
3. **Open new shell windows.** Windows that were already open are not observed.

**WSL:** the server runs on Windows and WSL reaches it over the virtual network. If `install.ps1` does not run as administrator, it prints a firewall command to run once in an admin PowerShell. Details: [docs/installation.md](docs/installation.md#wsl).

### Linux / macOS

`bash start.sh` starts the server, `bash install.sh` installs the hooks for bash and zsh. Options:

- `bash install.sh --no-atuin` – without Atuin
- `bash install.sh --service` – start the server automatically as a systemd user service

### Coming from *joshi*?

shellSniffer was called *joshi* before. Your data is taken over automatically: on the first start, `~/.joshi` is copied to `~/.shellsniffer` (the old folder stays untouched). Run the installer once more so your shells load the renamed hooks.

---

## Using shellSniffer

### The page at a glance

| Where | What |
|---|---|
| **Header** | Live / Journal, the **Ollama chip** with its on/off switch, counters, **⚙ Settings**, **ⓘ** info, ☾/☀ theme, **📥 Import dumps**, Markdown export |
| **Left** | the two search fields, 🦴 SniffOptions, ✨ SniffMagic – and your documentation (or the search results) |
| **Right** | the live stream of all shells, with filters per shell session |

### Sniff logs – exact words (upper search field)

Full-text search over all commands, their output and directories, ranked with **BM25**. Every word matches as a prefix (`dock` finds `docker`). Matching **📄 documented sessions** are listed above the commands. Click a hit to jump to its topic. No language model involved.

### 🍄 Sniff logs with shroom – words + meaning (lower search field)

**Hybrid search:** BM25 plus semantic similarity from an Ollama embedding model, merged with weighted Reciprocal Rank Fusion – *remove container* also finds `docker rm`. Every hit shows where it came from (`BM25 #3`, `🍄 0.82`).

**Press Enter to ask the shroom.** The best matching sessions and commands are handed to the chat model as its only source; the answer appears below the field, word by word, with its sources as `[n]` – click one to open its topic. If your logs do not contain the answer, the shroom says so. While it works, the dog sniffs at a spinning 🍄 – *shrooming..*

### 🦴 SniffOptions

- **Hybrid balance** – slider from *BM25 only* to *cosine only* for the shroom search.
- **Time range** – from / to, or *Last hour*, *Today*, *Last 7 days*, *Last 30 days*. Applies to both search fields and to the shroom's answers. With a range but no search words, all commands in the range are listed.

### ✨ SniffMagic

Your commands are grouped into **sessions** per shell window. SniffMagic opens a panel where you choose **which sessions** to document – a time range or *Everything* – and shows how many are affected. A local model then gives each session a **domain**, a **topic**, a **title** and a **documentation** in ASD-STE100 style (Simplified Technical English): **Purpose**, numbered **Procedure** steps citing the commands they are based on (`[3]` = command 3 of the session), **Result**. The documentation is indexed and vectorized at once, so both searches and the shroom find it. Optionally re-document sessions that already have a topic.

### ⚙ Settings

Saved on the server – shellSniffer always opens with them:

- **🦙 Ollama** on / off (same as the switch on the Ollama chip). *Off*: not a single request goes to Ollama; SniffMagic, the shroom and continuous shrooming are unavailable, everything else keeps working.
- **🐚 Capture** – with / without Atuin.
- **🍯 Honeypot log** – only without Atuin: additionally write every shell session, commands and full output, to its own text file in `honeypot/`.
- **🐕 Roaming dog** – the mascot sniffs its way across the page (clicking the logo toggles it, too).
- **🍄 Continuous shrooming** – vectorize every new command the moment it arrives, so the shroom always covers all logs. Off by default; shows its progress.
- **🎨 Theme** – system / light / dark (remembered per browser).

### 📥 Import dumps

Upload shell activity collected earlier – files or whole folders, by drag & drop. The format is detected per file: bash / zsh / PowerShell history, PowerShell transcripts, terminal logs with prompt + output, shellSniffer honeypot logs, JSON / JSONL / CSV with a `command` field; anything else is read as one command per line. Re-uploading the same file skips what is already there. Document the imported sessions with SniffMagic afterwards.

### Journal

Days on the left, topics (or domains) on the right; the thicker the line, the more commands went into that topic that day. Click a day to read its sessions.

More details: [docs/user-guide.md](docs/user-guide.md).

---

## Where your data is stored

| What | Where |
|---|---|
| Database – all commands, output, sessions, topics, vectors | `~/.shellsniffer/shellsniffer.db` (Windows: `%USERPROFILE%\.shellsniffer\`) |
| Honeypot logs (if enabled) | `honeypot/` in the shellSniffer folder (ignored by git) |
| Temporary output buffers of running shells | `~/.shellsniffer/ps-transcript-*.txt`, `typescript-*.log` |
| Access token for clients not on localhost (WSL) | `~/.shellsniffer/token` |

The **ⓘ** dialog shows the exact paths of your installation. **Markdown export** downloads the whole documentation as one file. To start over, stop the server and delete `~/.shellsniffer`.

---

## Configuration

shellSniffer works without configuration; most things are switched in the UI. Useful environment variables (set them before starting the server):

| Variable | Default | Meaning |
|---|---|---|
| `SHELLSNIFFER_OLLAMA_MODEL` | first installed chat model | chat model for SniffMagic and the shroom, e.g. `qwen2.5:7b` |
| `SHELLSNIFFER_EMBED_MODEL` | first installed embedding model | model for the hybrid search, e.g. `nomic-embed-text` |
| `SHELLSNIFFER_DOC_LANGUAGE` | `English` | language of the documentation, e.g. `German` |
| `SHELLSNIFFER_OLLAMA_URL` | `http://127.0.0.1:11434` | where Ollama runs |
| `SHELLSNIFFER_DIR` | `~/.shellsniffer` | data folder |
| `SHELLSNIFFER_HONEYPOT_DIR` | `<shellSniffer folder>/honeypot` | folder for the honeypot logs |
| `SHELLSNIFFER_DISABLE=1` | – | set in a shell to keep it out of shellSniffer entirely |

All variables and script options: [docs/configuration.md](docs/configuration.md).

---

## Privacy

shellSniffer records commands **and their output**. Before anything is stored, written to a honeypot file, sent to the browser or to the model, a secret filter masks passwords, tokens, API keys, authorization headers, credentials in URLs and private keys. The filter catches common patterns but is no guarantee. The language model runs locally and only on your request – or not at all, if you disable Ollama. To keep a shell out entirely, set `SHELLSNIFFER_DISABLE=1` in it; in bash with `HISTCONTROL=ignorespace`, commands starting with a space are skipped.

---

## Uninstalling

```powershell
.\install.ps1 -Uninstall          # Windows: removes all shellSniffer lines from the shell profiles
```

```bash
bash install.sh --uninstall       # Linux / macOS: also removes the systemd service
```

Atuin stays installed. Delete `~/.shellsniffer` (and `honeypot/`) to remove the recorded data.

---

## Development

```
start.ps1 / start.sh         start the server (creates .venv on first run)
install.ps1 / install.sh     install / remove the shell hooks (and Atuin)
hooks/                       the hooks: shellsniffer.ps1, shellsniffer.bash, shellsniffer.zsh
shellsniffer/                the server (Python package) – app.py lists every module in its docstring
  app.py                     HTTP API, WebSocket, background workers
  ollama.py                  the only Ollama client (honours "Ollama disabled")
  search.py, embeddings.py   BM25 + hybrid search, vectors, continuous shrooming
  ask.py                     ask the shroom (RAG, streamed)
  cluster.py, documenter.py  SniffMagic: documentation of sessions
  ingest.py, importer.py     storing commands; batch import
  …                          config, db, state, atuin, honeypot, timerange, ontology, redact
  static/                    web UI without a build step: index.html, css/, js/ (one file per area), logo.svg
tests/smoke_test.py          end-to-end test with a built-in fake Ollama
docs/                        detailed documentation
```

Run the server by hand with auto-reload:

```bash
python -m uvicorn shellsniffer.app:app --port 8765 --reload
```

Run the test (starts its own server and a fake Ollama on free ports, uses a temporary data folder):

```bash
.venv/Scripts/python tests/smoke_test.py      # Windows
.venv-linux/bin/python tests/smoke_test.py    # Linux / macOS
```

How the pieces fit together – capture pipeline, data model, API, search and the shroom: [docs/architecture.md](docs/architecture.md).

---

## Further documentation

- [Installation](docs/installation.md) – Windows, WSL, Git Bash, Linux in detail
- [Ollama setup](docs/ollama.md) – choosing models, documentation language
- [User guide](docs/user-guide.md) – every view and control
- [Configuration](docs/configuration.md) – all environment variables and script options
- [Troubleshooting](docs/troubleshooting.md)
- [How it works](docs/architecture.md) – capture pipeline, data model, API

## License

[MIT](LICENSE)
