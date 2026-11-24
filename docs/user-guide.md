# User guide

Open <http://127.0.0.1:8765> while the server is running. The header has two views, **Live** and **Journal**, plus status chips:

| Chip | Meaning |
|---|---|
| **Ollama: <model>** | the model used for clustering (or *Ollama not reachable · heuristic*) |
| **Atuin: Windows, WSL Ubuntu** | the Atuin histories shellSniffer is currently reading |
| **n commands / n topics** | totals in the database |

On the right of the header:

| Control | Effect |
|---|---|
| **⚙** | settings: capture with / without Atuin, honeypot log, roaming dog, continuous shrooming, theme (see [⚙ Settings](#-settings)) |
| **☾ / ☀** | switch between dark and light mode (remembered in this browser; by default the system setting is used) |
| **Markdown export** | downloads the whole documentation as one Markdown file, grouped by domain and topic |

### With or without Atuin (⚙ Settings → Capture)

- **With Atuin:** Atuin records the commands, shellSniffer reads its history and the hook only adds the output.
- **Without Atuin:** shellSniffer ignores Atuin completely and takes commands and output straight from its own hooks – also in shells where Atuin is loaded. When you switch back to Atuin, nothing recorded in the meantime is imported twice.

### Honeypot log (⚙ Settings)

With **Without Atuin** selected, tick **Honeypot log**. From then on every command of every observed shell is appended, with its output, to one file per shell session:

```
honeypot/2026-11-24_pwsh_MYPC_17984-1176662699.log

===== 2026-11-24 14:03:12 | pwsh | MYPC | D:\proj | exit 0 | 1200 ms
$ docker compose up -d
Container web Started
```

The folder lies in the shellSniffer folder (`honeypot/`, ignored by git) and can be moved with `SHELLSNIFFER_HONEYPOT_DIR`. The same secret filter as for the database is applied before writing.

## Live view

### Right: live shell

Every command appears here a moment after it finishes, with time, shell, working directory, exit code and duration. Failed commands are marked red. Click **Output (n lines)** to expand the captured output.

Each open shell window is its own **session** and gets a label and colour, such as `pwsh #1`, `wsl #2` or `zsh #1`. When more than one shell is active, filter buttons appear at the top so you can show a single shell.

Below the filters shellSniffer shows what is pending:

- *Running session in pwsh: 4 commands · click “SniffMagic” to assign a topic* – nothing happens with it until you click.
- *… queued for Ollama* / *… is being assigned to a topic …* – after the click, the model works through the queue.

### Left: documentation

Your commands are grouped into **sessions**. A session ends after 5 minutes without commands or after 25 commands.

**Ollama never runs on its own.** Sessions wait until you start **✨ SniffMagic**. A click opens a panel:

- **Time range** – which sessions to document: *Last hour*, *Today*, *Last 7 days*, *Last 30 days*, your own *from / to*, or *Everything* (all sessions not documented yet). A session counts if it overlaps the range.
- **also re-document sessions that are already documented** – redo sessions in the range that already have a topic, e.g. after switching to a better model.
- The panel shows beforehand how many sessions (and commands) are affected and which model will be used.
- **Start SniffMagic** – sessions still running in the range are closed, then the model goes through them one by one and gives each a domain, a topic, a title and a summary. The button shows how many are left.

The summary is written as technical documentation in the style of **ASD-STE100 Simplified Technical English** (followed to about 80 %): short sentences, imperative steps, simple words. It has a fixed structure – **Purpose**, numbered **Procedure** steps, **Result** – and every step cites the commands it is based on, e.g. `[3]`. These numbers match the numbered *All commands* list of the session; hover one to see the command. Right after a session is documented, its documentation is vectorized and indexed, so the search also finds the documentation itself (see *Sniffing the logs*).

The documentation is organised in three levels:

1. **Domains** – a fixed list with fixed colours: Version control, Containers, Network & remote, Packages & environments, Files & system, Development & build, Data & cloud, AI & models, and Other.
2. **Topics** – grow freely inside a domain, for example "Docker Compose setup" or "Python venv for research-rag".
3. **Sessions** – each with a title, a Markdown summary, a list of all commands, and chips for its **project** (📁, derived from the working directory) and its **tools** (the programs that were run).

### Sniffing the logs (search)

The search box searches **all logged commands, their output, working directories and shell names** – also commands that are not documented yet. Results are ranked with **BM25** (SQLite FTS5); a hit in the command counts more than one in the output or directory. Every word is matched as a prefix (`dock` finds `docker`), and results containing more and rarer search words rank higher. Matches are highlighted, the score is shown on the right. Click a hit to jump to its topic.

Above the command hits, **📄 documented sessions** that match are listed: their SniffMagic title and summary are searched too – with BM25, and with *Sniff logs with shroom* also by meaning (the documentation is vectorized when SniffMagic writes it).

There are **two search fields**: the upper one, **Sniff logs**, searches exact words only (BM25, no language model). The lower one, **🍄 Sniff logs with shroom**, runs a **hybrid search**: in addition to the exact words (BM25), a local Ollama model compares the *meaning* of your query with every command, its directory and the start of its output. Both rankings are merged with Reciprocal Rank Fusion, so a command that matches both ways comes first, and a command that only *means* the same ("remove container" → `docker rm`) is found too. Each hit shows `BM25 #n` (its rank in the word search) and `🍄 0.83` (semantic similarity). The vectors are computed on demand: the first shroom search embeds up to a few hundred commands; if more are left, the header says so and the next search continues. Install an embedding model for speed and quality (`ollama pull nomic-embed-text`); otherwise the chat model is used and the header shows a hint. The model only runs while you search in this field. Typing in one field clears the other. When Ollama is disabled, the shroom field is greyed out.

**Ask the shroom:** press **Enter** in *Sniff logs with shroom* to get an answer below the field – like a short chat about your own work. The hybrid search first picks the best documented sessions and commands (with output); they are numbered and given to the Ollama chat model as its only source. The model answers in the language of your question, shows the exact commands when you ask *how*, and cites its sources as `[n]`. The answer appears word by word; under it the sources are listed – click one to open its topic. If the logs do not contain the answer, the shroom says so instead of guessing. While the shroom works, a spinning ring with a 🍄 and *shrooming..* is shown. The SniffOptions time range and hybrid balance apply here too.

**🦴 SniffOptions** next to the search box holds the search settings: the **hybrid balance** (for the shroom field) and the time range.

**Hybrid balance** (slider, used by *Sniff logs with shroom*): how much each side counts when the two rankings are merged – from *BM25 only* (exact words) over *50 % / 50 %* (default) to *cosine only* (meaning). Move it towards BM25 when you search for exact command names or flags, towards cosine when you describe what you did in your own words. The header of the results shows the balance used; the browser remembers it.

**🍄 Continuous shrooming** (in **⚙ Settings**): when on, every new command is vectorized by an Ollama model the moment it arrives and also works off older commands, so *Sniff logs with shroom* always covers all logs without delay. When off (default), commands are vectorized only while you search with the shroom. Next to the switch you see the progress (e.g. *vectorizing … 120 / 456 commands*), *✓ up to date*, or an error such as Ollama not running (it retries every 30 seconds). The setting is stored on the server and stays on across restarts. It uses Ollama continuously while on – with a small embedding model (`ollama pull nomic-embed-text`) that is cheap. **Time range:** pick *from* / *to* or a preset (*Last hour*, *Today*, *Last 7 days*, *Last 30 days*, *Any time*). The range applies to every search; with a range but no search words, all commands in that range are listed, newest first. The button shows a dot while a range is set, and the browser remembers your choice (presets move with the clock).

Per session you can click **re-cluster** to send it through the model again right away, for example after changing the model.

### Duplicate topics

Over time, similar topics can appear ("Docker setup" and "Setting up containers"). shellSniffer merges near-identical names automatically at every start.

## ⚙ Settings

The gear at the top right opens the general settings. They are saved on the server, so shellSniffer always opens with them – in every browser:

| Setting | Effect |
|---|---|
| 🐚 **Capture** | *With Atuin* / *Without Atuin* – where commands come from; takes effect immediately |
| 🍯 **Honeypot log** | per-shell text files with full output (only without Atuin) |
| 🐕 **Roaming dog** | whether the dog sniffs its way across the page (clicking the logo toggles the same setting) |
| 🍄 **Continuous shrooming** | vectorize every new command right away for *Sniff logs with shroom* (see above) |
| 🦙 **Ollama** | enable / disable the language model. Disabled: no request is ever sent to Ollama; SniffMagic, *Sniff logs with shroom* and continuous shrooming are not available. Same switch as on the Ollama chip. |
| 🎨 **Theme** | system / light / dark – this one is remembered per browser |

## Importing collected shell dumps

**📥 Import dumps** (top right) loads shell activity that was collected before shellSniffer was running – your own or someone else's. Drop files or whole folders into the dialog, or choose them. Each file is uploaded and its format detected:

| Format | Recognised by |
|---|---|
| JSON / JSONL | objects with `command` (or `cmd`); optional `output`, `cwd`, `timestamp`/`ts`/`time`, `exit_code`, `shell`, `host` |
| CSV | a header with a `command` (or `cmd`) column, same optional columns |
| shellSniffer honeypot log | `===== 2026-11-24 14:03:12 \| pwsh \| …` blocks |
| PowerShell transcript | `Start-Transcript` header, `PS C:\path> command` lines + output |
| Terminal log | prompt lines such as `user@host:~/dir$ cmd`, `$ cmd`, `PS C:\> cmd`, followed by their output |
| zsh history | `: 1700000000:0;command` |
| bash history | `#1700000000` timestamp lines before commands (optional) |
| anything else | one command per line, e.g. PSReadLine `ConsoleHost_history.txt` |

Commands without a timestamp get the file's date, one second apart in their original order. Every imported file becomes its own session(s) named `import:<file>`; secrets are masked as usual, and imported commands are never written to the honeypot log. Uploading the same file again skips what is already there. The dialog shows per file the detected format and how many commands were found and imported. Afterwards, document them with **✨ SniffMagic** (for example with the time range *Everything*).

## Journal

The Journal shows your work over time:

- **Left column:** one entry per day, newest on top, with the number of commands and topics.
- **Right column:** the topics (or domains) you worked on, grouped and coloured by domain.
- **Lines:** connect a day with each topic you worked on that day. The thicker the line, the more commands went into that topic on that day. Commands that are not clustered yet go to *Not assigned yet*.

Controls:

| Control | Effect |
|---|---|
| Last 7 days / Last 30 days / All days | time range |
| Right: topics / Right: domains | show individual topics or only the nine domains |
| All domains / All projects / All tools | filter to one domain, project or tool |

Hover over a day, topic or line to highlight its connections. Click a day to see its sessions and summaries in the panel on the right.

## How projects and tools are detected

- **Project:** shellSniffer looks at the working directory. Below a typical root folder (`Programming Projects`, `projects`, `repos`, `src`, `code`, `dev`, `workspace`, …) the next folder is the project, so `D:\Programming Projects\shellsniffer\src` becomes `shellsniffer`. Otherwise it uses the first folder below your home directory.
- **Tools:** the first word of each command (without `sudo`, `env`, variable assignments and paths), ignoring navigation such as `cd`, `ls`, `cat` or `echo`. `python -m pip` counts as `pip`, `python -m venv` as `venv`.

## Pausing observation

Set `SHELLSNIFFER_DISABLE=1` in a shell before the hook loads (for example at the top of your profile) to turn shellSniffer off there. To pause just one window that is already observed, open a new window with the variable set:

```powershell
$env:SHELLSNIFFER_DISABLE = "1"; powershell -NoLogo     # or pwsh
```

```bash
SHELLSNIFFER_DISABLE=1 bash
```

With `SHELLSNIFFER_CAPTURE_OUTPUT=0` shellSniffer records commands but not their output.

## Your data

All data lives in `~/.shellsniffer/` (`%USERPROFILE%\.shellsniffer\` on Windows):

| File | Content |
|---|---|
| `shellsniffer.db` | SQLite database with commands, sessions, topics |
| `token` | random token that non-localhost clients (WSL) must send |
| `<shellSniffer folder>/honeypot/*.log` | honeypot logs per shell session (only when enabled) |
| `ps-transcript-*.txt`, `typescript-*.log` | temporary output buffers of running shells |

To start over, stop the server and delete the folder. To back up, copy `shellsniffer.db` while the server is stopped.

The API is local and simple, so you can query it yourself, for example `http://127.0.0.1:8765/api/topics` or `http://127.0.0.1:8765/api/events?limit=50`.
