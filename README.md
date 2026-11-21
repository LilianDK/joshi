# joshi – Journal Shell Intelligence

joshi watches what you type in your shells and keeps a running, topic-based journal of your work.

- **Left:** your documentation, clustered by topic and grouped into fixed domains (version control, containers, packages, …).
- **Right:** a live view of every command and its output, across all open shells at once.
- **Journal:** one entry per day, connected to the topics you worked on. The thicker the line, the more commands went into that topic.

Everything stays on your machine. Commands are stored in SQLite under `~/.joshi/`, and topics and summaries are written by a **local** language model running in [Ollama](https://ollama.com). No cloud service is involved.

Supported shells: Windows PowerShell 5.1, PowerShell 7, Git Bash, every WSL distribution, and bash and zsh on Linux.

## Requirements

| Dependency | Needed for | Install |
|---|---|---|
| Python 3.10+ | the joshi server | Windows: `winget install -e --id Python.Python.3.12` · Debian/Ubuntu: `sudo apt install python3 python3-venv` |
| [Ollama](https://ollama.com) + a chat model | topic clustering and summaries | see [docs/ollama.md](docs/ollama.md) – e.g. `ollama pull llama3.1` |
| [Atuin](https://atuin.sh) (optional, recommended) | reliable command capture, Ctrl+R history search | installed automatically by `install.ps1` / `install.sh` |
| `curl`, `script` (util-linux) | bash/zsh hooks on Linux and WSL | usually preinstalled |

The Python packages (`fastapi`, `uvicorn`) are listed in `requirements.txt` and installed into a local virtual environment by the start scripts. Without Ollama joshi still works and falls back to a simple heuristic for topics.

## Quick start

### Windows

```powershell
git clone https://github.com/LilianDK/joshi.git
cd joshi
.\start.ps1        # starts the server and opens http://127.0.0.1:8765 – keep this window open
```

In a **second** PowerShell window, once:

```powershell
.\install.ps1      # sets up observation in every shell found on this machine
```

Then open new shell windows. Shells that were already open are not observed.

### Linux

```bash
git clone https://github.com/LilianDK/joshi.git
cd joshi
bash start.sh          # starts the server (own venv in .venv-linux), opens http://127.0.0.1:8765
bash install.sh        # in a second terminal: Atuin + hooks for bash and zsh
```

`bash install.sh --service` additionally installs a systemd user service that starts the server automatically.

## Documentation

- [Installation](docs/installation.md) – Windows, WSL, Git Bash, Linux, uninstalling
- [Ollama setup](docs/ollama.md) – installing Ollama, choosing a model, documentation language
- [User guide](docs/user-guide.md) – live view, documentation, journal, domains and topics, export
- [Configuration](docs/configuration.md) – all environment variables and script options
- [Troubleshooting](docs/troubleshooting.md) – common problems and how to fix them
- [How it works](docs/architecture.md) – capture pipeline, Atuin integration, data model, privacy

## Privacy

joshi records commands and their output. Before anything is stored or sent to the model, a secret filter masks passwords, tokens, API keys, authorization headers, credentials in URLs and private keys. The filter catches common patterns but is not a guarantee. To keep a command out of joshi entirely, set `JOSHI_DISABLE=1` in that shell, or (in bash with `HISTCONTROL=ignorespace`) start the command with a space.

## License

[MIT](LICENSE)
