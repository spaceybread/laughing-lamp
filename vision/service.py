"""HTTP service: POST a camera frame, get back the position and Stockfish's best move.

  POST /analyze?side=auto|white|black&think=1.0      body: JPEG/PNG bytes
  POST /calibrate?white_bottom=1                     body: frame of the START position (adds templates)
  GET  /health
"""
import base64
import glob
import pathlib
import sys

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


def ensure_templates():
    """First run: build templates from the start-position samples shipped in vision/samples."""
    if clf.feats:
        return
    for f in sorted(glob.glob(str(pathlib.Path(__file__).parent / "samples" / "*.jpg"))):
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


@app.post("/analyze")
def analyze():
    img = decode(request)
    if img is None:
        return jsonify(ok=False, error="bad image"), 400
    r = detect_board(img)
    if r is None:
        return jsonify(ok=False, error="board not found in frame"), 422
    if r["score"] < 0.35 or not r["dark_ok"]:
        return jsonify(ok=False, error=f"board found but grid looks wrong (score {r['score']:.2f})"), 422
    grid, conf = clf.classify_board(r["board"])
    side_req = request.args.get("side", "auto")
    side = side_req if side_req in ("white", "black") else guess_side(grid)
    fen = grid_to_fen(grid, side)
    board = chess.Board(fen)
    problems = validate(board)
    moves = []
    if not problems:
        moves = best_move(fen, float(request.args.get("think", "1.0")))
    best = moves[0] if moves else None
    # best-move arrow in original frame coordinates, for an overlay on the live video
    arrow = None
    if best:
        Minv = np.linalg.inv(r["M"])
        pts = np.array([square_centre(best["from"], side), square_centre(best["to"], side)], np.float32).reshape(-1, 1, 2)
        fp = cv2.perspectiveTransform(pts, Minv).reshape(2, 2)
        arrow = {"from": fp[0].tolist(), "to": fp[1].tolist()}
    q = r["quad"]
    square_px = float(np.mean([np.linalg.norm(q[(i + 1) % 4] - q[i]) for i in range(4)]) / 8)
    return jsonify(ok=True, fen=fen, side=side, grid=grid, problems=problems, moves=moves, square_px=square_px,
                   board_score=round(r["score"], 2), min_conf=float(conf.min()),
                   quad=r["quad"].tolist(), frame={"w": img.shape[1], "h": img.shape[0]}, arrow=arrow,
                   annotated=annotate(r["board"], grid, conf, best, side))


if __name__ == "__main__":
    ensure_templates()
    app.run(host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 3001, threaded=False)
