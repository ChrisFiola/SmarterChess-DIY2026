# -*- coding: utf-8 -*-
"""
Stockfish opponent wrapper.

Maps the 1-8 skill scale from the Pico UI to Stockfish's internal
Skill Level (0-20) or UCI_Elo parameters, then retrieves best moves.
"""
from __future__ import annotations

import sys
import traceback
from typing import Optional

import chess

from core.engine import EngineContext
from core.opponent import Opponent


def _clamp(n: int, lo: int, hi: int) -> int:
    return lo if n < lo else hi if n > hi else n


def _map_skill_to_elo(skill_level: int) -> int:
    """Convert a 0-20 skill level to an approximate Elo rating.

    Produces 8 equally spaced steps from 650 to 2050.
    """
    s = _clamp(skill_level, 0, 20)
    idx = int(round((s / 20.0) * 7))
    elo_steps = [650, 850, 1050, 1250, 1450, 1650, 1850, 2050]
    return elo_steps[_clamp(idx, 0, 7)]


def _map_skill_to_stockfish_level(raw_0_20: int) -> int:
    """Convert a 0-20 skill level to Stockfish's internal Skill Level (0-20).

    Uses a beginner-friendly curve so the lower end of the slider is
    noticeably weaker (more accessible for new players).
    """
    s = _clamp(int(raw_0_20), 0, 20)
    idx = int(round((s / 20.0) * 7))
    steps = [0, 1, 2, 4, 6, 9, 13, 18]
    return steps[_clamp(idx, 0, 7)]


class StockfishOpponent(Opponent):
    def __init__(
        self,
        ctx: EngineContext,
        move_time_ms: int,
        skill_level: int = 5,
        use_elo: bool = False,
    ):
        self.ctx = ctx
        self.move_time_ms = move_time_ms
        self.skill_level = _clamp(int(skill_level), 0, 20)
        self.use_elo = use_elo

        self._configured = False
        self._last_skill = None

    def set_time_ms(self, ms: int) -> None:
        self.move_time_ms = ms

    def _set_skill(self, skill_level: int) -> None:
        skill_level = _clamp(int(skill_level), 0, 20)
        if skill_level != self.skill_level:
            self.skill_level = skill_level
            self._configured = False  # force reconfigure on next move

    def _ensure_configured(self) -> None:
        """Send skill/elo configuration to the engine if it has changed."""
        if self._configured and self._last_skill == self.skill_level:
            return

        engine = self.ctx.ensure()
        print(
            f"[ENGINE] configuring skill={self.skill_level} use_elo={self.use_elo}",
            file=sys.stderr,
            flush=True,
        )

        try:
            if self.use_elo:
                elo = _map_skill_to_elo(self.skill_level)
                engine.configure({"UCI_LimitStrength": True, "UCI_Elo": elo})
                print(f"[ENGINE] configured UCI_Elo={elo}", file=sys.stderr, flush=True)
            else:
                level = _map_skill_to_stockfish_level(self.skill_level)
                engine.configure({"UCI_LimitStrength": False, "Skill Level": level})
                print(f"[ENGINE] configured Skill Level={level}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[ENGINE] configuration failed: {e!r}", file=sys.stderr, flush=True)
            traceback.print_exc()
            return  # don't mark configured so we retry next move

        self._configured = True
        self._last_skill = self.skill_level

    def get_move(self, board: chess.Board) -> Optional[str]:
        self._ensure_configured()
        return self.ctx.bestmove(board, self.move_time_ms)
