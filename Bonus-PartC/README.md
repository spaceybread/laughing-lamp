# Bonus-PartC: Field Evaluation

Run controller, trace analysis and replay-board games. During a run, the relay (`server.js`) writes everything to `Bonus-PartC/traces/<run>/`.

## Layout

```
run.sh            run controller (talks to the relay)
analyze.py        traces -> results/ (summary.csv, summary.md, figures)
carve.py          cut a time window out of traces/_idle/ into a run
pgn2json.py       pgn/*.pgn -> pgn/games.json (replay-board playlist); --synth N = generate Stockfish games
pgn/              ground truth: 2 chess.com bot games + 3 seeded Stockfish games
results/          analysis of the recorded scenarios
traces/           raw logs, one folder per run (git-ignored, kept locally)
```

One run folder:

```
traces/<YYYYMMDD-HHMMSS>_<scenario>/
  events.jsonl      one JSON object per line: {"t": <relay clock, ms>, "ev": <type>, ...}
  frames/a00017.jpg every picture sent to the vision service (named by analyze request id)
  game.pgn          optional, added by hand: PGN of the game played during the run
```

## Procedure

1. Start everything: `./demo.sh --replay`.
2. Point the glasses at the replay board, then start it:

   ```sh
   ./Bonus-PartC/run.sh replay play          # play | pause | restart | next
   ```

   It plays `pgn/games.json` at one move per 10 s and logs every position it shows, so every picture can be scored against what was on screen.

   Alternative: play a chess.com bot, then save the game's PGN as `traces/<run>/game.pgn`.
3. Record. Use the viewer's **Field run** panel, or:

   ```sh
   ./Bonus-PartC/run.sh start <scenario> <net> "<note>"
   ./Bonus-PartC/run.sh mark "<label>"                  # optional marker
   ./Bonus-PartC/run.sh stop
   ./Bonus-PartC/run.sh series <scenario> 10 85 <net>   # 10 runs of 85 s
   ```

   - **Run length:** 30 s to 5 min.
   - **Spec:** at least 10 runs per scenario and more than 1 h in total.
   - **Names:** scenario names become the groups in every figure, so keep them consistent.
4. Analyse:

   ```sh
   .venv/bin/python Bonus-PartC/analyze.py                                  # all runs
   .venv/bin/python Bonus-PartC/analyze.py Bonus-PartC/traces/*_home-home   # a subset
   ```

### Other `run.sh` commands

| Command | Effect |
|---|---|
| `run.sh status` | current run and connected clients |
| `run.sh probe` | bandwidth probe: 2 MB down and 2 MB up over the stream path (also runs automatically at every run start) |
| `run.sh pause` / `resume` | stop or restart streaming and the picture loop on the glasses |
| `run.sh reset` | make the vision service forget the tracked game |

### Output (`results/`)

| File | Content |
|---|---|
| `summary.csv` | one row per run, every metric |
| `summary.md` | per-scenario summary |
| `latency_cdf.png` | CDFs: WebSocket RTT; glasses→relay frame latency (WebRTC RTT when no JPEG frames); capture→hint |
| `throughput.png` | throughput per run; uplink over time |
| `streaming.png` | viewer fps, stalls per minute, inter-frame jitter (box plots) |
| `pipeline.png` | median ms per capture→hint stage: encode, transfer, vision stages, engine (uncached only) |
| `kpi.png` | assistant KPIs; time since the last good read |
| `part_d.png` | raw vs tracked reading against ground truth (only for runs that have ground truth) |

## Recorded scenarios (November 2026)

The committed `results/` come from 35 runs of 85 s each, 49.6 min in total. In every run these were open:
- the HUD on the glasses;
- the laptop viewer;
- a phone mirror (`/hud?mirror=1`), which is a second WebRTC stream from the glasses.

| Scenario | Runs | Glasses | Laptop | Path | Board |
|---|---|---|---|---|---|
| `5g-eduroam-tunnel` | 10 | phone 5G hotspot | campus Wi-Fi (eduroam) | Cloudflare tunnel + STUN | live chess.com game, no PGN |
| `home-home` | 15 | home Wi-Fi | home Wi-Fi | LAN (`adb reverse` over Wi-Fi) | replay board |
| `5g-glasses-home-laptop` | 10 | phone 5G hotspot | home Wi-Fi | Cloudflare tunnel + STUN | replay board |

Reproduce:

```sh
.venv/bin/python Bonus-PartC/analyze.py Bonus-PartC/traces/*_5g-eduroam-tunnel \
    Bonus-PartC/traces/*_home-home Bonus-PartC/traces/*_5g-glasses-home-laptop
```

Caveats:

| Topic | Note |
|---|---|
| Viewer browser | Chrome on campus, Safari at home (Chrome blocks LAN WebRTC). The campus tab was in the background, so `fps_viewer` and `stalls` are invalid for `5g-eduroam-tunnel`; use the `rtc_*` columns. |
| `5g-eduroam-tunnel` | Run 1 includes phone-mirror reconnects. |
| `5g-glasses-home-laptop` | Run 6 (`20261124-051242`) is a tracker cold start after a mid-game `/reset`: the raw reading saw the white king as black. Not a network effect. |
| Resolution | The glasses send 1280×720 to a single viewer, and 320–640 px wide when the phone mirror is also open. |
| Excluded | `*-aborted-*` runs (glasses battery died; viewer stuck in JPEG mode) and `check-*` setup runs. |
| `part_d.png` | Pooled per picture. home-home: raw 0.65, tracked 1.00 (n=351). 5G glasses: raw 0.58, tracked 0.86 (n=214). Runs 6–7 pull the 5G tracked figure down; without them it is 167/168. The campus scenario has no ground truth. |

## Metrics

**Clock sync**
- The relay pings every client every 2 s, and the client replies with its own clock.
- This gives the WebSocket RTT (`rtt` events) and the client − relay clock offset.
- The offset used is the median of the lowest-RTT quarter of the samples.

**Streaming (Part A)**

| Metric | Source |
|---|---|
| One-way latency glasses→relay, glasses→laptop | 16-byte header on each JPEG frame (sequence number, capture time); `frame` events; viewer `stats.shown` |
| Jitter, frames lost / dropped | inter-frame gaps; sequence gaps; `relay` events (back-pressure drops) |
| WebRTC | `getStats()` every 2 s from both ends: bytes, packets lost, RTP jitter, candidate-pair RTT, encoder limitation, fps, freezes |
| Throughput | uplink = everything leaving the glasses (frames, pictures, WebRTC); downlink = everything the viewer receives; probe results |
| Stalls | viewer gaps > 500 ms between displayed frames (count, total time) |
| Failures | WebSocket disconnects, camera track lost, WebRTC `failed`, JPEG fallback, analysis errors or timeouts |

**Assistant (Part B)**

Each picture produces an `analyze` event with:
- stage timings (decode, detect, classify, match, engine);
- the decision: `state` = `nodetect` / `badgrid` / `unreadable` / `unclear` / `confirming` / `start` / `tracked` / `adopted`;
- whose turn it is;
- whether the engine result came from the cache;
- the raw reading next to the final position.

The glasses also report when each picture was taken, sent, answered and drawn.

| Metric | Definition |
|---|---|
| detect / readable / hint rate | fraction of pictures |
| capture→hint | on the glasses' clock (no sync needed) |
| time since last good read | what the HUD shows, sampled every 2 s |
| tracker losses, cache hit rate | per run |
| `raw_exact_rate`, raw wrong squares | raw reading vs the accepted position |

**Ground truth (Part D)**

| Source | Metrics |
|---|---|
| Replay board (`replay` events) | `replay_raw_correct`, `replay_tracked_correct`, `replay_move_hit`, `move2pos_med` (move shown → correct position on the glasses) |
| PGN (`traces/<run>/game.pgn` or `--pgn`) | `pgn_raw_correct`, `pgn_tracked_correct` |

Use `?track=0` on the glasses page to run the raw-reader baseline live.

## Event types (`events.jsonl`)

| ev | From | Fields |
|---|---|---|
| `run_start`, `run_stop`, `mark`, `control` | relay | run id, scenario, net, note, clients; duration and counters at stop |
| `ws_open`, `ws_close` | relay | role, id, ip, user agent, page; close code, duration, frames sent/dropped |
| `camera_ready`, `want_stream`, `signal` | relay | camera settings; viewer mode; offer/answer routing |
| `frame` | relay | seq, cap (glasses clock), bytes, recipients, glasses send buffer |
| `analyze` | relay | req, src, bytes, cap, seq, frame, status, vision_ms, total_ms, ok, state, lost, side, agree, board_score, plies, fen, raw{fen,problems,agree}, san, eval, cached, timing{} |
| `rtt` | relay | role, id, rtt (ms), offset (client − relay, ms) |
| `stats` (broadcaster) | glasses | ws_buffered, frames_sent, frame_bytes, frames_skipped, peers[getStats], assist[{seq,cap,t_send,t_resp,t_shown,status,ok,state,bytes}], last_ok_age_ms, status, cam, battery, reconnects, paused |
| `stats` (viewer) | laptop | mode, frames, bytes, stalls, stall_ms, gap_mean/std/max, lost, shown[[seq,t,cap]], rtc{getStats}, reconnects, fallbacks |
| `client_event` | both | camera_started/ended/error, peer_state, webrtc_fallback, mode, paused/resumed, ws_reconnected |
| `probe` | both | bytes, down_ms, down_mbps, up_ms, up_mbps |
| `relay` | relay | every 5 s: glasses RTT/buffer, per-viewer buffer, frames sent/dropped, process memory |
| `replay` | replay board | game, gi, ply, fen, san, orient, playing, tc (board clock); one per painted position |

## Games (`pgn/`)

| File | Content |
|---|---|
| `game1_2340942112.pgn`, `game2_2347152640.pgn` | chess.com bot games; game 2 was played during `20261015-205506_parta-wifi-webrtc` |
| `synth_1..3.pgn` | Stockfish games (`pgn2json.py --synth 3`; seeded, reproducible) |
| `games.json` | replay-board playlist built by `pgn2json.py` |
