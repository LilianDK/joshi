# User guide

Open <http://127.0.0.1:8765> while the server is running. The header has two views, **Live** and **Journal**, plus status chips:

| Chip | Meaning |
|---|---|
| **Ollama: <model>** | the model used for clustering (or *Ollama not reachable · heuristic*) |
| **Atuin: Windows, WSL Ubuntu** | the Atuin histories joshi is currently reading |
| **n commands / n topics** | totals in the database |

**Markdown export** (top right) downloads the whole documentation as one Markdown file, grouped by domain and topic.

## Live view

### Right: live shell

Every command appears here a moment after it finishes, with time, shell, working directory, exit code and duration. Failed commands are marked red. Click **Output (n lines)** to expand the captured output.

Each open shell window is its own **session** and gets a label and colour, such as `pwsh #1`, `wsl #2` or `zsh #1`. When more than one shell is active, filter buttons appear at the top so you can show a single shell.

Below the filters joshi shows what is pending:

- *Running session in pwsh: 4 commands · clustered after 30s pause* – the session is still open.
- *Session … is being assigned to a topic …* – the model is working on it.

### Left: documentation

Your commands are grouped into **sessions**. A session ends after 5 minutes without commands or after 25 commands. Once a session has been idle for 45 seconds, it is assigned to a topic and gets a title and summary.

The documentation is organised in three levels:

1. **Domains** – a fixed list with fixed colours: Version control, Containers, Network & remote, Packages & environments, Files & system, Development & build, Data & cloud, AI & models, and Other.
2. **Topics** – grow freely inside a domain, for example "Docker Compose setup" or "Python venv for research-rag".
3. **Sessions** – each with a title, a Markdown summary, a list of all commands, and chips for its **project** (📁, derived from the working directory) and its **tools** (the programs that were run).

Use the search box to filter topics and sessions by any text, including commands.

Per session you can click **re-cluster** to send it through the model again, for example after changing the model.

### Tidy up topics

Over time, similar topics can appear ("Docker setup" and "Setting up containers"). joshi merges near-identical names automatically. **Tidy up topics** asks the model to go through all topics, reassign domains and merge duplicates. It needs Ollama.

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

- **Project:** joshi looks at the working directory. Below a typical root folder (`Programming Projects`, `projects`, `repos`, `src`, `code`, `dev`, `workspace`, …) the next folder is the project, so `D:\Programming Projects\joshi\src` becomes `joshi`. Otherwise it uses the first folder below your home directory.
- **Tools:** the first word of each command (without `sudo`, `env`, variable assignments and paths), ignoring navigation such as `cd`, `ls`, `cat` or `echo`. `python -m pip` counts as `pip`, `python -m venv` as `venv`.

## Pausing observation

Set `JOSHI_DISABLE=1` in a shell before the hook loads (for example at the top of your profile) to turn joshi off there. To pause just one window that is already observed, open a new window with the variable set:

```powershell
$env:JOSHI_DISABLE = "1"; powershell -NoLogo     # or pwsh
```

```bash
JOSHI_DISABLE=1 bash
```

With `JOSHI_CAPTURE_OUTPUT=0` joshi records commands but not their output.

## Your data

All data lives in `~/.joshi/` (`%USERPROFILE%\.joshi\` on Windows):

| File | Content |
|---|---|
| `joshi.db` | SQLite database with commands, sessions, topics |
| `token` | random token that non-localhost clients (WSL) must send |
| `ps-transcript-*.txt`, `typescript-*.log` | temporary output buffers of running shells |

To start over, stop the server and delete the folder. To back up, copy `joshi.db` while the server is stopped.

The API is local and simple, so you can query it yourself, for example `http://127.0.0.1:8765/api/topics` or `http://127.0.0.1:8765/api/events?limit=50`.
