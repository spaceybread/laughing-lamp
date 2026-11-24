"""HTTP service: POST a camera frame, get back the position and Stockfish's best move.

  POST /analyze?side=auto|white|black&think=1.0&track=1   body: JPEG/PNG bytes
       track=0 -> raw per-picture reading without game tracking (Part D baseline)
       Every response carries `state`, `timing` (ms per stage) and `raw` (the unmodified
       reading) so the Part C traces can score the tracker against the raw reader.
  POST /calibrate?white_bottom=1                     body: frame of the START position (adds templates)
  GET  /health
"""
import base64
import glob
import pathlib
import sys
import time

import chess
import cv2
import numpy as np
from flask import Flask, jsonify, request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from board import detect_board, SQ, BOARD_PX           # noqa: E402
from classify import Classifier, TEMPLATE_DIR          # noqa: E402
from engine import grid_to_fen, guess_side, validate, best_move   # noqa: E402

app = Flask(__name__)
clf = Classifier()

# Game tracking. The position only changes by legal moves, so each picture is matched against the
# tracked position and its 1- and 2-ply continuations; the best-agreeing candidate wins (robust to a
# few misread squares) and tells us whose turn it is. If nothing agrees, the raw reading is adopted
# after it has been seen three times in a row (new game / lost track).
state = {"board": None, "side": None, "raw_fen": None, "raw_count": 0, "cache": {}}
AGREE_MIN = 58       # of 64 squares


def board_rows(board, side):
    """Piece grid as displayed on the phone (row 0 = top) for the given colour at the bottom."""
    rows = []
    for rank in range(7, -1, -1):
        rows.append("".join((board.piece_at(chess.square(f, rank)).symbol() if board.piece_at(chess.square(f, rank)) else ".") for f in range(8)))
    return rows if side == "white" else [r[::-1] for r in rows[::-1]]


def grid_masks(grid, side):
    """Recognised grid -> {symbol: bitboard} in board coordinates, for fast agreement counting."""
    rows = grid if side == "white" else [r[::-1] for r in grid[::-1]]     # rank 8 first, file a first
    masks = {}
    for i, row in enumerate(rows):
        rank = 7 - i
        for f, ch in enumerate(row):
            masks[ch] = masks.get(ch, 0) | chess.BB_SQUARES[chess.square(f, rank)]
    return masks


def agreement_bb(board, masks):
    """Squares where the board and the recognised grid agree (0..64), via popcounts."""
    n = chess.popcount(masks.get(".", 0) & ~board.occupied)
    for sym, bb in masks.items():
        if sym == ".":
            continue
        p = chess.Piece.from_symbol(sym)
        n += chess.popcount(bb & board.pieces_mask(p.piece_type, p.color))
    return n


def agreement(rows_a, rows_b):
    return sum(x == y for ra, rb in zip(rows_a, rows_b) for x, y in zip(ra, rb))


def match_continuation(board, side, grid):
    """Best of {current, 1-ply, 2-ply} by agreement with the recognised grid -> (board, agreement, plies)."""
    masks = grid_masks(grid, side)
    best = (board.copy(stack=False), agreement_bb(board, masks), 0)
    b = board.copy(stack=False)
    for m1 in list(b.legal_moves):
        b.push(m1)
        a1 = agreement_bb(b, masks)
        if a1 > best[1]: best = (b.copy(stack=False), a1, 1)
        for m2 in list(b.legal_moves):
            b.push(m2)
            a2 = agreement_bb(b, masks)
            if a2 > best[1]: best = (b.copy(stack=False), a2, 2)
            b.pop()
        b.pop()
    return best


LIMITS = {"k": 1, "q": 1, "r": 2, "n": 2, "b": 2, "p": 8}


def repair(grid, scores):
    """Fix the recognised grid where it breaks piece-count rules, using per-square label scores:
    over-counted piece types keep their most confident squares (others take their best other label),
    a missing king goes to the square where 'K'/'k' scores best. Returns a new grid."""
    g = [list(r) for r in grid]
    def margin(r, c, lab):
        sc = scores[r][c]; others = [v for k, v in sc.items() if k != lab]
        return sc.get(lab, -1) - (max(others) if others else -1)
    for _ in range(3):
        changed = False
        for sym in "KQRNBPkqrnbp":
            limit = LIMITS[sym.lower()]
            cells = [(margin(r, c, sym), r, c) for r in range(8) for c in range(8) if g[r][c] == sym]
            if len(cells) > limit:
                cells.sort(reverse=True)
                for _, r, c in cells[limit:]:
                    sc = scores[r][c]
                    alt = max((k for k in sc if k != sym and (k == "." or (len([1 for rr in g for x in rr if x == k]) < LIMITS[k.lower()]
                               and not (k in "Pp" and r in (0, 7))))), key=lambda k: sc[k], default=".")   # no pawns on back ranks
                    g[r][c] = alt; changed = True
        for king in "Kk":
            if not any(king in row for row in g):
                cands = [(scores[r][c].get(king, -9) - scores[r][c].get(g[r][c], 0), r, c) for r in range(8) for c in range(8)
                         if g[r][c] != "." and (g[r][c].isupper() == king.isupper())]
                if cands:
                    _, r, c = max(cands); g[r][c] = king; changed = True
        if not changed:
            break
    return ["".join(r) for r in g]


def user_turn(board, side):
    return (board.turn == chess.WHITE) == (side == "white")


def ensure_templates():
    """First run: build templates from the start-position samples shipped in vision/samples."""
    if clf.feats:
        return
    for f in sorted(glob.glob(str(pathlib.Path(__file__).parent / "samples" / "board_*.jpg"))):
        r = detect_board(cv2.imread(f))
        if r and r["score"] >= 0.4:
            clf.add_board(r["board"])
    if clf.feats:
        clf.save()
    print(f"templates: {len(clf.feats)} squares from samples", flush=True)


def decode(req):
    data = req.get_data()
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    return img


def annotate(board, grid, conf, move, side):
    """Warped board with recognised labels and the best-move arrow, as a JPEG data URL."""
    out = board.copy()
    for r in range(8):
        for c in range(8):
            ch = grid[r][c]
            if ch == ".":
                continue
            col = (0, 0, 255) if conf[r, c] < 0.01 else (0, 200, 0)
            cv2.putText(out, ch, (c * SQ + 4, r * SQ + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
            cv2.putText(out, ch, (c * SQ + 4, r * SQ + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)
    if move:
        p1, p2 = square_centre(move["from"], side), square_centre(move["to"], side)
        for p, col in ((p1, (60, 200, 60)), (p2, (40, 40, 230))):        # green = piece to move, red = where to put it
            cv2.circle(out, p, 30, (0, 0, 0), -1); cv2.circle(out, p, 24, col, -1)
    small = cv2.resize(out, (400, 400), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()


def square_centre(name, side):
    """Centre of a square (e.g. 'e4') in warped-board pixels, for the given bottom colour."""
    file, rank = ord(name[0]) - 97, int(name[1]) - 1
    if side == "white":
        c, r = file, 7 - rank
    else:
        c, r = 7 - file, rank
    return (int(c * SQ + SQ / 2), int(r * SQ + SQ / 2))


@app.get("/health")
def health():
    return jsonify(ok=True, templates=len(clf.feats))


@app.post("/calibrate")
def calibrate():
    img = decode(request)
    if img is None:
        return jsonify(ok=False, error="bad image"), 400
    r = detect_board(img)
    if r is None or r["score"] < 0.4:
        return jsonify(ok=False, error="board not found"), 422
    clf.add_board(r["board"], white_bottom=request.args.get("white_bottom", "1") == "1")
    clf.save()
    return jsonify(ok=True, templates=len(clf.feats))


class Timer:
    """Stage timings in ms for the trace: t.mark('detect') closes the current stage."""
    def __init__(self):
        self.t0 = self.last = time.perf_counter(); self.ms = {}
    def mark(self, name):
        now = time.perf_counter(); self.ms[name] = round((now - self.last) * 1000, 1); self.last = now
    def done(self):
        self.ms["total"] = round((time.perf_counter() - self.t0) * 1000, 1); return self.ms


@app.post("/analyze")
def analyze():
    tm = Timer()
    img = decode(request)
    if img is None:
        return jsonify(ok=False, error="bad image", state="badimage", timing=tm.done()), 400
    tm.mark("decode")
    r = detect_board(img)
    tm.mark("detect")
    if r is None:
        return jsonify(ok=False, error="board not found in frame", state="nodetect", timing=tm.done()), 422
    if r["score"] < 0.35 or not r["dark_ok"]:
        return jsonify(ok=False, error=f"board found but grid looks wrong (score {r['score']:.2f})", state="badgrid",
                       board_score=round(r["score"], 2), timing=tm.done()), 422
    grid, conf, scores = clf.classify_board(r["board"], with_scores=True)
    tm.mark("classify")
    think = float(request.args.get("think", "1.0"))
    side_req = request.args.get("side", "auto")
    track = request.args.get("track", "1") != "0"
    side = side_req if side_req in ("white", "black") else (state["side"] or guess_side(grid))
    raw_grid = grid
    raw = {"fen": grid_to_fen(raw_grid, side), "problems": validate(chess.Board(grid_to_fen(raw_grid, side)))}
    common = dict(grid=grid, board_score=round(r["score"], 2), min_conf=float(conf.min()), side=side, raw=raw,
                  quad=r["quad"].tolist(), frame={"w": img.shape[1], "h": img.shape[0]})

    def reply(status, **kw):
        """Attach timings and how far the raw reading is from the position we answered with."""
        kw["timing"] = tm.done()
        if kw.get("fen"):
            try: raw["agree"] = agreement(board_rows(chess.Board(kw["fen"]), side), raw_grid)
            except ValueError: pass
        return jsonify(**kw), status

    if not track:
        # Part D baseline: believe each picture on its own (repair + validate), assume it is the user's turn.
        repaired = repair(grid, scores)
        fen = grid_to_fen(repaired, side)
        problems = validate(chess.Board(fen))
        tm.mark("match")
        if problems:
            return reply(200, ok=False, state="raw_unclear", error="position unclear: " + ", ".join(problems), fen=fen,
                         annotated=annotate(r["board"], repaired, conf, None, side), **common)
        cached = fen in state["cache"]
        if not cached:
            state["cache"][fen] = best_move(fen, think)
        tm.mark("engine")
        moves = state["cache"][fen]
        return finish(r, img, repaired, conf, side, chess.Board(fen), moves, 64, "raw", cached, reply, common)

    # 1) tracked game: pick the legal continuation that best explains the picture
    tracked = None; agree = 0; plies = None; lost = False; st = "tracked"
    if state["board"] is not None and side == state["side"]:
        tracked, agree, plies = match_continuation(state["board"], side, grid)
        if agree >= AGREE_MIN:
            state["board"] = tracked
            state["raw_fen"], state["raw_count"], state["raw_grid"] = None, 0, None
        else:
            tracked = None; lost = True
    # 2) no track (or lost it): adopt the raw reading once it is stable and legal
    if tracked is None:
        # strong prior: every game begins from the start position; snap to it when close
        start = chess.Board()
        for cand_side in ((side,) if side_req in ("white", "black") else ("white", "black")):
            if agreement(board_rows(start, cand_side), grid) >= 56:
                side = cand_side
                grid = board_rows(start, side)
                st = "start"
                break
        else:
            repaired = repair(grid, scores)
            if agreement(repaired, grid) < 61:      # more than 3 squares had to be changed: too unreliable to adopt
                tm.mark("match")
                return reply(200, ok=False, state="unreadable", lost=lost, error="position unreadable (too many doubtful squares)",
                             fen=grid_to_fen(repaired, side), annotated=annotate(r["board"], grid, conf, None, side), **common)
            grid = repaired
            st = "adopted"
        fen = grid_to_fen(grid, side)
        problems = validate(chess.Board(fen))
        prev = state.get("raw_grid")
        if prev is not None and agreement(prev, grid) >= 62:     # same position up to a couple of noisy squares
            state["raw_count"] += 1
        else:
            state["raw_count"] = 1
        state["raw_grid"], state["raw_fen"] = grid, fen
        need = 2 if state["board"] is None else 3
        if problems:
            tm.mark("match")
            return reply(200, ok=False, state="unclear", lost=lost, error="position unclear: " + ", ".join(problems), fen=fen,
                         annotated=annotate(r["board"], grid, conf, None, side), **common)
        if state["raw_count"] < need:
            tm.mark("match")
            return reply(200, ok=False, state="confirming", lost=lost, error=f"confirming position ({state['raw_count']}/{need})…", fen=fen,
                         annotated=annotate(r["board"], grid, conf, None, side), **common)
        state["board"], state["side"] = chess.Board(fen), side
        tracked = state["board"]; agree = agreement(board_rows(tracked, side), grid)
    tm.mark("match")

    board = tracked
    fen = board.fen()
    if not user_turn(board, side):
        return reply(200, ok=False, clear=True, state=st, plies=plies, lost=lost, error="opponent to move", fen=fen, agree=agree,
                     annotated=annotate(r["board"], grid, conf, None, side), **common)
    if board.is_game_over():
        return reply(200, ok=False, clear=True, state="over", plies=plies, error="game over: " + board.result(), fen=fen, agree=agree, **common)
    cached = fen in state["cache"]
    if not cached:
        state["cache"][fen] = best_move(fen, think)
    tm.mark("engine")
    moves = state["cache"][fen]
    return finish(r, img, board_rows(board, side), conf, side, board, moves, agree, st, cached, reply, common, plies=plies, lost=lost)


def finish(r, img, shown_grid, conf, side, board, moves, agree, st, cached, reply, common, plies=None, lost=False):
    """Successful answer: best move, arrow in frame coordinates, annotated board."""
    best = moves[0] if moves else None
    arrow = None
    if best:
        Minv = np.linalg.inv(r["M"])
        pts = np.array([square_centre(best["from"], side), square_centre(best["to"], side)], np.float32).reshape(-1, 1, 2)
        fp = cv2.perspectiveTransform(pts, Minv).reshape(2, 2)
        arrow = {"from": fp[0].tolist(), "to": fp[1].tolist()}
    q = r["quad"]
    square_px = float(np.mean([np.linalg.norm(q[(i + 1) % 4] - q[i]) for i in range(4)]) / 8)
    return reply(200, ok=True, fen=board.fen(), problems=[], moves=moves, square_px=square_px, arrow=arrow, agree=agree,
                 state=st, plies=plies, lost=lost, cached=cached,
                 annotated=annotate(r["board"], shown_grid, conf, best, side), **common)


@app.post("/reset")
def reset():
    """Forget the tracked game (e.g. when starting a new one and the tracker is stuck)."""
    state.update(board=None, side=None, raw_fen=None, raw_count=0, raw_grid=None)
    if request.args.get("cache") == "1":       # also forget engine results (start every scenario cold)
        state["cache"] = {}
    return jsonify(ok=True, cache=len(state["cache"]))


if __name__ == "__main__":
    ensure_templates()
    app.run(host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 3001, threaded=False)
