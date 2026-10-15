"""Locate the chess board in a camera frame of a phone screen and warp it to a square.

Approach (theme-agnostic):
  1. Phone screen = bright region; take its largest contour.
  2. Inside it, the two most common mid-tone colours are the two square colours
     (the board dominates the screen in the chess.com app).
  3. Mask the DARK square colour only (the light one merges with white UI), take the
     largest blob, fit a quad from the four longest hull edges, warp.
  4. Refine: find the 7 internal grid lines in the warped image (top/bottom and
     left/right bands), extrapolate the outer lines, intersect -> exact corners, re-warp.
  5. Validate by checking the 8x8 alternation of the warped image.
"""
import cv2
import numpy as np

BOARD_PX = 800          # warped board size (100 px per square)
SQ = BOARD_PX // 8


def _order_quad(pts):
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(1); d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def _line(p, q):
    return np.cross([p[0], p[1], 1.0], [q[0], q[1], 1.0])


def _intersect(l1, l2):
    p = np.cross(l1, l2)
    return p[:2] / p[2]


def _quad_from_hull_edges(pts):
    """Corners from robust line fits to the four sides of the convex hull.
    A minAreaRect gives the rough sides; hull points are assigned to the nearest side,
    the side lines are refit (Huber, then outliers dropped) and intersected. Robust to the
    two board corners that are light squares (missing from a dark-square mask)."""
    hull = cv2.convexHull(pts.reshape(-1, 1, 2).astype(np.float32)).reshape(-1, 2)
    box = _order_quad(cv2.boxPoints(cv2.minAreaRect(hull)))
    sides = [(box[k], box[(k + 1) % 4]) for k in range(4)]
    side_len = [np.linalg.norm(q - p) for p, q in sides]

    def dist(p, a, b):
        u, v = b - a, p - a
        return abs(u[0] * v[1] - u[1] * v[0]) / (np.linalg.norm(u) + 1e-6)

    assigned = [[] for _ in range(4)]
    for h in hull:
        d = [dist(h, a, b) for a, b in sides]
        k = int(np.argmin(d))
        if d[k] < 0.15 * side_len[k]:
            assigned[k].append(h)
    lines = []
    for k in range(4):
        P = np.array(assigned[k], np.float32)
        if len(P) < 2:
            a, b = sides[k]; lines.append(_line(a, b)); continue
        for _ in range(2):
            vx, vy, x0, y0 = cv2.fitLine(P, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
            a = np.array([x0, y0]); b = a + np.array([vx, vy])
            res = np.array([dist(q, a, b) for q in P])
            keep = res < max(3.0, 0.03 * side_len[k])
            if keep.sum() >= 2:
                P = P[keep]
        lines.append(_line(a, b))
    corners = [_intersect(lines[k], lines[(k + 1) % 4]) for k in range(4)]
    return _order_quad(corners)


def normalize_exposure(img, target=230):
    """Scale brightness so the 99.5th percentile of V hits `target` (dark rooms, auto-exposure)."""
    v = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 2]
    p = float(np.percentile(v, 99.5))
    if p < 1:
        return img
    gain = min(4.0, target / p)
    if gain <= 1.05:
        return img
    return np.clip(img.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def find_screen(img):
    """Return a mask of the phone screen (bright region) and its bounding quad, or (None, None)."""
    v = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 2]
    thr, _ = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = (v > max(thr, 60)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None, None
    cnt = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(cnt) < 0.02 * img.shape[0] * img.shape[1]:
        return None, None
    screen = np.zeros_like(mask)
    cv2.drawContours(screen, [cv2.convexHull(cnt)], -1, 255, -1)
    return screen, cv2.boxPoints(cv2.minAreaRect(cnt))


def square_colours(img, region_mask, k=6):
    """K-means the colours inside region_mask; return (dark, light) square colours (BGR)."""
    pix = img[region_mask > 0].reshape(-1, 3).astype(np.float32)
    if len(pix) > 20000:
        pix = pix[np.random.default_rng(0).choice(len(pix), 20000, replace=False)]
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0)
    _, labels, centers = cv2.kmeans(pix, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k)
    order = np.argsort(-counts)
    brightest = max(c.mean() for c in centers)
    picked = [centers[i] for i in order if centers[i].mean() > 0.3 * brightest][:2]   # skip dark UI / bezel
    if len(picked) < 2 or abs(picked[0].mean() - picked[1].mean()) < 15:
        return None
    return sorted(picked, key=lambda c: c.mean())


def colour_mask(img, colour, region_mask, tol=36):
    lab = cv2.cvtColor(cv2.GaussianBlur(img, (7, 7), 0), cv2.COLOR_BGR2LAB).astype(np.int16)   # blur: gain amplifies noise
    c_lab = cv2.cvtColor(np.uint8([[colour]]), cv2.COLOR_BGR2LAB)[0, 0].astype(np.int16)
    m = (np.abs(lab - c_lab).sum(2) < tol).astype(np.uint8) * 255
    m &= region_mask
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    return m


def square_blobs(mask):
    """Contours of the individual dark squares: everything at least 20% of the biggest blob."""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return []
    areas = [cv2.contourArea(c) for c in cnts]
    big = max(areas)
    out = []
    for c, a in zip(cnts, areas):
        if a < 0.2 * big:
            continue
        x, y, w, h = cv2.boundingRect(c)
        if 0.33 <= w / h <= 3.0:        # drop long thin strips (phone edge, UI bars); pieces can split squares
            out.append(c)
    return out


def warp(img, quad, size=BOARD_PX):
    dst = np.array([[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]], np.float32)
    M = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(img, M, (size, size)), M


def _grid_positions(profile, sq=SQ, search=0.3):
    """Given a 1-D edge profile of length 8*sq, locate internal lines 1..7 and fit x_i = a + b*i."""
    xs, idx = [], []
    for i in range(1, 8):
        lo, hi = int((i - search) * sq), int((i + search) * sq)
        seg = profile[lo:hi]
        if seg.max() <= 0:
            continue
        xs.append(lo + int(np.argmax(seg))); idx.append(i)
    if len(xs) < 4:
        return None
    b, a = np.polyfit(idx, xs, 1)
    return a, b   # position(i) = a + b*i


def refine_quad(img, quad):
    """One refinement step: re-detect the grid in the warped image, return a corrected quad in image coords."""
    board, M = warp(img, quad)
    g = cv2.GaussianBlur(cv2.cvtColor(board, cv2.COLOR_BGR2GRAY), (5, 5), 0).astype(np.float32)
    gx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    H = BOARD_PX; band = H // 3
    # vertical grid lines, measured in the top and bottom thirds
    fits_x = [_grid_positions(gx[:band].sum(0)), _grid_positions(gx[-band:].sum(0))]
    fits_y = [_grid_positions(gy[:, :band].sum(1)), _grid_positions(gy[:, -band:].sum(1))]
    if any(f is None for f in fits_x + fits_y):
        return None
    yc = [band / 2, H - band / 2]; xc = [band / 2, H - band / 2]
    def vline(i):  # line through the two measured positions of vertical grid line i
        return _line((fits_x[0][0] + fits_x[0][1] * i, yc[0]), (fits_x[1][0] + fits_x[1][1] * i, yc[1]))
    def hline(i):
        return _line((xc[0], fits_y[0][0] + fits_y[0][1] * i), (xc[1], fits_y[1][0] + fits_y[1][1] * i))
    corners_w = np.array([_intersect(hline(0), vline(0)), _intersect(hline(0), vline(8)),
                          _intersect(hline(8), vline(8)), _intersect(hline(8), vline(0))], np.float32)
    ideal = np.array([[0, 0], [H, 0], [H, H], [0, H]], np.float32)
    if np.abs(corners_w - ideal).max() > 0.6 * SQ:      # implausible correction: keep the previous quad
        return None
    Minv = np.linalg.inv(M)
    corners_img = cv2.perspectiveTransform(corners_w.reshape(-1, 1, 2), Minv).reshape(4, 2)
    return _order_quad(corners_img)


def checker_score(board):
    """How strongly does the warped image alternate like an 8x8 board? ~1.0 = perfect.
    Also returns True if the bottom-left square is the darker parity (as it must be)."""
    g = cv2.cvtColor(board, cv2.COLOR_BGR2GRAY).astype(np.float32)
    means = np.zeros((8, 8), np.float32)
    for r in range(8):
        for c in range(8):
            cell = g[r * SQ:(r + 1) * SQ, c * SQ:(c + 1) * SQ]
            ring = np.concatenate([cell[:8].ravel(), cell[-8:].ravel(), cell[:, :8].ravel(), cell[:, -8:].ravel()])
            means[r, c] = ring.mean()
    parity = (np.indices((8, 8)).sum(0) % 2) == 0        # a8 parity (top-left); bottom-left (7,0) has parity 1
    a, b = means[parity].mean(), means[~parity].mean()
    sep = abs(a - b)
    within = (means[parity].std() + means[~parity].std()) / 2 + 1e-3
    return float(sep / (sep + within)), bool(b < a)


def square_blobs_adaptive(img, block=101, c=10):
    """Lighting-invariant square finder. Adaptive thresholding makes dark squares (pixel below the
    local mean) and light squares (above it) into blobs regardless of exposure gradients. Keep the
    blobs of similar size that form one spatial cluster: those are the 64 squares."""
    g = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    masks = [cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block, c),   # darker than local mean
             cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, block, -c)]      # lighter than local mean
    cands = []
    for m in masks:
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in cnts:
            area = cv2.contourArea(cnt)
            if area < 150:
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            if not (0.5 <= w / h <= 2.0):
                continue
            if area / (w * h) < 0.45:
                continue
            cands.append((cnt, area, (x + w / 2, y + h / 2)))
    if len(cands) < 12:
        return []
    # typical square area: median of the larger half of candidates (pieces are smaller blobs)
    areas = np.array(sorted(c[1] for c in cands))
    typ = float(np.median(areas[len(areas) // 2:]))
    sq = [c for c in cands if 0.45 * typ <= c[1] <= 1.8 * typ]
    if len(sq) < 12:
        return []
    # spatial coherence: link blobs whose centres are within ~2 square widths; keep the biggest group
    side = np.sqrt(typ)
    cen = np.array([c[2] for c in sq], np.float32)
    n = len(sq); parent = list(range(n))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    d = np.linalg.norm(cen[:, None] - cen[None], axis=2)
    for i in range(n):
        for j in range(i + 1, n):
            if d[i, j] < 2.2 * side:
                parent[find(i)] = find(j)
    roots = [find(i) for i in range(n)]
    big = max(set(roots), key=roots.count)
    return [sq[i][0] for i in range(n) if roots[i] == big]


def _comb_search(gx, gy, p_min, p_max):
    """Find the 8x8 grid (period P, offset ox, oy) in an edge image pair by maximising the summed
    edge energy along nine vertical and nine horizontal lines. Uses prefix sums; exhaustive."""
    Hs, Ws = gx.shape
    k = np.ones((1, 3), np.float32)
    gx = cv2.dilate(gx, np.ones((1, 3), np.uint8)); gy = cv2.dilate(gy, np.ones((3, 1), np.uint8))   # 1px tolerance
    Sx = np.vstack([np.zeros((1, Ws), np.float32), np.cumsum(gx, 0)])     # Sx[y, x] = sum gx[:y, x]
    Sy = np.hstack([np.zeros((Hs, 1), np.float32), np.cumsum(gy, 1)])     # Sy[y, x] = sum gy[y, :x]
    best = (-1, None)
    for P in range(p_min, p_max + 1):
        L = 8 * P
        if L >= Hs or L >= Ws:
            continue
        nY, nX = Hs - L, Ws - L                   # valid top-left offsets
        A = (Sx[L:Hs + 1] - Sx[:Hs + 1 - L])[:nY]       # (nY, Ws): vertical-line energy over [oy, oy+L)
        B = (Sy[:, L:Ws + 1] - Sy[:, :Ws + 1 - L])[:, :nX]   # (Hs, nX): horizontal-line energy over [ox, ox+L)
        sv = sum(A[:, i * P:i * P + nX] for i in range(9))            # (nY, nX)
        sh = sum(B[i * P:i * P + nY, :] for i in range(9))            # (nY, nX)
        sc = (sv + sh) / (18.0 * L)
        idx = np.unravel_index(np.argmax(sc), sc.shape)
        if sc[idx] > best[0]:
            best = (float(sc[idx]), (P, int(idx[1]), int(idx[0])))
    return best


def propose_from_screen(img, width=480):
    """Warp the bright phone-screen region to a frontal rectangle and comb-search it for the board.
    Returns a quad in image coords, or None."""
    screen, box = find_screen(img)
    if screen is None:
        return None
    box = _order_quad(box)
    w = int(max(np.linalg.norm(box[1] - box[0]), np.linalg.norm(box[2] - box[3])))
    h = int(max(np.linalg.norm(box[3] - box[0]), np.linalg.norm(box[2] - box[1])))
    if w < 40 or h < 40:
        return None
    scale = width / w
    Ws, Hs = width, int(h * scale)
    dst = np.array([[0, 0], [Ws - 1, 0], [Ws - 1, Hs - 1], [0, Hs - 1]], np.float32)
    Ms = cv2.getPerspectiveTransform(box, dst)
    flat = cv2.warpPerspective(img, Ms, (Ws, Hs))
    g = cv2.GaussianBlur(cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
    gx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)); gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    cap = np.percentile(np.concatenate([gx.ravel(), gy.ravel()]), 98) + 1e-3     # don't let a few strong edges dominate
    gx = np.minimum(gx, cap); gy = np.minimum(gy, cap)
    short = min(Ws, Hs)
    score, hit = _comb_search(gx, gy, max(8, int(0.35 * short / 8)), int(short / 8))
    if hit is None:
        return None
    P, ox, oy = hit
    quad_s = np.array([[ox, oy], [ox + 8 * P, oy], [ox + 8 * P, oy + 8 * P], [ox, oy + 8 * P]], np.float32)
    quad = cv2.perspectiveTransform(quad_s.reshape(-1, 1, 2), np.linalg.inv(Ms)).reshape(4, 2)
    return _order_quad(quad)


def detect_board(img, debug_dir=None, tag="frame"):
    """Return dict(board=warped BGR 800x800, quad=4x2, score=float, dark_ok=bool) or None."""
    img = normalize_exposure(img)
    # proposals from independent heuristics; each is refined and the best-alternating one wins
    proposals = []
    q = propose_from_screen(img)
    if q is not None: proposals.append(("screen-comb", q))
    blobs = square_blobs_adaptive(img)
    if len(blobs) >= 12: proposals.append(("adaptive", _quad_from_hull_edges(np.concatenate(blobs))))
    screen_quad = None; mask = None
    screen, screen_quad = find_screen(img)
    if screen is not None:
        cols = square_colours(img, screen)
        if cols is not None:
            mask = colour_mask(img, cols[0], screen)
            cb = square_blobs(mask)
            if len(cb) >= 8: proposals.append(("colour", _quad_from_hull_edges(np.concatenate(cb))))
    if not proposals:
        return None
    best = None
    for method, quad0 in proposals:
        quad = quad0
        for _ in range(2):
            qq = refine_quad(img, quad)
            if qq is None:
                break
            quad = qq
        board, M = warp(img, quad)
        score, dark_ok = checker_score(board)
        if best is None or score > best[0]:
            best = (score, dark_ok, method, quad0, quad, board, M)
    score, dark_ok, method, quad0, quad, board, M = best
    if debug_dir:
        dbg = img.copy()
        if screen_quad is not None: cv2.polylines(dbg, [screen_quad.astype(int)], True, (255, 0, 0), 1)
        cv2.polylines(dbg, [quad0.astype(int)], True, (0, 200, 255), 1)
        cv2.polylines(dbg, [quad.astype(int)], True, (0, 255, 0), 2)
        cv2.imwrite(f"{debug_dir}/{tag}_detect.jpg", dbg)
        if mask is not None: cv2.imwrite(f"{debug_dir}/{tag}_mask.png", mask)

        grid = board.copy()
        for i in range(1, 8):
            cv2.line(grid, (i * SQ, 0), (i * SQ, BOARD_PX), (0, 0, 255), 1)
            cv2.line(grid, (0, i * SQ), (BOARD_PX, i * SQ), (0, 0, 255), 1)
        cv2.imwrite(f"{debug_dir}/{tag}_board.jpg", grid)
    return {"board": board, "quad": quad, "M": M, "score": score, "dark_ok": dark_ok, "method": method}


if __name__ == "__main__":
    import sys
    for f in sys.argv[1:]:
        img = cv2.imread(f)
        tag = f.split("/")[-1].rsplit(".", 1)[0]
        r = detect_board(img, debug_dir="vision/debug", tag=tag)
        print(f, "-> none" if r is None else f"-> {r['method']} score {r['score']:.2f} dark_ok {r['dark_ok']} quad {r['quad'].astype(int).tolist()}")
