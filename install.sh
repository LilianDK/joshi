#!/usr/bin/env bash
# shellSniffer installer for Linux/macOS: sets up Atuin and the shellSniffer hooks for bash and zsh.
#   ./install.sh               install
#   ./install.sh --no-atuin    without Atuin (shellSniffer then captures commands itself)
#   ./install.sh --service     also autostart the server as a systemd user service
#   ./install.sh --uninstall   remove all shellSniffer entries (and the service); Atuin stays installed
# Safe to run repeatedly: previous entries are replaced.
set -uo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ATUIN=1; SERVICE=0; UNINSTALL=0
for a in "$@"; do
  case "$a" in
    --no-atuin) ATUIN=0 ;;
    --service) SERVICE=1 ;;
    --uninstall) UNINSTALL=1; ATUIN=0 ;;
    *) echo "Unknown option: $a"; exit 1 ;;
  esac
done
done_list=()

# Remove lines carrying our markers and (when installing) append them again
update_rc() {  # $1 = file, $2.. = lines to append
  local f="$1"; shift
  touch "$f"
  local pattern='#[[:space:]]*(shelldoc|docuduck|joshi|shellsniffer)[[:space:]]*$'
  [[ "$UNINSTALL" == 1 ]] && pattern='#[[:space:]]*(shelldoc|docuduck|joshi|atuin-joshi|shellsniffer|atuin-shellsniffer)[[:space:]]*$'
  sed -i.shellsniffer-bak -E "/$pattern/d" "$f" && rm -f "$f.shellsniffer-bak"
  if [[ "$UNINSTALL" == 0 ]]; then printf '%s\n' "$@" >> "$f"; fi
}

# ---------------------------------------------------------------- Atuin
atuin_bin() { command -v atuin 2>/dev/null || { [[ -x "$HOME/.atuin/bin/atuin" ]] && echo "$HOME/.atuin/bin/atuin"; }; }
if [[ "$ATUIN" == 1 ]]; then
  if [[ -z "$(atuin_bin)" ]]; then
    echo "Installing Atuin ..."
    curl --proto '=https' --tlsv1.2 -LsSf https://setup.atuin.sh | sh || echo "WARNING: Atuin installation failed – shellSniffer will capture commands itself."
  fi
  if [[ -n "$(atuin_bin)" ]]; then
    # bash: Atuin needs bash-preexec
    if ! grep -q 'atuin init bash' "$HOME/.bashrc" 2>/dev/null; then
      [[ -f "$HOME/.bash-preexec.sh" ]] || curl -fsSL https://raw.githubusercontent.com/rcaloras/bash-preexec/master/bash-preexec.sh -o "$HOME/.bash-preexec.sh"
      printf '%s\n' '[ -f "$HOME/.atuin/bin/env" ] && . "$HOME/.atuin/bin/env"  # atuin-shellsniffer' \
        '[ -f ~/.bash-preexec.sh ] && source ~/.bash-preexec.sh  # atuin-shellsniffer' \
        'command -v atuin >/dev/null && eval "$(atuin init bash)"  # atuin-shellsniffer' >> "$HOME/.bashrc"
    fi
    if command -v zsh >/dev/null 2>&1 && ! grep -q 'atuin init zsh' "$HOME/.zshrc" 2>/dev/null; then
      printf '%s\n' '[ -f "$HOME/.atuin/bin/env" ] && . "$HOME/.atuin/bin/env"  # atuin-shellsniffer' \
        'command -v atuin >/dev/null && eval "$(atuin init zsh)"  # atuin-shellsniffer' >> "$HOME/.zshrc"
    fi
    done_list+=("Atuin: $(atuin_bin)")
  fi
fi

# ---------------------------------------------------------------- Hooks (always last, after Atuin)
update_rc "$HOME/.bashrc" "source \"$ROOT/hooks/shellsniffer.bash\"  # shellsniffer"
done_list+=("bash: ~/.bashrc")
if command -v zsh >/dev/null 2>&1 || [[ -f "$HOME/.zshrc" ]]; then
  update_rc "$HOME/.zshrc" "source \"$ROOT/hooks/shellsniffer.zsh\"  # shellsniffer"
  done_list+=("zsh: ~/.zshrc")
fi
command -v script >/dev/null 2>&1 || echo "Note: 'script' (util-linux) is missing – only commands, no output, will be captured."

# ---------------------------------------------------------------- Autostart (systemd --user)
UNIT="$HOME/.config/systemd/user/shellsniffer.service"
if [[ "$UNINSTALL" == 1 && -f "$UNIT" ]]; then
  systemctl --user disable --now shellsniffer.service 2>/dev/null; rm -f "$UNIT"; systemctl --user daemon-reload 2>/dev/null
  done_list+=("autostart service")
fi
# service from before the rename
OLD_UNIT="$HOME/.config/systemd/user/joshi.service"
if [[ -f "$OLD_UNIT" ]]; then
  systemctl --user disable --now joshi.service 2>/dev/null; rm -f "$OLD_UNIT"; systemctl --user daemon-reload 2>/dev/null
  done_list+=("old joshi.service removed")
fi
if [[ "$UNINSTALL" == 1 ]]; then
  :
elif [[ "$SERVICE" == 1 ]]; then
  if command -v systemctl >/dev/null 2>&1; then
    mkdir -p "$(dirname "$UNIT")"
    cat > "$UNIT" <<UNITEOF
[Unit]
Description=shellSniffer

[Service]
ExecStart=$ROOT/start.sh --no-browser
Restart=on-failure

[Install]
WantedBy=default.target
UNITEOF
    systemctl --user daemon-reload && systemctl --user enable --now shellsniffer.service && done_list+=("autostart: systemd user service shellsniffer.service")
  else
    echo "systemd not found – skipping autostart."
  fi
fi

echo
if [[ "$UNINSTALL" == 1 ]]; then echo "shellSniffer entries removed from:"; else echo "shellSniffer is now watching:"; fi
for d in "${done_list[@]}"; do echo "  - $d"; done
[[ "$UNINSTALL" == 0 ]] && echo && echo "Open a new terminal – terminals that are already open are not watched."
