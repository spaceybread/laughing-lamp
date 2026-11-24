#!/usr/bin/env python3
"""Build Bonus-PartC/pgn/games.json, the playlist for the replay board (public/replay.html).

    .venv/bin/python Bonus-PartC/pgn2json.py                 # the real games + pgn/synth_*.pgn, in playlist order
    .venv/bin/python Bonus-PartC/pgn2json.py --synth 3       # (re)generate 3 Stockfish games first (seeded, reproducible)

Playlist: the two real chess.com games interleaved with synthetic ones, so a 30-minute series at one
ply every 10 s never repeats a position (a repeat would hit the vision service's engine cache and make
capture->hint look faster than it is). Every scenario replays the same playlist from the start, so the
scenarios see identical positions.

Each game becomes {name, white, black, result, orient, synthetic, plies: [{ply, san, fen}, ...]} with
ply 0 = start position, so the page needs no chess logic. `pieces` holds the piece drawings
(Colin M.L. Burnett's set as shipped in python-chess, GPL/BSD/CC BY-SA) as SVG fragments on a 45x45 grid.
"""
import argparse
import glob
import json
import os
import random
import shutil

import chess
import chess.engine
import chess.pgn
import chess.svg

HERE = os.path.dirname(os.path.abspath(__file__))
PGN_DIR = os.path.join(HERE, "pgn")
USER = "spaceybread0"


def synth_game(seed, plies, engine):
    """A plausible game: Stockfish's top-3 moves at low depth, picked at random (weighted to the best)."""
    rnd = random.Random(seed)
    board = chess.Board()
    game = chess.pgn.Game()
    game.headers.update(Event="Synthetic replay game", White="Stockfish-synth", Black="Stockfish-synth",
                        Site=f"seed {seed}", Result="*")
    node = game
    while not board.is_game_over() and board.ply() < plies:
        infos = engine.analyse(board, chess.engine.Limit(depth=8), multipv=3)
        moves = [i["pv"][0] for i in infos if i.get("pv")]
        mv = rnd.choices(moves, weights=[6, 3, 1][:len(moves)])[0]
        node = node.add_variation(mv)
        board.push(mv)
    game.headers["Result"] = board.result() if board.is_game_over() else "*"
    return game


def convert(path):
    games = []
    with open(path) as f:
        while True:
            g = chess.pgn.read_game(f)
            if g is None:
                break
            b = g.board()
            plies = [{"ply": 0, "san": None, "fen": b.fen()}]
            for mv in g.mainline_moves():
                san = b.san(mv)
                b.push(mv)
                plies.append({"ply": len(plies), "san": san, "fen": b.fen()})
            name = os.path.splitext(os.path.basename(path))[0] + ("" if not games else f"#{len(games) + 1}")
            white, black = g.headers.get("White", "?"), g.headers.get("Black", "?")
            games.append({"name": name, "white": white, "black": black, "result": g.headers.get("Result", "*"),
                          "site": g.headers.get("Site", ""), "synthetic": name.startswith("synth_"),
                          "orient": "black" if black == USER else "white", "plies": plies})
    return games


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--synth", type=int, default=0, help="generate this many synthetic games into pgn/synth_<i>.pgn first")
    ap.add_argument("--synth-plies", type=int, default=60)
    a = ap.parse_args()

    if a.synth:
        sf = shutil.which("stockfish") or "/opt/homebrew/bin/stockfish"
        with chess.engine.SimpleEngine.popen_uci(sf) as eng:
            for i in range(1, a.synth + 1):
                g = synth_game(1000 + i, a.synth_plies, eng)
                with open(os.path.join(PGN_DIR, f"synth_{i}.pgn"), "w") as f:
                    print(g, file=f)
                print(f"synth_{i}.pgn: {len(list(g.mainline_moves()))} plies")

    if a.files:
        files = a.files
    else:   # real games first, synthetic ones interleaved
        real = sorted(glob.glob(os.path.join(PGN_DIR, "game*.pgn")))
        synth = sorted(glob.glob(os.path.join(PGN_DIR, "synth_*.pgn")))
        files = []
        for i in range(max(len(real), len(synth))):
            files += real[i:i + 1] + synth[i:i + 1]
    games = [g for f in files for g in convert(f)]
    pieces = {sym: chess.svg.PIECES[sym] for sym in "PNBRQKpnbrqk"}
    out = os.path.join(PGN_DIR, "games.json")
    with open(out, "w") as f:
        json.dump({"games": games, "pieces": pieces}, f)
    total = 0
    for g in games:
        n = len(g["plies"]) - 1
        total += n
        print(f"{g['name']}: {g['white']} vs {g['black']} {g['result']}, {n} plies, {g['orient']} at the bottom")
    print(f"{total} plies in total = {total * 10 / 60:.1f} min at 10 s per ply -> {out}")


if __name__ == "__main__":
    main()
