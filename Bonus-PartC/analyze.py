#!/usr/bin/env python
"""Part C trace analysis: Bonus-PartC/traces/<run>/events.jsonl  ->  results/summary.csv, summary.md and figures.

    .venv/bin/python Bonus-PartC/analyze.py                 # every run under Bonus-PartC/traces/
    .venv/bin/python Bonus-PartC/analyze.py Bonus-PartC/traces/2026*_pal3-*   # a subset
    .venv/bin/python Bonus-PartC/analyze.py --pgn game.pgn Bonus-PartC/traces/<run>   # score readings against the real game

A run directory may also contain game.pgn (the chess.com export of the game played during the run);
it is used automatically to score the raw and the tracked readings against the true positions (Part D).

Metrics per run (pooled per scenario in the figures):
  network   uplink/downlink throughput (Mb/s), WebSocket RTT and WebRTC RTT (ms), one-way frame latency
            glasses->relay and glasses->laptop (ms, via clock-sync offsets), bandwidth probes, failures
  streaming fps, stalls (no new frame for >500 ms) per minute and stalled time %, inter-frame jitter (ms),
            RTP packet loss / jitter / freezes (WebRTC mode), dropped frames (relay back-pressure)
  app KPIs  board-detection rate, readable-position rate, hint rate, capture->hint latency (ms, glasses
            clock), time since last good read (s), tracker losses, raw-reading vs tracked-position agreement
"""
import argparse
import bisect
import collections
import csv
import glob
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STALL_MS = 500


def load(run_dir):
    with open(os.path.join(run_dir, "events.jsonl")) as f:
        return [json.loads(line) for line in f if line.strip()]


def pct(x, p):
    return float(np.percentile(x, p)) if len(x) else math.nan


def med(x):
    return float(np.median(x)) if len(x) else math.nan


def mean(x):
    return float(np.mean(x)) if len(x) else math.nan


def clock_offset(rtts):
    """Client-minus-server clock offset from pong samples: median of the lowest-RTT quarter."""
    if not rtts:
        return None
    rtts = sorted(rtts, key=lambda r: r["rtt"])
    best = rtts[: max(1, len(rtts) // 4)]
    return float(np.median([r["offset"] for r in best]))


def delta_max_min(values):
    """Cumulative counter -> amount within the run (robust to a reconnect resetting it)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return 0
    total, prev = 0, vals[0]
    for v in vals[1:]:
        if v >= prev:
            total += v - prev
        prev = v
    return total


def pgn_positions(path):
    import chess.pgn
    pos = set()
    with open(path) as f:
        while True:
            game = chess.pgn.read_game(f)
            if game is None:
                break
            b = game.board()
            pos.add(b.board_fen())
            for mv in game.mainline_moves():
                b.push(mv)
                pos.add(b.board_fen())
    return pos


def summarize(run_dir, pgn=None):
    ev = load(run_dir)
    by = collections.defaultdict(list)
    for e in ev:
        by[e["ev"]].append(e)
    start = by["run_start"][0] if by["run_start"] else {"t": ev[0]["t"], "id": os.path.basename(run_dir), "scenario": "?", "net": ""}
    t0 = start["t"]
    t_end = by["run_stop"][0]["t"] if by["run_stop"] else ev[-1]["t"]
    dur_s = max(1e-3, (t_end - t0) / 1000)
    r = {"run": start.get("id", os.path.basename(run_dir)), "scenario": start.get("scenario", "?"), "net": start.get("net", ""),
         "start": t0, "duration_s": round(dur_s, 1)}
    series = {}

    # ---- clocks and RTT -------------------------------------------------------------------
    rtt_g = [e for e in by["rtt"] if e["role"] == "broadcaster"]
    rtt_v = collections.defaultdict(list)
    for e in by["rtt"]:
        if e["role"] == "viewer":
            rtt_v[e["id"]].append(e)
    off_g = clock_offset(rtt_g)
    off_v = {vid: clock_offset(lst) for vid, lst in rtt_v.items()}
    ws_rtt = [e["rtt"] for e in rtt_g]
    r.update(ws_rtt_med=med(ws_rtt), ws_rtt_p95=pct(ws_rtt, 95), ws_rtt_max=max(ws_rtt) if ws_rtt else math.nan)
    series["ws_rtt"] = ws_rtt
    series["ws_rtt_t"] = [(e["t"] - t0) / 1000 for e in rtt_g]

    # ---- JPEG frames through the relay ------------------------------------------------------
    frames = by["frame"]
    fbytes = sum(e["bytes"] for e in frames)
    lat1 = [e["t"] - (e["cap"] - off_g) for e in frames if e.get("cap") and off_g is not None]
    arr = np.array([e["t"] for e in frames], float)
    gaps = np.diff(arr) if len(arr) > 1 else np.array([])
    seqs = [e["seq"] for e in frames if e.get("seq") is not None]
    lost_relay = sum(max(0, b - a - 1) for a, b in zip(seqs, seqs[1:]))
    r.update(frames_relay=len(frames), fps_relay=round(len(frames) / dur_s, 2), frame_kb=round(fbytes / 1024 / max(1, len(frames)), 1),
             lat_glasses_relay_med=med(lat1), lat_glasses_relay_p95=pct(lat1, 95),
             jitter_relay_ms=float(np.std(gaps)) if len(gaps) else math.nan, frames_lost_before_relay=lost_relay)
    series["lat_relay"] = lat1

    # ---- analyses (chess assist) ------------------------------------------------------------
    an = [e for e in by["analyze"] if e.get("status") is not None]
    n = len(an)
    states = collections.Counter(e.get("state") or ("err%s" % e["status"]) for e in an)
    detected = [e for e in an if e.get("state") not in (None, "nodetect", "badgrid", "badimage") and e["status"] not in (0, 502, 503)]
    readable = [e for e in an if e.get("state") in ("tracked", "start", "adopted", "raw", "over")]
    hints = [e for e in an if e.get("ok") and e.get("san")]
    failed = [e for e in an if e["status"] in (0, 500, 502, 503)]
    abytes = sum(e.get("bytes") or 0 for e in an)
    tot = [e["total_ms"] for e in an if e.get("total_ms") is not None]
    stages = collections.defaultdict(list)
    for e in an:
        for k, v in (e.get("timing") or {}).items():
            if k == "engine" and e.get("cached"):
                continue                                   # cache hit: no engine work
            stages[k].append(v)
    raw_ok = [e for e in readable if e.get("raw") and not e["raw"].get("problems")]
    raw_exact = [e for e in readable if e.get("raw") and e["raw"].get("agree") == 64]
    raw_agree = [e["raw"]["agree"] for e in readable if e.get("raw") and e["raw"].get("agree") is not None]
    r.update(analyses=n, analyses_per_min=round(n / dur_s * 60, 1), analyze_fail=len(failed),
             detect_rate=len(detected) / n if n else math.nan, readable_rate=len(readable) / n if n else math.nan,
             hint_rate=len(hints) / n if n else math.nan, tracker_lost=sum(1 for e in an if e.get("lost")),
             engine_cached_rate=mean([1.0 if e.get("cached") else 0.0 for e in hints]) if hints else math.nan,
             analyze_total_med=med(tot), analyze_total_p95=pct(tot, 95),
             detect_ms=med(stages["detect"]), classify_ms=med(stages["classify"]), match_ms=med(stages["match"]), engine_ms=med(stages["engine"]),
             engine_runs=len(stages["engine"]),
             raw_valid_rate=len(raw_ok) / len(readable) if readable else math.nan,
             raw_exact_rate=len(raw_exact) / len(readable) if readable else math.nan,
             raw_wrong_squares_mean=mean([64 - a for a in raw_agree]), states=" ".join(f"{k}:{v}" for k, v in sorted(states.items())))
    series["stages"] = stages
    series["analyze_total"] = tot
    pgn_file = pgn or (os.path.join(run_dir, "game.pgn") if os.path.exists(os.path.join(run_dir, "game.pgn")) else None)
    if pgn_file:
        truth = pgn_positions(pgn_file)
        with_fen = [e for e in an if e.get("fen")]
        placement = lambda fen: fen.split()[0]
        r["pgn_tracked_correct"] = mean([placement(e["fen"]) in truth for e in with_fen]) if with_fen else math.nan
        r["pgn_raw_correct"] = mean([placement(e["raw"]["fen"]) in truth for e in with_fen if e.get("raw")]) if with_fen else math.nan
        series["pgn_tracked_hits"] = [placement(e["fen"]) in truth for e in with_fen]
        series["pgn_raw_hits"] = [placement(e["raw"]["fen"]) in truth for e in with_fen if e.get("raw")]

    # ---- glasses-side stats ------------------------------------------------------------------
    gs = [e["s"] for e in by["stats"] if e["role"] == "broadcaster"]
    assist = [a for s in gs for a in (s.get("assist") or [])]

    # ---- replay board ground truth (public/replay.html) ---------------------------------------
    # `replay` events say which position the laptop screen showed from when (relay clock). A picture's
    # capture time (glasses clock) minus the glasses' clock offset gives the position it photographed.
    # Pictures within REPLAY_GUARD_MS of a change are skipped (the frame may show either position).
    REPLAY_GUARD_MS = 750
    changes = []
    if start.get("replay") and start["replay"].get("fen"):
        changes.append((start["replay"]["t"], start["replay"]["fen"].split()[0]))
    for e in by["replay"]:
        if not changes or changes[-1][1] != e["fen"].split()[0]:
            changes.append((e["t"], e["fen"].split()[0]))
    if changes and off_g is not None:
        times = [c[0] for c in changes]
        shown_at = {a.get("req"): a["t_shown"] - off_g for a in assist if a.get("t_shown") and a.get("req") is not None}
        def truth_at(t):
            i = bisect.bisect_right(times, t) - 1
            if i < 0:
                return None, None
            prev_gap = t - times[i]
            next_gap = times[i + 1] - t if i + 1 < len(times) else float("inf")
            return (changes[i][1] if min(prev_gap, next_gap) >= REPLAY_GUARD_MS else None), i
        place = lambda fen: fen.split()[0] if fen else None
        scored = []                                   # (event, truth placement, change index)
        for e in an:
            if not e.get("cap"):
                continue
            tr, idx = truth_at(e["cap"] - off_g)
            if tr is not None:
                scored.append((e, tr, idx))
        with_fen = [(e, tr) for e, tr, _ in scored if e.get("fen")]
        with_raw = [(e, tr) for e, tr, _ in scored if e.get("raw") and e["raw"].get("fen")]
        # move -> correct position on the glasses: first picture of the new position whose final reading
        # matches, from the change to when the glasses drew that result (relay clock)
        lags, hit = [], 0
        for k in range(len(changes)):
            if changes[k][0] < t0:
                continue
            cand = [e for e, tr, idx in scored if idx == k and place(e.get("fen")) == changes[k][1]]
            if cand:
                hit += 1
                e = min(cand, key=lambda x: x["cap"])
                lags.append((shown_at.get(e["req"], e["t"]) - changes[k][0]))
        n_moves = sum(1 for c in changes if c[0] >= t0)
        r.update(replay_scored=len(scored), replay_moves=n_moves,
                 replay_correct=mean([place(e.get("fen")) == tr for e, tr, _ in scored]) if scored else math.nan,
                 replay_tracked_correct=mean([place(e["fen"]) == tr for e, tr in with_fen]) if with_fen else math.nan,
                 replay_raw_correct=mean([place(e["raw"]["fen"]) == tr for e, tr in with_raw]) if with_raw else math.nan,
                 replay_move_hit=hit / n_moves if n_moves else math.nan,
                 move2pos_med=med(lags), move2pos_p95=pct(lags, 95))
        series["move2pos"] = lags
        series["replay_tracked_hits"] = [place(e["fen"]) == tr for e, tr in with_fen]   # per picture, for part_d.png
        series["replay_raw_hits"] = [place(e["raw"]["fen"]) == tr for e, tr in with_raw]
    cap2hint = [a["t_shown"] - a["cap"] for a in assist if a.get("t_shown") and a.get("ok")]
    cap2resp = [a["t_resp"] - a["cap"] for a in assist if a.get("t_resp")]
    enc = [a["t_send"] - a["cap"] for a in assist if a.get("t_send")]
    ages = [s["last_ok_age_ms"] / 1000 for s in gs if s.get("last_ok_age_ms") is not None]
    vision_by_req = {e["req"]: (e.get("timing") or {}).get("total") for e in an}
    stages["encode"] = enc
    stages["transfer"] = [(a["t_resp"] - a["t_send"]) - vision_by_req[a["req"]] for a in assist
                          if a.get("t_resp") and a.get("t_send") and vision_by_req.get(a.get("req")) is not None]
    rtc_out = collections.defaultdict(list)
    for s in gs:
        for p in s.get("peers") or []:
            rtc_out[p["viewer"]].append(p)
    rtc_bytes_up = sum(delta_max_min([p.get("bytesSent") for p in lst]) for lst in rtc_out.values())
    rtc_rtt = [p["rtt"] * 1000 for lst in rtc_out.values() for p in lst if p.get("rtt") is not None]
    rtc_lost_up = sum(delta_max_min([p.get("lost") for p in lst]) for lst in rtc_out.values())
    rtc_pkts_up = sum(delta_max_min([p.get("packetsSent") for p in lst]) for lst in rtc_out.values())
    limits = collections.Counter(p.get("limit") for lst in rtc_out.values() for p in lst if p.get("limit"))
    r.update(cap2hint_med=med(cap2hint), cap2hint_p95=pct(cap2hint, 95), cap2resp_med=med(cap2resp), encode_ms=med(enc), transfer_ms=med(stages["transfer"]),
             last_ok_age_med=med(ages), last_ok_age_p95=pct(ages, 95), last_ok_age_max=max(ages) if ages else math.nan,
             glasses_reconnects=max([s.get("reconnects", 0) for s in gs] or [0]),
             rtc_rtt_med=med(rtc_rtt), rtc_rtt_p95=pct(rtc_rtt, 95),
             rtc_loss_up=rtc_lost_up / rtc_pkts_up if rtc_pkts_up else math.nan,
             rtc_limit=" ".join(f"{k}:{v}" for k, v in limits.items()),
             battery_drop=(gs[0]["battery"] - gs[-1]["battery"]) if len(gs) > 1 and gs[0].get("battery") is not None and gs[-1].get("battery") is not None else math.nan)
    series["cap2hint"] = cap2hint
    series["ages"] = ages
    series["rtc_rtt"] = rtc_rtt

    # ---- laptop viewer stats -----------------------------------------------------------------
    vs = collections.defaultdict(list)
    for e in by["stats"]:
        if e["role"] == "viewer":
            vs[e["id"]].append(e)
    stalls = stall_ms = vbytes = rtc_bytes_down = fallbacks = 0
    gap_std, fps_samples, rtc_jit, rtc_fps, freeze_n, freeze_s, lost_down, pkts_down, lat2 = [], [], [], [], 0, 0.0, 0, 0, []
    for vid, lst in vs.items():
        ss = [e["s"] for e in lst]
        stalls += delta_max_min([s.get("stalls") for s in ss]); stall_ms += delta_max_min([s.get("stall_ms") for s in ss])
        vbytes += delta_max_min([s.get("bytes") for s in ss]); fallbacks += delta_max_min([s.get("fallbacks") for s in ss])
        gap_std += [s["gap_std"] for s in ss if s.get("gap_std") is not None and s.get("gap_n", 0) >= 5]
        fps_samples += [s["gap_n"] / 2.0 for s in ss if s.get("gap_n") is not None]
        rtc = [s["rtc"] for s in ss if s.get("rtc")]
        rtc_bytes_down += delta_max_min([x.get("bytesReceived") for x in rtc])
        lost_down += delta_max_min([x.get("packetsLost") for x in rtc]); pkts_down += delta_max_min([x.get("packetsReceived") for x in rtc])
        rtc_jit += [x["jitter"] * 1000 for x in rtc if x.get("jitter") is not None]
        rtc_fps += [x["fps"] for x in rtc if x.get("fps") is not None]
        freeze_n += delta_max_min([x.get("freezeCount") for x in rtc]); freeze_s += delta_max_min([x.get("freezeS") for x in rtc])
        off = off_v.get(vid)
        if off is not None and off_g is not None:
            for s in ss:
                for seq, t_shown, cap in s.get("shown") or []:
                    if cap:
                        lat2.append((t_shown - off) - (cap - off_g))
    modes = collections.Counter(e["s"].get("mode") for lst in vs.values() for e in lst)
    r.update(viewer_mode=" ".join(f"{k}:{v}" for k, v in modes.items()), fps_viewer=mean(fps_samples),
             stalls=stalls, stalls_per_min=round(stalls / dur_s * 60, 2), stall_pct=round(100 * stall_ms / 1000 / dur_s, 2),
             jitter_viewer_ms=mean(gap_std), rtc_jitter_ms=mean(rtc_jit), rtc_fps=mean(rtc_fps),
             rtc_loss_down=lost_down / pkts_down if pkts_down else math.nan, rtc_freezes=freeze_n, rtc_freeze_s=round(freeze_s, 2),
             lat_glasses_laptop_med=med(lat2), lat_glasses_laptop_p95=pct(lat2, 95), webrtc_fallbacks=fallbacks)
    series["lat_laptop"] = lat2

    # ---- throughput, probes, failures -------------------------------------------------------
    up_bytes = fbytes + abytes + rtc_bytes_up
    down_bytes = vbytes + rtc_bytes_down
    r.update(uplink_mbps=round(up_bytes * 8 / dur_s / 1e6, 3), downlink_mbps=round(down_bytes * 8 / dur_s / 1e6, 3),
             uplink_video_mbps=round((fbytes + rtc_bytes_up) * 8 / dur_s / 1e6, 3), uplink_assist_mbps=round(abytes * 8 / dur_s / 1e6, 3))
    buckets = collections.Counter()
    for e in frames:
        buckets[int((e["t"] - t0) / 5000)] += e["bytes"]
    for e in an:
        buckets[int((e["t"] - t0) / 5000)] += e.get("bytes") or 0
    series["uplink_t"] = sorted(buckets)
    series["uplink_mbps"] = [buckets[k] * 8 / 5 / 1e6 for k in series["uplink_t"]]
    probes_g = [e for e in by["probe"] if e["role"] == "broadcaster" and e.get("down_mbps")]
    r.update(probe_down_mbps=mean([e["down_mbps"] for e in probes_g]), probe_up_mbps=mean([e["up_mbps"] for e in probes_g]))
    closes = [e for e in by["ws_close"] if e.get("role") == "broadcaster"]
    cev = collections.Counter(e["name"] for e in by["client_event"])
    peer_failed = sum(1 for e in by["client_event"] if e["name"] == "peer_state" and (e.get("data") or {}).get("state") == "failed")
    relay = by["relay"]
    dropped = delta_max_min([sum(v.get("dropped", 0) for v in e.get("viewers", [])) for e in relay])
    r.update(glasses_disconnects=len(closes), camera_ended=cev.get("camera_ended", 0) + cev.get("camera_error", 0),
             webrtc_failed=peer_failed, relay_dropped_frames=dropped,
             failures=len(closes) + cev.get("camera_ended", 0) + cev.get("camera_error", 0) + peer_failed + len(failed))
    return r, series


COLUMNS = ["run", "scenario", "net", "duration_s", "uplink_mbps", "downlink_mbps", "uplink_video_mbps", "uplink_assist_mbps",
           "probe_down_mbps", "probe_up_mbps", "ws_rtt_med", "ws_rtt_p95", "rtc_rtt_med", "rtc_rtt_p95",
           "lat_glasses_relay_med", "lat_glasses_relay_p95", "lat_glasses_laptop_med", "lat_glasses_laptop_p95",
           "viewer_mode", "fps_relay", "fps_viewer", "rtc_fps", "stalls", "stalls_per_min", "stall_pct", "jitter_relay_ms", "jitter_viewer_ms",
           "rtc_jitter_ms", "rtc_loss_up", "rtc_loss_down", "rtc_freezes", "rtc_freeze_s", "rtc_limit", "frames_lost_before_relay", "relay_dropped_frames",
           "failures", "glasses_disconnects", "glasses_reconnects", "camera_ended", "webrtc_failed", "webrtc_fallbacks", "analyze_fail",
           "analyses", "analyses_per_min", "detect_rate", "readable_rate", "hint_rate", "tracker_lost", "engine_cached_rate",
           "cap2hint_med", "cap2hint_p95", "cap2resp_med", "encode_ms", "analyze_total_med", "analyze_total_p95",
           "encode_ms", "transfer_ms", "detect_ms", "classify_ms", "match_ms", "engine_ms", "engine_runs", "last_ok_age_med", "last_ok_age_p95", "last_ok_age_max",
           "raw_valid_rate", "raw_exact_rate", "raw_wrong_squares_mean", "pgn_raw_correct", "pgn_tracked_correct",
           "replay_scored", "replay_moves", "replay_correct", "replay_raw_correct", "replay_tracked_correct", "replay_move_hit", "move2pos_med", "move2pos_p95",
           "battery_drop", "states"]


def fmt(v):
    if isinstance(v, float):
        return "" if math.isnan(v) else (f"{v:.3f}" if abs(v) < 10 else f"{v:.1f}")
    return "" if v is None else str(v)


def write_tables(rows, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: fmt(r.get(k)) for k in COLUMNS})
    # pooled per scenario (medians of per-run values, sums of counts)
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["scenario"]].append(r)
    sums = {"duration_s", "stalls", "failures", "analyses", "tracker_lost", "glasses_disconnects", "rtc_freezes"}
    cols = ["scenario", "runs", "duration_s", "uplink_mbps", "downlink_mbps", "ws_rtt_med", "ws_rtt_p95", "rtc_rtt_med", "lat_glasses_relay_med",
            "lat_glasses_laptop_med", "fps_viewer", "stalls", "stall_pct", "jitter_viewer_ms", "rtc_jitter_ms", "rtc_loss_down", "failures",
            "analyses", "detect_rate", "readable_rate", "hint_rate", "cap2hint_med", "encode_ms", "last_ok_age_med", "tracker_lost", "raw_exact_rate", "pgn_raw_correct", "pgn_tracked_correct",
            "replay_correct", "replay_raw_correct", "replay_tracked_correct", "replay_move_hit", "move2pos_med"]
    lines = ["# Part C summary", "", f"{len(rows)} runs, {sum(r['duration_s'] for r in rows) / 60:.1f} min in total.", "",
             "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for sc, rs in sorted(groups.items()):
        row = {"scenario": sc, "runs": len(rs)}
        for c in cols[2:]:
            vals = [r[c] for r in rs if isinstance(r.get(c), (int, float)) and not (isinstance(r[c], float) and math.isnan(r[c]))]
            row[c] = (sum(vals) if c in sums else med(vals)) if vals else math.nan
        lines.append("| " + " | ".join(fmt(row[c]) for c in cols) + " |")
    lines += ["", "Columns: Mb/s for throughput, ms for RTT/latency, rates are fractions of analyses; `cap2hint` = picture taken on the",
              "glasses -> hint drawn on the glasses; `last_ok_age` = seconds since the last readable position; `raw_exact_rate` =",
              "share of readable frames where the raw reading already equalled the accepted position (tracker not needed)."]
    with open(os.path.join(out_dir, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def cdf(ax, data, label):
    if not len(data):
        return
    x = np.sort(np.asarray(data, float))
    ax.plot(x, np.arange(1, len(x) + 1) / len(x), label=f"{label} (n={len(x)})")


def legend(ax):
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=8)


def write_figures(rows, series, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    scen = sorted({r["scenario"] for r in rows})
    pooled = {sc: collections.defaultdict(list) for sc in scen}
    for r, s in zip(rows, series):
        p = pooled[r["scenario"]]
        for k in ("ws_rtt", "rtc_rtt", "lat_relay", "lat_laptop", "cap2hint", "ages", "analyze_total"):
            p[k] += list(s.get(k, []))
        for k, v in s["stages"].items():
            p["stage_" + k] += v
    saved = []

    def save(fig, name):
        path = os.path.join(out_dir, name); fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig); saved.append(path)

    # 1. latency CDFs
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
    for sc in scen:
        cdf(axes[0], pooled[sc]["ws_rtt"], sc); cdf(axes[1], pooled[sc]["lat_relay"] or pooled[sc]["rtc_rtt"], sc); cdf(axes[2], pooled[sc]["cap2hint"], sc)
    for ax, t in zip(axes, ["WebSocket RTT glasses↔relay (ms)", "frame latency glasses→relay (ms)\n(WebRTC RTT when no JPEG frames)", "capture→hint on glasses (ms)"]):
        ax.set_title(t, fontsize=10); ax.set_ylabel("CDF"); ax.grid(alpha=.3); legend(ax)
    save(fig, "latency_cdf.png")
    # 2. throughput per run (bars) + time series of the longest run per scenario
    fig, axes = plt.subplots(1, 2, figsize=(13, 3.8))
    xs = np.arange(len(rows))
    axes[0].bar(xs - 0.2, [r["uplink_mbps"] for r in rows], 0.4, label="uplink (glasses→)")
    axes[0].bar(xs + 0.2, [r["downlink_mbps"] for r in rows], 0.4, label="downlink (→laptop)")
    axes[0].set_xticks(xs); axes[0].set_xticklabels([f"{r['scenario']}\n#{i + 1}" for i, r in enumerate(rows)], fontsize=7, rotation=45, ha="right")
    axes[0].set_ylabel("Mb/s"); axes[0].set_title("application throughput per run", fontsize=10); axes[0].legend(fontsize=8); axes[0].grid(alpha=.3, axis="y")
    for sc in scen:
        i = max((j for j, r in enumerate(rows) if r["scenario"] == sc), key=lambda j: rows[j]["duration_s"])
        axes[1].plot(np.array(series[i]["uplink_t"]) * 5, series[i]["uplink_mbps"], label=f"{sc} #{i + 1}")
    axes[1].set_xlabel("s into run"); axes[1].set_ylabel("Mb/s (5 s buckets)"); axes[1].set_title("uplink over time (frames + pictures)", fontsize=10); legend(axes[1]); axes[1].grid(alpha=.3)
    save(fig, "throughput.png")
    # 3. streaming quality: fps, stalls, jitter per scenario
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
    for ax, key, title in zip(axes, ["fps_viewer", "stalls_per_min", "jitter_viewer_ms"], ["viewer fps", "stalls per minute (>0.5 s gaps)", "inter-frame jitter at viewer (ms)"]):
        data = [[r[key] for r in rows if r["scenario"] == sc and isinstance(r.get(key), (int, float)) and not math.isnan(r[key])] for sc in scen]
        ax.boxplot(data, tick_labels=scen); ax.set_title(title, fontsize=10); ax.grid(alpha=.3, axis="y"); ax.tick_params(axis="x", labelsize=8)
    save(fig, "streaming.png")
    # 4. assist pipeline breakdown
    fig, ax = plt.subplots(figsize=(8, 3.8))
    stages = ["encode", "transfer", "decode", "detect", "classify", "match", "engine"]
    width = 0.8 / max(1, len(scen))
    for i, sc in enumerate(scen):
        vals = [med(pooled[sc]["stage_" + k]) if pooled[sc]["stage_" + k] else 0 for k in stages]
        ax.bar(np.arange(len(stages)) + i * width, vals, width, label=sc)
    ax.set_xticks(np.arange(len(stages)) + width * (len(scen) - 1) / 2); ax.set_xticklabels(stages)
    ax.set_ylabel("median ms"); ax.set_title("capture→hint stages per picture (encode on the glasses, transfer = upload + relay + download, engine = uncached only)", fontsize=9); ax.legend(fontsize=8); ax.grid(alpha=.3, axis="y")
    save(fig, "pipeline.png")
    # 5. app KPIs and Part D raw-vs-tracked
    fig, axes = plt.subplots(1, 2, figsize=(13, 3.8))
    keys = ["detect_rate", "readable_rate", "hint_rate", "raw_exact_rate"]
    labels = ["board detected", "position readable", "hint shown", "raw read exact"]
    for i, sc in enumerate(scen):
        rs = [r for r in rows if r["scenario"] == sc]
        axes[0].bar(np.arange(len(keys)) + i * width, [mean([r[k] for r in rs if not math.isnan(r.get(k, math.nan))]) for k in keys], width, label=sc)
    axes[0].set_xticks(np.arange(len(keys)) + width * (len(scen) - 1) / 2); axes[0].set_xticklabels(labels, fontsize=8)
    axes[0].set_ylim(0, 1.05); axes[0].set_title("assist KPIs (fraction of pictures)", fontsize=10); axes[0].legend(fontsize=8); axes[0].grid(alpha=.3, axis="y")
    for sc in scen:
        cdf(axes[1], pooled[sc]["ages"], sc)
    axes[1].set_title("time since last good read (s)", fontsize=10); axes[1].set_ylabel("CDF"); axes[1].grid(alpha=.3); legend(axes[1])
    save(fig, "kpi.png")
    # 5. Part D: raw reading vs tracked position, against ground truth, pooled over every scored picture
    #    of a scenario (replay board: the position on screen when the picture was taken; PGN: any
    #    position of the real game). Bars are labelled with the number of pictures behind them.
    groups = []
    for kind, label in (("replay", "replay board"), ("pgn", "PGN")):
        for sc in scen:
            raw = [h for r, s in zip(rows, series) if r["scenario"] == sc for h in s.get(kind + "_raw_hits", [])]
            trk = [h for r, s in zip(rows, series) if r["scenario"] == sc for h in s.get(kind + "_tracked_hits", [])]
            if raw or trk:
                groups.append((f"{sc}\n({label})", raw, trk))
    if groups:
        fig, ax = plt.subplots(figsize=(1.9 * len(groups) + 2.5, 3.8))
        x = np.arange(len(groups))
        for off, idx, name in ((-0.2, 1, "raw reading (no tracking)"), (0.2, 2, "tracked position")):
            vals = [mean(g[idx]) if g[idx] else 0 for g in groups]
            bars = ax.bar(x + off, vals, 0.4, label=name)
            for b, g in zip(bars, groups):
                ax.annotate(f"{b.get_height():.2f}\nn={len(g[idx])}", (b.get_x() + b.get_width() / 2, b.get_height()),
                            ha="center", va="bottom", fontsize=7, xytext=(0, 2), textcoords="offset points")
        ax.set_xticks(x); ax.set_xticklabels([g[0] for g in groups], fontsize=8); ax.set_ylim(0, 1.18)
        ax.set_ylabel("fraction of pictures correct"); ax.set_title("Part D: game tracker vs raw reader (ground truth)", fontsize=10)
        ax.legend(fontsize=8, loc="lower right"); ax.grid(alpha=.3, axis="y")
        save(fig, "part_d.png")
    return saved


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="*", help="run directories (default: Bonus-PartC/traces/* except _idle)")
    ap.add_argument("--pgn", help="PGN of the game played, applied to every given run")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args()
    runs = args.runs or sorted(d for d in glob.glob(os.path.join(HERE, "traces", "*")) if os.path.isdir(d) and not os.path.basename(d).startswith("_"))
    runs = [d for d in runs if os.path.exists(os.path.join(d, "events.jsonl"))]
    if not runs:
        sys.exit("no runs found (Bonus-PartC/traces/<run>/events.jsonl)")
    rows, series = [], []
    for d in runs:
        try:
            r, s = summarize(d, args.pgn)
        except Exception as e:                        # a broken trace should not hide the others
            print(f"skip {d}: {e}", file=sys.stderr); continue
        rows.append(r); series.append(s)
        print(f"{r['run']:40s} {r['duration_s']:7.1f} s  up {r['uplink_mbps']:.2f} Mb/s  rtt {fmt(r['ws_rtt_med'])} ms  "
              f"stalls {r['stalls']}  analyses {r['analyses']}  hint {fmt(r['hint_rate'])}  cap→hint {fmt(r['cap2hint_med'])} ms")
    write_tables(rows, args.out)
    if not args.no_figures:
        for p in write_figures(rows, series, args.out):
            print("wrote", os.path.relpath(p, ROOT))


if __name__ == "__main__":
    main()
