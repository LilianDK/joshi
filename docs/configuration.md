# Configuration

shellSniffer is configured with environment variables. Set them before starting the server (or before the hook loads, for hook options).

## Server

| Variable | Default | Meaning |
|---|---|---|
| `SHELLSNIFFER_OLLAMA_MODEL` | first installed chat model | Ollama model used for clustering, e.g. `llama3.1` or `qwen2.5:7b` |
| `SHELLSNIFFER_OLLAMA_URL` | `http://127.0.0.1:11434` | address of the Ollama server |
| `SHELLSNIFFER_VECTORIZE` | `0` | initial state of *Continuous shrooming* (`1` = on); afterwards the switch in ⚙ Settings decides |
| `SHELLSNIFFER_VECTORIZE_CHUNK` | `64` | commands vectorized per background round |
| `SHELLSNIFFER_EMBED_MODEL` | first installed embedding model, else the chat model | Ollama model for the hybrid search (*Sniff logs with shroom*), e.g. `nomic-embed-text` |
| `SHELLSNIFFER_EMBED_PER_SEARCH` | `200` | how many commands without a vector are embedded per shroom search |
| `SHELLSNIFFER_DOC_LANGUAGE` | `English` | language of titles, summaries and new topic names, e.g. `German` |
| `SHELLSNIFFER_DIR` | `~/.shellsniffer` | data folder (database, token, output buffers) |
| `SHELLSNIFFER_ATUIN` | `1` | initial mode on the very first start: `0` = without Atuin. Afterwards the **With Atuin / Without Atuin** switch in the UI decides |
| `SHELLSNIFFER_HONEYPOT` | `0` | initial state of the **Honeypot log** switch (`1` = on; only active without Atuin) |
| `SHELLSNIFFER_HONEYPOT_DIR` | `<shellSniffer folder>/honeypot` | folder for the honeypot logs |
| `SHELLSNIFFER_ATUIN_DAYS` | `14` | how many days of Atuin history to import on the first start |
| `SHELLSNIFFER_ATUIN_POLL` | `2` | seconds between Atuin reads |
| `SHELLSNIFFER_FALLBACK_AFTER` | `20` | seconds to wait for an Atuin entry before the hook's own report becomes the command |
| `SHELLSNIFFER_IDLE` | `45` | seconds without commands after which a session counts as finished (it is still only documented when you click **SniffMagic**) |
| `SHELLSNIFFER_GAP` | `300` | seconds of pause after which a new session starts |
| `SHELLSNIFFER_MAX_BLOCK` | `25` | maximum commands per session |
| `SHELLSNIFFER_MAX_OUTPUT` | `16000` | maximum stored characters of output per command (start and end are kept) |

Example (Windows, permanent for your user):

```powershell
setx SHELLSNIFFER_OLLAMA_MODEL qwen2.5:7b
setx SHELLSNIFFER_DOC_LANGUAGE German
```

Example (Linux, systemd service): run `systemctl --user edit shellsniffer` and add

```ini
[Service]
Environment=SHELLSNIFFER_OLLAMA_MODEL=qwen2.5:7b
```

## Hooks

| Variable | Default | Meaning |
|---|---|---|
| `SHELLSNIFFER_URL` | `http://127.0.0.1:8765` (in WSL: the Windows host) | server address the hook reports to |
| `SHELLSNIFFER_CAPTURE_OUTPUT` | `1` | `0` = report commands only, no output |
| `SHELLSNIFFER_DISABLE` | – | `1` = do not load the hook in this shell |

## Script options

| Script | Option | Effect |
|---|---|---|
| `start.ps1` | `-LocalOnly` | listen on localhost only, even if WSL is installed |
| `start.ps1` | `-Port 9000` | use another port (set `SHELLSNIFFER_URL` for the hooks accordingly) |
| `start.sh` | `--port 9000` | use another port |
| `start.sh` | `--no-browser` | do not open the browser |
| `install.ps1` | `-NoAtuin` | set up hooks without Atuin |
| `install.ps1` | `-Uninstall` | remove all shellSniffer lines from all profiles |
| `install.sh` | `--no-atuin` | set up hooks without Atuin |
| `install.sh` | `--service` | install a systemd user service for the server |
| `install.sh` | `--uninstall` | remove all shellSniffer lines and the service |

## Security

The server accepts requests from localhost without authentication. All other clients (for example WSL shells) must send the token from `~/.shellsniffer/token` in the `X-ShellSniffer-Token` header or as `?token=`. The token is created on the first start. Do not expose port 8765 beyond your own machine.
