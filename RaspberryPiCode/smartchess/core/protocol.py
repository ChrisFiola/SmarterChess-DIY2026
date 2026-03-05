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


NEW_GAME_TOKENS = {"n", "new", "in", "newgame", "btn_new"}
HINT_TOKENS = {"hint", "btn_hint"}
OK_TOKENS = {"ok", "btnok", "btn_ok"}


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

    move = _parse_uci_like(low)
    if move:
        return Event(EventType.MOVE, move)

    return Event(EventType.UNKNOWN, p)


RESERVED_NON_MOVES = NEW_GAME_TOKENS | HINT_TOKENS | OK_TOKENS | {"draw", "btn_draw"}


def _parse_uci_like(s: str) -> Optional[str]:
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


def format_hint(uci: str, is_capture: bool) -> str:
    return f"hint_{uci}{'_cap' if is_capture else ''}"


def format_capture_reply(is_capture: bool) -> str:
    return f"capr_{1 if is_capture else 0}"