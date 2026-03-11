# -*- coding: utf-8 -*-
"""Readable game controller built on your existing modules.

This is a *behavior-preserving* refactor: the UART protocol and UI messaging
remain the same, but the core play loop becomes easier to follow.
"""

from dataclasses import dataclass
import time

import chess

from core.boardlink import BoardLink
from core.game_flow import (
    GameConfig,
    GameState,
    ReturnToMenu,
    check_move_captures,
    handle_typing_message,
    notify_game_over,
    prompt_next_turn,
    send_move_hint,
    send_turn_notification,
    shutdown_raspberry_pi,
    validate_and_push_move,
)
from core.protocol import (
    EventType,
    parse_payload,
    parse_uci_move,
    format_capture_reply,
    format_engine_move,
)
from modes.vs_computer.stockfish_opponent import StockfishOpponent
from screen.display import Display


@dataclass
class GameDeps:
    link: BoardLink
    display: Display
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
        send_turn_notification(self.deps.link, self.board)

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
        if typ == EventType.SHUTDOWN:
            shutdown_raspberry_pi(self.deps.link, self.deps.display)
            raise ReturnToMenu()

        if typ == EventType.NEW_GAME:
            raise ReturnToMenu()

        if typ == EventType.TYPING:
            handle_typing_message(
                self.deps.link, self.deps.display, payload, self.board
            )
            return

        if typ == EventType.OK:
            print("[PICO OK] prompt_move()", flush=True)
            # OK is used as an acknowledgement / "enter move" trigger from the Pico UI.
            # It should NEVER be treated as a move payload.
            side = "WHITE" if self.board.turn == chess.WHITE else "BLACK"
            self.deps.display.prompt_move(side)
            return

        if typ == EventType.CAPTURE_QUERY:
            try:
                cap = check_move_captures(self.board, payload)
            except Exception:
                cap = False
            self.deps.link.send_to_board(format_capture_reply(cap))
            return

        if typ == EventType.HINT:
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
            # Use validate_and_push_move directly (not apply_human_move) to avoid
            # sending a spurious turn_{engine_color} notification.  That message
            # would trigger _handle_turn on the Pico, whose 80-ms window then
            # accidentally eats the subsequent m{engine_uci} from the UART buffer.
            # The correct turn_{human_color} is sent by _play_one_engine_move via
            # prompt_next_turn once Stockfish has responded.
            move = validate_and_push_move(
                link=self.deps.link,
                display=self.deps.display,
                board=self.board,
                uci=payload,
            )
            if move is not None and self.board.is_game_over():
                notify_game_over(self.deps.link, self.deps.display, self.board)
            return

        # Unknown messages: ignore in nonblocking mode, else show as invalid
        if not nonblocking and not parse_uci_move(payload):
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

        if self.board.is_game_over():
            notify_game_over(self.deps.link, self.deps.display, self.board)
            return

        # Preserve OLED arrow/status behavior
        dummy_cfg = GameConfig(
            skill_level=5,
            move_time_ms=int(self.deps.opponent.move_time_ms),
            human_is_white=self.human_is_white,
        )
        # If the engine's move puts the human king in check, send the check signal
        # BEFORE the engine-overlay message.  blink_square_keep on the Pico blocks
        # for ~1440 ms (4 × 360 ms); delaying the overlay by 1.6 s ensures the
        # blink finishes before _handle_engine_move sets engine_ack_pending, so the
        # check_ message is not silently discarded in the ack-pending loop.
        if self.board.is_check():
            ksq = self.board.king(self.board.turn)
            if ksq is not None:
                self.deps.link.send_to_board(f"check_{chess.square_name(ksq)}")
                time.sleep(1.6)

        prompt_next_turn(
            self.deps.link, self.deps.display, self.board, "stockfish", dummy_cfg, uci
        )
