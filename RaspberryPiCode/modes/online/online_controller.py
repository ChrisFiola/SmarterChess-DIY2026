# -*- coding: utf-8 -*-
"""
Online (Lichess manual-start) controller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import time
import chess

from core.boardlink import BoardLink
from screen.display import Display
from modes.online.lichess_client import LichessClient
from modes.online.lichess_game import (
    extract_moves,
    extract_players,
    extract_status,
    extract_winner,
)
from core.net_utils import is_ap_mode, wifi_config_url
from core.protocol import (
    send_lcd_ack_for_payload,
    parse_uci_move,
    format_engine_move,
    NEW_GAME_MSGS,
    OK_MSGS,
    HINT_MSGS,
    IGNORED_MSGS,
)
from core.game_flow import (
    GameConfig,
    ReturnToMenu,
    handle_typing_message,
    handle_capq_message,
    validate_and_push_move,
    notify_game_over,
    handle_illegal_move,
    resolve_uci_promotion,
    send_turn_notification,
    shutdown_raspberry_pi,
)


class OnlineController:
    """Manages one complete Lichess game session."""

    def __init__(self, link: BoardLink, display: Display, cfg: GameConfig):
        self.link = link
        self.display = display
        self.cfg = cfg
        self.client = LichessClient()

    # ── Common Pico message handling ─────────────────────────────────────────

    def _handle_common(self, msg: str, board: chess.Board) -> bool:
        """Handle messages that are processed identically in every state.

        Returns True if the message was consumed.
        Raises ReturnToMenu or calls shutdown_raspberry_pi as appropriate.
        """
        if msg == "shutdown":
            shutdown_raspberry_pi(self.link, self.display)
            return True

        if msg.startswith("typing_"):
            handle_typing_message(
                self.link, self.display, msg[len("typing_"):], board, log_prefix="[ONLINE ACK]"
            )
            return True

        if handle_capq_message(self.link, board, msg):
            return True

        if msg in HINT_MSGS:
            self.display.send("Online mode\nHints disabled")
            return True

        return False

    # ── Connection / lobby ───────────────────────────────────────────────────

    def run(self) -> None:
        link, display = self.link, self.display

        link.send_to_board("SetupComplete")
        link.send_to_board("GameStart")
        link.send_to_board("ok_back_enable")
        display.send("Lichess connecting...\nOK = cancel")

        if is_ap_mode():
            url = wifi_config_url() or "http://192.168.4.1/"
            if hasattr(display, "show_qr"):
                display.show_qr(url, "Scan to setup WiFi", "OK = cancel")
            else:
                display.send(f"AP mode\nOpen:\n{url}\nOK = cancel")
            while True:
                m = link.read_from_board()
                if not m:
                    continue
                if m == "shutdown":
                    shutdown_raspberry_pi(link, display)
                    return
                if m in OK_MSGS | NEW_GAME_MSGS:
                    link.send_to_board("ok_back_disable")
                    raise ReturnToMenu()

        # Retry account fetch up to 3 times
        acct = None
        for _ in range(3):
            peek = link.try_read_from_board()
            if peek and peek in OK_MSGS | NEW_GAME_MSGS:
                link.send_to_board("ok_back_disable")
                raise ReturnToMenu()
            if peek == "shutdown":
                shutdown_raspberry_pi(link, display)
                return
            acct = self.client.get_account()
            if not acct.get("_error"):
                break
            time.sleep(1.0)

        if not acct or acct.get("_error"):
            display.send("Lichess offline\nWiFi/DNS error\nOK = Menu")
            while True:
                m = link.read_from_board()
                if not m:
                    continue
                if m == "shutdown":
                    shutdown_raspberry_pi(link, display)
                    return
                if m in OK_MSGS | NEW_GAME_MSGS:
                    link.send_to_board("ok_back_disable")
                    raise ReturnToMenu()

        username = (acct.get("username") or acct.get("id") or "").strip().lower()
        display.send("Lichess online\nStart a game\non lichess.org\nOK = cancel")

        # Poll for gameStart
        game_id = None
        last_banner_ms = 0
        while not game_id:
            peek = link.try_read_from_board()
            if peek == "shutdown":
                shutdown_raspberry_pi(link, display)
                return
            if peek and peek in OK_MSGS | NEW_GAME_MSGS:
                link.send_to_board("ok_back_disable")
                raise ReturnToMenu()

            now = int(time.time() * 1000)
            if now - last_banner_ms > 1500:
                display.send("Lichess online\nWaiting for game...\nOK = cancel")
                last_banner_ms = now

            try:
                for ev in self.client.stream_events(timeout_s=5):
                    if ev.get("type") == "gameStart":
                        game_id = (ev.get("game") or {}).get("id")
                        break
                    peek2 = link.try_read_from_board()
                    if peek2 == "shutdown":
                        shutdown_raspberry_pi(link, display)
                        return
                    if peek2 and peek2 in OK_MSGS | NEW_GAME_MSGS:
                        link.send_to_board("ok_back_disable")
                        raise ReturnToMenu()
                    if game_id:
                        break
            except Exception:
                time.sleep(0.5)
                continue

        if not game_id:
            display.send("No game found\nTry again")
            time.sleep(2)
            link.send_to_board("ok_back_disable")
            raise ReturnToMenu()

        display.send("Lichess connecting...\nLoading game")
        link.send_to_board("ok_back_disable")
        self._play_game(game_id, username)

    # ── Active game ──────────────────────────────────────────────────────────

    def _play_game(self, game_id: str, username: str) -> None:
        link, display = self.link, self.display
        stream = self.client.stream_game(game_id)

        board = chess.Board()
        last_move_count = 0
        you_are_white: Optional[bool] = None

        def send_turn_if_human():
            if board.turn != your_color:
                return
            send_turn_notification(link, board)

        awaiting_ok_ack = False
        in_move_entry = False

        def apply_new_moves(move_list, announce_new: bool = True):
            nonlocal last_move_count, awaiting_ok_ack, in_move_entry
            for uci in move_list[last_move_count:]:
                try:
                    mv = chess.Move.from_uci(uci)
                except Exception:
                    last_move_count += 1
                    continue
                is_cap = board.is_capture(mv)
                board.push(mv)
                last_move_count += 1
                link.send_to_board(format_engine_move(uci, is_cap))
                time.sleep(0.3)
                send_turn_if_human()
                if announce_new:
                    side_to_move = "WHITE" if board.turn == chess.WHITE else "BLACK"
                    promo_line = ""
                    if mv.promotion:
                        pl = chess.piece_symbol(mv.promotion)
                        promo_line = display.format_promo_line(pl)
                    display.show_arrow(
                        uci,
                        suffix=f"{promo_line}\n{side_to_move} to move" if promo_line else f"{side_to_move} to move",
                    )
                    awaiting_ok_ack = True
                    in_move_entry = False

        try:
            first = next(stream)
        except Exception:
            display.send("Lichess error\nGame stream")
            time.sleep(3)
            raise ReturnToMenu()

        white_name, black_name = extract_players(first)
        u = (username or "").strip().lower()
        if u and (black_name or "").strip().lower() == u:
            you_are_white = False
        elif u and (white_name or "").strip().lower() == u:
            you_are_white = True
        else:
            you_are_white = True
        your_color = chess.WHITE if you_are_white else chess.BLACK

        display.send(f"Connected\nYou are {'WHITE' if you_are_white else 'BLACK'}")
        apply_new_moves(extract_moves(first), announce_new=False)
        send_turn_if_human()

        prompted_for_this_turn = False
        last_wait_banner_ms = 0

        while True:
            # Non-blocking peek
            peek = link.try_read_from_board()
            if peek:
                if self._handle_common(peek, board):
                    if peek.startswith("typing_"):
                        awaiting_ok_ack = False
                        in_move_entry = True
                    # capq/hint handled; continue
                elif peek in NEW_GAME_MSGS:
                    display.send("Resigning...")
                    try:
                        self.client.resign_game(game_id)
                    except Exception:
                        pass
                    raise ReturnToMenu()
                elif peek in ("draw", "btn_draw"):
                    display.send("Offering draw...")
                    try:
                        self.client.offer_draw(game_id)
                    except Exception:
                        pass

            if board.is_game_over():
                notify_game_over(link, display, board)
                raise ReturnToMenu()

            # Opponent's turn — poll stream
            if board.turn != your_color:
                now = int(time.time() * 1000)
                if now - last_wait_banner_ms > 1500:
                    display.send("Waiting\nfor opponent...")
                    last_wait_banner_ms = now
                try:
                    while True:
                        payload = next(stream)
                        move_list = extract_moves(payload)
                        if len(move_list) > last_move_count:
                            apply_new_moves(move_list, announce_new=True)
                            break
                        status = extract_status(payload)
                        if status and status != "started":
                            winner = extract_winner(payload)
                            result = "1/2-1/2"
                            if winner == "white":
                                result = "1-0"
                            elif winner == "black":
                                result = "0-1"
                            link.send_to_board(f"GameOver:{result}")
                            display.send(f"GAME OVER\nResult {result}\nOK = Menu")
                            raise ReturnToMenu()
                except StopIteration:
                    display.send("Lichess ended")
                    time.sleep(2)
                    raise ReturnToMenu()
                except ReturnToMenu:
                    raise
                except Exception:
                    display.send("Lichess error\nStream lost")
                    time.sleep(3)
                    raise ReturnToMenu()
                prompted_for_this_turn = False
                continue

            # Your turn
            send_turn_if_human()
            if not prompted_for_this_turn and not awaiting_ok_ack and not in_move_entry:
                side = "WHITE" if your_color == chess.WHITE else "BLACK"
                display.prompt_move(side)
                prompted_for_this_turn = True

            msg = link.read_from_board()
            if not msg:
                continue

            if self._handle_common(msg, board):
                if msg.startswith("typing_"):
                    awaiting_ok_ack = False
                    in_move_entry = True
                continue

            if msg in NEW_GAME_MSGS:
                display.send("Resigning...")
                try:
                    self.client.resign_game(game_id)
                except Exception:
                    pass
                raise ReturnToMenu()

            if msg in ("draw", "btn_draw"):
                display.send("Offering draw...")
                try:
                    self.client.offer_draw(game_id)
                except Exception:
                    pass
                continue

            if msg in OK_MSGS:
                awaiting_ok_ack = False
                in_move_entry = False
                side = "WHITE" if your_color == chess.WHITE else "BLACK"
                display.prompt_move(side)
                prompted_for_this_turn = True
                continue

            awaiting_ok_ack = False
            in_move_entry = True

            uci = parse_uci_move(msg)
            if not uci:
                link.send_to_board(f"error_invalid_{msg}")
                display.show_invalid(msg)
                continue

            # Validate locally (promotion + legality) without pushing yet
            try:
                uci = resolve_uci_promotion(link, display, board, uci) or uci
            except ReturnToMenu:
                raise

            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                link.send_to_board(f"error_invalid_{uci}")
                display.show_invalid(uci)
                continue

            if move not in board.legal_moves:
                handle_illegal_move(link=link, display=display, board=board, uci=uci, label="ILLEGAL")
                continue

            # Submit to Lichess
            resp = self.client.make_move(game_id, uci)
            if not resp.get("ok"):
                display.send("Move rejected")
                time.sleep(2)
                continue

            board.push(move)
            last_move_count += 1
            send_turn_if_human()
            prompted_for_this_turn = False
            in_move_entry = False
            awaiting_ok_ack = False
