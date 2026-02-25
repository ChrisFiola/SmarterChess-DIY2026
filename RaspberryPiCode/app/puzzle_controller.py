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

        sol = [str(m) for m in solution]

        # --- Generic alignment: pick the ply where the solution actually fits ---
        start_board, used_ply, matched = _find_best_start_board_from_pgn(
            pgn=pgn,
            initial_ply=initial_ply,
            sol=sol,
            back=6,
            forward=10,
        )

        # If nothing matches even 1 move, still proceed, but you’ll likely see “Try again”.
        # This usually means PGN parsing mismatch or an unexpected puzzle payload.
        fen = start_board.fen()
        return (
            PuzzleState(puzzle_id=puzzle_id, fen_start=fen, solution=sol, idx=0),
            None,
        )

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

        link.sendtoboard("puzzle_setup_begin")
        try:
            display.send("DAILY PUZZLE\nSetup position\nOK = next")
            __import__("time").sleep(1.0)

            link.sendtoboard("setup_clear")

            for act in actions:
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

                # wait for OK after each step
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
        link.sendtoboard(f"turn_{'white' if board.turn == chess.WHITE else 'black'}")
        display.send(
            f"Daily Puzzle\n{'WHITE' if board.turn else 'BLACK'} to move\nEnter move"
        )

        # 4) Main solve loop
        while True:
            # solved
            if st.idx >= len(st.solution):
                display.send("Puzzle solved!\nOK = menu")
                link.sendtoboard("GameOver:1-0")

                # Wait for OK before returning (match Pico protocol)
                while True:
                    msg2 = link.getboard()
                    if msg2 is None:
                        continue

                    if msg2 == "shutdown":
                        from piGame import shutdown_pi

                        shutdown_pi(link, display)
                        return

                    if msg2 in ("btn_ok", "ok"):
                        return

                    if msg2 in ("n", "new", "in", "newgame", "btn_new"):
                        return

                    if msg2.startswith("typing_") or msg2 in ("hint", "btn_hint"):
                        continue

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

            # Hint
            if msg in ("hint", "btn_hint"):
                link.sendtoboard(
                    f"hint_{expected}{'_cap' if _is_cap(board, expected) else ''}"
                )
                display.send("Hint:\n" + f"{expected[:2]} → {expected[2:4]}")
                continue

            # Show typing on LCD (same UX as other modes)
            if msg.startswith("typing_"):
                try:
                    from piGame import handle_typing_preview

                    handle_typing_preview(display, msg[len("typing_") :])
                except Exception:
                    display.send(msg.replace("typing_", ""))
                continue

            # Moves arrive as UCI from Pico: "e2e4" (or sometimes "me2e4")
            uci = msg.strip().lower()
            if uci.startswith("m"):
                uci = uci[1:]
            uci = "".join(ch for ch in uci if ch.isalnum())

            # Ignore incomplete input silently
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
                # Pico restarts input only when it receives "heyArduinoerror..."
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
            __import__("time").sleep(0.5)

            mover = "WHITE" if board.turn == chess.WHITE else "BLACK"
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
                    # The opponent is the side that is ABOUT to move now (before pushing rmv)
                    opp = "WHITE" if board.turn == chess.WHITE else "BLACK"
                    cap = board.is_capture(rmv)

                    # Show opponent move on LCD (consistent with other modes)
                    display.send(f"{opp} played:\n{reply[:2]} → {reply[2:4]}")
                    __import__("time").sleep(0.8)

                    # LEDs/trail on Pico
                    link.sendtoboard(f"m{reply}{'_cap' if cap else ''}")

                    board.push(rmv)
                    st.idx += 1

            # Next prompt
            link.sendtoboard(
                f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
            )
            display.send(f"{'WHITE' if board.turn else 'BLACK'} to move\nEnter move")
