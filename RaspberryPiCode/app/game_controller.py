# -*- coding: utf-8 -*-
"""Readable game controller built on your existing modules.

This is a *behavior-preserving* refactor: the UART protocol and UI messaging
remain the same, but the core play loop becomes easier to follow.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
import chess  # type: ignore

from .protocol import EventType, parse_payload, format_capture_reply, format_engine_move
from .stockfish_opponent import StockfishOpponent


@dataclass
class LoopDeps:
    link: "BoardLink"      # from piSerial
    display: "Display"     # from piDisplay
    opponent: StockfishOpponent


class GameController:
    def __init__(self, deps: LoopDeps, *, human_is_white: bool = True):
        self.deps = deps
        self.board = chess.Board()
        self.human_is_white = human_is_white

    def _human_to_move(self) -> bool:
        if self.board.turn == chess.WHITE:
            return self.human_is_white
        return not self.human_is_white

    def _send_turn_prompt(self) -> None:
        side = "white" if self.board.turn == chess.WHITE else "black"
        self.deps.link.sendtoboard(f"turn_{side}")

    def _drain_nonblocking(self) -> None:
        # Drain a few events per tick so typing previews remain responsive.
        for _ in range(6):
            payload = self.deps.link.getboard_nonblocking()
            if payload is None:
                return
            evt = parse_payload(payload)
            self._handle_event(evt.type, evt.payload, nonblocking=True)

    def play_stockfish(self, *, move_time_ms: int) -> None:
        self.deps.opponent.set_time_ms(move_time_ms)
        self.board = chess.Board()
        self.deps.link.sendtoboard("GameStart")

        if not self.human_is_white:
            self.deps.display.send("Computer starts first.")
            time.sleep(0.25)
            self._engine_step()
        else:
            self._send_turn_prompt()
            self.deps.display.prompt_move("WHITE")

        while True:
            self._drain_nonblocking()

            if not self.board.is_game_over() and not self._human_to_move():
                self.deps.display.send("Engine Thinking...")
                self._engine_step()
                continue

            payload = self.deps.link.getboard()
            if payload is None:
                continue
            evt = parse_payload(payload)
            self._handle_event(evt.type, evt.payload)

    def _handle_event(self, typ: EventType, payload: str, nonblocking: bool = False) -> None:
        from piGame import GoToModeSelect  # keep exception class stable

        if typ == EventType.SHUTDOWN:
            from piGame import shutdown_pi
            shutdown_pi(self.deps.link, self.deps.display)
            raise GoToModeSelect()

        if typ == EventType.NEW_GAME:
            raise GoToModeSelect()

        if typ == EventType.TYPING:
            from piGame import handle_typing_preview
            handle_typing_preview(self.deps.display, payload)
            return

        if typ == EventType.CAPTURE_QUERY:
            from piGame import compute_capture_preview
            try:
                cap = compute_capture_preview(self.board, payload)
            except Exception:
                cap = False
            self.deps.link.sendtoboard(format_capture_reply(cap))
            return

        if typ == EventType.HINT:
            from piGame import send_hint_to_board, RuntimeState, GameConfig
            state = RuntimeState(board=self.board, mode="stockfish")
            cfg = GameConfig(skill_level=5, move_time_ms=int(self.deps.opponent.move_time_ms), human_is_white=self.human_is_white)
            send_hint_to_board(self.deps.link, self.deps.display, self.deps.opponent.ctx, state, cfg)
            return

        if typ == EventType.MOVE:
            from piGame import process_human_move
            process_human_move(link=self.deps.link, display=self.deps.display, board=self.board, uci=payload)
            return

        # Unknown messages: ignore in nonblocking mode, else show as invalid
        if not nonblocking:
            from piGame import parse_move_payload
            if not parse_move_payload(payload):
                self.deps.link.sendtoboard(f"error_invalid_{payload}")
                self.deps.display.show_invalid(payload)

    def _engine_step(self) -> None:
        uci = self.deps.opponent.get_move(self.board)
        if not uci:
            return
        mv = chess.Move.from_uci(uci)
        is_cap = self.board.is_capture(mv)
        self.deps.link.sendtoboard(format_engine_move(uci, is_cap))
        self.board.push(mv)

        from piGame import report_game_over, handoff_next_turn, GameConfig
        if self.board.is_game_over():
            report_game_over(self.deps.link, self.deps.display, self.board)
            return

        # Preserve OLED arrow/status behavior
        dummy_cfg = GameConfig(skill_level=5, move_time_ms=int(self.deps.opponent.move_time_ms), human_is_white=self.human_is_white)
        handoff_next_turn(self.deps.link, self.deps.display, self.board, "stockfish", dummy_cfg, uci)
