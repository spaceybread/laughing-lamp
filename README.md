# Glasses Camera Stream + Chess Assistant (INMO Air2)

A small website that shows the live camera feed from a pair of INMO Air2 glasses,
plus a chess assistant: the glasses photograph a chess.com board on a phone, the
Mac recognises the position and asks Stockfish for the best move, and the glasses
display a green dot on the piece to move and a red dot on its destination.

Using engine help in rated games against people breaks chess.com's fair-play
rules; this is for bots, puzzles and analysis.

How it works: the Air2 is a standalone Android 11 device, so a browser on the
glasses opens the **broadcast** page, which grabs the camera with `getUserMedia`
and sends it to a tiny Node relay on your laptop. The **viewer** page on your
laptop shows it, using WebRTC when it can and falling back to JPEG frames over
the WebSocket when it can't (e.g. glasses connected only over USB).

```
glasses (Chrome/Firefox) --/broadcast--> Node relay (laptop:3000) --/--> viewer (laptop browser)
```

## Files

| File | What it is |
|---|---|
| `server.js` | Express + ws relay: static pages, WebRTC signaling, JPEG frame fan-out |
| `public/index.html`, `public/viewer.js` | Viewer page (open on the laptop) |
| `public/broadcast.html`, `public/broadcast.js` | Broadcaster page (open on the glasses) |
| `glasses.sh` | adb helper: port-forward and launch the broadcast page on the glasses |

## Setup

```bash
npm install
npm start            # http://localhost:3000  (PORT=xxxx to change)
```

Open <http://localhost:3000/> on the laptop. It says "Waiting for the glasses…"
until the broadcaster connects.

## Getting the glasses onto the broadcast page

### 1. Enable developer mode / ADB on the Air2 (one-time)

From the [INMO guide](https://support.inmoxr.com/air2/guides/developer-mode/):
Settings → About → My Glasses, long-press the right touchpad for 1.5 s **twice**,
enter password `20210108`. ADB is now enabled. (INMO notes this voids the 7-day
no-questions return policy.)

`adb` is already installed on this Mac via Homebrew (`android-platform-tools`).

### 2. Connect the glasses by USB and run

```bash
./glasses.sh check   # confirms the device, Android version, and lists browsers on it
./glasses.sh         # adb reverse tcp:3000 + opens http://localhost:3000/broadcast?autostart=1 on the glasses
```

`adb reverse` makes `localhost:3000` on the glasses point at the laptop, which
matters for two reasons: no Wi-Fi is needed, and `localhost` counts as a secure
origin so the browser allows camera access without HTTPS.

The first time, the browser on the glasses shows a camera permission prompt.
Tap **Allow** on the touchpad once; it is remembered for the origin.

### 3. Watch

The viewer page picks the stream up automatically. If WebRTC can't connect
within 10 s (which happens when the glasses and laptop share no IP network,
i.e. USB-only), it switches itself to **Snapshot frames** mode. You can also
pick the mode from the dropdown.

## If the glasses have no browser

`./glasses.sh check` lists packages matching chrome/browser/firefox. If none
exist, sideload one over ADB. Firefox for Android is published as a plain APK by
Mozilla; Chromium builds are also available. Then:

```bash
adb install Firefox.apk
BROWSER=org.mozilla.firefox ./glasses.sh
```

## Over Wi-Fi instead of USB (no cable after setup)

ADB itself can run over Wi-Fi, and `adb reverse` works the same way, so the
glasses keep seeing the server as `http://localhost:3000` (a secure origin,
no certificates needed). With both devices on one LAN, WebRTC connects too,
which gives full-rate video instead of JPEG frames.

1. Join the glasses to the same Wi-Fi as the Mac (2.4 GHz, WPA2; the Air2
   doesn't do 5 GHz or WPA3). Use the INMOLens phone app or the glasses'
   Settings.
2. With the USB cable attached **once**, switch ADB to TCP and connect:

   ```bash
   ./glasses.sh wifi <glasses-ip>     # prints the IP it finds via `./glasses.sh check`
   export GLASSES_IP=<glasses-ip>
   ```

3. Unplug. From now on every `./glasses.sh` command connects over Wi-Fi:

   ```bash
   ./glasses.sh watch     # terminal 2
   ./glasses.sh           # opens the broadcast page on the glasses
   ```

The TCP ADB setting resets when the glasses reboot; redo step 2 then.
(Alternative without ADB at all: `npm run gen-cert` and open
`https://<laptop-ip>:3000/broadcast` on the glasses, but the stock browser may
refuse the self-signed certificate.)

## Broadcast page options (query string)

| Param | Default | Meaning |
|---|---|---|
| `autostart` | `1` | Start the camera on load; `0` requires tapping the button |
| `w`, `h` | `1280`, `720` | Requested capture size |
| `facing` | `environment` | `user` for a front camera |
| `fps`, `q`, `fw` | `10`, `0.6`, `640` | Frames-mode rate, JPEG quality, width |
| `fake` | – | `1` sends a moving test pattern instead of a camera (for testing the pipeline) |

Viewer: `?mode=frames` starts in frames mode.

## Testing without the glasses

Open <http://localhost:3000/broadcast?fake=1> in a second tab on the laptop, or
plain `/broadcast` to use the laptop webcam. The viewer should show it in both
modes.

## What the Air2 actually is (verified 2026-10-14)

- Android **9**, not 11. Stock AOSP browser (`com.android.browser`) backed by
  the system WebView, Chromium **74**. Keep the page JavaScript conservative:
  no numeric separators (`1_000_000`), no optional chaining (`?.`).
- The stock browser already holds the CAMERA permission and grants
  `getUserMedia` to the page without a prompt. No sideloading needed.
- The magnetic 3-pin charge cable **does** carry ADB data. The link drops now
  and then and `adb reverse` is lost each time, so run `./glasses.sh watch` in
  a second terminal to re-add it automatically.
- Over USB only (glasses not on Wi-Fi) WebRTC has no IP path, so the viewer
  falls back to JPEG frames after 10 s. That is the working configuration.
  Putting the glasses on Wi-Fi would enable the WebRTC path.
- Display is 640×400 (rendered at 1280×800). Camera delivers 1280×720 @30fps.

## Working run

```bash
npm start                 # terminal 1
./glasses.sh watch        # terminal 2: keeps the port-forward alive
./glasses.sh              # terminal 3: opens the broadcast page on the glasses
open http://localhost:3000/
```

To stop: close the browser tab on the glasses (back/home on the touchpad) or
just unplug; nothing is installed or changed on the device.

## Chess assistant

```
glasses (/broadcast)  --picture every 3 s-->  Node :3000  -->  Python vision :3001 (OpenCV + Stockfish)
        ^-------------- best move + dot positions ----------------------'
```

### Run (three terminals, plus the glasses step above)

```bash
npm start                                   # 1: relay + static site
.venv/bin/python vision/service.py          # 2: vision + engine service (first run: python3 -m venv .venv && .venv/bin/pip install -r vision/requirements.txt; brew install stockfish)
GLASSES_IP=<ip> ./glasses.sh                # 3: opens http://localhost:3000/broadcast on the glasses
```

Look at the phone with the board filling a good part of the view. Within a few
seconds the glasses show e.g. `e4  e2 → e4 · +0.31` with the dots. The laptop
viewer at http://localhost:3000 shows the same result, the recognised board
(labels; red label = low confidence) and the FEN, and has its own Analyze button.

### How it works

- `vision/board.py` finds the phone screen (bright region), warps it flat, and
  searches it exhaustively for an 8×8 grid: nine evenly spaced vertical and
  horizontal edge lines (integral-image comb search). It then refines the four
  corners by locating the real grid lines in the warped board, and checks that
  the squares alternate like a chess board.
- `vision/classify.py` recognises each square by nearest-neighbour matching
  against templates cut from frames of the **start position** (so they match
  this phone, theme and camera). Features are background-subtracted so a piece
  looks the same on light and dark squares. `POST /calibrate` with a start-position
  frame adds templates for a new phone/theme.
- `vision/engine.py` builds the FEN (side at the bottom of the screen = you,
  castling rights inferred from kings/rooks on home squares, your side to move)
  and runs Stockfish for 1 s with three principal variations.
- The Node server forwards pictures to the service and pushes every result to
  all connected pages over the WebSocket.

### Broadcast page parameters (assistant)

| Param | Default | Meaning |
|---|---|---|
| `assist` | `1` | `0` turns the picture loop off |
| `every` | `3000` | ms between pictures |
| `side` | `auto` | `white` / `black` if the auto guess (more of your pieces in the bottom half) is wrong |
| `stream` | `1` | `0` stops the live video to the laptop; only pictures and results travel |

### Known limits

- It always assumes it is your move; while the opponent thinks, the suggestion is
  simply what you would play if it were your turn.
- Templates were cut from the brown board theme and the default pieces at this
  phone size. A different theme or piece set needs `POST /calibrate` with a
  start-position frame (see `vision/service.py`).
- Phone lock screen, no board in view, or the board too small (< ~250 px wide in
  the 1280×720 frame) gives "board not found".
