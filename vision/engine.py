"""Turn an 8x8 label grid into a FEN and ask Stockfish for the best move."""
import shutil

import chess
import chess.engine

STOCKFISH = shutil.which("stockfish") or "/opt/homebrew/bin/stockfish"


def grid_to_fen(grid, side, turn=None):
    """grid: 8 strings, row 0 = top of the screen as displayed. side: 'white'|'black' = the colour at the
    bottom of the screen. Returns a FEN with castling rights inferred from kings/rooks on home squares."""
    rows = grid if side == "white" else [r[::-1] for r in grid[::-1]]   # normalise to rank 8 first, file a first
    placement = []
    for row in rows:
        s, run = "", 0
        for ch in row:
            if ch == ".":
                run += 1
            else:
                if run: s += str(run); run = 0
                s += ch
        if run: s += str(run)
        placement.append(s)
    board = chess.Board(None)
    board.set_board_fen("/".join(placement))
    castling = ""
    if board.piece_at(chess.E1) == chess.Piece.from_symbol("K"):
        if board.piece_at(chess.H1) == chess.Piece.from_symbol("R"): castling += "K"
        if board.piece_at(chess.A1) == chess.Piece.from_symbol("R"): castling += "Q"
    if board.piece_at(chess.E8) == chess.Piece.from_symbol("k"):
        if board.piece_at(chess.H8) == chess.Piece.from_symbol("r"): castling += "k"
        if board.piece_at(chess.A8) == chess.Piece.from_symbol("r"): castling += "q"
    turn = turn or side
    return f"{'/'.join(placement)} {'w' if turn == 'white' else 'b'} {castling or '-'} - 0 1"


def guess_side(grid):
    """Which colour is at the bottom of the screen? The colour with more material in the bottom half."""
    bottom = "".join(grid[4:]); top = "".join(grid[:4])
    w = sum(ch.isupper() for ch in bottom) - sum(ch.isupper() for ch in top)
    b = sum(ch.islower() for ch in bottom) - sum(ch.islower() for ch in top)
    return "white" if w >= b else "black"


def validate(board):
    """Return a list of human-readable problems with the position (empty = looks legal)."""
    problems = []
    for colour, name in ((chess.WHITE, "white"), (chess.BLACK, "black")):
        kings = len(board.pieces(chess.KING, colour))
        if kings != 1: problems.append(f"{kings} {name} kings")
        if len(board.pieces(chess.PAWN, colour)) > 8: problems.append(f"too many {name} pawns")
    if board.pieces(chess.PAWN, chess.WHITE) & (chess.BB_RANK_1 | chess.BB_RANK_8): problems.append("white pawn on rank 1/8")
    if board.pieces(chess.PAWN, chess.BLACK) & (chess.BB_RANK_1 | chess.BB_RANK_8): problems.append("black pawn on rank 1/8")
    for colour, name in ((chess.WHITE, "white"), (chess.BLACK, "black")):
        for ptype, pname, limit in ((chess.ROOK, "rooks", 2), (chess.KNIGHT, "knights", 2), (chess.BISHOP, "bishops", 2), (chess.QUEEN, "queens", 1)):
            n = len(board.pieces(ptype, colour))
            if n > limit: problems.append(f"{n} {name} {pname}")
    if not problems and not board.is_valid():
        st = board.status()
        names = [n for n, flag in [("too many pieces", chess.STATUS_TOO_MANY_WHITE_PIECES | chess.STATUS_TOO_MANY_BLACK_PIECES),
                                   ("side not to move is in check", chess.STATUS_OPPOSITE_CHECK),
                                   ("bad castling rights", chess.STATUS_BAD_CASTLING_RIGHTS)] if st & flag]
        problems.append(", ".join(names) or f"invalid position ({int(st)})")
    return problems


_engine = None
def engine():
    global _engine
    if _engine is None:
        _engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH)
    return _engine


def best_move(fen, think_s=1.0, multipv=3):
    global _engine
    board = chess.Board(fen)
    if not board.is_valid():
        return []
    try:
        info = engine().analyse(board, chess.engine.Limit(time=think_s), multipv=multipv)
    except chess.engine.EngineTerminatedError:
        _engine = None                      # Stockfish died (it segfaults on some illegal positions); restart once
        info = engine().analyse(board, chess.engine.Limit(time=think_s), multipv=multipv)
    out = []
    for line in info:
        mv = line["pv"][0]
        score = line["score"].pov(board.turn)
        out.append({
            "uci": mv.uci(), "san": board.san(mv),
            "from": chess.square_name(mv.from_square), "to": chess.square_name(mv.to_square),
            "eval": (f"M{score.mate()}" if score.is_mate() else f"{score.score() / 100:+.2f}"),
            "pv": " ".join(board.variation_san(line["pv"][:6]).split()),
        })
    return out


if __name__ == "__main__":
    from classify import START_ROWS
    fen = grid_to_fen(START_ROWS, "white")
    print(fen)
    b = chess.Board(fen); print("problems:", validate(b))
    for m in best_move(fen, 0.5): print(m)
    engine().quit()
