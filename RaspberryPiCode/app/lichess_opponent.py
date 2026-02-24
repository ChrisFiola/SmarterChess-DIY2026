# -*- coding: utf-8 -*-
"""Lichess Board API opponent (manual-start).

This opponent:
  - waits for a Lichess gameStart event
  - attaches to the game stream
  - provides opponent moves via a queue
  - submits our moves via make_move()
"""

from __future__ import annotations

import threading
import queue
from dataclasses import dataclass
from typing import Optional, List

import chess  # type: ignore

from .lichess_client import LichessClient


@dataclass
class OnlineGameInfo:
    game_id: str
    is_white: bool
    opponent: str = ""


class LichessOpponent:
    def __init__(self, client: LichessClient):
        self.client = client
        self.game: Optional[OnlineGameInfo] = None

        self._stop = threading.Event()
        self._moves_q: "queue.Queue[str]" = queue.Queue()
        self._stream_thread: Optional[threading.Thread] = None

        # Track moves we've already seen in stream order (UCI list)
        self._seen_moves: List[str] = []

    def stop(self) -> None:
        self._stop.set()
        t = self._stream_thread
        if t and t.is_alive():
            t.join(timeout=2.0)

    def wait_for_game_start(self, *, display=None) -> OnlineGameInfo:
        """Block until we receive a gameStart event on the global event stream."""
        if display:
            display.send("Lichess online\nStart a game\non lichess.org")

        for evt in self.client.stream_events():
            if self._stop.is_set():
                raise RuntimeError("Stopped")

            if evt.get("type") != "gameStart":
                continue

            g = evt.get("game", {}) or {}
            game_id = g.get("id")
            if not game_id:
                continue

            opp = (g.get("opponent") or {}).get("username", "") or ""
            # Color determined after attaching to game stream
            self.game = OnlineGameInfo(game_id=game_id, is_white=True, opponent=opp)

            if display:
                display.send(f"Connected\nvs {opp or 'opponent'}\n{game_id}")
            return self.game

        raise RuntimeError("No gameStart event")

    def attach_and_sync(self, board: chess.Board, *, display=None) -> OnlineGameInfo:
        """Attach to the game stream, determine our color, and sync initial moves."""
        if not self.game:
            self.wait_for_game_start(display=display)

        assert self.game is not None
        game_id = self.game.game_id

        for msg in self.client.stream_game(game_id):
            if self._stop.is_set():
                raise RuntimeError("Stopped")

            if msg.get("type") != "gameFull":
                continue

            w = (msg.get("white") or {})
            b = (msg.get("black") or {})

            # Determine our color.
            w_me = w.get("me") is True
            b_me = b.get("me") is True
            if w_me or b_me:
                self.game.is_white = bool(w_me and not b_me)
            else:
                try:
                    acct = self.client.get_account()
                    my_id = (acct.get("id") or "").lower()
                    w_id = (w.get("id") or "").lower()
                    b_id = (b.get("id") or "").lower()
                    if my_id and (my_id == w_id or my_id == b_id):
                        self.game.is_white = (my_id == w_id)
                    else:
                        self.game.is_white = True
                except Exception:
                    self.game.is_white = True

            # Sync initial moves
            st = msg.get("state") or {}
            moves = (st.get("moves") or "").strip()
            if moves:
                for uci in moves.split():
                    if uci and uci not in self._seen_moves:
                        try:
                            board.push_uci(uci)
                            self._seen_moves.append(uci)
                        except Exception:
                            pass

            self._start_stream_thread(game_id)
            return self.game

        raise RuntimeError("Failed to attach game stream")

    def _start_stream_thread(self, game_id: str) -> None:
        if self._stream_thread and self._stream_thread.is_alive():
            return

        def _run() -> None:
            for msg in self.client.stream_game(game_id):
                if self._stop.is_set():
                    return
                if msg.get("type") != "gameState":
                    continue
                moves = (msg.get("moves") or "").strip()
                if not moves:
                    continue
                seq = moves.split()
                # Enqueue any new moves beyond what we've already seen.
                for uci in seq[len(self._seen_moves):]:
                    if not uci:
                        continue
                    self._moves_q.put(uci)
                    self._seen_moves.append(uci)

        self._stream_thread = threading.Thread(target=_run, daemon=True)
        self._stream_thread.start()

    def submit_our_move(self, uci: str) -> bool:
        if not self.game:
            return False
        return self.client.make_move(self.game.game_id, uci)

    def wait_opponent_move(self, timeout_s: float = 0.25) -> Optional[str]:
        try:
            return self._moves_q.get(timeout=timeout_s)
        except queue.Empty:
            return None
