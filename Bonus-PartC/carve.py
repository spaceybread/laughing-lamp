#!/usr/bin/env python
"""Carve a time window out of Bonus-PartC/traces/_idle/events.jsonl into its own run directory.

    .venv/bin/python Bonus-PartC/carve.py "2026-10-15 16:58:00" "2026-10-15 17:00:00" home-game-after-run2 [net] [note]

For sessions where nobody pressed Start run: the relay logs everything to _idle anyway (events only,
no saved frames). Times are local. The new run gets synthetic run_start / run_stop events.
"""
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def ms(s):
    return int(datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)

def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    t0, t1, scenario = ms(sys.argv[1]), ms(sys.argv[2]), sys.argv[3]
    net = sys.argv[4] if len(sys.argv) > 4 else ""
    note = sys.argv[5] if len(sys.argv) > 5 else "carved from _idle"
    src = os.path.join(ROOT, "Bonus-PartC", "traces", "_idle", "events.jsonl")
    with open(src) as f:
        ev = [json.loads(l) for l in f if l.strip()]
    sel = [e for e in ev if t0 <= e["t"] <= t1]
    if not sel:
        sys.exit("no events in that window")
    stamp = datetime.datetime.fromtimestamp(t0 / 1000).strftime("%Y%m%d-%H%M%S")
    run_id = f"{stamp}_{scenario}"
    out = os.path.join(ROOT, "Bonus-PartC", "traces", run_id)
    os.makedirs(os.path.join(out, "frames"), exist_ok=True)
    head = {"t": t0, "ev": "run_start", "id": run_id, "scenario": scenario, "net": net, "note": note, "startedAt": t0, "saveFrames": False, "carved": True}
    tail = {"t": sel[-1]["t"], "ev": "run_stop", "id": run_id, "duration_ms": sel[-1]["t"] - t0, "carved": True}
    with open(os.path.join(out, "events.jsonl"), "w") as f:
        for e in [head] + sel + [tail]:
            f.write(json.dumps(e) + "\n")
    print(f"{run_id}: {len(sel)} events, {(sel[-1]['t'] - t0) / 1000:.0f} s, analyses={sum(1 for e in sel if e['ev'] == 'analyze')}")

if __name__ == "__main__":
    main()
