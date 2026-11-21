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

joshi uses [Atuin](https://atuin.sh) as its main command source where it can:

1. **Atuin** records every command in PowerShell, bash and zsh with directory, exit code, duration and session.
2. **The server** reads new Atuin entries every ~2 seconds. On Windows it reads Atuin's local SQLite database directly; for WSL it runs a small reader with `wsl.exe -d <distro> -- python3` for every *running* distribution. Only finished commands are imported. On the first start it imports the last 14 days.
3. **The joshi hook** in each shell reports the **output** of each command, because Atuin does not store output. The server links output and Atuin entry by session ID, command text and start time.
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
- When a block has been idle for 45 seconds, the clustering worker sends it to Ollama together with the list of existing topics. The model returns a domain, an existing topic ID or a new topic, a title and a Markdown summary (JSON mode).
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

The domains are defined in `joshi/ontology.py`, together with a mapping of over 100 common tools to domains. Their internal keys are short German words (`netzwerk`, `pakete`, …) kept for compatibility with existing databases; the display names are English.

On every start, `migrate_ontology()` fills in missing domains, projects and tools and merges obvious duplicates. Once per database (and on **Tidy up topics**) the model is asked to review all topics.

## Data model

SQLite in `~/.joshi/joshi.db` (WAL mode):

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
| `POST /api/ontology/consolidate` | let the model tidy up topics |
| `POST /api/blocks/{id}/recluster` | cluster a session again |
| `GET /api/status` | counts, Ollama model, Atuin sources |
| `GET /api/export.md` | the whole documentation as Markdown |
| `WS /ws` | live updates for the browser |

Both POST endpoints accept JSON or `application/x-www-form-urlencoded`.

## Privacy

Every command and output passes through `joshi/redact.py` before it is stored or sent to the model. It masks values of keys like `password`, `token`, `secret`, `api_key`; options such as `--password x`; `Authorization` headers; credentials in URLs; and well-known token formats (GitHub, AWS, Slack, JWTs, `sk-…` keys, PEM private keys).
