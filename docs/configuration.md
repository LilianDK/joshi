# Configuration

joshi is configured with environment variables. Set them before starting the server (or before the hook loads, for hook options).

## Server

| Variable | Default | Meaning |
|---|---|---|
| `JOSHI_OLLAMA_MODEL` | first installed chat model | Ollama model used for clustering, e.g. `llama3.1` or `qwen2.5:7b` |
| `JOSHI_OLLAMA_URL` | `http://127.0.0.1:11434` | address of the Ollama server |
| `JOSHI_DOC_LANGUAGE` | `English` | language of titles, summaries and new topic names, e.g. `German` |
| `JOSHI_DIR` | `~/.joshi` | data folder (database, token, output buffers) |
| `JOSHI_ATUIN` | `1` | `0` = do not read Atuin; hooks then capture commands themselves |
| `JOSHI_ATUIN_DAYS` | `14` | how many days of Atuin history to import on the first start |
| `JOSHI_ATUIN_POLL` | `2` | seconds between Atuin reads |
| `JOSHI_FALLBACK_AFTER` | `20` | seconds to wait for an Atuin entry before the hook's own report becomes the command |
| `JOSHI_IDLE` | `45` | seconds without commands before a session is clustered |
| `JOSHI_GAP` | `300` | seconds of pause after which a new session starts |
| `JOSHI_MAX_BLOCK` | `25` | maximum commands per session |
| `JOSHI_MAX_OUTPUT` | `16000` | maximum stored characters of output per command (start and end are kept) |

Example (Windows, permanent for your user):

```powershell
setx JOSHI_OLLAMA_MODEL qwen2.5:7b
setx JOSHI_DOC_LANGUAGE German
```

Example (Linux, systemd service): run `systemctl --user edit joshi` and add

```ini
[Service]
Environment=JOSHI_OLLAMA_MODEL=qwen2.5:7b
```

## Hooks

| Variable | Default | Meaning |
|---|---|---|
| `JOSHI_URL` | `http://127.0.0.1:8765` (in WSL: the Windows host) | server address the hook reports to |
| `JOSHI_CAPTURE_OUTPUT` | `1` | `0` = report commands only, no output |
| `JOSHI_DISABLE` | – | `1` = do not load the hook in this shell |

## Script options

| Script | Option | Effect |
|---|---|---|
| `start.ps1` | `-LocalOnly` | listen on localhost only, even if WSL is installed |
| `start.ps1` | `-Port 9000` | use another port (set `JOSHI_URL` for the hooks accordingly) |
| `start.sh` | `--port 9000` | use another port |
| `start.sh` | `--no-browser` | do not open the browser |
| `install.ps1` | `-NoAtuin` | set up hooks without Atuin |
| `install.ps1` | `-Uninstall` | remove all joshi lines from all profiles |
| `install.sh` | `--no-atuin` | set up hooks without Atuin |
| `install.sh` | `--service` | install a systemd user service for the server |
| `install.sh` | `--uninstall` | remove all joshi lines and the service |

## Security

The server accepts requests from localhost without authentication. All other clients (for example WSL shells) must send the token from `~/.joshi/token` in the `X-Joshi-Token` header or as `?token=`. The token is created on the first start. Do not expose port 8765 beyond your own machine.
