# -*- coding: utf-8 -*-
"""
Stockfish opponent wrapper.

Maps the 1-8 skill scale from the Pico UI to Stockfish's internal
parameters, then retrieves best moves.

Each level combines three knobs for a smooth difficulty ramp:
  - Stockfish Skill Level or UCI_Elo (engine playing strength)
  - Search depth cap (limits how far ahead the engine looks)
  - Blunder chance (randomly picks a legal move instead of the best one)

  Level  1 → Skill Level 0,  depth 1, 40% blunder, 0.5s  (~200 Elo)
  Level  2 → Skill Level 0,  depth 2, 25% blunder, 0.5s  (~400 Elo)
  Level  3 → Skill Level 1,  depth 4, 15% blunder, 1.0s  (~600 Elo)
  Level  4 → Skill Level 3,  depth 8, 8% blunder,  1.0s  (~900 Elo)
  Level  5 → UCI_Elo  1200,  no cap,  4% blunder,  1.5s  (~1200 Elo)
  Level  6 → UCI_Elo  1500,  no cap,  0% blunder,  2.0s  (~1500 Elo)
  Level  7 → UCI_Elo  1800,  no cap,  0% blunder,  2.5s  (~1800 Elo)
  Level  8 → UCI_Elo  2200,  no cap,  0% blunder,  3.0s  (~2200 Elo)
"""
from __future__ import annotations

import random
import sys
import traceback
from typing import Optional

import chess

from core.engine import EngineContext


def _clamp(n: int, lo: int, hi: int) -> int:
    return lo if n < lo else hi if n > hi else n


# Per-level configuration: (method, value, depth_cap, blunder_pct, time_ms)
#   method="skill" → Stockfish Skill Level (0-20), UCI_LimitStrength off
#   method="elo"   → UCI_Elo, UCI_LimitStrength on
#   depth_cap=None → no depth limit (use time only)
#   blunder_pct    → 0-100, chance of playing a random legal move
#   time_ms        → think time per move in milliseconds
_LEVEL_CONFIG = {
    1: ("skill",  0,     1,  40,  500),
    2: ("skill",  0,     2,  25,  500),
    3: ("skill",  1,     4,  15, 1000),
    4: ("skill",  3,     8,   8, 1000),
    5: ("elo",  1200, None,   4, 1500),
    6: ("elo",  1500, None,   0, 2000),
    7: ("elo",  1800, None,   0, 2500),
    8: ("elo",  2200, None,   0, 3000),
}


class StockfishOpponent:
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

    def _ensure_configured(self) -> None:
        """Send strength configuration to the engine if it has changed."""
        if self.ctx.hint_override_active:
            # hint() set engine to full strength — must reconfigure
            self.ctx.hint_override_active = False
            self._configured = False
        if self._configured and self._last_skill == self.skill_level:
            return

        engine = self.ctx.ensure()
        method, value, _depth, _blunder, _time = _LEVEL_CONFIG[self.skill_level]

        try:
            if method == "skill":
                engine.configure({"UCI_LimitStrength": False, "Skill Level": value})
                print(
                    f"[ENGINE] skill={self.skill_level} → Skill Level={value}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                engine.configure({"UCI_LimitStrength": True, "UCI_Elo": value})
                print(
                    f"[ENGINE] skill={self.skill_level} → UCI_Elo={value}",
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

        _method, _value, depth_cap, blunder_pct, time_ms = _LEVEL_CONFIG[self.skill_level]

        # Random blunder: pick any legal move
        if blunder_pct > 0 and random.randint(1, 100) <= blunder_pct:
            legal = list(board.legal_moves)
            if legal:
                pick = random.choice(legal).uci()
                print(
                    f"[ENGINE] blunder! random move {pick} (level {self.skill_level})",
                    file=sys.stderr,
                    flush=True,
                )
                return pick

        return self.ctx.bestmove(board, time_ms, depth=depth_cap)
