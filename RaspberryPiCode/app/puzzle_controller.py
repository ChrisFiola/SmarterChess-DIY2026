# -*- coding: utf-8 -*-
"""Daily puzzle controller (Phase 1).

Goals:
  - Fetch daily puzzle from Lichess
  - Show the starting position (FEN) on the LCD so the user can set up pieces
  - Validate user-entered moves locally against the puzzle solution
  - Show "Correct" / "Try again" feedback

This intentionally does NOT submit results back to Lichess yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
from collections import defaultdict

import chess  # type: ignore
import chess.pgn  # type: ignore

from piDisplay import Display
from piSerial import BoardLink
from .lichess_client import LichessClient


# -------------------- LED-guided physical setup helpers --------------------


def _dist(a: str, b: str) -> int:
    af, ar = ord(a[0]) - 97, int(a[1]) - 1
    bf, br = ord(b[0]) - 97, int(b[1]) - 1
    return abs(af - bf) + abs(ar - br)


def _pieces_by_type_and_color(brd: chess.Board):
    buckets = defaultdict(list)  # (color, piece_type) -> [sq,...]
    for sq in chess.SQUARES:
        p = brd.piece_at(sq)
        if not p:
            continue
        buckets[(p.color, p.piece_type)].append(chess.square_name(sq))
    for k in buckets:
        buckets[k].sort()
    return buckets



def _compute_place_steps_from_fen(target_fen: str):
    """Return a simple list of placement steps for an *empty* physical board.

    Each step: (side_char, square, piece_symbol)
      side_char: 'w' or 'b'
      square: 'e4'
      piece_symbol: like 'P','n', etc (case per chess lib)
    """
    brd = chess.Board(target_fen)
    steps = []
    for sq in chess.SQUARES:
        p = brd.piece_at(sq)
        if not p:
            continue
        side = "w" if p.color == chess.WHITE else "b"
        steps.append((side, chess.square_name(sq), p.symbol()))
    # Order: White then Black, then piece type, then square
    order_pt = {chess.KING: 0, chess.QUEEN: 1, chess.ROOK: 2, chess.BISHOP: 3, chess.KNIGHT: 4, chess.PAWN: 5}
    def key(t):
        side, square, sym = t
        ptype = chess.Piece.from_symbol(sym).piece_type
        return (0 if side=="w" else 1, order_pt.get(ptype, 9), square)
    steps.sort(key=key)
    return steps

def _compute_setup_actions_from_start(target_fen: str):
    """Compute physical actions to reach target_fen from standard start.

    Assumes the physical board starts in standard initial setup.
    Returns list of tuples:
      ("move", side_char, from_sq, to_sq, piece_symbol)
      ("remove", side_char, from_sq, "", piece_symbol)
    """
    start = chess.Board()
    target = chess.Board(target_fen)

    start_b = _pieces_by_type_and_color(start)
    targ_b = _pieces_by_type_and_color(target)

    actions = []
    used_start = set()

    for (color, ptype), targ_sqs in sorted(
        targ_b.items(), key=lambda x: (x[0][0], x[0][1])
    ):
        avail = [s for s in start_b.get((color, ptype), []) if s not in used_start]
        for t in targ_sqs:
            if not avail:
                continue
            best = min(avail, key=lambda s: _dist(s, t))
            avail.remove(best)
            used_start.add(best)
            sym = chess.Piece(ptype, color).symbol()
            if best != t:
                actions.append(
                    ("move", "w" if color == chess.WHITE else "b", best, t, sym)
                )

    for (color, ptype), start_sqs in start_b.items():
        for s in start_sqs:
            if s in used_start:
                continue
            sym = chess.Piece(ptype, color).symbol()
            actions.append(("remove", "w" if color == chess.WHITE else "b", s, "", sym))

    removes_b = sorted(
        [a for a in actions if a[0] == "remove" and a[1] == "b"], key=lambda x: x[2]
    )
    moves_b = sorted(
        [a for a in actions if a[0] == "move" and a[1] == "b"],
        key=lambda x: (x[2], x[3]),
    )
    removes_w = sorted(
        [a for a in actions if a[0] == "remove" and a[1] == "w"], key=lambda x: x[2]
    )
    moves_w = sorted(
        [a for a in actions if a[0] == "move" and a[1] == "w"],
        key=lambda x: (x[2], x[3]),
    )

    return removes_b + moves_b + removes_w + moves_w


def _piece_name(sym: str) -> str:
    u = sym.upper()
    return {
        "P": "PAWN",
        "N": "KNIGHT",
        "B": "BISHOP",
        "R": "ROOK",
        "Q": "QUEEN",
        "K": "KING",
    }.get(u, "PIECE")


@dataclass
class PuzzleState:
    puzzle_id: str
    fen_start: str
    solution: List[str]  # UCI moves
    idx: int = 0  # next expected move index


def _board_from_pgn_at_ply(pgn_text: str, initial_ply: int) -> chess.Board:
    """Parse PGN and return a board positioned at 'initial_ply'."""
    game = chess.pgn.read_game(io := __import__("io").StringIO(pgn_text))
    if game is None:
        return chess.Board()
    board = game.board()
    ply = 0
    node = game
    while node.variations and ply < initial_ply:
        node = node.variation(0)
        board.push(node.move)
        ply += 1
    return board


def _is_cap(board: chess.Board, uci: str) -> bool:
    try:
        mv = chess.Move.from_uci(uci)
        return board.is_capture(mv)
    except Exception:
        return False


def _play_solution_prefix_len(b: chess.Board, sol: List[str]) -> int:
    """Return how many initial moves from sol are legal when played sequentially."""
    tmp = b.copy()
    n = 0
    for u in sol:
        try:
            mv = chess.Move.from_uci(u)
        except Exception:
            break
        if mv not in tmp.legal_moves:
            break
        tmp.push(mv)
        n += 1
    return n


def _find_best_start_board_from_pgn(
    pgn: str,
    initial_ply: int,
    sol: List[str],
    back: int = 6,
    forward: int = 10,
) -> Tuple[chess.Board, int, int]:
    """
    Search plies around initial_ply and return (best_board, best_ply, matched_len).

    Heuristic:
      - maximize consecutive legal moves from solution starting at solution[0]
      - tie-break: closest ply to initial_ply (abs delta)
      - tie-break: lower ply (earlier) for stability
    """
    best_board = _board_from_pgn_at_ply(pgn, max(0, initial_ply))
    best_ply = max(0, initial_ply)
    best_len = _play_solution_prefix_len(best_board, sol)

    candidates: List[int] = []
    for d in range(0, max(back, forward) + 1):
        # try 0, +1, -1, +2, -2, ...
        if d == 0:
            candidates.append(initial_ply)
        else:
            candidates.append(initial_ply + d)
            candidates.append(initial_ply - d)

    seen = set()
    for ply_try in candidates:
        if ply_try in seen:
            continue
        seen.add(ply_try)
        if ply_try < 0:
            continue
        if ply_try < initial_ply - back or ply_try > initial_ply + forward:
            continue

        b = _board_from_pgn_at_ply(pgn, ply_try)
        mlen = _play_solution_prefix_len(b, sol)

        if mlen > best_len:
            best_board, best_ply, best_len = b, ply_try, mlen
            continue

        if mlen == best_len:
            # tie-break: closest to initial_ply
            if abs(ply_try - initial_ply) < abs(best_ply - initial_ply):
                best_board, best_ply = b, ply_try
            elif abs(ply_try - initial_ply) == abs(best_ply - initial_ply):
                # tie-break: earlier ply
                if ply_try < best_ply:
                    best_board, best_ply = b, ply_try

    return best_board, best_ply, best_len


class DailyPuzzleController:
    """Run the daily puzzle loop using the Pico for input and LEDs."""

    def __init__(self, client: LichessClient, mode: str = "daily"):
        self.client = client
        self.mode = (mode or "daily").strip().lower()

    def fetch_daily(self) -> Tuple[Optional[PuzzleState], Optional[str]]:
        payload = self.client.get_daily_puzzle()
        if not isinstance(payload, dict) or payload.get("_error"):
            return None, str(payload.get("_error") or "Unknown error")

        puzzle = payload.get("puzzle") or {}
        game = payload.get("game") or {}

        puzzle_id = str(puzzle.get("id") or "")
        pgn = str(game.get("pgn") or "")
        initial_ply = int(puzzle.get("initialPly") or 0)
        solution = puzzle.get("solution") or []

        if not puzzle_id or not pgn or not solution:
            return None, "Daily puzzle response missing required fields"

        sol = [str(m) for m in solution]

        # --- Generic alignment: pick the ply where the solution actually fits ---
        start_board, used_ply, matched = _find_best_start_board_from_pgn(
            pgn=pgn,
            initial_ply=initial_ply,
            sol=sol,
            back=6,
            forward=10,
        )

        fen = start_board.fen()
        return (
            PuzzleState(puzzle_id=puzzle_id, fen_start=fen, solution=sol, idx=0),
            None,
        )

    def run(self, link: BoardLink, display: Display) -> None:
        # 1) Fetch puzzle (daily or mix)
        display.send("Puzzle\nLoading…")
        if self.mode == "mix":
            st, err = self.fetch_mix()
        else:
            st, err = self.fetch_daily()

        if err or st is None:
            display.send("Puzzle error\n" + (err or "unknown"))
            link.sendtoboard("error_puzzle_fetch")
            return

        # 2) Guided setup on an EMPTY board (fast)
        steps = _compute_place_steps_from_fen(st.fen_start)

        link.sendtoboard("puzzle_setup_begin")
        try:
            display.send("PUZZLE SETUP\nClear board\nOK = next")
            __import__("time").sleep(0.8)
            link.sendtoboard("setup_clear")

            # Wait for OK after clearing
            while True:
                msg = link.getboard()
                if msg is None:
                    continue

                if msg == "shutdown":
                    from piGame import shutdown_pi
                    shutdown_pi(link, display)
                    return

                if msg in ("n", "new", "in", "newgame", "btn_new"):
                    return

                if msg in ("btn_ok", "ok"):
                    break

            for (side, sq, sym) in steps:
                display.send(
                    f"PLACE {('WHITE' if side=='w' else 'BLACK')}\n{_piece_name(sym)} {sq}\nOK = next"
                )
                link.sendtoboard(f"setup_place_{sq}_{side}")

                while True:
                    msg = link.getboard()
                    if msg is None:
                        continue

                    if msg == "shutdown":
                        from piGame import shutdown_pi
                        shutdown_pi(link, display)
                        return

                    if msg in ("n", "new", "in", "newgame", "btn_new"):
                        return

                    if msg in ("btn_ok", "ok"):
                        break

                    # ignore chatter
                    if msg.startswith("typing_") or msg in ("hint", "btn_hint"):
                        continue

            display.send("SETUP DONE\nPuzzle begins")
            __import__("time").sleep(0.8)
        finally:
            link.sendtoboard("puzzle_setup_done")

# 3) Load board state
        board = chess.Board(st.fen_start)

        # You always play the side-to-move at the puzzle start position
        player_color = "WHITE" if board.turn == chess.WHITE else "BLACK"

        link.sendtoboard(f"turn_{'white' if board.turn == chess.WHITE else 'black'}")
        display.send(f"Daily Puzzle\nYou are {player_color}\nEnter move:")

        # Helper: wait for OK acknowledgement coming from Pico (requires Pico patch above)
        def _wait_ack_ok() -> bool:
            while True:
                m = link.getboard()
                if m is None:
                    continue

                if m == "shutdown":
                    from piGame import shutdown_pi

                    shutdown_pi(link, display)
                    return False

                if m in ("n", "new", "in", "newgame", "btn_new"):
                    return False

                if m in ("btn_ok", "ok"):
                    return True

                # ignore everything else while waiting for OK
                if (
                    m.startswith("typing_")
                    or m.startswith("capq_")
                    or m in ("hint", "btn_hint")
                ):
                    continue

        # 4) Main solve loop
        while True:
            # solved
            if st.idx >= len(st.solution):
                display.send("Puzzle solved!\nOK = menu")
                link.sendtoboard("GameOver:1-0")
                # Wait for OK before returning
                if _wait_ack_ok():
                    return
                return

            expected = st.solution[st.idx]

            msg = link.getboard()
            if msg is None:
                continue

            if msg == "shutdown":
                from piGame import shutdown_pi

                shutdown_pi(link, display)
                return

            if msg in ("n", "new", "in", "newgame", "btn_new"):
                return

            # Capture probe from Pico (user-move capture blink UX)
            if msg.startswith("capq_"):
                q = msg[len("capq_") :].strip().lower()
                q = "".join(ch for ch in q if ch.isalnum())
                cap_flag = 0
                try:
                    mvq = chess.Move.from_uci(q)
                    cap_flag = 1 if board.is_capture(mvq) else 0
                except Exception:
                    cap_flag = 0
                link.sendtoboard(f"capr_{cap_flag}")
                continue

            # Hint
            if msg in ("hint", "btn_hint"):
                link.sendtoboard(
                    f"hint_{expected}{'_cap' if _is_cap(board, expected) else ''}"
                )
                display.send("Hint:\n" + f"{expected[:2]} → {expected[2:4]}")
                continue

            # Typing preview
            if msg.startswith("typing_"):
                try:
                    from piGame import handle_typing_preview

                    handle_typing_preview(display, msg[len("typing_") :], board)
                except Exception:
                    display.send(msg.replace("typing_", ""))
                continue

            # Moves arrive as UCI from Pico: "e2e4" (or sometimes "me2e4")
            uci = msg.strip().lower()
            if uci.startswith("m"):
                uci = uci[1:]
            uci = "".join(ch for ch in uci if ch.isalnum())

            if len(uci) not in (4, 5):
                continue

            # Check expected match
            wrong = False
            if uci[:4] != expected[:4]:
                wrong = True
            elif len(expected) == 5:
                if len(uci) != 5 or uci[4] != expected[4]:
                    wrong = True

            if wrong:
                link.sendtoboard(f"error_wrong_{uci}")
                display.send("Try again\nEnter move")
                continue

            # Must be legal in local position too
            try:
                mv = chess.Move.from_uci(expected)
            except Exception:
                display.send("Puzzle error")
                link.sendtoboard("error_puzzle_parse")
                return

            if mv not in board.legal_moves:
                link.sendtoboard(f"error_illegal_{expected}")
                display.send("Try again\nEnter move")
                continue

            # Correct player move
            display.send("Correct")
            __import__("time").sleep(2)

            board.push(mv)
            st.idx += 1

            # Auto-play opponent reply if present/legal
            if st.idx < len(st.solution):
                reply = st.solution[st.idx]
                try:
                    rmv = chess.Move.from_uci(reply)
                except Exception:
                    display.send("Puzzle error")
                    link.sendtoboard("error_puzzle_parse")
                    return

                if rmv in board.legal_moves:
                    opp = "WHITE" if board.turn == chess.WHITE else "BLACK"
                    cap = board.is_capture(rmv)

                    display.send(
                        f"{opp} played:\n{reply[:2]} → {reply[2:4]}\nOK = continue"
                    )
                    link.sendtoboard(f"m{reply}{'_cap' if cap else ''}")

                    board.push(rmv)
                    st.idx += 1

                    # Wait for OK ack (NOW Pico will send btn_ok after the Pico patch)
                    ok = _wait_ack_ok()
                    if not ok:
                        return

                    # Immediately show prompt BEFORE user starts typing
                    display.send(f"You are {player_color}\nEnter move:")

            # Next prompt for normal flow (if there was no opponent move)
            link.sendtoboard(
                f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
            )
            # Keep consistent prompt text
            display.send(f"You are {player_color}\nEnter move:")
