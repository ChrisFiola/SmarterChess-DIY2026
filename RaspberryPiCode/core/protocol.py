# -*- coding: utf-8 -*-
"""Protocol helpers for Pico <-> Pi messages.

BoardLink returns *payloads* (strings after the `heypi` prefix).

Keeping parsing/formatting here prevents stringly-typed logic from spreading
throughout the game loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class EventType(str, Enum):
    MOVE = "move"
    HINT = "hint"
    NEW_GAME = "new_game"
    SHUTDOWN = "shutdown"
    TYPING = "typing"
    CAPTURE_QUERY = "capture_query"
    OK = "ok"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Event:
    type: EventType
    payload: str = ""


# ── Shared message token sets ──────────────────────────────────────────────────
NEW_GAME_MSGS: frozenset = frozenset({"n", "new", "in", "newgame", "btn_new"})
OK_MSGS: frozenset = frozenset({"ok", "btnok", "btn_ok"})
HINT_MSGS: frozenset = frozenset({"hint", "btn_hint"})

# Legacy aliases used internally by parse_payload
NEW_GAME_TOKENS = NEW_GAME_MSGS
OK_TOKENS = OK_MSGS
HINT_TOKENS = HINT_MSGS

#: Tokens that should be silently ignored in most message loops
IGNORED_MSGS: frozenset = NEW_GAME_MSGS | OK_MSGS | HINT_MSGS | frozenset({"draw", "btn_draw"})


def parse_payload(payload: str) -> Event:
    if not payload:
        return Event(EventType.UNKNOWN, "")

    p = payload.strip()
    low = p.lower()

    if low == "shutdown":
        return Event(EventType.SHUTDOWN, "")
    if low in NEW_GAME_TOKENS:
        return Event(EventType.NEW_GAME, low)
    if low in HINT_TOKENS:
        return Event(EventType.HINT, low)
    if low in OK_TOKENS:
        return Event(EventType.OK, low)

    if low.startswith("typing_"):
        return Event(EventType.TYPING, low[len("typing_"):])

    if low.startswith("capq_"):
        return Event(EventType.CAPTURE_QUERY, low[len("capq_"):].strip())

    move = parse_uci_move(low)
    if move:
        return Event(EventType.MOVE, move)

    return Event(EventType.UNKNOWN, p)


RESERVED_NON_MOVES = NEW_GAME_TOKENS | HINT_TOKENS | OK_TOKENS | {"draw", "btn_draw"}


def parse_uci_move(s: str) -> Optional[str]:
    """Parse a raw Pico payload into a 4-5 char UCI string, or None."""
    s = (s or "").strip().lower()
    if not s:
        return None
    if s.startswith("m"):
        s = s[1:].strip()
    cleaned = "".join(ch for ch in s if ch.isalnum())
    if 4 <= len(cleaned) <= 5 and cleaned.isalnum():
        if cleaned in RESERVED_NON_MOVES:
            return None
        return cleaned
    return None


def format_engine_move(uci: str, is_capture: bool) -> str:
    return f"m{uci}{'_cap' if is_capture else ''}"


def _format_hint(uci: str, is_capture: bool) -> str:
    return f"hint_{uci}{'_cap' if is_capture else ''}"


def format_capture_reply(is_capture: bool) -> str:
    return f"capr_{1 if is_capture else 0}"

def piece_name(sym: str) -> str:
    """Return the display name for a piece symbol (e.g. 'P' -> 'PAWN')."""
    return {
        "P": "PAWN", "N": "KNIGHT", "B": "BISHOP",
        "R": "ROOK",  "Q": "QUEEN",  "K": "KING",
    }.get((sym or "").upper(), "PIECE")


def send_lcd_ack_for_payload(link, payload: str, *, log_prefix: str = "[LCD ACK]") -> None:
    """Send the matching LCD ACK for a typing_* payload.

    Shared across game, puzzle, and online flows so typing preview behavior
    stays identical in every mode.
    """
    if payload.startswith("confirm_"):
        print(f"{log_prefix} confirm payload={payload!r}", flush=True)
        link.send_to_board("lcd_ack_confirm")
    elif payload.startswith("to_"):
        print(f"{log_prefix} to payload={payload!r}", flush=True)
        link.send_to_board("lcd_ack_to")
    elif payload.startswith("from_"):
        print(f"{log_prefix} from payload={payload!r}", flush=True)
        link.send_to_board("lcd_ack_from")
