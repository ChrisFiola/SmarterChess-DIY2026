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

import chess  # type: ignore
import chess.pgn  # type: ignore

from piDisplay import Display
from piSerial import BoardLink
from .lichess_client import LichessClient


@dataclass
class PuzzleState:
    puzzle_id: str
    fen_start: str
    solution: List[str]  # UCI moves
    idx: int = 0         # next expected move index


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
        out[-1] = (out[-1][: max(0, width - 1)] + "…")
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

        # 2) Ask user to set up position
        fen_lines = _fen_to_lines(st.fen_start)
        # show FEN over multiple frames so it is readable
        display.send("Daily Puzzle\nSet position")
        __import__("time").sleep(1.0)
        display.send("FEN:")
        __import__("time").sleep(0.7)
        display.send("\n".join(fen_lines), size="auto")
        __import__("time").sleep(0.7)
        display.send("Press OK\nwhen ready")
        link.sendtoboard("turn_white")  # just to keep Pico in input-ready UX

        # Wait for OK (Pico sends 'n' on OK in some scenes; here we accept both)
        while True:
            msg = link.getboard()
            if msg is None:
                continue
            if msg in ("n", "ok", "btn_ok"):
                break
            if msg in ("new", "in", "newgame", "btn_new"):
                # treat as cancel
                return

        # 3) Load board state
        board = chess.Board(st.fen_start)
        display.send(f"Daily Puzzle\n{'WHITE' if board.turn else 'BLACK'} to move")

        # 4) Main solve loop
        while True:
            # game over if solved
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
                # exit back to mode select
                return

            if msg in ("hint", "btn_hint"):
                # show next move as hint
                link.sendtoboard(f"hint_{expected}{'_cap' if _is_cap(board, expected) else ''}")
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

            # Validate expected move
            if uci[:4] != expected[:4] or (len(expected) == 5 and len(uci) == 5 and uci[4] != expected[4]):
                display.send("Try again")
                continue

            # Must also be legal from the current board
            try:
                mv = chess.Move.from_uci(expected)
            except Exception:
                display.send("Puzzle error")
                return
            if mv not in board.legal_moves:
                display.send("Try again")
                continue

            # Correct!
            display.send("Correct")
            board.push(mv)
            st.idx += 1

            # If the puzzle includes an opponent reply, play it automatically
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
            display.send(f"{'WHITE' if board.turn else 'BLACK'} to move\nEnter move")
