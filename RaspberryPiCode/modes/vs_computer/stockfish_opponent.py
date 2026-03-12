# -*- coding: utf-8 -*-
"""
Stockfish opponent wrapper.

Maps the 1-8 skill scale from the Pico UI to Stockfish's internal
parameters, then retrieves best moves.

Strength mapping:
  Levels 1-2  — Skill Level (0-20): UCI_Elo has a ~1320 floor inside
                Stockfish, so very weak play requires the Skill Level
                parameter instead.
  Levels 3-8  — UCI_Elo: gives a more realistic, human-like difficulty
                curve at the mid and upper range.

  Level  1 → Skill Level 0   (roughly 500 Elo equivalent)
  Level  2 → Skill Level 1   (roughly 800 Elo equivalent)
  Level  3 → UCI_Elo  1000
  Level  4 → UCI_Elo  1300
  Level  5 → UCI_Elo  1600
  Level  6 → UCI_Elo  1800
  Level  7 → UCI_Elo  2000
  Level  8 → UCI_Elo  2300
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


# Levels 1-2 use Stockfish Skill Level (0-20) because UCI_Elo bottoms out
# at ~1320 and cannot produce truly beginner-level play below that.
_SKILL_LEVEL_MAP = {1: 0, 2: 1}

# Levels 3-8 use UCI_Elo for a realistic human-like difficulty curve.
_ELO_MAP = {3: 1000, 4: 1300, 5: 1600, 6: 1800, 7: 2000, 8: 2300}


class StockfishOpponent(Opponent):
    def __init__(
        self,
        ctx: EngineContext,
        move_time_ms: int,
        skill_level: int = 1,
    ):
        """
        ctx           — shared EngineContext (the single Stockfish process)
        move_time_ms  — think time per move in milliseconds
        skill_level   — 1-8 UI scale (see module docstring for the mapping)
        """
        self.ctx = ctx
        self.move_time_ms = move_time_ms
        self.skill_level = _clamp(int(skill_level), 1, 8)

        # Avoid reconfiguring the engine every move — only push settings when
        # skill_level actually changes between games.
        self._configured = False
        self._last_skill = None

    def set_time_ms(self, ms: int) -> None:
        self.move_time_ms = ms

    def _set_skill(self, skill_level: int) -> None:
        skill_level = _clamp(int(skill_level), 1, 8)
        if skill_level != self.skill_level:
            self.skill_level = skill_level
            self._configured = False  # force reconfigure on next move

    def _ensure_configured(self) -> None:
        """Send strength configuration to the engine if it has changed."""
        if self._configured and self._last_skill == self.skill_level:
            return

        engine = self.ctx.ensure()

        try:
            if self.skill_level in _SKILL_LEVEL_MAP:
                sf_level = _SKILL_LEVEL_MAP[self.skill_level]
                engine.configure({"UCI_LimitStrength": False, "Skill Level": sf_level})
                print(
                    f"[ENGINE] skill={self.skill_level} → Skill Level={sf_level}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                elo = _ELO_MAP[self.skill_level]
                engine.configure({"UCI_LimitStrength": True, "UCI_Elo": elo})
                print(
                    f"[ENGINE] skill={self.skill_level} → UCI_Elo={elo}",
                    file=sys.stderr,
                    flush=True,
                )
        except Exception as e:
            print(f"[ENGINE] configuration failed: {e!r}", file=sys.stderr, flush=True)
            traceback.print_exc()
            return  # don't mark configured so we retry next move

        self._configured = True
        self._last_skill = self.skill_level

    def get_move(self, board: chess.Board) -> Optional[str]:
        self._ensure_configured()
        return self.ctx.bestmove(board, self.move_time_ms)
