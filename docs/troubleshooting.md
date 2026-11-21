# Troubleshooting

## The web UI is empty / nothing happens

1. Is the server running? The `start.ps1` / `start.sh` window must stay open (or the systemd service must be active).
2. Did you run the installer, and did you open **new** shell windows afterwards? Already open windows are not observed.
3. Check that the hook is loaded:
   - PowerShell: `Get-Content $PROFILE` should contain a line ending in `# joshi`.
   - bash/zsh: `grep joshi ~/.bashrc ~/.zshrc`.
4. Run a command and look at the live view. If it does not appear, test the server directly: open <http://127.0.0.1:8765/api/status>.

## `start.ps1`: "Python was not found" or the Microsoft Store opens

Windows ships a `python.exe` placeholder that opens the Store. Install a real Python:

```powershell
winget install -e --id Python.Python.3.12
```

Then open a new PowerShell window and run `.\start.ps1` again. If an old, broken `.venv` folder exists, delete it first.

## `.\install.ps1` is not recognized

You are not in the joshi folder. Run `cd path\to\joshi` first, or call the script with its full path.

## "Running scripts is disabled on this system"

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## WSL commands do not show up

- The server must listen beyond localhost: start it with `.\start.ps1` (not `-LocalOnly`). It prints the address it listens on.
- Windows Firewall must allow port 8765 from WSL. Create the rule **joshi (WSL)** as described in [installation.md](installation.md#wsl), or switch WSL to mirrored networking.
- Test from inside WSL:

  ```bash
  curl "http://$(ip route show default | awk '{print $3; exit}'):8765/api/status"
  ```

- For Atuin import, `python3` must be installed in the distribution.

## Ollama: "Ollama not reachable · heuristic"

- Is Ollama running? `ollama list` should work.
- Is a chat model installed? `ollama pull llama3.1`.
- If `JOSHI_OLLAMA_MODEL` is set, does that model exist?
- If Ollama runs elsewhere, set `JOSHI_OLLAMA_URL`.

joshi checks again on every clustering run, so you do not need to restart the server once Ollama is up. Click **re-cluster** on sessions that were handled by the heuristic.

## Atuin chip says "Atuin not found"

- Check that Atuin is installed: `atuin --version`.
- Check that Atuin records commands: run a command, then `atuin history list | tail`.
- joshi finds the database through `atuin info`, `ATUIN_DB_PATH` or the default locations. Set `ATUIN_DB_PATH` if you moved it.

joshi still works without Atuin. The hooks then report commands themselves.

## Output is missing

- **PowerShell:** output is read from a transcript. Output of cmdlets is captured reliably; output of external programs (`git`, `python`, …) only partly, depending on the PowerShell version.
- **Git Bash:** has no `script` command, so only commands are captured.
- **bash/zsh on Linux and WSL:** the shell runs inside `script` (util-linux). Check that `script` is installed. Full-screen programs (`vim`, `htop`) produce little useful output by design.
- Output is cut to 16,000 characters per command (`JOSHI_MAX_OUTPUT`).

## A command was captured twice / not at all

- In bash, commands starting with a space are not captured when `HISTCONTROL` contains `ignorespace` or `ignoreboth`. This is useful for secrets.
- With `HISTCONTROL=ignoredups`, an identical command repeated directly is captured once.
- If Atuin and the hook both report a command, joshi links them by session, command and start time (±60 s). If you see duplicates, please open an issue with the shell and the commands.

## Topics are messy

Click **Tidy up topics** (needs Ollama). It reassigns domains and merges duplicates. A larger model (for example `qwen2.5:7b` or `qwen2.5:14b`) gives more consistent topics.

## Resetting everything

Stop the server, delete `~/.joshi/` (`%USERPROFILE%\.joshi\`), and start again. Atuin's own history is not affected, so joshi re-imports the last 14 days from it.
