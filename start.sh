#!/usr/bin/env bash
# Starts the joshi server on Linux/macOS (creates a virtual environment on first run).
#   ./start.sh                reachable from localhost only (http://127.0.0.1:8765)
#   ./start.sh --port 9000    different port
#   ./start.sh --no-browser   do not open a browser
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
PORT=8765; BROWSER=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --no-browser) BROWSER=0; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

find_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}

VENV=.venv-linux   # separate folder so a Windows .venv in the same project stays untouched
if [[ -d $VENV && ! -x $VENV/bin/python ]]; then rm -rf "$VENV"; fi   # remove a half-created venv
if [[ ! -x $VENV/bin/python ]]; then
  PY="$(find_python)" || { echo "No Python 3.10+ found. Install it, e.g.: sudo apt install python3 python3-venv"; exit 1; }
  echo "Creating Python virtual environment ($PY) ..."
  if ! "$PY" -m venv "$VENV"; then
    rm -rf "$VENV"
    echo "Creating the venv failed. On Debian/Ubuntu you often need: sudo apt install python3-venv"; exit 1
  fi
  echo "Installing dependencies ..."
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet -r requirements.txt || { rm -rf "$VENV"; echo "Installation failed."; exit 1; }
fi

echo
echo "joshi is running at http://127.0.0.1:$PORT  (Ctrl+C stops the server)"
echo "To capture commands: run ./install.sh once, then open a new terminal."
echo
if [[ "$BROWSER" == 1 ]] && command -v xdg-open >/dev/null 2>&1; then
  (sleep 1.5; xdg-open "http://127.0.0.1:$PORT" >/dev/null 2>&1 || true) &
elif [[ "$BROWSER" == 1 ]] && command -v open >/dev/null 2>&1; then
  (sleep 1.5; open "http://127.0.0.1:$PORT" >/dev/null 2>&1 || true) &
fi
exec "$VENV/bin/python" -m uvicorn joshi.app:app --host 127.0.0.1 --port "$PORT" --log-level warning
