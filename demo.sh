#!/usr/bin/env bash
# One command for the whole demo: vision + Stockfish service, relay, public HTTPS tunnel, the glasses,
# and the laptop viewer. Ctrl-C stops all of it and closes the page on the glasses.
#
#   ./demo.sh                # normal: works whatever networks the glasses and laptop are on (needs internet)
#   ./demo.sh --replay       # also open the replay board: a chess game playing on this screen to point a camera at
#   ./demo.sh --local        # no internet: the glasses stay on the USB cable (adb reverse), no tunnel
#   ./demo.sh --no-glasses   # don't touch any ADB device (use a phone or the laptop webcam as the camera)
#
# Optional settings in ./demo.env (copy demo.env.example; it is git-ignored):
#   HOTSPOT_SSID / HOTSPOT_PASSWORD  Wi-Fi the glasses should be on (e.g. your phone's hotspot); demo.sh
#                                    joins it over USB if they aren't on it yet
#   PORT (3000), VISION_PORT (3001)
# First run ./setup.sh once.
set -euo pipefail
cd "$(dirname "$0")"
[[ -f demo.env ]] && { set -a; source demo.env; set +a; }

MODE=tunnel; REPLAY=0; GLASSES=1
for a in "$@"; do
  case "$a" in
    --local) MODE=local ;;
    --replay) REPLAY=1 ;;
    --no-glasses) GLASSES=0 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $a (see ./demo.sh --help)"; exit 2 ;;
  esac
done
export PORT="${PORT:-3000}"
VISION_PORT="${VISION_PORT:-3001}"
export VISION_URL="http://127.0.0.1:${VISION_PORT}"
mkdir -p logs

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!!  %s\033[0m\n' "$*"; }
die()  { printf '\033[31mxx  %s\033[0m\n' "$*"; exit 1; }
qr()   { node -e "require('qrcode-terminal').generate(process.argv[1], {small: true})" "$1" 2>/dev/null || true; }
have_glasses() { [[ $GLASSES == 1 ]] && command -v adb >/dev/null && adb get-state >/dev/null 2>&1; }

# ---- preflight ---------------------------------------------------------------------------------
[[ -d node_modules/qrcode-terminal && -x .venv/bin/python ]] || die "not set up yet: run ./setup.sh first"
command -v stockfish >/dev/null || [[ -x /opt/homebrew/bin/stockfish ]] || die "stockfish not found: run ./setup.sh"
if [[ $MODE == tunnel ]] && ! command -v cloudflared >/dev/null; then
  warn "cloudflared not installed: falling back to --local (run ./setup.sh to get it)"; MODE=local
fi
# stop a previous run of this demo still holding the ports (anything else on them is an error)
for p in "$PORT" "$VISION_PORT"; do
  for pid in $(lsof -tiTCP:"$p" -sTCP:LISTEN 2>/dev/null || true); do
    if ps -o command= -p "$pid" | grep -qE 'server\.js|service\.py'; then
      warn "stopping an earlier relay/vision process on port $p (pid $pid)"; kill "$pid"; sleep 1
    else
      die "port $p is used by another program: $(ps -o command= -p "$pid")"
    fi
  done
done

PIDS=()
cleanup() {
  trap - EXIT INT TERM
  echo; say "stopping"
  if have_glasses; then glasses/glasses.sh close || true; fi
  kill ${PIDS[@]+"${PIDS[@]}"} 2>/dev/null || true
  wait 2>/dev/null || true
  echo "Stopped. Logs are in logs/."
}
trap cleanup EXIT INT TERM

# ---- vision service + relay --------------------------------------------------------------------
say "starting the vision + Stockfish service (port $VISION_PORT)"
.venv/bin/python vision/service.py "$VISION_PORT" >logs/vision.log 2>&1 & PIDS+=($!)

export ACCESS_KEY="${ACCESS_KEY:-$(openssl rand -hex 16)}"
DEFAULT_ICE='[{"urls":"stun:stun.l.google.com:19302"}]'
export ICE_SERVERS="${ICE_SERVERS:-$DEFAULT_ICE}"
say "starting the relay (port $PORT)"
node server.js >logs/relay.log 2>&1 & PIDS+=($!)

for _ in $(seq 1 60); do curl -fsS -o /dev/null "http://127.0.0.1:${VISION_PORT}/health" 2>/dev/null && break; sleep 0.5; done
curl -fsS -o /dev/null "http://127.0.0.1:${VISION_PORT}/health" || die "vision service did not start; see logs/vision.log"
for _ in $(seq 1 30); do curl -fsS -o /dev/null "http://127.0.0.1:${PORT}/config.js" 2>/dev/null && break; sleep 0.5; done
curl -fsS -o /dev/null "http://127.0.0.1:${PORT}/config.js" || die "relay did not start; see logs/relay.log"

# ---- public tunnel -----------------------------------------------------------------------------
BASE_URL=""
if [[ $MODE == tunnel ]]; then
  say "opening the public HTTPS tunnel (Cloudflare quick tunnel, about 10 s)"
  cloudflared tunnel --no-autoupdate --url "http://localhost:${PORT}" >logs/tunnel.log 2>&1 & PIDS+=($!)
  CF_PID=$!
  for _ in $(seq 1 40); do
    BASE_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' logs/tunnel.log | head -1 || true)
    [[ -n "$BASE_URL" ]] && break
    kill -0 "$CF_PID" 2>/dev/null || break
    sleep 1
  done
  ROUTED=0
  # The new hostname takes a few seconds to resolve. Ask Cloudflare's DNS-over-HTTPS directly: an early
  # lookup through the system resolver gets "no such host" cached by macOS for a minute or more.
  probe() {
    curl -fsS -o /dev/null --max-time 5 --doh-url https://1.1.1.1/dns-query "${BASE_URL}/config.js?k=${ACCESS_KEY}" 2>/dev/null ||
    curl -fsS -o /dev/null --max-time 5 "${BASE_URL}/config.js?k=${ACCESS_KEY}" 2>/dev/null
  }
  if [[ -n "$BASE_URL" ]]; then
    for _ in $(seq 1 30); do probe && { ROUTED=1; break; }; sleep 2; done
  fi
  if [[ $ROUTED != 1 ]]; then
    warn "the tunnel did not come up (no internet?); falling back to --local. Details: logs/tunnel.log"
    kill "$CF_PID" 2>/dev/null || true; MODE=local; BASE_URL=""
  fi
fi

# ---- glasses -----------------------------------------------------------------------------------
GLASSES_URL=""
if [[ $MODE == tunnel ]]; then GLASSES_URL="${BASE_URL}/hud?autostart=1&k=${ACCESS_KEY}"; else GLASSES_URL="http://localhost:${PORT}/hud?autostart=1"; fi
if have_glasses; then
  if [[ $MODE == tunnel ]]; then
    if [[ -n "${HOTSPOT_SSID:-}" ]]; then
      say "putting the glasses on Wi-Fi '${HOTSPOT_SSID}'"
      pwarg=(); [[ -n "${HOTSPOT_PASSWORD:-}" ]] && pwarg=(-p "$HOTSPOT_PASSWORD")
      .venv/bin/python glasses/glasses_wifi.py "$HOTSPOT_SSID" ${pwarg[@]+"${pwarg[@]}"} \
        || warn "could not join '${HOTSPOT_SSID}' (is the hotspot on? On an iPhone, keep Personal Hotspot open with Maximize Compatibility on)"
    fi
    gip=$(adb shell ip -4 addr show wlan0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 || true)
    [[ -n "$gip" ]] || warn "the glasses have no Wi-Fi address: they need internet for this mode"
    say "opening the HUD on the glasses"
    BASE_URL="$BASE_URL" glasses/glasses.sh || warn "could not open the page on the glasses"
  else
    say "opening the HUD on the glasses over the USB cable"
    BASE_URL="" glasses/glasses.sh || warn "could not open the page on the glasses"
    glasses/glasses.sh watch >logs/adb-watch.log 2>&1 & PIDS+=($!)   # re-adds the port-forward if the cable blips
  fi
  say "waiting for the glasses to connect"
  for _ in $(seq 1 30); do
    g=$(curl -fsS "http://127.0.0.1:${PORT}/run" 2>/dev/null | python3 -c 'import json,sys;g=json.load(sys.stdin).get("glasses");print((g["via"]+" from "+g["ip"]) if g else "")' 2>/dev/null || true)
    [[ -n "$g" ]] && break; sleep 1
  done
  if [[ -n "$g" ]]; then
    say "glasses connected ($g)"
    [[ $MODE == tunnel ]] && echo "    You can unplug the USB cable now."
  else
    warn "the glasses have not connected yet; check them (camera prompt?) or re-run ./demo.sh"
  fi
elif [[ $GLASSES == 1 ]]; then
  warn "no glasses on ADB: open the glasses page below on them by hand, or use a phone as the camera"
fi

# ---- laptop pages ------------------------------------------------------------------------------
VIEWER="http://localhost:${PORT}/"
REPLAY_URL="http://localhost:${PORT}/replay?autoplay=1"
opener() { if [[ "$(uname)" == Darwin ]]; then open "$@"; elif command -v xdg-open >/dev/null; then xdg-open "$@" >/dev/null 2>&1; fi; }
# On macOS use Safari for the viewer: Chrome blocks WebRTC to devices on the local network.
if [[ "$(uname)" == Darwin && -d /Applications/Safari.app ]]; then open -a Safari "$VIEWER"; else opener "$VIEWER" || true; fi
[[ $REPLAY == 1 ]] && { sleep 1; opener "$REPLAY_URL" || true; }

# ---- summary -----------------------------------------------------------------------------------
echo
say "ready"
echo "  Laptop viewer : $VIEWER"
echo "  Replay board  : $REPLAY_URL   (a game playing itself; point the camera at it)"
if [[ $MODE == tunnel ]]; then
  echo "  Glasses page  : $GLASSES_URL"
  CAMERA_URL="${BASE_URL}/broadcast?autostart=1&k=${ACCESS_KEY}"
  MIRROR_URL="${BASE_URL}/hud?mirror=1&k=${ACCESS_KEY}"
  echo "  Phone as the camera (instead of glasses): $CAMERA_URL"
  echo "  Mirror of the glasses' display on a phone: $MIRROR_URL"
  if have_glasses || [[ -n "${g:-}" ]]; then echo; echo "  Scan for the mirror on your phone:"; qr "$MIRROR_URL"
  else echo; echo "  Scan with a phone to use it as the camera:"; qr "$CAMERA_URL"; fi
  echo "  The links contain a random key that changes on every start; anyone with it can see the camera."
else
  echo "  Glasses page  : $GLASSES_URL   (keep the USB cable plugged in)"
  echo "  Laptop webcam as the camera: http://localhost:${PORT}/broadcast"
fi
echo
echo "Ctrl-C stops everything."
wait "${PIDS[1]}"   # the relay
