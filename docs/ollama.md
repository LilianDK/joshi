# Ollama setup

shellSniffer uses a local language model through [Ollama](https://ollama.com) to

- assign each work session to a **domain** and a **topic**,
- write a **title and summary** for the session, and

Nothing leaves your machine: shellSniffer talks only to the Ollama server on `http://127.0.0.1:11434`.

Without Ollama shellSniffer keeps working. It then names topics after the main tool of a session (for example "Git work" or "Docker") and uses the list of commands as the summary. The status chip at the top of the UI shows **Ollama not reachable · heuristic** in that case.

## 1. Install Ollama

| System | Command |
|---|---|
| Windows | `winget install -e --id Ollama.Ollama` (or the installer from <https://ollama.com/download>) |
| Linux | `curl -fsSL https://ollama.com/install.sh \| sh` |
| macOS | `brew install ollama` or the app from <https://ollama.com/download> |

On Windows and macOS Ollama runs in the background after installation. On Linux the install script sets up a systemd service. Check that it is running:

```bash
ollama list
```

## 2. Pull a chat model

shellSniffer needs a model that can follow instructions and answer in JSON. Good choices:

| Model | Size on disk | Notes |
|---|---|---|
| `llama3.1` (8B) | ~4.7 GB | good default |
| `qwen2.5:7b` | ~4.7 GB | very reliable JSON output |
| `mistral` (7B) | ~4.1 GB | fine alternative |
| `llama3.2:3b` | ~2.0 GB | for machines with little RAM/VRAM; summaries are simpler |
| `qwen2.5:14b` | ~9 GB | better summaries if you have a GPU with 12 GB+ |

```bash
ollama pull llama3.1
```

## 3. Tell shellSniffer which model to use

By default shellSniffer uses the **first installed chat model** it finds (embedding models are skipped). To pick one explicitly, set `SHELLSNIFFER_OLLAMA_MODEL` before starting the server:

```powershell
# Windows, current window only
$env:SHELLSNIFFER_OLLAMA_MODEL = "qwen2.5:7b"; .\start.ps1
# Windows, permanently
setx SHELLSNIFFER_OLLAMA_MODEL qwen2.5:7b
```

```bash
# Linux
SHELLSNIFFER_OLLAMA_MODEL=qwen2.5:7b bash start.sh
```

The name may be given with or without the tag (`qwen2.5` matches `qwen2.5:7b`).

If Ollama runs on another machine or port, set `SHELLSNIFFER_OLLAMA_URL`, for example `http://192.168.1.20:11434`.

## Documentation language

The UI is English. Session titles, summaries and new topic names are written in the language set by `SHELLSNIFFER_DOC_LANGUAGE` (default `English`). For German documentation:

```powershell
$env:SHELLSNIFFER_DOC_LANGUAGE = "German"; .\start.ps1
```

Existing topics keep their names. The model prefers existing topics either way.

## Performance

- The model only runs when you click **SniffMagic** (or **re-cluster**) in the UI – never automatically in the background. It then works through the waiting sessions one at a time. Your shells are never slowed down.
- A 7–8B model on CPU takes roughly 10–60 seconds per session. A GPU makes it near-instant.
- The model sees at most the commands of one session (up to 25) and the first and last part of each output (about 600 characters).

## Checking that it works

1. Open the web UI. The chip at the top should read **Ollama: <model name>**.
2. Run a few commands in an observed shell, then click **SniffMagic**.
3. The session appears on the left under a domain and topic. The small label next to the date says `ollama:<model>` (or `heuristic` if the fallback was used).

To re-run the model on a session, click **re-cluster** next to it.
