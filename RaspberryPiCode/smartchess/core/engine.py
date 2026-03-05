# -*- coding: utf-8 -*-
"""
Engine context and helpers (Stockfish) for SmarterChess (modular version).
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING
import time

STOCKFISH_PATH: str = "/usr/games/stockfish"

if TYPE_CHECKING:
    import chess
    import chess.engine


class EngineContext:
    def __init__(self):
        self.engine: Optional["chess.engine.SimpleEngine"] = None
        self._chess_engine = None  # cached module

    def _engine_mod(self):
        if self._chess_engine is None:
            import chess.engine

            self._chess_engine = chess.engine
        return self._chess_engine

    def ensure(self, path: str = STOCKFISH_PATH) -> "chess.engine.SimpleEngine":
        if self.engine is not None:
            return self.engine

        chess_engine = self._engine_mod()

        while True:
            try:
                self.engine = chess_engine.SimpleEngine.popen_uci(
                    path, stderr=None, timeout=None
                )
                return self.engine
            except Exception:
                time.sleep(1)

    def quit(self):
        if self.engine:
            try:
                self.engine.quit()
            except Exception:
                pass
            self.engine = None


def engine_bestmove(ctx: EngineContext, brd: "chess.Board", ms: int) -> Optional[str]:
    if brd.is_game_over():
        return None

    chess_engine = ctx._engine_mod()

    engine = ctx.ensure(STOCKFISH_PATH)
    limit = chess_engine.Limit(time=max(0.01, ms / 1000.0))

    result = engine.play(brd, limit)
    return result.move.uci() if result.move else None


def engine_hint(ctx: EngineContext, brd: "chess.Board", ms: int) -> Optional[str]:
    if brd.is_game_over():
        return None

    chess_engine = ctx._engine_mod()
    limit = chess_engine.Limit(time=max(0.01, ms / 1000.0))

    try:
        engine = ctx.ensure(STOCKFISH_PATH)
        info = engine.analyse(brd, limit, multipv=1)

        pv = info.get("pv")
        if pv:
            return pv[0].uci()

    except Exception:
        pass

    return engine_bestmove(ctx, brd, ms)
