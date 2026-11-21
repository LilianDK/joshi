# joshi – hook for zsh (Linux, macOS, WSL)
# Source it at the end of ~/.zshrc (install.sh does this for you):
#   source /pfad/zu/joshi/hooks/joshi.zsh
#
# Options (set before sourcing):
#   JOSHI_URL=http://host:8765    server address (default: localhost; in WSL the Windows host)
#   JOSHI_CAPTURE_OUTPUT=0        capture commands only, no output
#   JOSHI_DISABLE=1               disable the hook entirely

[[ -o interactive ]] || return 0
[[ -n "$JOSHI_DISABLE" ]] && return 0
[[ -n "$__JOSHI_LOADED" ]] && return 0
__JOSHI_LOADED=1
zmodload zsh/datetime 2>/dev/null

# --- detect environment -----------------------------------------------------
__jd_shell="zsh"
__jd_tokfile="$HOME/.joshi/token"
if grep -qi microsoft /proc/version 2>/dev/null; then
  __jd_shell="wsl"
  __jd_winhome="$(wslpath "$(cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')" 2>/dev/null)"
  __jd_tokfile="${__jd_winhome}/.joshi/token"
  if [[ -z "$JOSHI_URL" ]]; then
    if curl -s -m 0.3 -o /dev/null http://127.0.0.1:8765/api/status 2>/dev/null; then
      JOSHI_URL="http://127.0.0.1:8765"
    else
      JOSHI_URL="http://$(ip route show default 2>/dev/null | awk '{print $3; exit}'):8765"
    fi
  fi
fi
JOSHI_URL="${JOSHI_URL:-http://127.0.0.1:8765}"
__jd_token="$(cat "$__jd_tokfile" 2>/dev/null)"
__jd_session="$$-$RANDOM"
__jd_host="$(hostname 2>/dev/null)"

# --- capture output: wrap the session in `script` ---------------------------
if [[ "${JOSHI_CAPTURE_OUTPUT:-1}" == "1" && -z "$JOSHI_TYPESCRIPT" ]] && command -v script >/dev/null 2>&1 \
   && script --version 2>&1 | grep -q util-linux; then
  mkdir -p "$HOME/.joshi"
  export JOSHI_TYPESCRIPT="$HOME/.joshi/typescript-$$.log"
  unset __JOSHI_LOADED
  SHELL="$(command -v zsh)" exec script -qf "$JOSHI_TYPESCRIPT"
fi
if [[ -n "$JOSHI_TYPESCRIPT" && -f "$JOSHI_TYPESCRIPT" ]]; then
  trap 'rm -f "$JOSHI_TYPESCRIPT"' EXIT
fi

__jd_ms() { print -r -- $(( ${EPOCHREALTIME:-$(date +%s)} * 1000 )) | cut -d. -f1; }
__jd_size() { [[ -f "$JOSHI_TYPESCRIPT" ]] && wc -c < "$JOSHI_TYPESCRIPT" | tr -d ' ' || echo 0; }

typeset -g __jd_cmd="" __jd_start="" __jd_offset="$(__jd_size)"

__jd_preexec() {
  # leading space = do not capture (like HIST_IGNORE_SPACE)
  [[ "$1" == " "* ]] && { __jd_cmd=""; return; }
  __jd_cmd="$1"
  __jd_start="$(__jd_ms)"
}

__jd_precmd() {
  local ec=$? size outfile dur
  size="$(__jd_size)"
  if [[ -n "$__jd_cmd" && -n "$__jd_start" ]]; then
    dur=$(( $(__jd_ms) - __jd_start ))
    outfile=""
    if [[ -n "$JOSHI_TYPESCRIPT" && "$size" -gt "$__jd_offset" ]]; then
      outfile="$(mktemp 2>/dev/null)"
      tail -c +$(( __jd_offset + 1 )) "$JOSHI_TYPESCRIPT" | head -c 65536 \
        | sed -E 's/\x1b\[[0-9;?]*[ -\/]*[@-~]//g; s/\x1b\][^\x07]*(\x07|\x1b\\)//g; s/\x1b[()][A-Z0-9]//g; s/\r$//' \
        | awk -v c="${__jd_cmd%%$'\n'*}" '
            !found && index($0, c) { found = 1; next }
            found { print; next }
            { buf[++n] = $0 }
            END { if (!found) for (i = 2; i <= n; i++) print buf[i] }' > "$outfile"
    fi
    # With Atuin, Atuin records the command; joshi adds the output (linked via ATUIN_SESSION)
    local apipath="/api/events" session="$__jd_session"
    if [[ -n "$ATUIN_SESSION" ]]; then apipath="/api/outputs"; session="$ATUIN_SESSION"; fi
    local -a outarg
    [[ -n "$outfile" ]] && outarg=(--data-urlencode "output@$outfile")
    {
      curl -s -m 3 -o /dev/null -X POST "$JOSHI_URL$apipath" \
        -H "X-Joshi-Token: $__jd_token" \
        --data-urlencode "shell=$__jd_shell" \
        --data-urlencode "host=$__jd_host" \
        --data-urlencode "session=$session" \
        --data-urlencode "cwd=$PWD" \
        --data-urlencode "command=$__jd_cmd" \
        --data-urlencode "exit_code=$ec" \
        --data-urlencode "duration_ms=$dur" \
        --data-urlencode "started_at=$__jd_start" \
        "${outarg[@]}"
      [[ -n "$outfile" ]] && rm -f "$outfile"
    } >/dev/null 2>&1 &!
  fi
  __jd_cmd=""; __jd_start=""
  __jd_offset="$size"
  return $ec
}

autoload -Uz add-zsh-hook
add-zsh-hook preexec __jd_preexec
add-zsh-hook precmd __jd_precmd
