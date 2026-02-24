# -*- coding: utf-8 -*-
"""Stockfish opponent wrapper.

Uses existing EngineContext + engine_bestmove() helper.
"""

from __future__ import annotations

from typing import Optional
import chess  # type: ignore

from .opponent import Opponent
from piEngine import EngineContext, engine_bestmove


class StockfishOpponent(Opponent):
    def __init__(self, ctx: EngineContext, move_time_ms: int):
        self.ctx = ctx
        self.move_time_ms = move_time_ms

    def set_time_ms(self, ms: int) -> None:
        self.move_time_ms = ms

    def get_move(self, board: chess.Board) -> Optional[str]:
        return engine_bestmove(self.ctx, board, self.move_time_ms)
