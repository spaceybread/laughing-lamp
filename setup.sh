#!/usr/bin/env bash
# One-time setup, then a self-test. Safe to re-run.
#   - command-line tools: Node.js 18+, Python 3.9+, Stockfish (required); cloudflared, adb (optional)
#     On macOS with Homebrew, missing ones are installed with `brew install`; elsewhere it prints what to install.
#   - Node packages (node_modules/) and a Python virtual environment (.venv/) inside this folder only.
#   - Self-test: the vision service reads a sample board photo and Stockfish suggests a move.
set -euo pipefail
cd "$(dirname "$0")"
say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!!  %s\033[0m\n' "$*"; }
die()  { printf '\033[31mxx  %s\033[0m\n' "$*"; exit 1; }

# ---- tools -------------------------------------------------------------------------------------
# tool : Homebrew formula : Debian/Ubuntu hint : required?
TOOLS="node:node:sudo apt install nodejs npm:yes
python3:python:sudo apt install python3 python3-venv:yes
stockfish:stockfish:sudo apt install stockfish:yes
cloudflared:cloudflared:see https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/:no
adb:android-platform-tools:sudo apt install adb:no"
missing_req=0
while IFS=: read -r tool formula hint required; do
  command -v "$tool" >/dev/null && continue
  if command -v brew >/dev/null; then
    say "installing $tool (brew install $formula)"
    brew install "$formula" || { [[ $required == yes ]] && missing_req=1; warn "brew install $formula failed"; }
  elif [[ $required == yes ]]; then
    warn "$tool is required: $hint"; missing_req=1
  else
    warn "$tool not found (optional: needed for $( [[ $tool == adb ]] && echo 'the glasses' || echo 'the public tunnel')): $hint"
  fi
done <<< "$TOOLS"
[[ $missing_req == 0 ]] || die "install the required tools above, then run ./setup.sh again"

node -e 'process.exit(parseInt(process.versions.node) >= 18 ? 0 : 1)' || die "Node.js 18 or newer is needed (found $(node -v))"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || die "Python 3.9 or newer is needed (found $(python3 -V))"

# ---- packages ----------------------------------------------------------------------------------
say "installing Node packages"
npm install --no-fund --no-audit --loglevel=error
say "creating the Python environment (.venv): vision/requirements.txt + Bonus-PartC/requirements.txt"
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r vision/requirements.txt -r Bonus-PartC/requirements.txt

# ---- self-test ---------------------------------------------------------------------------------
say "self-test: reading vision/samples/board_1.jpg"
PORT_T=3099; mkdir -p logs
.venv/bin/python vision/service.py "$PORT_T" >logs/selftest.log 2>&1 & SVC=$!
trap 'kill $SVC 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do curl -fsS -o /dev/null "http://127.0.0.1:$PORT_T/health" 2>/dev/null && break; sleep 0.5; done
out=$(curl -fsS -X POST -H 'content-type: image/jpeg' --data-binary @vision/samples/board_1.jpg \
      "http://127.0.0.1:$PORT_T/analyze?track=0" 2>/dev/null || true)
summary=$(printf '%s' "$out" | .venv/bin/python -c '
import json, sys
d = json.load(sys.stdin)
m = (d.get("moves") or [{}])[0]
print("position %s, best move %s (%s)" % (d["fen"].split()[0], m["san"], m["eval"]) if d.get("ok") and m else "")
' 2>/dev/null || true)
[[ -n "$summary" ]] || die "self-test failed; see logs/selftest.log"
echo "    $summary"
say "setup complete. Next: ./demo.sh   (see README.md)"
