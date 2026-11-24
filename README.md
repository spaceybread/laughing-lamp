# Chess Assistant on INMO Air2 Smart Glasses

CS 536 bonus project. The glasses stream their camera to a laptop. The laptop reads the board (OpenCV), gets the best move from Stockfish, and shows it on the glasses.

```
glasses / phone --video + picture every 3 s--> relay (Node, :3000) --> vision + Stockfish (Python, :3001)
       ^------------------------------- best move ---------------------------------'
```

Glasses are optional: a phone can be the camera.

For bots, puzzles and analysis only. Engine help in rated games against people breaks chess.com's fair-play rules.

## Requirements

- macOS with [Homebrew](https://brew.sh). Tested on Apple silicon.
- Linux: install the tools by hand.

| Tool | Needed for |
|---|---|
| Node.js 18+, Python 3.9+, Stockfish | everything |
| `cloudflared` | public HTTPS link (phone or glasses on another network) |
| `adb` (`android-platform-tools`) | the glasses |

## Quick start

1. Install (once, about 1 min):

   ```bash
   git clone https://github.com/spaceybread/laughing-lamp.git
   cd laughing-lamp
   ./setup.sh
   ```

   `setup.sh` does three things:
   - installs missing tools with Homebrew;
   - installs `node_modules/` and `.venv/` inside the folder;
   - runs a self-test.

   Expected last lines:

   ```
       position rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR, best move e4 (+0.26)
   ==> setup complete. Next: ./demo.sh   (see README.md)
   ```

2. Start:

   ```bash
   ./demo.sh --replay
   ```

   This starts the vision service, the relay and a public HTTPS link (Cloudflare quick tunnel), then opens:
   - the viewer;
   - the replay board, a game that plays itself at one move per 10 s.

   It also prints the links and a QR code.

3. Point a camera at the replay board:

   | Camera | How |
   |---|---|
   | Phone | Scan the QR code. Allow the camera. |
   | Laptop webcam | Open <http://localhost:3000/broadcast>. Needs the board on another screen. |
   | INMO Air2 | See [Glasses](#glasses). |

   Keep the board large in the picture. The camera page shows the best move: a green dot on the piece, a red dot on the target square. The viewer shows the video, the recognised board, the FEN and the engine lines.

4. Stop: **Ctrl-C**.

`demo.sh` options:

| Option | Effect |
|---|---|
| `--replay` | also open the replay board |
| `--local` | no tunnel and no internet needed. Camera = laptop webcam, or glasses on USB. |
| `--no-glasses` | do not touch ADB devices |
| `--help` | usage |

Logs: `logs/`.

## Glasses

One-time setup:
1. Enable ADB on the glasses. See [Developer mode](#developer-mode).
2. Save the Wi-Fi the glasses should use:

   ```bash
   cp demo.env.example demo.env      # set HOTSPOT_SSID, HOTSPOT_PASSWORD (git-ignored)
   ```

Each time:
1. Turn on the phone hotspot. On an iPhone, turn on **Maximize Compatibility**: that is the tested setting. The laptop needs internet from any network.
2. Connect the glasses to the laptop with the magnetic USB cable.
3. Run `./demo.sh`. In about 15 s it:
   - puts the glasses on the hotspot;
   - opens the HUD;
   - accepts the camera prompt;
   - waits for the glasses to connect.
4. When it prints `You can unplug the USB cable now`, unplug the glasses and wear them.
5. Optional: scan the QR code with a phone to mirror the glasses' display.
6. Ctrl-C stops everything. If the glasses are still plugged in, it also closes their browser.

Glasses and laptop may be on different networks:
- **Pictures:** go over the HTTPS link.
- **Video:** goes over WebRTC, direct via STUN when possible.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `not set up yet` | Run `./setup.sh`. |
| `tunnel did not come up ... falling back to --local` | No internet, or Cloudflare blocked. Use the webcam, or the glasses on USB. |
| `could not join '<ssid>'` | Check the hotspot is on (iPhone: Maximize Compatibility). Or join on the glasses by hand and rerun. |
| Video but no move | Move closer: the board must fill much of the view. "opponent to move" = waiting for the other side. |
| Viewer in "JPEG frames" mode | WebRTC fallback. On macOS use Safari: Chrome blocks WebRTC to LAN devices. |
| `port 3000 is used by another program` | `PORT=4000 VISION_PORT=4001 ./demo.sh` |

## Repository layout

```
setup.sh                  install + self-test
demo.sh                   start everything; --help for options
demo.env.example          template for demo.env (hotspot credentials, git-ignored)
server.js                 relay: pages, WebRTC signalling, JPEG fallback, /analyze proxy, access key, traces
public/
  index.html, viewer.js     laptop viewer
  hud.html                  glasses HUD (?mirror=1: same display on another device)
  broadcast.html/.js        camera page: capture, upload, picture loop, telemetry
  replay.html/.js           replay board
vision/                   Python service (Flask, :3001)
  service.py                HTTP API, game tracking, engine cache
  board.py                  board detection and warp
  classify.py               piece recognition
  engine.py                 FEN, side to move, Stockfish
  samples/                  test photos
  templates/calib/          recognition model (templates.npz)
glasses/                  ADB helpers
  glasses.sh                open page, accept camera prompt, close
  glasses_wifi.py           join Wi-Fi via the Settings UI
Bonus-PartC/              field measurements (see its README)
  run.sh                    run controller
  analyze.py, carve.py      trace analysis; cut a run from the idle log
  pgn2json.py, pgn/         replay-board games -> pgn/games.json
  results/                  summary table and figures
  traces/                   raw logs (git-ignored)
```

---

# Reference

## Pages

| URL | Device | Content |
|---|---|---|
| `/` | laptop | live video, recognised board (red label = low confidence), FEN, engine lines, Analyze button, field-run panel |
| `/hud` | glasses | video with move arrow (left 2/3); move and status (right 1/3) |
| `/broadcast` | glasses, phone | full-screen camera preview with dots |
| `/hud?mirror=1` | phone, laptop | copy of the glasses' display |
| `/replay` | laptop | replay board. `?autoplay=1`, `?every=<ms>`. Keys: space play/pause, → next, r restart. |

Test the HUD without glasses:
1. Open `http://localhost:3000/hud?fake=1&assist=0`.
2. Send it a photo:

   ```bash
   curl -X POST --data-binary @vision/samples/board_1.jpg localhost:3000/analyze
   ```

## Page parameters

Parameters for `/broadcast` and `/hud`:

| Param | Default | Meaning |
|---|---|---|
| `autostart` | `1` | start the camera on load (`0`: wait for a tap) |
| `w`, `h` | `1280`, `720` | capture size |
| `facing` | `environment` | `user` = front camera |
| `fps`, `q`, `fw` | `10`, `0.6`, `640` | JPEG-frames mode: rate, quality, width |
| `fake` | – | `1` = test pattern instead of the camera |
| `assist` | `1` | `0` = no picture loop |
| `every` | `3000` | ms between pictures |
| `side` | `auto` | `white` / `black` to override the side guess |
| `stream` | `1` | `0` = no live video; pictures and results only |
| `track` | `1` | `0` = no game tracking (Part D baseline) |
| `stats` | `2000` | ms between telemetry reports |

Viewer: `?mode=frames` starts in JPEG-frames mode.

## How it works

**Vision**
- **`board.py`:** finds the bright screen region, warps it flat, then finds the 8×8 grid with an exhaustive comb search over edge lines. It refines the corners and checks the square colours alternate.
- **`classify.py`:** nearest-neighbour match of each square against templates cut from start-position photos. Background subtraction makes a piece look the same on light and dark squares.
- **`engine.py`:** builds the FEN (bottom side = you; castling rights from the king and rook squares) and runs Stockfish for 1 s with 3 lines.

**Game tracking**
- Each picture is matched against the tracked position and all its 1- and 2-ply continuations. The best match is accepted at ≥58/64 squares.
- This tolerates misread squares and gives the side to move ("opponent to move").
- A reading near the start position snaps to it.
- No match: the raw reading is adopted once it is stable and legal, with ≤3 repaired squares.
- `POST /reset` forgets the game. `?cache=1` also clears the engine cache.

**Networking** (`demo.sh`)
- **Tunnel:** a Cloudflare quick tunnel gives HTTPS, so the camera works without certificates or `adb reverse`.
- **Access key:** tunnelled requests need `?k=<key>` once; it is then kept in a cookie. Requests from the laptop itself need no key. The key is random on every start.
- **Video:** WebRTC uses STUN (`ICE_SERVERS`, default Google's server). If it fails, the viewer falls back to JPEG frames after 10 s.
- **Pictures and results:** always go over HTTPS/WebSocket.
- **`--local`:** `adb reverse` maps the glasses' `localhost:3000` to the laptop. `localhost` is a secure origin, so the camera works.

## Manual run

```bash
.venv/bin/python vision/service.py      # vision + Stockfish, :3001
npm start                               # relay, :3000 (no tunnel, no access key)
glasses/glasses.sh                      # USB: adb reverse, open the HUD, accept the camera prompt
open http://localhost:3000/             # viewer (Safari on macOS)
```

| Command | Effect |
|---|---|
| `glasses/glasses.sh check` | device, Android version, browsers, cameras |
| `glasses/glasses.sh watch` | re-add the port-forward on every reconnect |
| `glasses/glasses.sh close` | close the browser, free the camera |
| `glasses/glasses_wifi.py "<ssid>" [--adb-wifi]` | join Wi-Fi (prompts for the password). `--adb-wifi` switches ADB to Wi-Fi and prints `GLASSES_IP`. |
| `PAGE=broadcast glasses/glasses.sh` | open `/broadcast` instead of `/hud` |
| `BROWSER=<package> glasses/glasses.sh` | use another browser, e.g. a sideloaded `org.mozilla.firefox` |

ADB over Wi-Fi resets when the glasses reboot.

## Developer mode

Enable it once, from the [INMO guide](https://support.inmoxr.com/air2/guides/developer-mode/):
1. Open Settings → About → My Glasses.
2. Long-press the right touchpad for 1.5 s, twice.
3. Enter `20210108`.

INMO notes this voids the 7-day return policy.

## Field runs (Part C)

| Action | How |
|---|---|
| Control the glasses page | `POST /control?action=pause\|resume\|analyze\|probe\|reload` (or `Bonus-PartC/run.sh`) |
| Start a run | `POST /run/start?scenario=...&net=...` |
| Stop a run | `POST /run/stop` |
| Output | `Bonus-PartC/traces/<timestamp>_<scenario>/` |
| Analysis | `Bonus-PartC/analyze.py` → `Bonus-PartC/results/` |
| Ground truth | replay board positions (logged), or a PGN |

Procedure, scenarios and metrics: [`Bonus-PartC/README.md`](Bonus-PartC/README.md).

## INMO Air2 notes

| Item | Detail |
|---|---|
| OS / browser | Android 9; stock browser `com.android.browser` on WebView (Chromium 74). No `?.` or numeric separators in page JS. |
| Camera prompt | shown on every page load; `glasses/glasses.sh` accepts it from a UI dump |
| Cable | the magnetic 3-pin cable carries ADB; the link drops occasionally (`glasses.sh watch`) |
| Wi-Fi | joined 2.4 and 5 GHz WPA2 networks |
| Display / camera | 640×400 (rendered at 1280×800) / 1280×720 at 30 fps |
| Battery | 1.3–1.6 %/min with camera, 2 WebRTC streams and Wi-Fi, even on laptop USB. Charge from a wall adapter. |

## Known limits

- **New board theme or piece set:** needs one `POST /calibrate` with a start-position photo, your side at the bottom. Calibrated so far: chess.com brown (phone), chess.com green (monitor), the replay board.
- **Joining a game mid-way:** depends on a legal raw reading. A systematic misread (e.g. a king read as the wrong colour) can be adopted.
- **"Board not found":** lock screen, no board in view, or a board narrower than about 250 px in a 1280×720 picture.
- **Chrome on macOS:** blocks LAN WebRTC. Use Safari for the viewer.
