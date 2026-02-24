# -*- coding: utf-8 -*-
"""Lichess opponent adapter (manual-start).

This runs a background stream reader for a single game and exposes:
  - wait_for_game(): blocks until a gameStart event is seen, then returns (game_id, is_white)
  - pop_remote_move(): returns next remote move uci (blocking with timeout)
  - submit_our_move(): POSTs our move to lichess
"""

from __future__ import annotations

import threading
import queue
from dataclasses import dataclass
from typing import Optional, Tuple, List

import chess  # type: ignore

from .lichess_client import LichessClient


@dataclass
class OnlineGame:
    game_id: str
    is_white: bool
    opponent: str = ""


class LichessOpponent:
    def __init__(self, client: LichessClient, username: str):
        self.client = client
        self.username = (username or "").lower()
        self.game: Optional[OnlineGame] = None

        self._stop = threading.Event()
        self._moves_q: "queue.Queue[str]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None

        self._seen_moves: List[str] = []

    def wait_for_game(self) -> OnlineGame:
        # Wait for a gameStart event
        for evt in self.client.stream_events():
            if self._stop.is_set():
                raise RuntimeError("Stopped")
            if evt.get("type") == "gameStart":
                gid = evt.get("game", {}).get("id")
                if gid:
                    # Determine color by reading the first gameFull message
                    gstream = self.client.stream_game(gid)
                    first = next(gstream, None)
                    if not first or first.get("type") != "gameFull":
                        # continue waiting; rare but safe
                        continue
                    white_name = (first.get("white", {}).get("name") or first.get("white", {}).get("id") or "").lower()
                    black_name = (first.get("black", {}).get("name") or first.get("black", {}).get("id") or "").lower()
                    is_white = (white_name == self.username)
                    opp = black_name if is_white else white_name
                    self.game = OnlineGame(game_id=gid, is_white=is_white, opponent=opp)
                    # Prime seen moves
                    moves_str = (first.get("state", {}) or {}).get("moves", "") or ""
                    self._seen_moves = [m for m in moves_str.split() if m]
                    return self.game
        raise RuntimeError("Event stream ended")

    def start_stream(self) -> None:
        if not self.game:
            raise RuntimeError("Call wait_for_game() first")
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_stream, daemon=True)
        self._thread.start()

    def _run_stream(self) -> None:
        assert self.game is not None
        for evt in self.client.stream_game(self.game.game_id):
            if self._stop.is_set():
                return
            t = evt.get("type")
            if t not in ("gameState", "gameFull"):
                continue
            state = evt.get("state", evt)  # gameFull nests state
            moves_str = (state.get("moves") or "")
            moves = [m for m in moves_str.split() if m]
            if len(moves) <= len(self._seen_moves):
                continue
            # enqueue new moves
            for mv in moves[len(self._seen_moves):]:
                self._moves_q.put(mv)
            self._seen_moves = moves

    def pop_remote_move(self, timeout_s: float = 0.1) -> Optional[str]:
        try:
            return self._moves_q.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def submit_our_move(self, uci: str) -> bool:
        if not self.game:
            return False
        return self.client.make_move(self.game.game_id, uci)

    def stop(self) -> None:
        self._stop.set()
