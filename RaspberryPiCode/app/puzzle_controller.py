# -*- coding: utf-8 -*-
"""Daily puzzle controller.

Features:
  - Fetch daily puzzle (or random "mix" puzzle IDs) from Lichess
  - Show an LCD-friendly puzzle label (theme + rating)
  - LED-guided physical setup on an EMPTY board
  - Validate user-entered moves locally against the puzzle solution
  - Wrong-move feedback: identify piece moved, show RED trail to put it back, wait OK
  - Promotion handling: prompt on Pico when needed (Q/R/B/N)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
from collections import defaultdict

import os
import random

import chess  # type: ignore
import chess.pgn  # type: ignore

from piDisplay import Display
from piSerial import BoardLink
from .lichess_client import LichessClient


# -------------------- Mix puzzle ids --------------------

PUZZLE_IDS_PATH = os.path.join(os.path.dirname(__file__), "puzzle_ids.txt")


def _pick_random_line_seek(path: str, max_tries: int = 25) -> str:
    """Fast random line selection without loading the whole file."""
    try:
        size = os.path.getsize(path)
        if size <= 0:
            return ""
        with open(path, "rb") as f:
            for _ in range(max_tries):
                pos = random.randrange(0, size)
                f.seek(pos)
                if pos > 0:
                    f.readline()  # drop partial line
                line = f.readline()
                if not line:
                    continue
                s = line.decode("utf-8", errors="ignore").strip()
                if s:
                    return s
    except Exception:
        return ""
    return ""


# -------------------- Setup helpers --------------------


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
    """Return placement steps for an *empty* physical board.

    Each step: (side_char, square, piece_symbol)
      side_char: 'w' or 'b'
      square: 'e4'
      piece_symbol: like 'P','n', etc
    """
    brd = chess.Board(target_fen)
    steps = []
    for sq in chess.SQUARES:
        p = brd.piece_at(sq)
        if not p:
            continue
        side = "w" if p.color == chess.WHITE else "b"
        steps.append((side, chess.square_name(sq), p.symbol()))

    order_pt = {
        chess.KING: 0,
        chess.QUEEN: 1,
        chess.ROOK: 2,
        chess.BISHOP: 3,
        chess.KNIGHT: 4,
        chess.PAWN: 5,
    }

    def key(t):
        side, square, sym = t
        ptype = chess.Piece.from_symbol(sym).piece_type
        return (0 if side == "w" else 1, order_pt.get(ptype, 9), square)

    steps.sort(key=key)
    return steps


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


# -------------------- Puzzle label (themes + rating) --------------------

THEME_MAP = {
    "mateIn1": "Mate in 1",
    "mateIn2": "Mate in 2",
    "mateIn3": "Mate in 3",
    "mateIn4": "Mate in 4",
    "mateIn5": "Mate in 5",
    "fork": "Fork",
    "pin": "Pin",
    "skewer": "Skewer",
    "discoveredAttack": "Discovered attack",
    "doubleCheck": "Double check",
    "hangingPiece": "Hanging piece",
    "deflection": "Deflection",
    "attraction": "Attraction",
    "interference": "Interference",
    "xRayAttack": "X-ray attack",
    "backRankMate": "Back rank mate",
    "sacrifice": "Sacrifice",
    "zugzwang": "Zugzwang",
    "endgame": "Endgame",
    "opening": "Opening",
    "middlegame": "Middlegame",
}


def _format_puzzle_label(
    themes: Optional[List[str]],
    rating: Optional[int],
    fallback: str = "Puzzle",
) -> str:
    """Return a short label suitable for a 16–20 char LCD line."""
    t = themes or []
    label = fallback
    if t:
        raw = str(t[0])
        label = THEME_MAP.get(
            raw, raw.replace("_", " ").replace("-", " ").strip().title()
        )
    if rating is not None:
        label = f"{label} • {int(rating)}"
    return label[:20]


# -------------------- PGN alignment helpers --------------------


@dataclass
class PuzzleState:
    puzzle_id: str
    fen_start: str
    solution: List[str]  # UCI moves
    themes: Optional[List[str]] = None
    rating: Optional[int] = None
    idx: int = 0  # next expected move index


def _board_from_pgn_at_ply(pgn_text: str, initial_ply: int) -> chess.Board:
    game = chess.pgn.read_game(__import__("io").StringIO(pgn_text))
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
    """Search plies around initial_ply to maximize the legal prefix of sol."""
    best_board = _board_from_pgn_at_ply(pgn, max(0, initial_ply))
    best_ply = max(0, initial_ply)
    best_len = _play_solution_prefix_len(best_board, sol)

    candidates: List[int] = []
    for d in range(0, max(back, forward) + 1):
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
            if abs(ply_try - initial_ply) < abs(best_ply - initial_ply):
                best_board, best_ply = b, ply_try
            elif (
                abs(ply_try - initial_ply) == abs(best_ply - initial_ply)
                and ply_try < best_ply
            ):
                best_board, best_ply = b, ply_try

    return best_board, best_ply, best_len


# -------------------- Controller --------------------


class DailyPuzzleController:
    """Run the daily puzzle loop using the Pico for input and LEDs."""

    def __init__(self, client: LichessClient, mode: str = "daily", *, theme: Optional[str] = None):
        self.client = client
        self.mode = (mode or "daily").strip().lower()
        self.theme = (theme or "").strip() or None

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

        themes = puzzle.get("themes") or []
        rating = puzzle.get("rating")

        if not puzzle_id or not pgn or not solution:
            return None, "Daily puzzle response missing required fields"

        sol = [str(m) for m in solution]

        start_board, _used_ply, _matched = _find_best_start_board_from_pgn(
            pgn=pgn,
            initial_ply=initial_ply,
            sol=sol,
            back=6,
            forward=10,
        )

        return (
            PuzzleState(
                puzzle_id=puzzle_id,
                fen_start=start_board.fen(),
                solution=sol,
                themes=[str(x) for x in (themes or [])],
                rating=int(rating) if rating is not None else None,
                idx=0,
            ),
            None,
        )

    def fetch_mix(self) -> Tuple[Optional[PuzzleState], Optional[str]]:
        if not os.path.exists(PUZZLE_IDS_PATH):
            return None, "puzzle_ids.txt missing"

        pid = _pick_random_line_seek(PUZZLE_IDS_PATH)
        if not pid:
            return None, "No valid puzzle IDs found"

        pid = "".join(ch for ch in pid if ch.isalnum())
        if not pid:
            return None, "Invalid puzzle ID line"

        payload = self.client.get_puzzle(pid)
        if not isinstance(payload, dict) or payload.get("_error"):
            return None, str(payload.get("_error") or "Puzzle fetch failed")

        puzzle = payload.get("puzzle") or {}
        game = payload.get("game") or {}

        puzzle_id = str(puzzle.get("id") or pid)
        pgn = str(game.get("pgn") or "")
        initial_ply = int(puzzle.get("initialPly") or 0)
        solution = puzzle.get("solution") or []

        themes = puzzle.get("themes") or []
        rating = puzzle.get("rating")

        if not puzzle_id or not pgn or not solution:
            return None, "Puzzle response missing required fields"

        sol = [str(m) for m in solution]

        start_board, _used_ply, _matched = _find_best_start_board_from_pgn(
            pgn=pgn,
            initial_ply=initial_ply,
            sol=sol,
            back=6,
            forward=10,
        )

        return (
            PuzzleState(
                puzzle_id=puzzle_id,
                fen_start=start_board.fen(),
                solution=sol,
                themes=[str(x) for x in (themes or [])],
                rating=int(rating) if rating is not None else None,
                idx=0,
            ),
            None,
        )

    def fetch_theme(self, theme: str) -> Tuple[Optional[PuzzleState], Optional[str]]:
        """Fetch a puzzle that matches a Lichess theme tag.

        Primary strategy: use /api/puzzle/next?theme=<tag> (fast, accurate).
        Fallback strategy: sample random IDs from puzzle_ids.txt and filter
        by returned puzzle themes (slower but works even if /next is blocked).
        """
        theme = (theme or "").strip()
        if not theme:
            return None, "Theme missing"

        # 1) Fast path
        payload = self.client.get_next_puzzle(theme=theme)
        if isinstance(payload, dict) and not payload.get("_error"):
            puzzle = payload.get("puzzle") or {}
            game = payload.get("game") or {}

            puzzle_id = str(puzzle.get("id") or "")
            pgn = str(game.get("pgn") or "")
            initial_ply = int(puzzle.get("initialPly") or 0)
            solution = puzzle.get("solution") or []
            themes = puzzle.get("themes") or []
            rating = puzzle.get("rating")

            if puzzle_id and pgn and solution:
                sol = [str(m) for m in solution]
                start_board, _used_ply, _matched = _find_best_start_board_from_pgn(
                    pgn=pgn,
                    initial_ply=initial_ply,
                    sol=sol,
                    back=6,
                    forward=10,
                )
                return (
                    PuzzleState(
                        puzzle_id=puzzle_id,
                        fen_start=start_board.fen(),
                        solution=sol,
                        themes=[str(x) for x in (themes or [])],
                        rating=int(rating) if rating is not None else None,
                        idx=0,
                    ),
                    None,
                )

        # 2) Fallback path: sample from local ID list
        if not os.path.exists(PUZZLE_IDS_PATH):
            err = str(payload.get("_error") or "Theme fetch failed") if isinstance(payload, dict) else "Theme fetch failed"
            return None, err

        last_err = str(payload.get("_error") or "Theme fetch failed") if isinstance(payload, dict) else "Theme fetch failed"
        for _ in range(35):
            pid = _pick_random_line_seek(PUZZLE_IDS_PATH)
            pid = "".join(ch for ch in (pid or "") if ch.isalnum())
            if not pid:
                continue
            p = self.client.get_puzzle(pid)
            if not isinstance(p, dict) or p.get("_error"):
                last_err = str(p.get("_error") or last_err)
                continue
            puzzle = p.get("puzzle") or {}
            themes = [str(x) for x in (puzzle.get("themes") or [])]
            if theme not in themes:
                continue

            game = p.get("game") or {}
            puzzle_id = str(puzzle.get("id") or pid)
            pgn = str(game.get("pgn") or "")
            initial_ply = int(puzzle.get("initialPly") or 0)
            solution = puzzle.get("solution") or []
            rating = puzzle.get("rating")
            if not puzzle_id or not pgn or not solution:
                continue

            sol = [str(m) for m in solution]
            start_board, _used_ply, _matched = _find_best_start_board_from_pgn(
                pgn=pgn,
                initial_ply=initial_ply,
                sol=sol,
                back=6,
                forward=10,
            )

            return (
                PuzzleState(
                    puzzle_id=puzzle_id,
                    fen_start=start_board.fen(),
                    solution=sol,
                    themes=themes,
                    rating=int(rating) if rating is not None else None,
                    idx=0,
                ),
                None,
            )

        return None, last_err

    def run(self, link: BoardLink, display: Display) -> None:
        # 1) Fetch puzzle
        display.send("Puzzle\nLoading…")
        if self.mode == "mix":
            st, err = self.fetch_mix()
        elif self.mode == "theme":
            st, err = self.fetch_theme(self.theme or "")
        else:
            st, err = self.fetch_daily()

        if err or st is None:
            display.send("Puzzle error\n" + (err or "unknown"))
            link.sendtoboard("error_puzzle_fetch")
            return

        # 2) Guided setup on an EMPTY board
        steps = _compute_place_steps_from_fen(st.fen_start)

        link.sendtoboard("puzzle_setup_begin")
        try:
            label = _format_puzzle_label(
                st.themes,
                st.rating,
                fallback=(
                    "Mix & Match"
                    if self.mode == "mix"
                    else (THEME_MAP.get(self.theme or "", "Theme") if self.mode == "theme" else "Daily")
                ),
            )
            display.send(f"{label}\nSetup position\nOK = next")
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

            for side, sq, sym in steps:
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

                    if msg.startswith("typing_") or msg in ("hint", "btn_hint"):
                        continue

            display.send(f"{label}\nSetup done\nPuzzle begins")
            __import__("time").sleep(0.8)
        finally:
            link.sendtoboard("puzzle_setup_done")

        # 3) Load board state
        board = chess.Board(st.fen_start)
        player_color = "WHITE" if board.turn == chess.WHITE else "BLACK"
        side_prefix = f"You are {player_color}"

        def _show_prompt_enter_move() -> None:
            display.send(f"{side_prefix}\nEnter move:")

        def _show_try_again(extra: str = "") -> None:
            if extra:
                display.send(f"{side_prefix}\n{extra}\nEnter move")
            else:
                display.send(f"{side_prefix}\nTry again\nEnter move")

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

                if (
                    m.startswith("typing_")
                    or m.startswith("capq_")
                    or m in ("hint", "btn_hint")
                ):
                    continue

        def _wait_promotion_choice() -> Optional[str]:
            """Wait for Pico to return btn_q/btn_r/btn_b/btn_n."""
            while True:
                m = link.getboard()
                if m is None:
                    continue

                if m == "shutdown":
                    from piGame import shutdown_pi

                    shutdown_pi(link, display)
                    return None

                if m in ("n", "new", "in", "newgame", "btn_new"):
                    return None

                if m in ("btn_q", "btn_r", "btn_b", "btn_n"):
                    return m[-1]

                if (
                    m.startswith("typing_")
                    or m.startswith("capq_")
                    or m in ("hint", "btn_hint")
                ):
                    continue

        def _wrong_move_feedback(user_uci: str) -> bool:
            """Show what piece moved and where to put it back. Lights red trail and waits OK."""
            u = (user_uci or "").strip().lower()
            if u.startswith("m"):
                u = u[1:]
            u = "".join(ch for ch in u if ch.isalnum())

            if len(u) < 4:
                display.send(f"Wrong {side_prefix} move\nPut it back + OK")
                link.sendtoboard("puzzle_wrong_")
                return _wait_ack_ok()

        def _illegal_move_feedback(user_uci: str) -> bool:
            """Illegal move: show where to put the piece back (red trail) and wait for OK."""
            u = (user_uci or "").strip().lower()
            if u.startswith("m"):
                u = u[1:]
            u = "".join(ch for ch in u if ch.isalnum())

            if len(u) < 4:
                display.send(f"Illegal {side_prefix} move\nOK = continue")
                return _wait_ack_ok()

            frm, to = u[:2], u[2:4]

            piece_txt = "PIECE"
            try:
                p = board.piece_at(chess.parse_square(frm))
                if p:
                    piece_txt = _piece_name(p.symbol())
            except Exception:
                pass

            display.send(
                f"Illegal {side_prefix} move:\n{piece_txt} {frm}->{to}\nPut it back + OK"
            )
            # Trail from TO back to FROM so the user knows where to return it
            link.sendtoboard(f"puzzle_wrong_{to}{frm}")
            return _wait_ack_ok()

        # Kick Pico into input state immediately after setup
        link.sendtoboard(f"turn_{'white' if board.turn == chess.WHITE else 'black'}")
        _show_prompt_enter_move()

        # 4) Solve loop
        while True:
            if st.idx >= len(st.solution):
                display.send(f"{side_prefix}\nPuzzle solved!\nOK = menu")
                link.sendtoboard("GameOver:1-0")
                _wait_ack_ok()
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

            # Capture probe from Pico for preview cap blink
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

            # Hint button
            if msg in ("hint", "btn_hint"):
                link.sendtoboard(
                    f"hint_{expected}{'_cap' if _is_cap(board, expected) else ''}"
                )
                display.send(f"{side_prefix}\nHint: {expected[:2]}→{expected[2:4]}")
                continue

            # Typing previews
            if msg.startswith("typing_"):
                try:
                    from piGame import handle_typing_preview

                    handle_typing_preview(display, msg[len("typing_") :], board)
                except Exception:
                    display.send(msg.replace("typing_", ""))
                continue

            # Parse user move
            uci = msg.strip().lower()
            if uci.startswith("m"):
                uci = uci[1:]
            uci = "".join(ch for ch in uci if ch.isalnum())
            if len(uci) not in (4, 5):
                continue

            # Promotion: Pico sends 4-char; if a promotion is possible for that from->to, prompt for piece.
            if len(uci) == 4:
                try:
                    frm_sq = chess.parse_square(uci[:2])
                    to_sq = chess.parse_square(uci[2:4])
                    promo_needed = any(
                        (
                            mvv.from_square == frm_sq
                            and mvv.to_square == to_sq
                            and mvv.promotion is not None
                        )
                        for mvv in board.legal_moves
                    )
                    if promo_needed:
                        display.send(f"{side_prefix}\nPromotion!\n1Q 2R 3B 4N")
                        link.sendtoboard("promotion_choice_needed")
                        pick = _wait_promotion_choice()
                        if pick is None:
                            return
                        uci = uci + pick
                except Exception:
                    pass

            # If it's not legal in the current position, treat as illegal (not as "wrong solution")
            try:
                user_mv = chess.Move.from_uci(uci)
            except Exception:
                # Bad/partial UCI from Pico; treat as illegal and guide the user to undo it.
                if not _illegal_move_feedback(uci):
                    return
                # Re-arm Pico input state and prompt again
                link.sendtoboard(
                    f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
                )
                _show_prompt_enter_move()
                continue

            if user_mv not in board.legal_moves:
                # User made a move that's not legal in this position; guide them to undo it.
                if not _illegal_move_feedback(uci):
                    return
                link.sendtoboard(
                    f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
                )
                _show_prompt_enter_move()
                continue

            # Compare against expected (promotion-aware)
            wrong = False
            if uci[:4] != expected[:4]:
                wrong = True
            elif len(expected) == 5:
                if len(uci) != 5 or uci[4] != expected[4]:
                    wrong = True
            else:
                if len(uci) == 5:
                    wrong = True

            if wrong:
                if not _wrong_move_feedback(uci):
                    return
                _show_prompt_enter_move()
                # Ensure Pico is back in input state
                link.sendtoboard(
                    f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
                )
                continue

            # Correct move (must use expected string to match solution exactly)
            mv = chess.Move.from_uci(expected)
            if mv not in board.legal_moves:
                # Shouldn't happen, but keep user unblocked.
                link.sendtoboard("error_puzzle_internal")
                _show_try_again("Puzzle error")
                __import__("time").sleep(1.0)
                link.sendtoboard(
                    f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
                )
                _show_prompt_enter_move()
                continue

            display.send(f"{side_prefix}\nCorrect")
            __import__("time").sleep(2)

            board.push(mv)
            st.idx += 1

            # Auto-play opponent reply
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
                        f"{side_prefix}\n{opp} played {reply[:2]}→{reply[2:4]}\nOK = continue"
                    )
                    link.sendtoboard(f"m{reply}{'_cap' if cap else ''}")

                    board.push(rmv)
                    st.idx += 1

                    if not _wait_ack_ok():
                        return

                    _show_prompt_enter_move()

            # Prompt next move
            link.sendtoboard(
                f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
            )
            _show_prompt_enter_move()
