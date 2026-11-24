<img src="shellsniffer/static/logo.svg" alt="shellSniffer – a sniffing pixel dog" width="190">

# shellSniffer

shellSniffer sniffs through your shells: it records every command you type in PowerShell, Git Bash, WSL, bash or zsh, **together with its output**, and turns it into

- a **live view** of all open shells at once,
- a **searchable log** (full-text search ranked with BM25, and a second field *Sniff logs with shroom* that also searches by meaning with a local language model, plus a time-range filter),
- a **topic-based documentation** of your work, written by a local language model when you ask for it,
- a **journal** that shows which topics you worked on, day by day.

Everything stays on your machine: commands are stored in SQLite, summaries are written by a local [Ollama](https://ollama.com) model, and no cloud service is involved.

---

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Setup on Windows](#setup-on-windows)
- [Setup on Linux / macOS](#setup-on-linux--macos)
- [Using shellSniffer](#using-shellsniffer)
- [Where your data is stored](#where-your-data-is-stored)
- [Configuration](#configuration)
- [Uninstalling](#uninstalling)
- [Project structure](#project-structure)
- [Privacy](#privacy)
- [Further documentation](#further-documentation)

---

## How it works

```
 your shells                     shellSniffer server (http://127.0.0.1:8765)            browser
 ───────────                     ───────────────────────────────────────────            ───────
 PowerShell ─┐  hook ──────────▶  secret filter ─▶ SQLite ─▶ live updates (WebSocket) ─▶ web UI
 Git Bash   ─┤                       │              │
 WSL bash   ─┤  Atuin (optional) ────┘              ├─▶ BM25 search
 bash / zsh ─┘                                      ├─▶ honeypot log files (optional)
                                                    └─▶ Ollama ── only when you click "SniffMagic"
```

1. **Hooks** – the installer adds one line to each shell profile. After every command, the hook sends the command, its working directory, exit code, duration and output to the local server. Your prompt is never blocked.
2. **Atuin (optional)** – if [Atuin](https://atuin.sh) is installed, it records the commands and shellSniffer only adds the output. You can switch Atuin off at any time in the web UI.
3. **Server** – a small Python (FastAPI) app stores everything in SQLite, after masking passwords, tokens and keys.
4. **Documentation on request** – commands are grouped into sessions per shell. When you click **SniffMagic**, a local Ollama model assigns each session to a topic and writes a title and summary. The model never runs in the background.

---

## Requirements

| What | Needed for | Required? |
|---|---|---|
| **Python 3.10+** | the server | yes |
| **Ollama** + a chat model | topic names and summaries | recommended – without it, a simple heuristic names the topics |
| an Ollama **embedding model** (`nomic-embed-text`) | *Sniff logs with shroom* (hybrid search) | optional – otherwise the chat model is used, slower |
| **Atuin** | more reliable command capture, Ctrl+R history search | optional – installed by the installer, can be switched off |
| `curl`, `script` (util-linux) | bash/zsh hooks on Linux and WSL | usually preinstalled |

The Python packages (`fastapi`, `uvicorn`) are installed automatically into a local virtual environment.

---

## Setup on Windows

**1. Install the prerequisites** (once):

```powershell
winget install -e --id Python.Python.3.12
winget install -e --id Ollama.Ollama     # recommended
ollama pull llama3.1                     # or any other chat model
```

**2. Get shellSniffer:**

```powershell
git clone https://github.com/LilianDK/shellSniffer.git
cd shellSniffer
```

**3. Start the server** – keep this window open while you work:

```powershell
.\start.ps1
```

The first start creates a virtual environment in `.venv`, installs the packages and opens <http://127.0.0.1:8765>.
If PowerShell refuses to run scripts, allow local scripts once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**4. Install the shell hooks** – in a **second** PowerShell window, from the same folder:

```powershell
.\install.ps1              # with Atuin (installed via winget if missing)
.\install.ps1 -NoAtuin     # or: without Atuin
```

The installer sets up Windows PowerShell, PowerShell 7 (console and VS Code), Git Bash and every WSL distribution it finds. Running it again is safe; it replaces its own lines.

**5. Open new shell windows.** Shells that were already open are not observed. Type a few commands – they appear in the web UI right away.

> **WSL:** the server runs on Windows and is reached from WSL over the virtual network. If `install.ps1` does not run as administrator, it prints a firewall command to run once in an admin PowerShell. Details: [docs/installation.md](docs/installation.md#wsl).

---

## Setup on Linux / macOS

```bash
git clone https://github.com/LilianDK/shellSniffer.git
cd shellSniffer

# optional, recommended: Ollama + a model (see https://ollama.com/download)
ollama pull llama3.1

bash start.sh              # terminal 1: starts the server and opens http://127.0.0.1:8765
bash install.sh            # terminal 2: hooks for bash and zsh (+ Atuin)
```

Options: `bash install.sh --no-atuin` (without Atuin), `bash install.sh --service` (start the server automatically as a systemd user service). Then open a new terminal.

---

## Using shellSniffer

| Where | What it does |
|---|---|
| **Live** (right) | every command of every open shell as it happens, with output. Filter by shell at the top. |
| **Sniff logs** (upper search field) | full-text search over commands, output and directories, ranked with **BM25**. Every word matches as a prefix (`dock` finds `docker`). Click a hit to jump to its topic. |
| **Ollama chip** (top left) | shows the model; its switch **enables / disables Ollama**. Disabled: shellSniffer never starts or contacts Ollama – SniffMagic, *Sniff logs with shroom* and continuous shrooming are unavailable; BM25 search, capture and import keep working. |
| **⚙ Settings** | general settings, saved on the server: **🦙 Ollama** on/off, **🐚 Capture** (with / without Atuin) and **🍯 Honeypot log**, **🐕 Roaming dog** on/off, **🍄 Continuous shrooming** (every new command is vectorized by Ollama the moment it arrives, so *Sniff logs with shroom* always covers all logs – off by default, shows its progress), **🎨 Theme** (system / light / dark, per browser). |
| **🍄 Sniff logs with shroom** (lower search field) | **hybrid search** – BM25 plus semantic similarity from a local Ollama model, merged with Reciprocal Rank Fusion. Also finds commands that mean the same in other words. Works best with an embedding model: `ollama pull nomic-embed-text`. **Enter** asks the shroom: the chat model answers your question from the best hits and cites them as `[n]` (RAG, shown below the field with its sources). |
| **🦴 SniffOptions** | search settings: **Hybrid balance** (slider: more BM25 or more cosine similarity) and **time range** (from / to, or presets such as *Last hour*, *Today*, *Last 7 days*). With a time range and no search words, all commands in that range are listed. |
| **✨ SniffMagic** | opens a panel where you choose **which sessions** to document – a time range (*Last hour*, *Today*, *Last 7 days*, *Last 30 days*, your own from/to) or *Everything*. It shows how many sessions are affected; optionally re-document sessions that already have a topic. Then Ollama sorts them into topics and documents each session in ASD-STE100 style (Purpose · numbered Procedure steps citing the commands `[n]` · Result). The documentation is vectorized right away and found by the search, too. Nothing is sent to the model without this. |
| **Journal** | days on the left, topics on the right; the thicker the line, the more commands went into that topic. |
| **📥 Import dumps** | upload shell activity someone collected earlier – files or whole folders (drag & drop). Detected formats: bash/zsh/PowerShell history, PowerShell transcripts, terminal logs with prompt + output, shellSniffer honeypot logs, JSON/JSONL/CSV with a `command` field; anything else is read as one command per line. Re-uploading skips duplicates. |
| **ⓘ** | explains the app and shows where your logs are stored. |
| **☾ / ☀** | dark / light mode. |
| **the dog** 🐕 | sniffs its way across the page now and then. Switch it off in **⚙ Settings** (or click the logo at the top left). |

---

## Where your data is stored

| What | Where |
|---|---|
| Database (all commands, output, sessions, topics) | `~/.shellsniffer/shellsniffer.db` (Windows: `%USERPROFILE%\.shellsniffer\shellsniffer.db`) |
| Honeypot logs (one file per shell session, if enabled) | `honeypot/` in the shellSniffer folder (ignored by git) |
| Temporary output buffers of running shells | `~/.shellsniffer/ps-transcript-*.txt`, `typescript-*.log` |
| Access token for clients not on localhost (WSL) | `~/.shellsniffer/token` |

The **ⓘ** dialog in the web UI shows the exact paths of your installation. **Markdown export** (top right) downloads the whole documentation as one file. To start over, stop the server and delete `~/.shellsniffer`.

---

## Configuration

shellSniffer works without configuration. The most useful settings (environment variables, set before starting the server):

| Variable | Default | Meaning |
|---|---|---|
| `SHELLSNIFFER_OLLAMA_MODEL` | first installed model | Ollama model for documentation, e.g. `qwen2.5:7b` |
| `SHELLSNIFFER_DOC_LANGUAGE` | `English` | language of titles and summaries, e.g. `German` |
| `SHELLSNIFFER_VECTORIZE` | `0` | initial state of *Continuous shrooming* (`1` = on); afterwards the switch in ⚙ Settings decides |
| `SHELLSNIFFER_EMBED_MODEL` | first installed embedding model | model for *Sniff logs with shroom*, e.g. `nomic-embed-text` |
| `SHELLSNIFFER_DIR` | `~/.shellsniffer` | data folder |
| `SHELLSNIFFER_HONEYPOT_DIR` | `<shellSniffer folder>/honeypot` | folder for the honeypot logs |
| `SHELLSNIFFER_DISABLE=1` | – | set in a shell to keep it out of shellSniffer entirely |

All variables: [docs/configuration.md](docs/configuration.md).

---

## Uninstalling

```powershell
.\install.ps1 -Uninstall          # Windows: removes all shellSniffer lines from the shell profiles
```

```bash
bash install.sh --uninstall       # Linux / macOS: also removes the systemd service
```

Atuin stays installed. Delete `~/.shellsniffer` to remove the recorded data.

---

## Project structure

```
start.ps1 / start.sh         start the server (creates .venv on first run)
install.ps1 / install.sh     install / remove the shell hooks (and Atuin)
hooks/                       the hooks: shellsniffer.ps1, shellsniffer.bash, shellsniffer.zsh
shellsniffer/                the server (Python package)
  app.py                     HTTP API, WebSocket, web UI routes – start here
  config.py                  paths, environment variables, access token
  db.py                      SQLite schema, migrations, FTS5 search index
  state.py                   UI settings, worker status, live updates
  ingest.py                  storing commands and outputs from hooks / Atuin
  atuin.py, atuin_import.py  reading Atuin's history (Windows + WSL)
  honeypot.py                per-shell plain-text logs
  importer.py                batch import of collected dumps (format detection)
  documenter.py, cluster.py  Ollama documentation (on request) and its heuristic fallback
  ontology.py                domains, projects, tools
  search.py                  BM25 search and hybrid search (Sniff logs with shroom)
  embeddings.py              semantic vectors via Ollama for the hybrid search
  redact.py                  secret filter
  static/                    web UI: index.html, css/, js/, logo.svg
docs/                        detailed documentation
honeypot/                    honeypot logs (created when used, not in git)
```

Run the server by hand (e.g. for development): `python -m uvicorn shellsniffer.app:app --port 8765 --reload`.

---

## Privacy

shellSniffer records commands **and their output**. Before anything is stored, written to a honeypot file or sent to the model, a secret filter masks passwords, tokens, API keys, authorization headers, credentials in URLs and private keys. The filter catches common patterns but is no guarantee. To keep a shell out entirely, set `SHELLSNIFFER_DISABLE=1` in it; in bash with `HISTCONTROL=ignorespace`, commands starting with a space are skipped.

---

## Further documentation

- [Installation](docs/installation.md) – Windows, WSL, Git Bash, Linux in detail
- [Ollama setup](docs/ollama.md) – choosing a model, documentation language
- [User guide](docs/user-guide.md) – all views and controls
- [Configuration](docs/configuration.md) – every environment variable and script option
- [Troubleshooting](docs/troubleshooting.md)
- [How it works](docs/architecture.md) – capture pipeline, data model, API

## License

[MIT](LICENSE)
