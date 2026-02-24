# -*- coding: utf-8 -*-
"""Opponent abstraction.

This is the clean extension point for Lichess later.
"""

from __future__ import annotations

from typing import Optional
import chess  # type: ignore


class Opponent:
    def start_game(self, board: chess.Board, human_is_white: bool) -> None:
        return

    def get_move(self, board: chess.Board) -> Optional[str]:
        raise NotImplementedError

    def stop(self) -> None:
        return
