# Ollama setup

joshi uses a local language model through [Ollama](https://ollama.com) to

- assign each work session to a **domain** and a **topic**,
- write a **title and summary** for the session, and
- tidy up topics (find duplicates, reassign domains) when you click **Tidy up topics**.

Nothing leaves your machine: joshi talks only to the Ollama server on `http://127.0.0.1:11434`.

Without Ollama joshi keeps working. It then names topics after the main tool of a session (for example "Git work" or "Docker") and uses the list of commands as the summary. The status chip at the top of the UI shows **Ollama not reachable · heuristic** in that case.

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

joshi needs a model that can follow instructions and answer in JSON. Good choices:

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

## 3. Tell joshi which model to use

By default joshi uses the **first installed chat model** it finds (embedding models are skipped). To pick one explicitly, set `JOSHI_OLLAMA_MODEL` before starting the server:

```powershell
# Windows, current window only
$env:JOSHI_OLLAMA_MODEL = "qwen2.5:7b"; .\start.ps1
# Windows, permanently
setx JOSHI_OLLAMA_MODEL qwen2.5:7b
```

```bash
# Linux
JOSHI_OLLAMA_MODEL=qwen2.5:7b bash start.sh
```

The name may be given with or without the tag (`qwen2.5` matches `qwen2.5:7b`).

If Ollama runs on another machine or port, set `JOSHI_OLLAMA_URL`, for example `http://192.168.1.20:11434`.

## Documentation language

The UI is English. Session titles, summaries and new topic names are written in the language set by `JOSHI_DOC_LANGUAGE` (default `English`). For German documentation:

```powershell
$env:JOSHI_DOC_LANGUAGE = "German"; .\start.ps1
```

Existing topics keep their names. Use **Tidy up topics** or rename nothing at all; the model prefers existing topics either way.

## Performance

- Clustering happens in the background, one session at a time, after 45 seconds without new commands (`JOSHI_IDLE`). Your shells are never slowed down.
- A 7–8B model on CPU takes roughly 10–60 seconds per session. A GPU makes it near-instant.
- The model sees at most the commands of one session (up to 25) and the first and last part of each output (about 600 characters).

## Checking that it works

1. Open the web UI. The chip at the top should read **Ollama: <model name>**.
2. Run a few commands in an observed shell, then wait about a minute.
3. The session appears on the left under a domain and topic. The small label next to the date says `ollama:<model>` (or `heuristic` if the fallback was used).

To re-run the model on a session, click **re-cluster** next to it.
