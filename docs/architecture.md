# How it works

```
 shells                          server (FastAPI, port 8765)                     browser
 ───────                          ───────────────────────────                     ───────
 PowerShell ─┐  hook: output ──▶  /api/outputs ─┐                               ┌▶ Live view
 WSL bash   ─┤                                   ├▶ secret filter ▶ SQLite ─▶ WS ┤
 Linux bash ─┤  Atuin history ─▶  Atuin reader ──┘          │                   └▶ Journal
 zsh        ─┤                                              ▼
 Git Bash   ─┘  hook: command ─▶  /api/events     clustering worker ──▶ Ollama
```

## Capturing commands

shellSniffer uses [Atuin](https://atuin.sh) as its main command source where it can:

1. **Atuin** records every command in PowerShell, bash and zsh with directory, exit code, duration and session.
2. **The server** reads new Atuin entries every ~2 seconds. On Windows it reads Atuin's local SQLite database directly; for WSL it runs a small reader with `wsl.exe -d <distro> -- python3` for every *running* distribution. Only finished commands are imported. On the first start it imports the last 14 days.
3. **The shellSniffer hook** in each shell reports the **output** of each command, because Atuin does not store output. The server links output and Atuin entry by session ID, command text and start time.
4. If no Atuin entry arrives within 20 seconds (Atuin not installed or not recording in this shell), the hook's report becomes the command itself, so nothing is lost. If the Atuin entry arrives later, it is linked to the existing command instead of creating a duplicate.
5. **Git Bash** is not supported by Atuin; there the hook reports commands directly. The same applies to all shells with `-NoAtuin` / `--no-atuin`.

How the hooks capture output:

| Shell | Command detection | Output |
|---|---|---|
| PowerShell | wraps the `prompt` function, reads `Get-History` | `Start-Transcript` file, read from the last offset |
| bash | DEBUG trap + `PROMPT_COMMAND`, or bash-preexec when Atuin is loaded | the shell runs inside `script`; the typescript log is read from the last offset and ANSI codes are removed |
| zsh | `preexec` / `precmd` hooks | same as bash |

Reports are sent in the background (`HttpClient` in PowerShell, `curl` in bash/zsh), so the prompt is never blocked.

## Sessions and clustering

- Commands of one shell window are grouped into **blocks** (shown as sessions). A new block starts after 5 minutes without commands or after 25 commands.
- The clustering worker never starts on its own. Only `POST /api/cluster` (button **SniffMagic**) queues blocks: those overlapping the chosen time range (`start` / `end`), optionally including already documented ones (`include_done`); running blocks in the range are closed; the worker then sends each block to Ollama together with the list of existing topics. The model returns a domain, an existing topic ID or a new topic, a title and a Markdown summary (JSON mode). The prompt asks for ASD-STE100-style documentation (Purpose / numbered Procedure / Result) whose steps cite the numbered commands of the session (`[n]`). After storing it, `embeddings.embed_doc_now` vectorizes the documentation (`doc_embeddings`, keyed by a hash of the text so re-documented sessions are re-embedded); the `docs_fts` triggers index it for BM25. Every search response carries `docs`: matching documented sessions (BM25, and in hybrid mode fused with the doc vectors).
- New topic names are compared with existing ones (token overlap after light stemming). Near-duplicates are merged into the existing topic.
- If Ollama is not available, a heuristic picks the domain from the tools used and names the topic after the main tool.

## Ontology

A deliberately lightweight structure, not OWL/RDF:

```
Domain (fixed, 9)  ──▶  Topic (grows freely)  ──▶  Session  ──▶  Commands
                                                   │
                                                   ├─ project  (from the working directory)
                                                   └─ tools    (first word of each command)
```

The domains are defined in `shellSniffer/ontology.py`, together with a mapping of over 100 common tools to domains. Their internal keys are short German words (`netzwerk`, `pakete`, …) kept for compatibility with existing databases; the display names are English.

On every start, `migrate_ontology()` fills in missing domains, projects and tools and merges obvious duplicates.

## Modes: with / without Atuin, honeypot

The capture mode is a setting stored in the database (`meta`) and switched in the UI. *Without Atuin* the Atuin reader pauses, and output reports sent to `/api/outputs` (from shells that have Atuin loaded) are taken as commands right away. With **Honeypot log** on (only without Atuin), every ingested command is also appended with its output to `honeypot/<date>_<shell>_<host>_<session>.log`.

## Search

`events_fts` is an FTS5 index over `events(command, output, cwd, shell)`, kept in sync by triggers and built once from existing data. `/api/search` turns every word into a prefix term, combines them with OR and orders by `bm25()` with column weights 4 / 1 / 0.5 / 0.3. Optional `start` / `end` (Unix seconds) restrict the time range; with a range but no query, commands are listed newest first (`ranked: false`).

### Hybrid search ("Sniff logs with shroom")

`search.hybrid_search` runs the BM25 search (top 100) and a semantic search: `embeddings.py` embeds command + directory + start of output with an Ollama model (`/api/embed`), stores normalised float32 vectors in the `embeddings` table (per command and model) and ranks by cosine similarity (top 20). Missing vectors are computed lazily, at most `SHELLSNIFFER_EMBED_PER_SEARCH` per search. With the setting `vectorize` on (*Continuous shrooming*), `embeddings.vectorize_worker` also embeds new and old commands in the background (`SHELLSNIFFER_VECTORIZE_CHUNK` per round); its progress is part of `/api/status` (`vectors`). The two lists are merged with weighted Reciprocal Rank Fusion (k = 60): `score = (1 − w)/(k + rank_bm25) + w/(k + rank_cosine)`, with `w` = `semantic_weight` (SniffOptions *Hybrid balance*, default 0.5). Model choice: `SHELLSNIFFER_EMBED_MODEL`, else an installed embedding model, else the chat model.

### Ask the shroom (RAG)

`ask.stream_answer` runs the hybrid search, takes up to 6 documented sessions (full summary) and 14 commands (with up to 400 characters of output), numbers them `[1]…[n]` and sends them with the question to the Ollama chat model (`stream: true`, temperature 0.2). The system prompt allows only these sources, asks for `[n]` citations and the language of the question. Tokens are forwarded to the browser as they arrive.

### Ollama on / off

The setting `ollama` is checked in `cluster._http_json`, the only function that talks to Ollama: while it is off, every request raises `OllamaDisabled` before anything is sent. The model probe, continuous shrooming and SniffMagic (`409`) stop, the hybrid search answers `503`.

## Code layout

The server is split into small modules; `shellsniffer/app.py` holds only the HTTP layer and lists all modules in its docstring. The web UI is plain HTML/CSS/JS without a build step (`static/index.html`, `static/css/shellsniffer.css`, `static/js/shellsniffer.js`), served under `/static`.

## Data model

SQLite in `~/.shellsniffer/shellsniffer.db` (WAL mode):

| Table | Content |
|---|---|
| `events` | one row per command: time, shell, host, session, cwd, command, exit code, duration, output, block |
| `blocks` | sessions: start/end, status (`open` → `ready` → `done`), topic, title, summary, method, project, tools |
| `topics` | name, description, domain |
| `outputs` | output reports from the hook waiting to be matched to an Atuin entry |
| `sources` | Atuin sources (`local`, `wsl:<distro>`) with their import watermark |
| `meta` | small key/value store for one-off migrations |

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/events` | report a command (hooks without Atuin) |
| `POST /api/outputs` | report the output of a command (hooks with Atuin) |
| `GET /api/events?limit=n` | latest commands |
| `GET /api/topics` | topics with their sessions and commands |
| `GET /api/blocks/open` | sessions not clustered yet |
| `GET /api/ontology` | domains and their colours |
| `POST /api/ontology/consolidate` | let the model tidy up topics (no button in the UI at the moment) |
| `POST /api/blocks/{id}/recluster` | cluster a session again (runs immediately) |
| `POST /api/import?name=…&mtime=…` | batch import of one uploaded dump file (raw text body); see `importer.py` |
| `GET /api/cluster/preview?start=…&end=…` | how many sessions SniffMagic would document in a time range |
| `POST /api/cluster` | SniffMagic: `{"start", "end", "include_done"}` (all optional) – queue sessions for Ollama |
| `GET /api/ask?q=…&start=…&end=…&semantic_weight=…` | ask the shroom: hybrid search → numbered sources → Ollama chat answer, streamed as NDJSON (`sources`, `model`, `token`…, `done`/`error`); see `ask.py` |
| `GET /api/search?q=…&mode=hybrid` | *Sniff logs with shroom*: BM25 + Ollama embeddings, merged with RRF (503 if Ollama cannot help) |
| `GET /api/search?q=…&limit=n&start=…&end=…` | BM25-ranked full-text search over commands, output, directories |
| `GET/POST /api/settings` | `{"atuin", "honeypot", "vectorize", "dog", "ollama"}` (booleans) – capture mode, honeypot logging, continuous shrooming, roaming dog, Ollama on/off |
| `GET /api/status` | counts, Ollama model, Atuin sources |
| `GET /api/export.md` | the whole documentation as Markdown |
| `WS /ws` | live updates for the browser |

Both POST endpoints accept JSON or `application/x-www-form-urlencoded`.

## Privacy

Every command and output passes through `shellSniffer/redact.py` before it is stored or sent to the model. It masks values of keys like `password`, `token`, `secret`, `api_key`; options such as `--password x`; `Authorization` headers; credentials in URLs; and well-known token formats (GitHub, AWS, Slack, JWTs, `sk-…` keys, PEM private keys).
