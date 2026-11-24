# Bonus-PartC: performance evaluation in the wild

Everything the system does during a field run is written to `Bonus-PartC/traces/<run>/` by the
relay (`server.js`). This folder holds the run controller, the scripts that turn those traces
into the tables and figures for the report, and the games shown on the replay board.

```
Bonus-PartC/
  run.sh            run controller (talks to the relay)
  analyze.py        traces -> results/summary.csv, summary.md and figures
  carve.py          cut a time window out of traces/_idle/ into its own run
  pgn2json.py       pgn/*.pgn -> pgn/games.json (replay-board playlist); --synth N makes Stockfish games
  pgn/              ground-truth games: two chess.com bot games + three seeded Stockfish games
  results/          the analysis of the recorded scenarios (committed)
  traces/           raw logs, one folder per run (git-ignored, kept locally)

traces/20261015-183622_home-wifi/
  events.jsonl      one JSON object per line: {"t": <relay clock, ms>, "ev": <type>, ...}
  frames/a00017.jpg every picture that went to the vision service (named by analyze req id)
  game.pgn          (optional, add by hand) chess.com PGN of the game played during the run
```

## Running a field trial

1. Start everything with `./demo.sh --replay` (see the main README). The laptop viewer opens
   at `http://localhost:3000/` and the replay board in a second window.
2. Start a run, either in the viewer's **Field run** panel (scenario name + network, *Start run*)
   or from a shell:
   ```sh
   ./Bonus-PartC/run.sh start home-wifi wifi "kitchen table, phone app"
   ./Bonus-PartC/run.sh mark "walked to the hallway"      # optional markers
   ./Bonus-PartC/run.sh stop
   ./Bonus-PartC/run.sh series pal3-lab 10 60 pal3         # 10 runs of 60 s back to back
   ```
   Each run should last 30 s to 5 min; the spec wants 10+ runs per scenario and more than an
   hour in total. Scenario names become the groups in every figure, so keep them consistent
   (`home-wifi`, `pal3-lab`, `5g-hotspot`, `usb`...).
3. Point the glasses at the replay board (`./Bonus-PartC/run.sh replay play|pause`), which plays
   `pgn/games.json` one move every 10 s and logs every position it shows, so each picture is
   scored against what was really on screen. Alternatively play a chess.com bot and save the
   game's PGN as `traces/<run>/game.pgn` afterwards.
4. Analyse:
   ```sh
   .venv/bin/python Bonus-PartC/analyze.py                 # all runs -> Bonus-PartC/results/
   .venv/bin/python Bonus-PartC/analyze.py Bonus-PartC/traces/*_home-home   # a subset
   ```
   Output: `results/summary.csv` (one row per run, every metric), `results/summary.md`
   (pooled per scenario) and `latency_cdf.png`, `throughput.png`, `streaming.png`,
   `pipeline.png`, `kpi.png`, `part_d.png` (when runs have ground truth: replay board or PGN).

A bandwidth probe (2 MB down + 2 MB up over the same path as the stream) runs automatically on the
glasses and on the laptop at the start of every run; *Probe bandwidth* / `run.sh probe` repeats it.
*Pause glasses* / `run.sh pause|resume` stops and restarts streaming and the assist loop on the
glasses without touching them (Part B runtime control).

Raw traces stay out of git (`Bonus-PartC/traces/` is ignored); keep them, the course may ask for them.

## Recorded scenarios (November 2026)

The committed `results/` come from these 35 runs of 85 s (49.6 min), with the HUD on the glasses,
the laptop viewer, and a phone mirroring the HUD (`/hud?mirror=1`, a second WebRTC stream from the
glasses) open in every run:

```sh
.venv/bin/python Bonus-PartC/analyze.py Bonus-PartC/traces/*_5g-eduroam-tunnel \
    Bonus-PartC/traces/*_home-home Bonus-PartC/traces/*_5g-glasses-home-laptop
```

| scenario | runs | glasses | laptop | path | board |
|---|---|---|---|---|---|
| `5g-eduroam-tunnel` | 10 | phone 5G hotspot | campus Wi-Fi (eduroam) | Cloudflare tunnel + STUN | live chess.com game (no PGN) |
| `home-home` | 15 | home Wi-Fi | same home Wi-Fi | LAN (`adb reverse` over Wi-Fi) | replay board |
| `5g-glasses-home-laptop` | 10 | phone 5G hotspot | home Wi-Fi | Cloudflare tunnel + STUN | replay board |

Caveats:
- **Laptop viewer browser:** Chrome on campus, Safari at home (Chrome blocks LAN WebRTC to the glasses). The campus viewer tab was in the background, so `fps_viewer`/`stalls` are not valid for `5g-eduroam-tunnel`; use the WebRTC `rtc_*` columns.
- **`5g-eduroam-tunnel`:** run 1 includes phone-mirror reconnects.
- **`5g-glasses-home-laptop`:** run 6 (`20261124-051242`) is a tracker cold start after a mid-game `/reset`, where the raw reading saw the white king as black; it is not a network effect.
- **Resolution:** with only one viewer the glasses send 1280×720; with the phone mirror open they drop to 320–640 px wide.
- **Excluded:** runs whose names end in `-aborted-*` (glasses battery died, viewer stuck in JPEG mode) and the `check-*` setup checks.
- **`part_d.png` (Part D):** raw vs tracked reading against the replay board, pooled over every scored picture: home-home raw 0.65 / tracked 1.00 (n=351), 5G glasses raw 0.58 / tracked 0.86 (n=214). The 5G tracker figure is pulled down by runs 6–7, the cold start above; without them it is 167/168. The campus scenario has no ground truth (live game, no PGN).

## What is measured and how

**Clocks.** The relay pings every client every 2 s (`{"type":"ping","ts"}`); the client answers with
its own clock. That gives the WebSocket RTT (`rtt` events) and the client-minus-relay clock offset,
so timestamps taken on the glasses can be compared with timestamps taken on the laptop. The offset
used is the median over the lowest-RTT quarter of the samples.

**Streaming (Part A).**
- Every JPEG frame from the glasses carries a 16-byte header (sequence number + capture time);
  the relay logs it (`frame`) and the viewer reports when it was shown (`stats.shown`). From these:
  one-way latency glasses→relay and glasses→laptop, inter-frame jitter, frames lost before the relay,
  frames dropped by the relay under back-pressure (`relay` events).
- In WebRTC mode both ends report `getStats()` every 2 s (`stats.peers` on the glasses, `stats.rtc`
  on the viewer): bytes, packets lost, RTP jitter, RTT of the selected candidate pair, encoder
  limitation reason, decoded fps, freezes.
- Throughput: uplink = everything leaving the glasses (frames + pictures + WebRTC bytesSent),
  downlink = everything the laptop viewer receives. Plus the probe's raw numbers.
- Stalls: the viewer counts every gap > 500 ms between displayed frames (count and total time).
- Failures: glasses WebSocket disconnects, camera track lost, WebRTC `failed`, fallback to JPEG
  frames, analysis requests that errored or timed out.

**Chess assistant KPIs (Part B).** Each picture is an `analyze` event with the vision service's
stage timings (decode, detect, classify, match, engine), its decision (`state`:
`nodetect` / `badgrid` / `unreadable` / `unclear` / `confirming` / `start` / `tracked` / `adopted`),
whose turn it is, whether the engine result was cached, and the *raw* reading next to the position
finally used. The glasses report, per picture, when it was taken, sent, answered and drawn.
- board-detection rate, readable-position rate, hint rate (fraction of pictures)
- capture→hint latency on the glasses' own clock (no clock sync needed)
- time since the last good read (what the HUD shows), sampled every 2 s
- tracker losses (continuation match below threshold), engine cache hit rate
- raw-vs-tracked: how often the raw reading was already exactly the accepted position, how many
  squares it got wrong, and with a PGN, how often each one matched a real position of the game.
  This is the before/after evidence for the Part D enhancement (game tracking); `?track=0` on the
  glasses page runs the raw baseline live.

## Event types in `events.jsonl`

| ev | from | fields |
|---|---|---|
| `run_start`, `run_stop`, `mark`, `control` | relay | run id, scenario, net, note, connected clients; duration + counters at stop |
| `ws_open`, `ws_close` | relay | role, id, ip, user agent, page; close code, duration, frames sent/dropped |
| `camera_ready`, `want_stream`, `signal` | relay | camera settings; viewer mode; offer/answer routing |
| `frame` | relay | seq, cap (glasses clock), bytes, viewers it was sent to, glasses send buffer |
| `analyze` | relay | req, src, bytes, cap, seq, saved frame, status, vision_ms, total_ms, ok, state, lost, side, agree, board_score, plies, fen, raw{fen,problems,agree}, san, eval, cached, timing{} |
| `rtt` | relay | role, id, rtt (ms), offset (client − relay, ms) |
| `stats` (broadcaster) | glasses | ws_buffered, frames_sent, frame_bytes, frames_skipped, peers[getStats subset], assist[{seq,cap,t_send,t_resp,t_shown,status,ok,state,bytes}], last_ok_age_ms, status, cam, battery, reconnects, paused |
| `stats` (viewer) | laptop | mode, frames, bytes, stalls, stall_ms, gap_mean/std/max, lost, shown[[seq,t,cap]], rtc{getStats subset}, reconnects, fallbacks |
| `client_event` | both | camera_started/ended/error, peer_state, webrtc_fallback, mode, paused/resumed, ws_reconnected |
| `probe` | both | bytes, down_ms, down_mbps, up_ms, up_mbps |
| `relay` | relay | every 5 s: glasses RTT/buffer, per-viewer buffer, frames sent/dropped, process memory |
| `replay` | replay board | game, gi, ply, fen, san, orient, playing, tc (board clock) every time a new position is painted |

## Ground-truth games

`pgn/` holds the chess.com games played during the runs (exported from the game pages) and the
synthetic games for the replay board. Copy a real one into `traces/<run>/game.pgn` and `analyze.py` scores every frame's raw and tracked reading
against the real positions (`pgn_raw_correct`, `pgn_tracked_correct`, figure `part_d.png`).
Runs on the replay board are scored automatically from the `replay` events (`replay_raw_correct`,
`replay_tracked_correct`, `replay_move_hit`, `move2pos_med`).
`game2_2347152640.pgn` was played during run `20261015-205506_parta-wifi-webrtc`.
