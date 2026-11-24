# Chess Assistant on INMO Air2 Smart Glasses (CS 536 bonus project)

The INMO Air2 glasses stream their camera to a laptop. The laptop finds the chess board in
the picture (OpenCV), reads the position, asks Stockfish for the best move and sends it back to
the glasses' display: the move in large type and an arrow from the piece to its square. A viewer
page on the laptop shows the live video and the same result.

```
glasses or phone (browser) --video + a picture every 3 s--> Node relay (laptop :3000) --> Python vision + Stockfish (:3001)
           ^------------------------------------- best move -----------------------------------------'
```

**You don't need the glasses to try it:** any phone can be the camera (step 3 below).

Using engine help in rated games against people breaks chess.com's fair-play rules; this is
meant for bots, puzzles and analysis.

## Quick start (step by step)

Tested on macOS (Apple silicon) with [Homebrew](https://brew.sh). Linux works too once the
tools listed in step 1 are installed by hand.

### 1. Get the code and install (once, about a minute)

```bash
git clone https://github.com/spaceybread/laughing-lamp.git
cd laughing-lamp
./setup.sh
```

`setup.sh`:
- installs whatever is missing with Homebrew: `node`, `python`, `stockfish`, `cloudflared`, `android-platform-tools`;
- installs the Node packages (`node_modules/`) and a Python virtual environment (`.venv/`), both inside this folder;
- checks that the vision service reads a sample board photo and that Stockfish answers.

It should end with:

```
    position rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR, best move e4 (+0.26)
==> setup complete. Next: ./demo.sh   (see README.md)
```

Without Homebrew it lists what to install:
- **Required:** Node.js 18+, Python 3.9+, Stockfish.
- **Optional:** `cloudflared` for the public link; `adb`, only for the glasses.

### 2. Start everything

```bash
./demo.sh --replay
```

Within about 10 seconds this:
- starts the vision service and the relay;
- opens a public HTTPS link to the relay (a free Cloudflare quick tunnel, no account needed);
- opens the laptop viewer (in Safari on macOS);
- with `--replay`, also opens the **replay board**, a chess game that plays itself at one move every 10 s.

It then prints the links and a QR code.

### 3. Point a camera at the board

- **A phone (easiest).**
  1. Scan the QR code in the terminal with the phone and allow the camera.
  2. Point the phone at the replay board on the laptop screen, with the board filling a good part of the picture.
  3. Within a few seconds the phone shows the best move with a green dot on the piece and a red dot on its target square. The laptop viewer shows the live video, the recognised board, the FEN and Stockfish's top lines.
- **The laptop webcam.** Open <http://localhost:3000/broadcast>. This needs a board on another screen.
- **The INMO Air2 glasses.** See the next section.

Press **Ctrl-C** in the terminal to stop everything.

Notes:
- **Which boards work:** a chess.com board in the green web theme or the brown phone theme works as well as the replay board. Other themes need one calibration picture (see *Known limits*).
- **Whose move:** the side at the bottom of the picture is "you". When it is the other side's turn the hint reads "opponent to move".
- **No internet:** run `./demo.sh --local`. There is no public link, so the camera must be this laptop's webcam or the glasses on their USB cable.
- **More options:** `./demo.sh --help`. Logs go to `logs/`.

## Running it with the INMO Air2 glasses

**One-time setup:**
1. Enable ADB on the glasses (developer mode, see [below](#enable-developer-mode--adb-on-the-air2-one-time)).
2. Save the Wi-Fi the glasses should use. With a phone hotspot, the glasses don't depend on the venue's Wi-Fi.

   ```bash
   cp demo.env.example demo.env     # then fill in HOTSPOT_SSID and HOTSPOT_PASSWORD (demo.env is git-ignored)
   ```

**Each time:**
1. Turn on the phone's hotspot.
   - On an iPhone: Settings → Personal Hotspot, and switch on **Maximize Compatibility** (2.4 GHz). That is the setting we tested with.
   - The laptop needs internet: the hotspot or any other Wi-Fi.
2. Plug the glasses into the laptop with their magnetic USB cable.
3. Run `./demo.sh`. In about 15 s it:
   - puts the glasses on the hotspot if they aren't on it;
   - opens the HUD on them through the HTTPS link;
   - accepts the camera prompt;
   - waits until the glasses report in, then prints `glasses connected` and `You can unplug the USB cable now`.
4. Unplug the glasses, put them on and look at the board. To show the audience what the wearer sees, scan the QR code with a phone: it opens a live mirror of the glasses' display.

**Ctrl-C** stops everything, and closes the browser on the glasses if they are still plugged in.

The glasses and the laptop don't need to be on the same network:
- **Pictures and results** go through the HTTPS link.
- **Live video** goes directly between devices over WebRTC when possible, using a STUN server to find a path.

### Troubleshooting

| Symptom | What to do |
|---|---|
| `not set up yet` | Run `./setup.sh`. |
| `the tunnel did not come up ... falling back to --local` | No internet, or Cloudflare is blocked on this network. Local mode still works with the webcam, or with the glasses on USB. |
| `could not join '<ssid>'` | Check the hotspot is on, and that Maximize Compatibility is on for an iPhone. Or join the network on the glasses by hand and run `./demo.sh` again. |
| The glasses show video but no move | Bring the board closer: it should fill a good part of the view. "opponent to move" means it is waiting for the other side. |
| The viewer says "JPEG frames" instead of WebRTC | This is the automatic fallback when WebRTC can't get through. On macOS, use Safari for the viewer: Chrome blocks WebRTC to devices on the local network. |
| `port 3000 is used by another program` | Run `PORT=4000 VISION_PORT=4001 ./demo.sh`. |

## Repository layout

```
setup.sh                  one-time install + self-test
demo.sh                   start everything (vision, relay, tunnel, glasses, viewer); --help for options
demo.env.example          template for demo.env (hotspot name/password; demo.env itself is git-ignored)
server.js                 Node relay: pages, WebRTC signalling, JPEG fallback, /analyze proxy, access key, traces
public/                   web pages served by the relay
  index.html, viewer.js     laptop viewer (live video, recognised board, FEN, engine lines, field-run panel)
  hud.html                  glasses heads-up display (also ?mirror=1: the same display on a phone or laptop)
  broadcast.html/.js        camera page: getUserMedia, WebRTC/JPEG upload, picture loop, telemetry
  replay.html/.js           replay board: plays Bonus-PartC/pgn/games.json one move at a time
vision/                   Python service (Flask, :3001)
  service.py                HTTP API, game tracking, engine cache
  board.py                  finds the board in a photo and warps it flat
  classify.py               recognises the piece on each square
  engine.py                 FEN, side to move, Stockfish
  samples/                  test photos (setup.sh's self-test uses board_1.jpg)
  templates/calib/          the recognition model (templates.npz)
glasses/                  ADB helpers for the INMO Air2
  glasses.sh                open the page on the glasses, accept the camera prompt, close it
  glasses_wifi.py           join a Wi-Fi network by driving the glasses' Settings over ADB
Bonus-PartC/              field measurements (Part C), see Bonus-PartC/README.md
  run.sh                    run controller (start/stop runs, series, replay board control)
  analyze.py, carve.py      trace analysis -> results/; cut a run out of the idle log
  pgn2json.py, pgn/         replay-board games (two real games + three Stockfish games) -> pgn/games.json
  results/                  summary table and figures from the field runs
  traces/                   raw logs, one folder per run (git-ignored, kept locally)
```

---

The rest of this file is the detailed reference: how the pieces work and how to run them by hand.

## How it works

```
glasses (/hud or /broadcast) --picture every 3 s--> Node :3000 --> Python vision :3001 (OpenCV + Stockfish)
        ^------------------- best move + arrow/dot positions -------------------'
        '--live video (WebRTC, or JPEG frames over the WebSocket)--> laptop viewer (/)
```

### Pages

| URL | Where | What |
|---|---|---|
| `/` | laptop | live video, the recognised board (red label = low confidence), FEN, engine lines, an Analyze button and the field-run panel |
| `/hud` | glasses | heads-up display: video with a yellow move arrow (left 2/3), the move in large type and the model status (right 1/3). `demo.sh` and `glasses/glasses.sh` open this. |
| `/broadcast` | glasses / phone | plain full-screen camera preview with green/red dots (`PAGE=broadcast glasses/glasses.sh`) |
| `/hud?mirror=1` | phone / laptop | the glasses' display, fed by their live video and results: what the wearer sees |
| `/replay` | laptop | replay board (`?autoplay=1` starts it, `?every=10000` ms per move; space = play/pause, → = next move, r = restart) |

Preview the HUD without glasses: open `http://localhost:3000/hud?fake=1&assist=0`, then push a
photo: `curl -X POST --data-binary @vision/samples/board_1.jpg localhost:3000/analyze`.

### Vision

- `vision/board.py` finds the screen showing the board (bright region), warps it flat, and
  searches it exhaustively for an 8×8 grid: nine evenly spaced vertical and horizontal edge
  lines (integral-image comb search). It then refines the four corners on the real grid lines
  and checks that the squares alternate like a chess board.
- `vision/classify.py` recognises each square by nearest-neighbour matching against templates
  cut from photos of the **start position**, so they match the screen, theme and camera.
  Features are background-subtracted, so a piece looks the same on light and dark squares.
- `vision/engine.py` builds the FEN and runs Stockfish for 1 s with three principal variations.
  The side at the bottom of the picture is you. Castling rights are inferred from kings and
  rooks on their home squares.
- The relay forwards pictures to the service and pushes every result to all connected pages
  over the WebSocket.

### Game tracking (why it's not just vision)

Only legal moves can happen between pictures, so the service tracks the game:
- **Matching:** each picture is compared with the tracked position and all its one- and two-ply continuations, by agreement over the 64 squares. The best match wins if it agrees on 58 or more squares.
- **What that buys:** a few misread squares don't matter, and the service knows whose turn it is. The glasses say "opponent to move" instead of guessing.
- **Start position:** a reading close to it snaps to it exactly.
- **No match** (a new game, or lost track): the raw reading is adopted once it is stable, legal, and needed at most three repaired squares.

`POST /reset` forgets the game (`?cache=1` also clears the engine cache).

### Networking

`demo.sh` runs the relay behind a free Cloudflare quick tunnel (`https://<random>.trycloudflare.com`):
- **No certificates:** HTTPS comes from the tunnel, so the glasses' browser allows the camera without `adb reverse` or self-signed certificates.
- **Access key:** requests from the laptop itself never need one. Anything through the tunnel does, given once as `?k=<key>` and then kept in a cookie. The key is random on every start. Without `ACCESS_KEY` the relay refuses all tunnelled requests.
- **Live video:** WebRTC tries a direct path, with STUN (`ICE_SERVERS`, default Google's public server) for different networks. When that fails, as behind some carrier NATs, the viewer falls back to JPEG frames over the WebSocket after 10 s.
- **Pictures for the assistant** always go over the tunnel's HTTPS/WebSocket.
- **Traces** record `via: "tunnel"` and the client's real IP for each connection.

In `--local` mode there is no tunnel. `adb reverse` makes `localhost:3000` on the glasses point
at the laptop over the USB cable, and `localhost` counts as a secure origin, so the camera
still works.

## Running the pieces by hand

```bash
.venv/bin/python vision/service.py      # 1: vision + Stockfish service on :3001
npm start                               # 2: relay on :3000 (LAN/USB only; demo.sh adds the tunnel and access key)
glasses/glasses.sh                      # 3: USB: adb reverse + open the HUD on the glasses, accept the camera prompt
open http://localhost:3000/             # viewer (Safari on macOS)
```

- `glasses/glasses.sh check`: device, Android version, browsers and cameras on the glasses.
- `glasses/glasses.sh watch`: re-adds the port-forward whenever the cable reconnects.
- `glasses/glasses.sh close`: closes the browser on the glasses, which frees the camera.

### Enable developer mode / ADB on the Air2 (one-time)

From the [INMO guide](https://support.inmoxr.com/air2/guides/developer-mode/):
1. Settings → About → My Glasses.
2. Long-press the right touchpad for 1.5 s, **twice**.
3. Enter the password `20210108`.

ADB is now enabled. INMO notes this voids the 7-day no-questions return policy.

### Wi-Fi without a tunnel (ADB over Wi-Fi)

With the cable plugged in:

```bash
glasses/glasses_wifi.py "Your Network" --adb-wifi
```

This drives the glasses' Wi-Fi settings over ADB: it finds the network, types the password
(it prompts for it, so it stays out of your shell history) and taps Connect. It then switches
ADB to Wi-Fi and prints an `export GLASSES_IP=...` line. After that, every `glasses/glasses.sh`
command works over Wi-Fi.

`adb reverse` works the same way over Wi-Fi, so the glasses still see the relay as `localhost`.
With both devices on one LAN, WebRTC connects directly. The TCP ADB setting resets when the
glasses reboot.

### If the glasses have no browser

`glasses/glasses.sh check` lists packages matching chrome/browser/firefox. If there are none,
sideload one (`adb install Firefox.apk`) and run `BROWSER=org.mozilla.firefox glasses/glasses.sh`.

## Page parameters (query string)

`/broadcast` and `/hud`:

| Param | Default | Meaning |
|---|---|---|
| `autostart` | `1` | start the camera on load; `0` waits for a tap |
| `w`, `h` | `1280`, `720` | requested capture size |
| `facing` | `environment` | `user` for a front camera |
| `fps`, `q`, `fw` | `10`, `0.6`, `640` | JPEG-frames mode: rate, quality, width |
| `fake` | – | `1` sends a moving test pattern instead of the camera |
| `assist` | `1` | `0` turns the picture loop off |
| `every` | `3000` | ms between pictures |
| `side` | `auto` | `white` / `black` if the guess (more of your pieces in the bottom half) is wrong |
| `stream` | `1` | `0` stops the live video; only pictures and results travel |
| `track` | `1` | `0` = raw per-picture reading without game tracking (the Part D baseline) |
| `stats` | `2000` | ms between telemetry reports to the relay (Part C) |

Viewer: `?mode=frames` starts in JPEG-frames mode.

## Runtime control and field runs (Part C)

- **Runtime control:** the laptop viewer, or `Bonus-PartC/run.sh`, can control the glasses page without touching the glasses: `POST /control?action=pause|resume|analyze|probe|reload`.
- **Recording a run:** `POST /run/start?scenario=...&net=...` makes the relay write every event, timing and analysed picture to `Bonus-PartC/traces/<timestamp>_<scenario>/` until `POST /run/stop`.
- **Analysis:** `Bonus-PartC/analyze.py` turns the traces into the summary table and figures in `Bonus-PartC/results/`.
- **Scoring:** the replay board logs every position it shows, so each picture can be scored against what was actually on screen.

See `Bonus-PartC/README.md` for the procedure, the scenarios and the metric definitions.

## Notes on the INMO Air2 (verified on the device)

- **Software:** Android **9**, with the stock AOSP browser (`com.android.browser`) on the system WebView, Chromium **74**. Keep page JavaScript conservative: no optional chaining (`?.`), no numeric separators.
- **Camera prompt:** the browser asks for the camera on every page load. `glasses/glasses.sh` accepts it for our page by tapping the Allow button found in a UI dump.
- **Cable:** the magnetic 3-pin charging cable carries ADB data. The link drops now and then, which is what `glasses/glasses.sh watch` is for.
- **Wi-Fi:** joined 2.4 GHz and 5 GHz WPA2 networks in our tests. For an iPhone hotspot we used Maximize Compatibility (2.4 GHz).
- **Display and camera:** the display is 640×400 (rendered at 1280×800). The camera delivers 1280×720 at 30 fps.
- **Battery:** with the camera, two WebRTC streams and Wi-Fi, it drains about 1.3–1.6 %/min even on laptop USB. Charge from a wall adapter before a demo.

## Known limits

- A new board theme or piece set needs one `POST /calibrate` with a photo of the start position (your side at the bottom). Calibrated so far: the chess.com brown phone theme, the green web theme on a monitor, and the replay board.
- **Picking up a game mid-way:** the tracker never saw the start, so it relies on the raw reading being legal. Blurry, tilted or hand-covered pictures may never settle, and a systematic misread (e.g. a king read as the wrong colour) can be adopted.
- "Board not found" means a lock screen, no board in view, or a board that is too small (under about 250 px wide in the 1280×720 picture).
- On macOS, Chrome blocks WebRTC to devices on the local network; use Safari for the viewer.
