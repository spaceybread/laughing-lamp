# Glasses Camera Stream (INMO Air2)

A small website that shows the live camera feed from a pair of INMO Air2 glasses.

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
