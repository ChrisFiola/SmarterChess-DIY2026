# -*- coding: utf-8 -*-
"""
Stockfish engine wrapper for SmarterChess.

EngineContext manages a single Stockfish process for the lifetime of the app.
It is shared across game modes so we only pay the startup cost once.
"""
from __future__ import annotations

import time
from typing import Optional, TYPE_CHECKING

STOCKFISH_PATH: str = "/usr/games/stockfish"

if TYPE_CHECKING:
    import chess
    import chess.engine


class EngineContext:
    def __init__(self):
        """Create an unstarted engine context.

        The Stockfish process is not launched until ensure() is first called,
        so construction is always cheap and safe to do at program start.
        """
        self.engine: Optional["chess.engine.SimpleEngine"] = None
        self._chess_engine = None  # chess.engine module, lazily loaded
        self.hint_override_active = False  # set after hint() overrides strength

    def _engine_mod(self):
        """Return the chess.engine module, importing it on first call."""
        if self._chess_engine is None:
            import chess.engine
            self._chess_engine = chess.engine
        return self._chess_engine

    def ensure(self, path: str = STOCKFISH_PATH) -> "chess.engine.SimpleEngine":
        """Return the running engine instance, starting it if necessary.

        Retries indefinitely if the engine fails to start (e.g. binary not found yet).
        """
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
        """Shut down the engine process cleanly."""
        if self.engine:
            try:
                self.engine.quit()
            except Exception:
                pass
            self.engine = None

    def bestmove(
        self, board: "chess.Board", time_ms: int, depth: Optional[int] = None
    ) -> Optional[str]:
        """Return the best move UCI string for the given position, or None if game is over.

        If *depth* is given, the search is capped at that many plies in
        addition to the time limit — whichever is reached first.
        """
        if board.is_game_over():
            return None

        chess_engine = self._engine_mod()
        engine = self.ensure()
        limit = chess_engine.Limit(
            time=max(0.01, time_ms / 1000.0),
            depth=depth,
        )

        result = engine.play(board, limit)
        return result.move.uci() if result.move else None

    def hint(self, board: "chess.Board", time_ms: int) -> Optional[str]:
        """Return the top suggested move UCI string for the given position.

        Temporarily sets the engine to full strength so hints are always the
        best possible advice, regardless of the current difficulty level.
        Uses engine analysis (multipv=1) and falls back to bestmove on failure.
        """
        if board.is_game_over():
            return None

        chess_engine = self._engine_mod()
        engine = self.ensure()
        limit = chess_engine.Limit(time=max(0.01, time_ms / 1000.0))

        try:
            # Full strength for hints
            engine.configure({"UCI_LimitStrength": False, "Skill Level": 20})
            self.hint_override_active = True
            info = engine.analyse(board, limit, multipv=1)
            pv = info.get("pv")
            if pv:
                return pv[0].uci()
        except Exception:
            pass

        return self.bestmove(board, time_ms)
