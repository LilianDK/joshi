# Installation

shellSniffer has two parts:

1. **The server** (`start.ps1` / `start.sh`): a small Python web app that stores commands, clusters them into topics and serves the web UI on <http://127.0.0.1:8765>.
2. **The shell hooks** (`install.ps1` / `install.sh`): a few lines added to your shell profiles that report each command (and its output) to the server. The installer also sets up [Atuin](https://atuin.sh), which shellSniffer uses as its main command source.

Install [Ollama](ollama.md) as well if you want real topic names and summaries.

## Windows

### 1. Prerequisites

```powershell
winget install -e --id Python.Python.3.12     # Python 3.10 or newer
winget install -e --id Ollama.Ollama          # optional but recommended
ollama pull llama3.1                          # a chat model for clustering
```

> The `python` command that ships with Windows is only a placeholder that opens the Microsoft Store. `start.ps1` skips it and looks for a real installation (py launcher, winget/python.org install folders). If none is found it tells you what to install.

### 2. Start the server

```powershell
cd path\to\shellSniffer
.\start.ps1
```

On the first run this creates a virtual environment in `.venv` and installs the packages from `requirements.txt`. It then opens the web UI in your browser. Keep the window open while you work.

If PowerShell refuses to run the script ("running scripts is disabled on this system"), allow local scripts once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 3. Install the hooks

In a **second** PowerShell window, from the shellSniffer folder:

```powershell
.\install.ps1
```

The installer looks for every shell on the machine and sets up:

| Shell | What changes |
|---|---|
| Windows PowerShell 5.1 and PowerShell 7 | console profile and VS Code profile (`$PROFILE`) |
| Git Bash | `~/.bashrc` (creates `~/.bash_profile` if missing so the `.bashrc` is loaded) |
| Every WSL distribution | `~/.bashrc` inside the distribution |

It also installs Atuin (via winget on Windows, via the official install script inside WSL) unless you pass `-NoAtuin`.

Every line it adds ends with a `# shellsniffer` or `# atuin-shellsniffer` marker, so running the installer again replaces the old lines instead of duplicating them.

Open **new** shell windows afterwards. Windows that were already open are not observed.

### WSL

shellSniffer's server runs on Windows. Shells inside WSL reach it over the WSL virtual network:

- When WSL is installed, `start.ps1` listens on all interfaces instead of only localhost. Use `.\start.ps1 -LocalOnly` to turn this off.
- Requests that do not come from localhost must carry the token from `%USERPROFILE%\.shellsniffer\token`. The WSL hook reads this file automatically.
- Windows Firewall must allow port 8765 from the WSL network. `install.ps1` creates the rule **shellSniffer (WSL)** when it runs as administrator. Otherwise it prints the command to run in an admin PowerShell:

  ```powershell
  New-NetFirewallRule -DisplayName "shellSniffer (WSL)" -Direction Inbound -Protocol TCP -LocalPort 8765 -RemoteAddress 172.16.0.0/12 -Action Allow -Profile Any
  ```

- Alternative: enable mirrored networking. Add this to `%USERPROFILE%\.wslconfig`, then run `wsl --shutdown`:

  ```ini
  [wsl2]
  networkingMode=mirrored
  ```

  WSL then reaches the server directly via localhost.

shellSniffer reads Atuin's history inside WSL with `python3`, so make sure it is installed in each distribution (`sudo apt install python3`). It only reads distributions that are running, so it never starts WSL on its own.

### Git Bash

Atuin does not support Git Bash. There the shellSniffer hook captures commands itself. Git Bash has no `script` command, so only commands are recorded, not their output.

### Uninstall (Windows)

```powershell
.\install.ps1 -Uninstall
```

This removes all shellSniffer and atuin-shellsniffer lines from every profile. Atuin itself stays installed (`winget uninstall Atuinsh.Atuin` removes it). Your data stays in `%USERPROFILE%\.shellsniffer\`. Delete that folder to remove it.

## Linux (and macOS, untested)

### 1. Prerequisites

```bash
sudo apt install python3 python3-venv curl      # Debian/Ubuntu; use your distribution's package manager otherwise
curl -fsSL https://ollama.com/install.sh | sh   # optional but recommended
ollama pull llama3.1
```

### 2. Start the server

```bash
cd path/to/shellSniffer
bash start.sh               # localhost only, opens the browser
bash start.sh --no-browser  # do not open a browser
bash start.sh --port 9000   # different port
```

The first run creates a virtual environment in `.venv-linux`.

> If you cloned onto a file system that drops the executable bit (network shares, some mounts), always call the scripts through `bash` as shown above.

### 3. Install the hooks

```bash
bash install.sh               # Atuin + shellSniffer hooks for bash and zsh
bash install.sh --no-atuin    # shellSniffer hooks only
bash install.sh --service     # additionally: systemd user service that runs the server
bash install.sh --uninstall   # remove all shellSniffer lines and the service (Atuin stays)
```

`install.sh` installs Atuin with its official install script and loads it in `~/.bashrc` (together with [bash-preexec](https://github.com/rcaloras/bash-preexec)) and in `~/.zshrc` if zsh is installed. The shellSniffer hook is always added as the last line.

With `--service`, the server is managed by systemd:

```bash
systemctl --user status shellSniffer
systemctl --user restart shellSniffer
journalctl --user -u shellSniffer -f
```

Open new terminals afterwards.

## Updating

```bash
git pull
```

Then restart the server. The database is migrated automatically on start. Run the installer again only if the release notes say the hooks changed (it is safe to run it at any time).
