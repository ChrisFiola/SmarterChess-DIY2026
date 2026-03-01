# -*- coding: utf-8 -*-
"""Stockfish opponent wrapper (robust strength-aware version)."""

from __future__ import annotations

from typing import Optional
import chess  # type: ignore

from .opponent import Opponent
from piEngine import EngineContext, engine_bestmove

print("LOADED StockfishOpponent from:", __file__, flush=True)

def clamp(n: int, lo: int, hi: int) -> int:
    return lo if n < lo else hi if n > hi else n


def map_skill_to_elo(skill_level: int) -> int:
    """
    Beginner-friendly steps.
    skill_level is 0..20; we bucket it into 8 UI-ish bands.
    """
    s = clamp(skill_level, 0, 20)

    # Convert 0..20 into an index 0..7
    # (so low skill values stay low longer)
    idx = int(round((s / 20.0) * 7))

    elo_steps = [650, 850, 1050, 1250, 1450, 1650, 1850, 2050]
    return elo_steps[clamp(idx, 0, 7)]


class StockfishOpponent(Opponent):
    def __init__(
        self,
        ctx: EngineContext,
        move_time_ms: int,
        skill_level: int = 5,
        use_elo: bool = True,
    ):
        self.ctx = ctx
        self.move_time_ms = move_time_ms
        self.skill_level = clamp(int(skill_level), 0, 20)
        self.use_elo = use_elo

        self._configured = False
        self._last_skill = None

    def set_time_ms(self, ms: int) -> None:
        self.move_time_ms = ms

    def set_skill(self, skill_level: int) -> None:
        skill_level = clamp(int(skill_level), 0, 20)
        if skill_level != self.skill_level:
            self.skill_level = skill_level
            self._configured = False  # force reconfigure next move

    def _ensure_configured(self) -> None:
        """
        Apply UCI config only if needed.
        Avoids spamming setoption every move.
        """
        if self._configured and self._last_skill == self.skill_level:
            return

        engine = self.ctx.ensure()

        try:
            if self.use_elo:
                elo = map_skill_to_elo(self.skill_level)
                engine.configure(
                    {
                        "UCI_LimitStrength": True,
                        "UCI_Elo": elo,
                    }
                )
                print(engine.options)
            else:
                engine.configure(
                    {
                        "UCI_LimitStrength": False,
                        "Skill Level": self.skill_level,
                    }
                )
                print(engine.options)
        except Exception:
            # If engine does not support the option, fail silently
            pass

        self._configured = True
        self._last_skill = self.skill_level

    def get_move(self, board: chess.Board) -> Optional[str]:
        self._ensure_configured()
        return engine_bestmove(self.ctx, board, self.move_time_ms)