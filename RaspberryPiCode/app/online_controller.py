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

        display.send("Connected\nAttaching...")

        stream = self.client.stream_game(game_id)

        board = chess.Board()
        last_move_count = 0
        you_are_white = True

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
        # else: keep default True (fallback)

        display.send(f"Connected\nYou are {'WHITE' if you_are_white else 'BLACK'}")

        def apply_new_moves(move_list):
            nonlocal last_move_count
            for uci in move_list[last_move_count:]:
                try:
                    mv = chess.Move.from_uci(uci)
                except Exception:
                    last_move_count += 1
                    continue

                is_cap = board.is_capture(mv)
                link.sendtoboard(f"m{uci}{'_cap' if is_cap else ''}")
                board.push(mv)
                last_move_count += 1

        apply_new_moves(extract_moves(first))

        def send_turn():
            link.sendtoboard(
                "turn_white" if board.turn == chess.WHITE else "turn_black"
            )

        send_turn()

        your_color = chess.WHITE if you_are_white else chess.BLACK
        prompted_for_this_turn = False

        while True:

            # --- Non blocking handling ---
            peek = link.getboard_nonblocking()
            if peek:

                if peek == "shutdown":
                    self.d.shutdown_pi(link, display)
                    return

                if peek.startswith("typing_"):
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

                display.send("Waiting\nfor opponent...")

                try:
                    while True:
                        payload = next(stream)
                        move_list = extract_moves(payload)

                        if len(move_list) > last_move_count:
                            apply_new_moves(move_list)
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

                send_turn()
                prompted_for_this_turn = False
                continue

            # --- Your turn ---
            send_turn()
            if not prompted_for_this_turn:
                display.prompt_move("WHITE" if your_color == chess.WHITE else "BLACK")
                prompted_for_this_turn = True

            msg = link.getboard()
            if not msg:
                continue

            if msg == "shutdown":
                self.d.shutdown_pi(link, display)
                return

            if msg.startswith("typing_"):
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

            if peek in ("draw", "btn_draw"):
                display.send("Offering draw...")
                try:
                    self.client.offer_draw(game_id)
                except Exception:
                    pass
                continue

            if msg in ("hint", "btn_hint"):
                display.send("Online mode\nHints disabled")
                continue

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
            prompted_for_this_turn = False
