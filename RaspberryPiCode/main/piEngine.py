# -*- coding: utf-8 -*-
"""
Engine context and helpers (Stockfish) for SmarterChess (modular version).
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING
import time

STOCKFISH_PATH: str = "/usr/games/stockfish"

if TYPE_CHECKING:
    import chess  # type: ignore
    import chess.engine  # type: ignore


class EngineContext:
    def __init__(self):
        self.engine: Optional["chess.engine.SimpleEngine"] = None

    def ensure(self, path: str = STOCKFISH_PATH) -> "chess.engine.SimpleEngine":
        if self.engine is not None:
            return self.engine

        # Local import: only pay import cost when actually starting the engine
        import chess.engine  # type: ignore

        while True:
            try:
                self.engine = chess.engine.SimpleEngine.popen_uci(
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
    # Local import: only pay import cost when actually starting the engine
    import chess.engine  # type: ignore

    if brd.is_game_over():
        return None
    engine = ctx.ensure(STOCKFISH_PATH)
    limit = chess.engine.Limit(time=max(0.01, ms / 1000.0))
    result = engine.play(brd, limit)  # type: ignore
    return result.move.uci() if result.move else None


def engine_hint(ctx: EngineContext, brd: "chess.Board", ms: int) -> Optional[str]:
    # Local import: only pay import cost when actually starting the engine
    import chess.engine  # type: ignore

    try:
        engine = ctx.ensure(STOCKFISH_PATH)
        info = engine.analyse(brd, chess.engine.Limit(time=max(0.01, ms / 1000.0)))  # type: ignore
        pv = info.get("pv")
        if pv:
            return pv[0].uci()
    except Exception:
        pass
    return engine_bestmove(ctx, brd, ms)
