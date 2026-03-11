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
import re

import os
import json
import random

import chess
import chess.pgn

from display import Display
from boardlink import BoardLink
from lichess_client import LichessClient
from protocol import (
    send_lcd_ack_for_payload,
    parse_uci_move,
    _piece_name,
    NEW_GAME_MSGS,
    OK_MSGS,
    HINT_MSGS,
)


def _pgn_opening_info(pgn_text: str) -> Tuple[str, str]:
    """Best-effort extraction of ECO / Opening from PGN headers.

    Useful for journalctl debugging to confirm the fetched puzzle actually
    belongs to the requested opening angle.
    """
    try:
        game = chess.pgn.read_game(__import__("io").StringIO(pgn_text))
        if game is None:
            return "", ""
        eco = str(game.headers.get("ECO") or "")
        opn = str(game.headers.get("Opening") or "")
        return eco, opn
    except Exception:
        return "", ""


# -------------------- Mix puzzle ids --------------------

PUZZLE_IDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "puzzle_ids.txt")


# -------------------- Seen-puzzle cache (avoid repeats) --------------------
# Lichess /api/puzzle/next can legitimately return the same puzzle multiple
# times, even when authenticated. We keep a small local cache of seen puzzle
# IDs (global + per-angle) to reduce repeats across sessions.
def _stable_home_dir() -> str:
    """Best-effort stable home directory for both interactive shells and systemd.

    If systemd starts the service with HOME unset or set to /root, using
    expanduser('~') will write the cache to the wrong place.

    When the project is installed under /home/<user>/..., we can infer the
    intended user from this file path.
    """
    home = os.environ.get("HOME")
    if home and home not in ("/", "/root"):
        return home

    # Infer /home/<user> from the absolute path of this module.
    try:
        p = os.path.abspath(__file__)
        m = re.match(r"^/home/([^/]+)/", p)
        if m:
            return f"/home/{m.group(1)}"
    except Exception:
        pass

    # Last resort (may still be /root, but it's better than crashing).
    return os.path.expanduser("~")


SEEN_CACHE_PATH = os.path.join(
    _stable_home_dir(),
    ".cache",
    "smartchess",
    "seen_puzzles.json",
)


# -------------------- Opening index (cached by opening tag) --------------------
def _puzzle_index_dir() -> str:
    # Centralized so it works correctly under systemd (HOME may be unset).
    return os.path.join(_stable_home_dir(), ".cache", "smartchess", "puzzle_index")


_slug_re_non_alnum = re.compile(r"[^a-z0-9]+")
_slug_re_multi_us = re.compile(r"_+")


def _opening_to_slug(opening_name: str) -> str:
    s = (opening_name or "").strip().lower()
    s = _slug_re_non_alnum.sub("_", s)
    s = _slug_re_multi_us.sub("_", s).strip("_")
    return s


def _opening_index_file(opening_name: str) -> str:
    slug = _opening_to_slug(opening_name)
    return os.path.join(_puzzle_index_dir(), f"opening_tag__{slug}.txt")


def _read_index_ids(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            # One puzzle id per line
            return [ln.strip() for ln in f if ln.strip()]
    except Exception:
        return []


def _load_seen_cache() -> dict:
    try:
        if not os.path.exists(SEEN_CACHE_PATH):
            return {"global": [], "by_angle": {}}
        with open(SEEN_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"global": [], "by_angle": {}}
        data.setdefault("global", [])
        data.setdefault("by_angle", {})
        if not isinstance(data["global"], list):
            data["global"] = []
        if not isinstance(data["by_angle"], dict):
            data["by_angle"] = {}
        return data
    except Exception:
        return {"global": [], "by_angle": {}}


def _save_seen_cache(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(SEEN_CACHE_PATH), exist_ok=True)
        tmp = SEEN_CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, SEEN_CACHE_PATH)
    except Exception:
        pass


def reset_seen_puzzles(angle: Optional[str] = None) -> None:
    """Clear seen_puzzles.json.

    If angle is provided, clears only that angle bucket.
    If angle is None, clears everything (removes the file).
    """
    try:
        if angle:
            data = _load_seen_cache()
            by = data.get("by_angle") or {}
            if isinstance(by, dict) and angle in by:
                by.pop(angle, None)
                data["by_angle"] = by
                _save_seen_cache(data)
            return

        # Full reset
        if os.path.exists(SEEN_CACHE_PATH):
            os.remove(SEEN_CACHE_PATH)
    except Exception:
        pass


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


class PuzzleController:
    """Run the daily puzzle loop using the Pico for input and LEDs."""

    def __init__(
        self,
        client: LichessClient,
        mode: str = "daily",
        *,
        theme: Optional[str] = None,
        theme_label: Optional[str] = None,
    ):
        self.client = client
        self.mode = (mode or "daily").strip().lower()
        self.theme = (theme or "").strip() or None
        self.theme_label = (theme_label or "").strip() or None
        # Track last /api/puzzle/next result to avoid returning the exact same
        # puzzle when the user switches angles quickly.
        self._last_next_angle: Optional[str] = None
        self._last_next_id: Optional[str] = None
        # Per-angle dedupe to avoid "same puzzle for different openings" when
        # the server (or intermediaries) serve a sticky result.
        self._last_next_id_by_angle: dict[str, str] = {}

        # Seen puzzle IDs (persisted)
        self._seen_cache = _load_seen_cache()
        self._seen_global = set(
            [str(x) for x in (self._seen_cache.get("global") or [])]
        )
        self._seen_by_angle = {
            str(k): set([str(x) for x in (v or [])])
            for k, v in (self._seen_cache.get("by_angle") or {}).items()
            if isinstance(v, list)
        }

    def _mark_seen(self, angle: str, puzzle_id: str) -> None:
        """Persistently remember a puzzle id (global + per-angle) to reduce repeats."""
        pid = str(puzzle_id or "").strip()
        if not pid:
            return
        self._seen_global.add(pid)
        a = str(angle or "").strip()
        if a:
            self._seen_by_angle.setdefault(a, set()).add(pid)

        # Bound cache sizes (avoid unbounded growth)
        def _bound(lst, max_n):
            if len(lst) > max_n:
                del lst[:-max_n]

        try:
            self._seen_cache["global"] = list(self._seen_global)
            # Keep per-angle lists bounded
            by = {}
            for k, s in self._seen_by_angle.items():
                by[k] = list(s)
                _bound(by[k], 150)
            self._seen_cache["by_angle"] = by
            # Also bound global
            _bound(self._seen_cache["global"], 500)
            _save_seen_cache(self._seen_cache)
        except Exception:
            pass

    def fetch_daily(self) -> Tuple[Optional[PuzzleState], Optional[str]]:
        # Daily puzzle occasionally fails transiently (Wi‑Fi bring-up, DNS hiccup,
        # Lichess 5xx). Retry once to avoid bouncing back to the main menu.
        payload = self.client.get_daily_puzzle()
        if not isinstance(payload, dict) or payload.get("_error"):
            err0 = str(payload.get("_error") if isinstance(payload, dict) else payload)
            try:
                print(f"[PUZZLE DAILY ERROR] first_attempt={err0!r}", flush=True)
            except Exception:
                pass
            try:
                __import__("time").sleep(1.0)
            except Exception:
                pass
            payload = self.client.get_daily_puzzle()
            if not isinstance(payload, dict) or payload.get("_error"):
                err1 = str(
                    payload.get("_error") if isinstance(payload, dict) else payload
                )
                try:
                    print(f"[PUZZLE DAILY ERROR] second_attempt={err1!r}", flush=True)
                except Exception:
                    pass
                return None, (err1 or err0 or "Unknown error")

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

        # Debug (journalctl)
        try:
            print(
                f"[PUZZLE DAILY] id={puzzle_id!r} rating={rating!r} themes={themes!r} initialPly={initial_ply}",
                flush=True,
            )
        except Exception:
            pass
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

        # Debug (journalctl)
        try:
            print(
                f"[PUZZLE MIX] id={puzzle_id!r} rating={rating!r} themes={themes!r} initialPly={initial_ply}",
                flush=True,
            )
        except Exception:
            pass

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

    def fetch_theme(self, angle: str) -> Tuple[Optional[PuzzleState], Optional[str]]:
        """Fetch a puzzle for a given Lichess *angle*.

        Angle can be either:
        - a puzzle theme/motif/phase tag (same taxonomy as lichess.org/training/themes)
        - an opening name (from lichess.org/training/openings)

        Primary strategy: use /api/puzzle/next?angle=<angle> (fast).
        Fallback strategy: sample random IDs from puzzle_ids.txt and filter
        by returned puzzle themes (slower).
        """
        angle = (angle or "").strip()
        if not angle:
            return None, "Theme missing"

        payload: dict = {}
        passed_checks = False

        PHASE_TAGS = {
            "opening",
            "middlegame",
            "endgame",
            "rookEndgame",
            "bishopEndgame",
            "pawnEndgame",
            "knightEndgame",
            "queenEndgame",
        }

        def _is_opening_name(a: str) -> bool:
            # Heuristic: opening names generally contain a space, apostrophe, hyphen, or "Defense/Gambit/Opening/System"
            # while theme tags are camelCase tokens.
            if a in PHASE_TAGS:
                return False
            return True

        # 0) Opening-tag index path (preferred for openings menu)
        # If the angle looks like an opening name and we have a local index file,
        # we pick an unseen puzzle ID from that file and fetch it directly.
        if _is_opening_name(angle):
            idx_path = _opening_index_file(angle)
            if os.path.exists(idx_path):
                try:
                    print(
                        f"[PUZZLE INDEX] angle={angle!r} dir={_puzzle_index_dir()!r} file={idx_path!r}",
                        flush=True,
                    )
                except Exception:
                    pass

                ids = _read_index_ids(idx_path)
                if ids:
                    # Load persistent seen state (solved-only) for global + per-angle.
                    seen = _load_seen_cache() or {}
                    seen_global = set(str(x) for x in (seen.get("global") or []))
                    seen_angle = set(
                        str(x) for x in ((seen.get("by_angle") or {}).get(angle) or [])
                    )

                    # Also exclude what we've served in-memory this run.
                    served_angle = self._seen_by_angle.get(angle, set())
                    served_global = self._seen_global

                    avoid = (
                        seen_global
                        | seen_angle
                        | set(served_global)
                        | set(served_angle)
                    )

                    unseen = [pid for pid in ids if pid not in avoid]

                    # If we've exhausted the opening, allow replay by resetting only that angle.
                    if not unseen:
                        reset_seen_puzzles(angle)
                        self._seen_by_angle.pop(angle, None)
                        served_angle = set()
                        avoid = set(
                            served_global
                        )  # keep global served to avoid immediate repeats in-session
                        unseen = [pid for pid in ids if pid not in avoid]

                    if unseen:
                        puzzle_id = random.choice(unseen)
                        payload = self.client.get_puzzle(puzzle_id) or {}
                        if isinstance(payload, dict) and not payload.get("_error"):
                            # Keep the same parsing path as api/puzzle/next
                            passed_checks = True
                            # Treat this as the "last" for this angle to avoid immediate repeats.
                            self._last_next_id_by_angle[angle] = puzzle_id
                        else:
                            # If the direct fetch failed, fall back to normal next() behavior.
                            payload = {}
                            passed_checks = False
        # 1) Fast path (api/puzzle/next)
        # payload/passed_checks may already be set by the opening index prefetch above.
        if not passed_checks:
            payload = {}
        seen_skips = 0

        # NOTE: we pass a nonce to reduce caching, and we dedupe per-angle.
        last_for_angle = self._last_next_id_by_angle.get(angle)
        seen_set = self._seen_by_angle.get(angle, set())

        def _try_next_batch(tries: int) -> None:
            nonlocal payload, passed_checks, seen_skips, last_for_angle, seen_set
            for _ in range(tries):
                payload = (
                    self.client.get_next_puzzle(
                        angle=angle,
                        nonce=str(random.getrandbits(32)),
                    )
                    or {}
                )
                try:
                    pid_try = str(((payload.get("puzzle") or {}).get("id")) or "")
                except Exception:
                    pid_try = ""
                if not pid_try:
                    break

                # Avoid puzzles we've already served (reduces repeats).
                if pid_try in self._seen_global:
                    seen_skips += 1
                    continue
                if pid_try in seen_set:
                    seen_skips += 1
                    continue

                # Dedupe against last result for this same angle.
                if last_for_angle and pid_try == last_for_angle:
                    continue

                # --- PHASE ENFORCEMENT ---
                # If angle is a phase tag, require it to be present in puzzle themes.
                if angle in PHASE_TAGS:
                    try:
                        tset = set(
                            str(x)
                            for x in ((payload.get("puzzle") or {}).get("themes") or [])
                        )
                    except Exception:
                        tset = set()
                    if angle not in tset:
                        continue

                passed_checks = True
                return

        if not passed_checks:
            _try_next_batch(8)

        # If we keep getting the same seen puzzle(s), allow a one-time reset for this angle.
        # This is useful when you have "seen" everything in a category and want to replay them.
        if (not passed_checks) and seen_skips >= 6:
            reset_seen_puzzles(angle)
            self._seen_by_angle.pop(angle, None)
            seen_set = set()
            last_for_angle = None
            seen_skips = 0
            if not passed_checks:
                _try_next_batch(4)
        if passed_checks and isinstance(payload, dict) and not payload.get("_error"):
            puzzle = payload.get("puzzle") or {}
            game = payload.get("game") or {}

            puzzle_id = str(puzzle.get("id") or "")
            pgn = str(game.get("pgn") or "")
            initial_ply = int(puzzle.get("initialPly") or 0)
            solution = puzzle.get("solution") or []
            themes = puzzle.get("themes") or []
            rating = puzzle.get("rating")

            eco, opening = _pgn_opening_info(pgn)

            # Debug (journalctl)
            try:
                print(
                    f"[PUZZLE NEXT] angle={angle!r} id={puzzle_id!r} rating={rating!r} eco={eco!r} opening={opening!r} themes={themes!r}",
                    flush=True,
                )
            except Exception:
                pass

            if puzzle_id and pgn and solution:
                sol = [str(m) for m in solution]
                start_board, _used_ply, _matched = _find_best_start_board_from_pgn(
                    pgn=pgn,
                    initial_ply=initial_ply,
                    sol=sol,
                    back=6,
                    forward=10,
                )
                self._last_next_angle = angle
                self._last_next_id = puzzle_id
                self._last_next_id_by_angle[angle] = puzzle_id
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

        # 2) Fallback path: only works for THEME TAGS (because it filters by 'themes')
        if not os.path.exists(PUZZLE_IDS_PATH):
            err = (
                str(payload.get("_error") or "Theme fetch failed")
                if isinstance(payload, dict)
                else "Theme fetch failed"
            )
            return None, err

        # If we're requesting an opening name, the local fallback can't reliably filter,
        # because the local list filter is based on puzzle 'themes' tags.
        if _is_opening_name(angle):
            err = (
                str(payload.get("_error") or "Opening fetch failed")
                if isinstance(payload, dict)
                else "Opening fetch failed"
            )
            return None, err

        last_err = (
            str(payload.get("_error") or "Theme fetch failed")
            if isinstance(payload, dict)
            else "Theme fetch failed"
        )
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
            if angle not in themes:
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
            link.send_to_board("error_puzzle_fetch")

            # Keep the error visible until the user acknowledges with OK.
            # This also makes the underlying error easier to read.
            try:
                from protocol import parse_payload, EventType
                import time

                while True:
                    raw = link.try_read_from_board()
                    if raw:
                        ev = parse_payload(raw)
                        if ev.type == EventType.OK:
                            break
                        if ev.type == EventType.SHUTDOWN:
                            break
                    time.sleep(0.05)
            except Exception:
                pass
            return

        # 2) Guided setup on an EMPTY board
        steps = _compute_place_steps_from_fen(st.fen_start)

        # Clear any stale button events from menu navigation so they can't
        # interfere with the first setup prompts / LED commands.
        try:
            link.clear_input()
        except Exception:
            pass

        # Disable hints on the Pico during setup so no hint requests can be
        # triggered while the user is placing pieces.
        link.send_to_board("hint_disable")

        link.send_to_board("puzzle_setup_begin")
        try:
            label = _format_puzzle_label(
                st.themes,
                st.rating,
                fallback=(
                    "Mix & Match"
                    if self.mode == "mix"
                    else (
                        self.theme_label or THEME_MAP.get(self.theme or "", "Theme")
                        if self.mode == "theme"
                        else "Daily"
                    )
                ),
            )
            display.send(f"{label}\nSetup position\nOK = next")
            __import__("time").sleep(0.8)
            link.send_to_board("setup_clear")

            # Wait for OK after clearing
            while True:
                msg = link.read_from_board()
                if msg is None:
                    continue

                if msg == "shutdown":
                    from piGame import shutdown_raspberry_pi

                    shutdown_raspberry_pi(link, display)
                    return

                if msg in NEW_GAME_MSGS:
                    return

                if msg in OK_MSGS:
                    break

            for side, sq, sym in steps:
                display.send(
                    f"PLACE {('WHITE' if side=='w' else 'BLACK')}\n{_piece_name(sym)} {sq}\nOK = next"
                )
                link.send_to_board(f"setup_place_{sq}_{side}")

                while True:
                    msg = link.read_from_board()
                    if msg is None:
                        continue

                    if msg == "shutdown":
                        from piGame import shutdown_raspberry_pi

                        shutdown_raspberry_pi(link, display)
                        return

                    if msg in NEW_GAME_MSGS:
                        return

                    if msg in OK_MSGS:
                        break

                    if msg.startswith("typing_") or msg in HINT_MSGS:
                        continue

            display.send(f"{label}\nSetup done\nPuzzle begins")
            __import__("time").sleep(0.8)
        finally:
            # Re-enable hints and always end setup mode.
            # NOTE: we send hint_enable both before and after puzzle_setup_done.
            # On the Pico, puzzle_setup_done flips into GAME_RUNNING and re-enables
            # the HINT IRQ. Sending hint_enable again avoids a rare race where the
            # first HINT press right after setup gets ignored.
            link.send_to_board("hint_enable")
            link.send_to_board("puzzle_setup_done")
            try:
                link.clear_input()
            except Exception:
                pass
            __import__("time").sleep(0.05)
            link.send_to_board("hint_enable")

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
                m = link.read_from_board()
                if m is None:
                    continue

                if m == "shutdown":
                    from piGame import shutdown_raspberry_pi

                    shutdown_raspberry_pi(link, display)
                    return False

                if m in NEW_GAME_MSGS:
                    return False

                if m in OK_MSGS:
                    return True

                if (
                    m.startswith("typing_")
                    or m.startswith("capq_")
                    or m in HINT_MSGS
                ):
                    continue

        def _wait_promotion_choice() -> Optional[str]:
            """Wait for Pico to return btn_q/btn_r/btn_b/btn_n."""
            while True:
                m = link.read_from_board()
                if m is None:
                    continue

                if m == "shutdown":
                    from piGame import shutdown_raspberry_pi

                    shutdown_raspberry_pi(link, display)
                    return None

                if m in NEW_GAME_MSGS:
                    return None

                if m in ("btn_q", "btn_r", "btn_b", "btn_n"):
                    return m[-1]

                if (
                    m.startswith("typing_")
                    or m.startswith("capq_")
                    or m in HINT_MSGS
                ):
                    continue

        def _wrong_move_feedback(user_uci: str) -> bool:
            """Show what piece moved and where to put it back. Lights red trail and waits OK."""
            u = (user_uci or "").strip().lower()
            if u.startswith("m"):
                u = u[1:]
            u = "".join(ch for ch in u if ch.isalnum())

            if len(u) < 4:
                display.send(f"Wrong move\nPut it back + OK")
                link.send_to_board("puzzle_wrong_")
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
                f"Incorrect move: \n {piece_txt} {frm}->{to}\nPut it back + OK"
            )
            # Trail from TO back to FROM so the user knows where to return it
            link.send_to_board(f"puzzle_wrong_{to}{frm}")
            return _wait_ack_ok()

        def _illegal_move_feedback(user_uci: str) -> bool:
            """Illegal move: show where to put the piece back (red trail) and wait for OK."""
            u = (user_uci or "").strip().lower()
            if u.startswith("m"):
                u = u[1:]
            u = "".join(ch for ch in u if ch.isalnum())

            if len(u) < 4:
                display.send(f"Illegal move\nOK = continue")
                return _wait_ack_ok()

            frm, to = u[:2], u[2:4]

            piece_txt = "PIECE"
            try:
                p = board.piece_at(chess.parse_square(frm))
                if p:
                    piece_txt = _piece_name(p.symbol())
            except Exception:
                pass

            display.send(f"Illegal move: \n {piece_txt} {frm}->{to}\nPut it back + OK")
            # Trail from TO back to FROM so the user knows where to return it
            link.send_to_board(f"puzzle_wrong_{to}{frm}")
            return _wait_ack_ok()

        # Kick Pico into input state immediately after setup
        link.send_to_board(f"turn_{'white' if board.turn == chess.WHITE else 'black'}")
        _show_prompt_enter_move()

        # 4) Solve loop
        while True:
            if st.idx >= len(st.solution):
                try:
                    angle_key = (self.theme or self.mode or "").strip()
                    self._mark_seen(angle_key, st.puzzle_id)
                except Exception:
                    pass
                display.send(f"{side_prefix}\nPuzzle solved!\nOK = menu")
                link.send_to_board("GameOver:1-0")
                _wait_ack_ok()
                return

            expected = st.solution[st.idx]

            msg = link.read_from_board()
            if msg is None:
                continue

            if msg == "shutdown":
                from piGame import shutdown_raspberry_pi

                shutdown_raspberry_pi(link, display)
                return

            if msg in NEW_GAME_MSGS:
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
                link.send_to_board(f"capr_{cap_flag}")
                continue

            # Hint button
            if msg in HINT_MSGS:
                link.send_to_board(
                    f"hint_{expected}{'_cap' if _is_cap(board, expected) else ''}"
                )
                display.send(f"{side_prefix}\nHint: {expected[:2]}→{expected[2:4]}")
                continue

            # Typing previews
            if msg.startswith("typing_"):
                try:
                    from piGame import update_typing_display

                    payload = msg[len("typing_") :]

                    print(f"[PUZZLE typing] payload={payload!r}", flush=True)
                    update_typing_display(display, payload, board)
                    send_lcd_ack_for_payload(link, payload, log_prefix="[PUZZLE ACK]")

                except Exception as e:
                    print(f"[PUZZLE typing ERROR] {e}", flush=True)
                    display.send(msg.replace("typing_", ""))
                continue

            # Navigation / acknowledgement tokens can leak through right after
            # the last setup OK press. They are *not* UCI moves.
            if msg in ("ok", "btn_ok", "btnok"):
                continue

            # Parse user move
            uci = msg.strip().lower()
            if uci.startswith("m"):
                uci = uci[1:]
            uci = "".join(ch for ch in uci if ch.isalnum())
            if uci in NEW_GAME_MSGS | OK_MSGS | HINT_MSGS:
                continue
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
                        link.send_to_board("promotion_choice_needed")
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
                link.send_to_board(
                    f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
                )
                _show_prompt_enter_move()
                continue

            if user_mv not in board.legal_moves:
                # User made a move that's not legal in this position; guide them to undo it.
                if not _illegal_move_feedback(uci):
                    return
                link.send_to_board(
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
                link.send_to_board(
                    f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
                )
                continue

            # Correct move (must use expected string to match solution exactly)
            mv = chess.Move.from_uci(expected)
            if mv not in board.legal_moves:
                # Shouldn't happen, but keep user unblocked.
                link.send_to_board("error_puzzle_internal")
                _show_try_again("Puzzle error")
                __import__("time").sleep(1.0)
                link.send_to_board(
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
                    link.send_to_board("error_puzzle_parse")
                    return

                if rmv in board.legal_moves:
                    opp = "WHITE" if board.turn == chess.WHITE else "BLACK"
                    cap = board.is_capture(rmv)

                    # If the opponent reply is a promotion (e.g. a7a8q), show what it became.
                    promo_line = ""
                    try:
                        if isinstance(reply, str) and len(reply) >= 5:
                            promo_letter = reply[4].lower()
                            if promo_letter in ("q", "r", "b", "n"):
                                promo_name = (
                                    display._promo_name(promo_letter)
                                    if hasattr(display, "_promo_name")
                                    else promo_letter.upper()
                                )
                                promo_line = f"Promoted to {promo_name}\n"
                    except Exception:
                        promo_line = ""

                    display.send(
                        f"{side_prefix}\n{opp} played {reply[:2]}→{reply[2:4]}\n{promo_line}OK = continue"
                    )
                    link.send_to_board(f"m{reply}{'_cap' if cap else ''}")

                    board.push(rmv)
                    st.idx += 1

                    if not _wait_ack_ok():
                        return

                    _show_prompt_enter_move()

            # Prompt next move
            link.send_to_board(
                f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
            )
            _show_prompt_enter_move()
