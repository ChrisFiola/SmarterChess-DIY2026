# -*- coding: utf-8 -*-
"""
Online (Lichess manual-start) controller.

Phase 1 extraction: move the online-mode state machine out of piGame.py.
Behavior parity with the previously working online mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional
import time
import chess  # type: ignore

from .lichess_client import LichessClient
from .lichess_game import extract_moves, extract_players, extract_status, extract_winner


@dataclass
class OnlineDeps:
    link: object
    display: object
    cfg: object

    parse_move_payload: Callable[[str], Optional[str]]
    compute_capture_preview: Callable[[chess.Board, str], bool]
    ask_promotion_piece: Callable[[object, object], str]
    side_name_from_board: Callable[[chess.Board], str]
    handle_typing_preview: Callable[[object, str], None]
    report_game_over: Callable[[object, object, chess.Board], str]
    shutdown_pi: Callable[[object, object], None]
    GoToModeSelect: type


class OnlineController:

    def __init__(self, deps: OnlineDeps):
        self.d = deps
        self.client = LichessClient()

    def run(self) -> None:
        link = self.d.link
        display = self.d.display

        # Handshake with Pico
        link.sendtoboard("SetupComplete")
        link.sendtoboard("GameStart")

        acct = self.client.get_account()
        if acct.get("_error"):
            display.send("Lichess offline\nCheck WiFi/DNS")
            time.sleep(5)
            raise self.d.GoToModeSelect()

        username = (acct.get("username") or acct.get("id") or "").strip().lower()
        display.send("Lichess online\nStart a game\non lichess.org")

        # Wait for gameStart
        game_id = None
        try:
            for ev in self.client.stream_events():
                if ev.get("type") == "gameStart":
                    game_id = (ev.get("game") or {}).get("id")
                    break
        except Exception:
            display.send("Lichess error\nEvent stream")
            time.sleep(3)
            raise self.d.GoToModeSelect()

        if not game_id:
            display.send("No game found\nTry again")
            time.sleep(2)
            raise self.d.GoToModeSelect()

        display.send("Connecting...\nLoading game")

        stream = self.client.stream_game(game_id)

        board = chess.Board()
        last_move_count = 0
        you_are_white: Optional[bool] = None

        # ---- helpers ----
        def uci_to_oled(uci: str) -> str:
            u = (uci or "").strip()
            if len(u) < 4:
                return u.upper()
            return f"{u[0].upper()}{u[1]} -> {u[2].upper()}{u[3]}"

        def send_turn_if_human():
            """Tell Pico to start/continue move entry ONLY when it's the human's turn.

            In this firmware, receiving `heyArduinoturn_*` immediately transitions the Pico UI
            into move-collection (lights CP coords + waits for OK). If we send turn updates
            for the opponent's turn, the CP will look like it's waiting for a human move
            when it shouldn't.
            """
            if board.turn != your_color:
                return
            link.sendtoboard(
                "turn_white" if board.turn == chess.WHITE else "turn_black"
            )

        # Flags controlling OLED overwrites
        awaiting_ok_ack = False  # True after opponent move until user starts input
        in_move_entry = (
            False  # True once typing_ starts; prevents prompt_move overwriting
        )

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

                # Pico: show trail + OK-only (engine_ack_pending behavior)
                link.sendtoboard(f"m{uci}{'_cap' if is_cap else ''}")
                send_turn_if_human()

                if announce_new:
                    side_to_move = "WHITE" if board.turn == chess.WHITE else "BLACK"
                    display.send(f"{uci_to_oled(uci)}\n{side_to_move} to move")

                    # Hold this message until OK is pressed and user starts input
                    awaiting_ok_ack = True
                    in_move_entry = False

        # ---- attach to game stream ----
        try:
            first = next(stream)
        except Exception:
            display.send("Lichess error\nGame stream")
            time.sleep(3)
            raise self.d.GoToModeSelect()

        white_name, black_name = extract_players(first)

        w = (white_name or "").strip().lower()
        b = (black_name or "").strip().lower()
        u = (username or "").strip().lower()

        if u and b and u == b:
            you_are_white = False
        elif u and w and u == w:
            you_are_white = True
        else:
            you_are_white = True  # fallback

        your_color = chess.WHITE if you_are_white else chess.BLACK

        display.send(f"Connected\nYou are {'WHITE' if you_are_white else 'BLACK'}")

        apply_new_moves(extract_moves(first), announce_new=False)
        send_turn_if_human()

        prompted_for_this_turn = False
        last_wait_banner_ms = 0

        while True:
            # --- Non blocking handling (buttons from Pico) ---
            peek = link.getboard_nonblocking()
            if peek:
                if peek == "shutdown":
                    self.d.shutdown_pi(link, display)
                    return

                if peek.startswith("typing_"):
                    # As soon as typing starts, we are in move entry => never show prompt_move this turn
                    awaiting_ok_ack = False
                    in_move_entry = True
                    self.d.handle_typing_preview(display, peek[7:])

                if peek.startswith("capq_"):
                    uciq = peek[5:].strip()
                    try:
                        cap = self.d.compute_capture_preview(board, uciq)
                    except Exception:
                        cap = False
                    link.sendtoboard(f"capr_{1 if cap else 0}")

                if peek in ("n", "new", "in", "newgame", "btn_new"):
                    display.send("Resigning...")
                    try:
                        self.client.resign_game(game_id)
                    except Exception:
                        pass
                    raise self.d.GoToModeSelect()

                if peek in ("draw", "btn_draw"):
                    display.send("Offering draw...")
                    try:
                        self.client.offer_draw(game_id)
                    except Exception:
                        pass

                if peek in ("hint", "btn_hint"):
                    display.send("Online mode\nHints disabled")

            if board.is_game_over():
                self.d.report_game_over(link, display, board)
                raise self.d.GoToModeSelect()

            # --- Opponent turn ---
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

                            link.sendtoboard(f"GameOver:{result}")
                            display.send(f"GAME OVER\nResult {result}\nStart new game?")
                            raise self.d.GoToModeSelect()

                except StopIteration:
                    display.send("Lichess ended")
                    time.sleep(2)
                    raise self.d.GoToModeSelect()

                except Exception:
                    display.send("Lichess error\nStream lost")
                    time.sleep(3)
                    raise self.d.GoToModeSelect()

                prompted_for_this_turn = False
                continue

            # --- Your turn ---
            send_turn_if_human()

            # IMPORTANT:
            # - If we're awaiting OK ack, keep opponent-move message
            # - If move entry has started (typing_), do NOT overwrite it with prompt_move
            if (
                (not prompted_for_this_turn)
                and (not awaiting_ok_ack)
                and (not in_move_entry)
            ):
                side = "WHITE" if your_color == chess.WHITE else "BLACK"
                display.prompt_move(side)
                prompted_for_this_turn = True

            msg = link.getboard()
            if not msg:
                continue

            if msg == "shutdown":
                self.d.shutdown_pi(link, display)
                return

            if msg.startswith("typing_"):
                awaiting_ok_ack = False
                in_move_entry = True
                self.d.handle_typing_preview(display, msg[7:])
                continue

            if msg.startswith("capq_"):
                uciq = msg[5:].strip()
                try:
                    cap = self.d.compute_capture_preview(board, uciq)
                except Exception:
                    cap = False
                link.sendtoboard(f"capr_{1 if cap else 0}")
                continue

            if msg in ("n", "new", "in", "newgame", "btn_new"):
                display.send("Resigning...")
                try:
                    self.client.resign_game(game_id)
                except Exception:
                    pass
                raise self.d.GoToModeSelect()

            if msg in ("draw", "btn_draw"):
                display.send("Offering draw...")
                try:
                    self.client.offer_draw(game_id)
                except Exception:
                    pass
                continue

            if msg in ("hint", "btn_hint"):
                display.send("Online mode\nHints disabled")
                continue

            # Any move payload means we are in move entry
            awaiting_ok_ack = False
            in_move_entry = True

            uci = self.d.parse_move_payload(msg)
            if not uci:
                link.sendtoboard(f"error_invalid_{msg}")
                display.show_invalid(msg)
                continue

            # Promotion check
            if len(uci) == 4:
                try:
                    piece = board.piece_at(chess.parse_square(uci[:2]))
                    if piece and piece.piece_type == chess.PAWN:
                        rank = int(uci[3])
                        if (piece.color == chess.WHITE and rank == 8) or (
                            piece.color == chess.BLACK and rank == 1
                        ):
                            promo = self.d.ask_promotion_piece(link, display)
                            uci += promo
                except Exception:
                    pass

            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                link.sendtoboard(f"error_invalid_{uci}")
                display.show_invalid(uci)
                continue

            if move not in board.legal_moves:
                link.sendtoboard(f"error_illegal_{uci}")
                display.show_illegal(uci, self.d.side_name_from_board(board))
                continue

            resp = self.client.make_move(game_id, uci)
            if not resp.get("ok"):
                display.send("Move rejected")
                time.sleep(2)
                continue

            board.push(move)
            last_move_count += 1
            send_turn_if_human()

            # Reset for next cycle
            prompted_for_this_turn = False
            in_move_entry = False
            awaiting_ok_ack = False
