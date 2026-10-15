# Bonus-PartC: performance evaluation in the wild

Everything the system does during a field run is written to `traces/<run>/` by the relay
(`server.js`); this folder holds the run controller and the scripts that turn those traces
into the tables and figures for the report.

```
traces/20261015-183622_home-wifi/
  events.jsonl      one JSON object per line: {"t": <relay clock, ms>, "ev": <type>, ...}
  frames/a00017.jpg every picture that went to the vision service (named by analyze req id)
  game.pgn          (optional, add by hand) chess.com PGN of the game played during the run
```

## Running a field trial

1. Start the three processes as usual (`node server.js`, `.venv/bin/python vision/service.py`,
   `./glasses.sh` or `GLASSES_IP=... ./glasses.sh`) and open the laptop viewer `http://localhost:3000/`.
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
3. Play against a chess.com bot during the run so the assist KPIs mean something. Afterwards,
   export the game's PGN from chess.com and save it as `traces/<run>/game.pgn` to score the
   readings against the real positions (used by Part D).
4. Analyse:
   ```sh
   .venv/bin/python Bonus-PartC/analyze.py                 # all runs -> Bonus-PartC/results/
   .venv/bin/python Bonus-PartC/analyze.py traces/*pal3*   # a subset
   ```
   Output: `results/summary.csv` (one row per run, every metric), `results/summary.md`
   (pooled per scenario) and `latency_cdf.png`, `throughput.png`, `streaming.png`,
   `pipeline.png`, `kpi.png`, `part_d.png` (when a PGN is present).

A bandwidth probe (2 MB down + 2 MB up over the same path as the stream) runs automatically on the
glasses and on the laptop at the start of every run; *Probe bandwidth* / `run.sh probe` repeats it.
*Pause glasses* / `run.sh pause|resume` stops and restarts streaming and the assist loop on the
glasses without touching them (Part B runtime control).

Raw traces stay out of git (`traces/` is ignored); keep them, the course may ask for them.

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
