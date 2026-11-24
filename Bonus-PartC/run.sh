#!/usr/bin/env bash
# Field-run controller for Part C. Talks to the relay (server.js) which writes Bonus-PartC/traces/<run>/.
#   ./Bonus-PartC/run.sh start <scenario> [net] [note]   begin a run (net: wifi|pal3|5g|usb)
#   ./Bonus-PartC/run.sh stop                            end the run
#   ./Bonus-PartC/run.sh status                          current run + connected clients
#   ./Bonus-PartC/run.sh mark <label>                    put a labelled marker in the trace
#   ./Bonus-PartC/run.sh pause|resume|analyze|probe      runtime control of the glasses page
#   ./Bonus-PartC/run.sh reset                           forget the tracked chess game (vision service)
#   ./Bonus-PartC/run.sh replay play|pause|restart|next  drive the replay board (/replay on the laptop screen)
#   ./Bonus-PartC/run.sh series <scenario> <runs> <secs> [net]   N back-to-back runs with a gap between them
# SERVER (default http://localhost:3000) and VISION (default http://127.0.0.1:3001) can be overridden.
set -euo pipefail
SERVER=${SERVER:-http://localhost:3000}
VISION=${VISION:-http://127.0.0.1:3001}
enc() { python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$1"; }
cmd=${1:-status}
case "$cmd" in
  start)   [ $# -ge 2 ] || { echo "usage: run.sh start <scenario> [net] [note]" >&2; exit 2; }
           curl -sS -X POST "$SERVER/run/start?scenario=$(enc "$2")&net=$(enc "${3:-}")&note=$(enc "${4:-}")"; echo ;;
  stop)    curl -sS -X POST "$SERVER/run/stop"; echo ;;
  status)  curl -sS "$SERVER/run"; echo ;;
  mark)    curl -sS -X POST "$SERVER/run/mark?label=$(enc "${2:-}")"; echo ;;
  pause|resume|analyze|probe|reload)
           curl -sS -X POST "$SERVER/control?action=$cmd"; echo ;;
  reset)   curl -sS -X POST "$VISION/reset"; echo ;;
  replay)  curl -sS -X POST "$SERVER/replay?action=${2:-play}"; echo ;;
  series)  [ $# -ge 4 ] || { echo "usage: run.sh series <scenario> <runs> <secs> [net]" >&2; exit 2; }
           for i in $(seq 1 "$3"); do
             echo "run $i/$3: $2 for $4 s"
             "$0" start "$2" "${5:-}" "series $i/$3" >/dev/null
             sleep "$4"
             "$0" stop >/dev/null
             [ "$i" -lt "$3" ] && sleep 5
           done ;;
  *)       sed -n '2,10p' "$0"; exit 2 ;;
esac
