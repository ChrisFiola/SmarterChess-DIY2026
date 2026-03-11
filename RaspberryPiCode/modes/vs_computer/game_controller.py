# -*- coding: utf-8 -*-
"""Readable game controller built on your existing modules.

This is a *behavior-preserving* refactor: the UART protocol and UI messaging
remain the same, but the core play loop becomes easier to follow.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from screen.display import Display
from core.boardlink import BoardLink
import chess

from core.protocol import (
    EventType,
    parse_payload,
    format_capture_reply,
    format_engine_move,
    send_lcd_ack_for_payload,
)
from modes.vs_computer.stockfish_opponent import StockfishOpponent


@dataclass
class GameDeps:
    link: "BoardLink"  # from piSerial
    display: "Display"  # from piDisplay
    opponent: StockfishOpponent


class GameController:
    def __init__(self, deps: GameDeps, *, human_is_white: bool = True):
        self.deps = deps
        self.board = chess.Board()
        self.human_is_white = human_is_white

    def _is_human_turn(self) -> bool:
        if self.board.turn == chess.WHITE:
            return self.human_is_white
        return not self.human_is_white

    def _send_turn_notification(self) -> None:
        side = "white" if self.board.turn == chess.WHITE else "black"
        self.deps.link.send_to_board(f"turn_{side}")

    def _process_pending_messages(self) -> None:
        # Drain a few events per tick so typing previews remain responsive.
        for _ in range(6):
            payload = self.deps.link.try_read_from_board()
            if payload is None:
                return
            evt = parse_payload(payload)
            self._handle_event(evt.type, evt.payload, nonblocking=True)

    def run_stockfish_game(self, *, move_time_ms: int) -> None:
        self.deps.opponent.set_time_ms(move_time_ms)
        self.board = chess.Board()
        self.deps.link.send_to_board("GameStart")

        if not self.human_is_white:
            self.deps.display.send("Computer starts first.")
            time.sleep(0.25)
            self._play_one_engine_move()
        else:
            self._send_turn_notification()
            self.deps.display.prompt_move("WHITE")

        while True:
            self._process_pending_messages()

            if not self.board.is_game_over() and not self._is_human_turn():
                # self.deps.display.send("Engine Thinking...")
                self._play_one_engine_move()
                continue

            payload = self.deps.link.read_from_board()
            if payload is None:
                continue
            evt = parse_payload(payload)
            self._handle_event(evt.type, evt.payload)

    def _handle_event(
        self, typ: EventType, payload: str, nonblocking: bool = False
    ) -> None:
        from core.game_flow import ReturnToMenu  # keep exception class stable

        if typ == EventType.SHUTDOWN:
            from core.game_flow import shutdown_raspberry_pi

            shutdown_raspberry_pi(self.deps.link, self.deps.display)
            raise ReturnToMenu()

        if typ == EventType.NEW_GAME:
            raise ReturnToMenu()

        if typ == EventType.TYPING:
            from core.game_flow import update_typing_display

            update_typing_display(self.deps.display, payload, self.board)

            # ACK must be tied to the LCD update that just happened,
            # not to a later OK press.
            send_lcd_ack_for_payload(self.deps.link, payload)

            return

        if typ == EventType.OK:
            print("[PICO OK] prompt_move()", flush=True)
            # OK is used as an acknowledgement / "enter move" trigger from the Pico UI.
            # It should NEVER be treated as a move payload.
            side = "WHITE" if self.board.turn == chess.WHITE else "BLACK"
            self.deps.display.prompt_move(side)
            return

        if typ == EventType.CAPTURE_QUERY:
            from core.game_flow import check_move_captures

            try:
                cap = check_move_captures(self.board, payload)
            except Exception:
                cap = False
            self.deps.link.send_to_board(format_capture_reply(cap))
            return

        if typ == EventType.HINT:
            from core.game_flow import send_move_hint, GameState, GameConfig

            state = GameState(board=self.board, mode="stockfish")
            cfg = GameConfig(
                skill_level=5,
                move_time_ms=int(self.deps.opponent.move_time_ms),
                human_is_white=self.human_is_white,
            )
            send_move_hint(
                self.deps.link, self.deps.display, self.deps.opponent.ctx, state, cfg
            )
            return

        if typ == EventType.MOVE:
            from core.game_flow import apply_human_move

            apply_human_move(
                link=self.deps.link,
                display=self.deps.display,
                board=self.board,
                uci=payload,
            )
            return

        # Unknown messages: ignore in nonblocking mode, else show as invalid
        if not nonblocking:
            from core.protocol import parse_uci_move

            if not parse_uci_move(payload):
                self.deps.link.send_to_board(f"error_invalid_{payload}")
                self.deps.display.show_invalid(payload)

    def _play_one_engine_move(self) -> None:
        uci = self.deps.opponent.get_move(self.board)
        if not uci:
            return
        mv = chess.Move.from_uci(uci)
        is_cap = self.board.is_capture(mv)
        self.deps.link.send_to_board(format_engine_move(uci, is_cap))
        self.board.push(mv)

        from core.game_flow import notify_game_over, prompt_next_turn, GameConfig

        if self.board.is_game_over():
            notify_game_over(self.deps.link, self.deps.display, self.board)
            return

        # Preserve OLED arrow/status behavior
        dummy_cfg = GameConfig(
            skill_level=5,
            move_time_ms=int(self.deps.opponent.move_time_ms),
            human_is_white=self.human_is_white,
        )
        prompt_next_turn(
            self.deps.link, self.deps.display, self.board, "stockfish", dummy_cfg, uci
        )
