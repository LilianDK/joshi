# shellSniffer – hook for bash (WSL, Git Bash, Linux)
# Source it at the end of ~/.bashrc (the installers do this for you):
#   source /pfad/zu/shellsniffer/hooks/shellsniffer.bash
#
# Options (set before sourcing):
#   SHELLSNIFFER_URL=http://host:8765    server address (default: localhost; in WSL the Windows host)
#   SHELLSNIFFER_CAPTURE_OUTPUT=0        capture commands only, no output
#   SHELLSNIFFER_DISABLE=1               disable the hook entirely

[[ $- == *i* ]] || return 0              # interactive shells only
[[ -n "$SHELLSNIFFER_DISABLE" ]] && return 0
[[ -n "$__SHELLSNIFFER_LOADED" ]] && return 0
__SHELLSNIFFER_LOADED=1

# --- detect environment -----------------------------------------------------
__sd_shell="bash"
__sd_is_wsl=""
if grep -qi microsoft /proc/version 2>/dev/null; then
  __sd_shell="wsl"; __sd_is_wsl=1
elif [[ -n "$MSYSTEM" ]]; then
  __sd_shell="gitbash"
fi

# Token + server address: the server stores its token in ~/.shellsniffer/token (%USERPROFILE%\.shellsniffer\token on Windows)
if [[ -n "$__sd_is_wsl" ]]; then
  __sd_winhome="$(wslpath "$(cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')" 2>/dev/null)"
  __sd_tokfile="${__sd_winhome}/.shellsniffer/token"
  if [[ -z "$SHELLSNIFFER_URL" ]]; then
    # WSL2 with "mirrored" networking reaches Windows via localhost, otherwise via the gateway
    if curl -s -m 0.3 -o /dev/null http://127.0.0.1:8765/api/status 2>/dev/null; then
      SHELLSNIFFER_URL="http://127.0.0.1:8765"
    else
      SHELLSNIFFER_URL="http://$(ip route show default 2>/dev/null | awk '{print $3; exit}'):8765"
    fi
  fi
else
  __sd_tokfile="$HOME/.shellsniffer/token"
fi
SHELLSNIFFER_URL="${SHELLSNIFFER_URL:-http://127.0.0.1:8765}"
__sd_token="$(cat "$__sd_tokfile" 2>/dev/null)"
__sd_session="$$-$RANDOM"
__sd_host="$(hostname 2>/dev/null)"

# --- capture output: wrap the session in `script` ---------------------------
# `script` records everything shown in the terminal. After each command we read the
# new part of the recording. Git Bash has no `script` -> commands only.
if [[ "${SHELLSNIFFER_CAPTURE_OUTPUT:-1}" == "1" && -z "$SHELLSNIFFER_TYPESCRIPT" ]] && command -v script >/dev/null 2>&1 \
   && script --version 2>&1 | grep -q util-linux; then
  mkdir -p "$HOME/.shellsniffer"
  export SHELLSNIFFER_TYPESCRIPT="$HOME/.shellsniffer/typescript-$$.log"
  unset __SHELLSNIFFER_LOADED
  SHELL="$BASH" exec script -qf "$SHELLSNIFFER_TYPESCRIPT"
fi
if [[ -n "$SHELLSNIFFER_TYPESCRIPT" && -f "$SHELLSNIFFER_TYPESCRIPT" ]]; then
  trap 'rm -f "$SHELLSNIFFER_TYPESCRIPT"' EXIT
fi

__sd_ms() { local t; t=$(date +%s%3N 2>/dev/null); [[ "$t" == *N ]] && t=$(( $(date +%s) * 1000 )); echo "$t"; }
__sd_size() { [[ -f "$SHELLSNIFFER_TYPESCRIPT" ]] && wc -c < "$SHELLSNIFFER_TYPESCRIPT" | tr -d ' ' || echo 0; }

__sd_start=""
__sd_last_hist=""
__sd_ready=""
__sd_offset="$(__sd_size)"

__sd_preexec() {
  [[ -n "$COMP_LINE" ]] && return
  [[ "$BASH_COMMAND" == __sd_precmd* ]] && return
  [[ -z "$__sd_start" ]] && __sd_start="$(__sd_ms)"
}

__sd_precmd() {
  local ec=$? h num cmd end dur outfile size
  h="$(HISTTIMEFORMAT= builtin history 1)"
  num="$(sed -E 's/^ *([0-9]+).*/\1/' <<<"$h" | head -n1)"
  cmd="$(sed -E '1s/^ *[0-9]+\*? +//' <<<"$h")"
  size="$(__sd_size)"
  if [[ -z "$__sd_ready" ]]; then   # first prompt: history has only just been loaded
    __sd_ready=1; __sd_last_hist="$num"; __sd_offset="$size"; __sd_start=""; return $ec
  fi
  if [[ -n "$__sd_start" && -n "$cmd" && "$num" != "$__sd_last_hist" ]]; then
    end="$(__sd_ms)"; dur=$(( end - __sd_start ))
    outfile=""
    if [[ -n "$SHELLSNIFFER_TYPESCRIPT" && "$size" -gt "$__sd_offset" ]]; then
      outfile="$(mktemp 2>/dev/null)"
      # new part of the recording without ANSI codes; everything up to and including the line
      # with the typed command (prompt echo) is dropped
      tail -c +$(( __sd_offset + 1 )) "$SHELLSNIFFER_TYPESCRIPT" | head -c 65536 \
        | sed -E 's/\x1b\[[0-9;?]*[ -\/]*[@-~]//g; s/\x1b\][^\x07]*(\x07|\x1b\\)//g; s/\x1b[()][A-Z0-9]//g; s/\r$//' \
        | awk -v c="$(head -n1 <<<"$cmd")" '
            !found && index($0, c) { found = 1; next }
            found { print; next }
            { buf[++n] = $0 }
            END { if (!found) for (i = 2; i <= n; i++) print buf[i] }' > "$outfile"
    fi
    # With Atuin, Atuin records the command; shellSniffer adds the output and links both via
    # Atuin's session id. (If no Atuin entry shows up, shellSniffer records the command itself.)
    local path="/api/events" session="$__sd_session"
    if [[ -n "$ATUIN_SESSION" ]]; then path="/api/outputs"; session="$ATUIN_SESSION"; fi
    (
      curl -s -m 3 -o /dev/null -X POST "$SHELLSNIFFER_URL$path" \
        -H "X-ShellSniffer-Token: $__sd_token" \
        --data-urlencode "shell=$__sd_shell" \
        --data-urlencode "host=$__sd_host" \
        --data-urlencode "session=$session" \
        --data-urlencode "cwd=$PWD" \
        --data-urlencode "command=$cmd" \
        --data-urlencode "exit_code=$ec" \
        --data-urlencode "duration_ms=$dur" \
        --data-urlencode "started_at=$__sd_start" \
        ${outfile:+--data-urlencode "output@$outfile"}
      [[ -n "$outfile" ]] && rm -f "$outfile"
    ) >/dev/null 2>&1 &
    disown 2>/dev/null
  fi
  __sd_last_hist="$num"
  __sd_offset="$size"
  __sd_start=""
  return $ec
}

if [[ -n "${bash_preexec_imported:-}${__bp_imported:-}" ]] || declare -F __bp_preexec_invoke_exec >/dev/null 2>&1; then
  # bash-preexec is active (e.g. via Atuin) -> use its hook arrays instead of our own traps
  __sd_bp_preexec() { [[ -z "$__sd_start" ]] && __sd_start="$(__sd_ms)"; }
  preexec_functions+=(__sd_bp_preexec)
  precmd_functions=(__sd_precmd "${precmd_functions[@]}")
else
  trap '__sd_preexec' DEBUG
  if [[ -z "$PROMPT_COMMAND" ]]; then
    PROMPT_COMMAND="__sd_precmd"
  elif [[ "$PROMPT_COMMAND" != *__sd_precmd* ]]; then
    PROMPT_COMMAND="__sd_precmd; ${PROMPT_COMMAND}"
  fi
fi
