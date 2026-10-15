"""Piece recognition on a warped 800x800 board, by nearest-neighbour against calibrated templates.

Templates come from frames of the standard start position (every chess.com game begins there),
so they match this phone, theme, camera and blur. Features are background-subtracted so a piece
looks alike on light and dark squares:  signed luminance difference from the square's own
background colour, on a 32x32 centre crop.  Empty squares are the templates with no piece.
"""
import json
import pathlib

import cv2
import numpy as np

from board import SQ, BOARD_PX

FEAT = 32
CROP = 0.84          # fraction of the square used (pieces are drawn with a margin)
TEMPLATE_DIR = pathlib.Path(__file__).parent / "templates" / "calib"
START_ROWS = ["rnbqkbnr", "pppppppp", "........", "........", "........", "........", "PPPPPPPP", "RNBQKBNR"]


def square_crop(board, r, c):
    m = int(SQ * (1 - CROP) / 2)
    return board[r * SQ + m:(r + 1) * SQ - m, c * SQ + m:(c + 1) * SQ - m]


def background_colour(board, r, c):
    """Estimate the square's own background from its border ring (pieces sit in the middle)."""
    cell = board[r * SQ:(r + 1) * SQ, c * SQ:(c + 1) * SQ]
    ring = np.concatenate([cell[:6].reshape(-1, 3), cell[-6:].reshape(-1, 3), cell[:, :6].reshape(-1, 3), cell[:, -6:].reshape(-1, 3)])
    return np.median(ring, axis=0)


def features(board, r, c):
    """(FEAT x FEAT) signed luminance difference from background, in [-1, 1]."""
    crop = cv2.resize(square_crop(board, r, c), (FEAT, FEAT), interpolation=cv2.INTER_AREA).astype(np.float32)
    bg = background_colour(board, r, c).astype(np.float32)
    lum = crop @ np.array([0.114, 0.587, 0.299], np.float32)
    bg_lum = float(bg @ np.array([0.114, 0.587, 0.299], np.float32))
    chroma = np.abs(crop - bg).sum(2) / 3.0            # colour distance (catches dark pieces on dark squares)
    signed = (lum - bg_lum) / 255.0
    return np.stack([signed, chroma / 255.0], 0)


def _corr(a, b):
    a = a - a.mean(); b = b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))


class Classifier:
    def __init__(self, template_dir=TEMPLATE_DIR):
        self.dir = pathlib.Path(template_dir)
        self.feats, self.labels = [], []
        self.load()

    # ---- templates ----------------------------------------------------------
    def load(self):
        self.feats, self.labels = [], []
        f = self.dir / "templates.npz"
        if f.exists():
            d = np.load(f, allow_pickle=True)
            self.feats, self.labels = list(d["feats"]), list(d["labels"])

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.dir / "templates.npz", feats=np.array(self.feats), labels=np.array(self.labels))

    def add_board(self, board, rows=START_ROWS, white_bottom=True):
        """Add all 64 squares of a labelled board. rows[0] is the top rank as displayed."""
        if not white_bottom:
            rows = [r[::-1] for r in rows[::-1]]
        for r in range(8):
            for c in range(8):
                self.feats.append(features(board, r, c))
                self.labels.append(rows[r][c])

    # ---- inference ----------------------------------------------------------
    def _matrix(self):
        """Templates as zero-mean unit-norm row vectors, per channel (cached)."""
        n = len(self.feats)
        if getattr(self, "_mat_n", -1) != n:
            T = np.array(self.feats, np.float32).reshape(n, 2, -1)
            T = T - T.mean(2, keepdims=True)
            T /= np.linalg.norm(T, axis=2, keepdims=True) + 1e-6
            self._T0, self._T1 = T[:, 0], T[:, 1]
            self._labs = np.array(self.labels)
            self._mat_n = n
        return self._T0, self._T1, self._labs

    def classify_square(self, board, r, c, shifts=2):
        """Return (label, confidence, runner_up). Nearest template by correlation (0.6 signed-luminance
        + 0.4 chroma channel), with +-shifts px translation tolerance on the query. Vectorised."""
        f = features(board, r, c)
        energy = float(np.abs(f[0]).mean() + f[1].mean())
        T0, T1, labs = self._matrix()
        qs = np.array([np.roll(f, (dr, dc), axis=(1, 2)) for dr in range(-shifts, shifts + 1) for dc in range(-shifts, shifts + 1)], np.float32)
        Q = qs.reshape(len(qs), 2, -1)
        Q = Q - Q.mean(2, keepdims=True)
        Q /= np.linalg.norm(Q, axis=2, keepdims=True) + 1e-6
        scores = (0.6 * Q[:, 0] @ T0.T + 0.4 * Q[:, 1] @ T1.T).max(0)    # best shift per template
        best = {}
        for lab in np.unique(labs):
            best[lab] = float(scores[labs == lab].max())
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        label, score = ranked[0]
        runner = ranked[1] if len(ranked) > 1 else (None, -1)
        if energy < 0.03:          # flat square: correlation is meaningless, it is empty
            return ".", 1.0, runner
        return str(label), float(score - runner[1]), runner

    def classify_board(self, board):
        grid, conf = [], np.zeros((8, 8), np.float32)
        for r in range(8):
            row = ""
            for c in range(8):
                lab, cf, _ = self.classify_square(board, r, c)
                row += lab; conf[r, c] = cf
            grid.append(row)
        return grid, conf


if __name__ == "__main__":
    # Leave-one-out test on start-position samples: python classify.py samples/*.jpg
    import sys
    from board import detect_board
    boards = []
    for f in sys.argv[1:]:
        r = detect_board(cv2.imread(f))
        if r is None or r["score"] < 0.4:
            print(f, "detection failed"); continue
        boards.append((f, r["board"]))
    total = correct = 0
    for i, (f, b) in enumerate(boards):
        clf = Classifier(template_dir="/dev/null")
        for j, (_, b2) in enumerate(boards):
            if j != i:
                clf.add_board(b2)
        grid, conf = clf.classify_board(b)
        errs = [(r, c, START_ROWS[r][c], grid[r][c]) for r in range(8) for c in range(8) if grid[r][c] != START_ROWS[r][c]]
        total += 64; correct += 64 - len(errs)
        print(f"{f}: {64 - len(errs)}/64  min conf {conf.min():.3f}  errors {errs[:10]}")
        print("\n".join(grid))
    print(f"overall {correct}/{total}")
