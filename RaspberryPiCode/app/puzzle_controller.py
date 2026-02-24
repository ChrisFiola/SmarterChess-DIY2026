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

# from os import link
from typing import List, Optional, Tuple

import chess  # type: ignore
import chess.pgn  # type: ignore

from piDisplay import Display
from piSerial import BoardLink
from .lichess_client import LichessClient


# -------------------- LED-guided physical setup helpers --------------------
from collections import defaultdict


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


def _fen_to_lines(fen: str, width: int = 22) -> List[str]:
    """Split a long FEN string into LCD-friendly lines."""
    fen = (fen or "").strip()
    if not fen:
        return ["(no FEN)"]
    out: List[str] = []
    i = 0
    while i < len(fen) and len(out) < 4:
        out.append(fen[i : i + width])
        i += width
    if i < len(fen) and out:
        # indicate truncation
        out[-1] = out[-1][: max(0, width - 1)] + "…"
    return out


def _is_cap(board: chess.Board, uci: str) -> bool:
    try:
        mv = chess.Move.from_uci(uci)
        return board.is_capture(mv)
    except Exception:
        return False


class DailyPuzzleController:
    """Run the daily puzzle loop using the Pico for input and LEDs."""

    def __init__(self, client: LichessClient):
        self.client = client

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

        board = _board_from_pgn_at_ply(pgn, initial_ply)
        fen = board.fen()
        sol = [str(m) for m in solution]
        return PuzzleState(puzzle_id=puzzle_id, fen_start=fen, solution=sol), None

    def run(self, link: BoardLink, display: Display) -> None:
        # 1) Fetch puzzle
        display.send("Daily puzzle\nLoading…")
        st, err = self.fetch_daily()
        if err or st is None:
            display.send("Puzzle error\n" + (err or "unknown"))
            link.sendtoboard("error_puzzle_fetch")
            return

        # 2) LED-guided setup from standard starting position
        actions = _compute_setup_actions_from_start(st.fen_start)

        # Put Pico in setup guidance mode (disables hint IRQ + forwards OK presses)
        link.sendtoboard("puzzle_setup_begin")
        try:
            display.send("DAILY PUZZLE\nSetup position\nOK = next")
            __import__("time").sleep(1.0)

            # Optional: clear any stale setup drawing
            link.sendtoboard("setup_clear")

            for act in actions:
                # Draw ONE instruction on LEDs + LCD
                if act[0] == "remove":
                    _, side, frm, _, sym = act
                    display.send(
                        f"REMOVE {('BLACK' if side=='b' else 'WHITE')}\n{_piece_name(sym)} {frm}\nOK = next"
                    )
                    link.sendtoboard(f"setup_remove_{frm}")

                else:
                    _, side, frm, to, sym = act
                    display.send(
                        f"MOVE {('BLACK' if side=='b' else 'WHITE')}\n{_piece_name(sym)}\n{frm} -> {to}\nOK = next"
                    )
                    link.sendtoboard(f"setup_move_{frm}{to}_{side}")

                # WAIT for OK *after each step*
                while True:
                    msg = link.getboard()
                    if msg is None:
                        continue

                    if msg == "shutdown":
                        # avoid circular import hell; keep it local
                        from piGame import shutdown_pi

                        shutdown_pi(link, display)
                        return

                    # allow abort
                    if msg in ("n", "new", "in", "newgame", "btn_new"):
                        return

                    # OK advances
                    if msg in ("btn_ok", "ok", "n"):
                        break

                    # ignore other chatter
                    if msg.startswith("typing_") or msg in ("hint", "btn_hint"):
                        continue

            display.send("SETUP DONE\nPuzzle begins")
            __import__("time").sleep(0.8)

        finally:
            link.sendtoboard("puzzle_setup_done")

        # 3) Load board state
        board = chess.Board(st.fen_start)

        # Tell Pico it’s the side-to-move (so it keeps normal UX flow)
        link.sendtoboard(f"turn_{'white' if board.turn == chess.WHITE else 'black'}")
        display.send(f"Daily Puzzle\n{'WHITE' if board.turn else 'BLACK'} to move")

        # 4) Main solve loop
        while True:
            # puzzle solved
            if st.idx >= len(st.solution):
                display.send("Puzzle solved!\nNice.")
                link.sendtoboard("GameOver:1-0")
                return

            expected = st.solution[st.idx]

            msg = link.getboard()
            if msg is None:
                continue

            if msg == "shutdown":
                return

            if msg in ("n", "new", "in", "newgame", "btn_new"):
                return

            if msg in ("hint", "btn_hint"):
                link.sendtoboard(
                    f"hint_{expected}{'_cap' if _is_cap(board, expected) else ''}"
                )
                display.send("Hint:\n" + f"{expected[:2]} → {expected[2:4]}")
                continue

            # Moves arrive as UCI from Pico
            uci = msg.strip().lower()
            if uci.startswith("m"):
                uci = uci[1:]
            uci = "".join(ch for ch in uci if ch.isalnum())

            if len(uci) not in (4, 5):
                display.send("Try again")
                continue

            # Check expected match
            if uci[:4] != expected[:4] or (
                len(expected) == 5 and (len(uci) != 5 or uci[4] != expected[4])
            ):
                display.send("Try again")
                continue

            # Must be legal
            try:
                mv = chess.Move.from_uci(expected)
            except Exception:
                display.send("Puzzle error")
                return

            if mv not in board.legal_moves:
                display.send("Try again")
                continue

            # Correct
            display.send("Correct")
            board.push(mv)
            st.idx += 1

            # Auto-play opponent reply if present/legal
            if st.idx < len(st.solution):
                reply = st.solution[st.idx]
                try:
                    rmv = chess.Move.from_uci(reply)
                except Exception:
                    display.send("Puzzle error")
                    return

                if rmv in board.legal_moves:
                    cap = board.is_capture(rmv)
                    link.sendtoboard(f"m{reply}{'_cap' if cap else ''}")
                    board.push(rmv)
                    st.idx += 1

            # Next prompt
            link.sendtoboard(
                f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
            )
            display.send(f"{'WHITE' if board.turn else 'BLACK'} to move\nEnter move")
