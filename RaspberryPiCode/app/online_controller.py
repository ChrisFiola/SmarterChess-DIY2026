# -*- coding: utf-8 -*-
"""Online (Lichess manual-start) controller.

Phase 1 extraction: move the online-mode state machine out of piGame.py
to reduce complexity and eliminate indentation-risk churn.

This module intentionally depends on helpers/functions defined in piGame.py
(parsers, promotion helpers, capture preview, UI helpers). The goal of Phase 1
is purely structural extraction with behavior parity.
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
    # Pico link + LCD display types are defined in piGame imports; kept as duck-typed.
    link: object
    display: object
    cfg: object

    # Functions imported from piGame and passed in (keeps Phase 1 minimal / safe).
    parse_move_payload: Callable[[str], Optional[str]]
    compute_capture_preview: Callable[[chess.Board, str], bool]
    ask_promotion_piece: Callable[[object, object], str]
    side_name_from_board: Callable[[chess.Board], str]
    handle_typing_preview: Callable[[object, str], None]
    report_game_over: Callable[[object, object, chess.Board], str]
    game_over_wait_ok_and_ack: Callable[[str], None]
    shutdown_pi: Callable[[object, object], None]
    GoToModeSelect: type


class OnlineController:
    def __init__(self, deps: OnlineDeps):
        self.d = deps
        self.client = LichessClient()

    def run(self) -> None:
        link = self.d.link
        display = self.d.display

        # Ensure Pico enters GAME_RUNNING (critical handshake)
        link.sendtoboard("SetupComplete")
        link.sendtoboard("GameStart")

        acct = self.client.get_account()
        if acct.get("_error"):
            display.send("Lichess offline\nCheck WiFi/DNS")
            time.sleep(5)
            raise self.d.GoToModeSelect()

        username = (acct.get("username") or acct.get("id") or "").strip().lower()
        display.send("Lichess online\nStart a game\non lichess.org")

        # Wait for a gameStart event
        game_id = None
        try:
            for ev in self.client.stream_events():
                if ev.get("type") == "gameStart":
                    game = ev.get("game") or {}
                    game_id = game.get("id")
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

        # Attach to game stream
        stream = self.client.stream_game(game_id)

        # Initialize local board + determine color from gameFull
        brd = chess.Board()
        last_n = 0
        you_are_white = True

        try:
            first = next(stream)
        except Exception:
            display.send("Lichess error\nGame stream")
            time.sleep(3)
            raise self.d.GoToModeSelect()

        wname, bname = extract_players(first)
        if wname and bname and username:
            you_are_white = (wname.strip().lower() == username)

        display.send(f"Connected\nYou are {'WHITE' if you_are_white else 'BLACK'}")

        def apply_new_moves(moves_list):
            nonlocal last_n
            for uci in moves_list[last_n:]:
                try:
                    mv = chess.Move.from_uci(uci)
                except Exception:
                    last_n += 1
                    continue
                is_cap = brd.is_capture(mv)
                link.sendtoboard(f"m{uci}{'_cap' if is_cap else ''}")
                brd.push(mv)
                last_n += 1

        apply_new_moves(extract_moves(first))

        def send_turn():
            if brd.turn == chess.WHITE:
                link.sendtoboard("turn_white")
            else:
                link.sendtoboard("turn_black")

        send_turn()

        your_color = chess.WHITE if you_are_white else chess.BLACK

        while True:
            # Non-blocking service of previews + capq + new game
            peek = link.getboard_nonblocking()
            if peek is not None:
                if peek == "shutdown":
                    self.d.shutdown_pi(link, display)
                    return
                if peek.startswith("typing_"):
                    self.d.handle_typing_preview(display, peek[len("typing_"):])
                if peek.startswith("capq_"):
                    uciq = peek[5:].strip()
                    try:
                        cap = self.d.compute_capture_preview(brd, uciq)
                    except Exception:
                        cap = False
                    link.sendtoboard(f"capr_{1 if cap else 0}")
                if peek in ("n", "new", "in", "newgame", "btn_new"):
                    raise self.d.GoToModeSelect()
                if peek in ("hint", "btn_hint"):
                    display.send("Online mode\nHints disabled")

            if brd.is_game_over():
                self.d.report_game_over(link, display, brd)
                raise self.d.GoToModeSelect()

            if brd.turn != your_color:
                # Wait for opponent move from stream
                display.send("Waiting\nfor opponent...")
                try:
                    while True:
                        payload = next(stream)
                        mvlist = extract_moves(payload)
                        if mvlist and len(mvlist) > last_n:
                            apply_new_moves(mvlist)
                            break
                        status = extract_status(payload)
                        if status and status != "started":
                            winner = extract_winner(payload)
                            if winner == "white":
                                res = "1-0"
                            elif winner == "black":
                                res = "0-1"
                            else:
                                res = "1/2-1/2"
                            self.d.game_over_wait_ok_and_ack(res)
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
                continue

            # Your turn
            send_turn()
            display.prompt_move("WHITE" if your_color == chess.WHITE else "BLACK")

            msg = link.getboard()
            if msg is None:
                continue
            if msg == "shutdown":
                self.d.shutdown_pi(link, display)
                return
            if msg.startswith("typing_"):
                self.d.handle_typing_preview(display, msg[len("typing_"):])
                continue
            if msg.startswith("capq_"):
                uciq = msg[5:].strip()
                try:
                    cap = self.d.compute_capture_preview(brd, uciq)
                except Exception:
                    cap = False
                link.sendtoboard(f"capr_{1 if cap else 0}")
                continue
            if msg in ("n", "new", "in", "newgame", "btn_new"):
                raise self.d.GoToModeSelect()
            if msg in ("hint", "btn_hint"):
                display.send("Online mode\nHints disabled")
                continue

            uci = self.d.parse_move_payload(msg)
            if not uci:
                link.sendtoboard(f"error_invalid_{msg}")
                display.show_invalid(msg)
                continue

            # Promotion pre-detect
            from_sq = uci[:2]
            to_sq = uci[2:4]
            if len(uci) == 4:
                try:
                    piece = brd.piece_at(chess.parse_square(from_sq))
                    if piece and piece.piece_type == chess.PAWN:
                        rank = int(to_sq[1])
                        if (piece.color == chess.WHITE and rank == 8) or (piece.color == chess.BLACK and rank == 1):
                            promo = self.d.ask_promotion_piece(link, display)
                            uci = uci + promo
                except Exception:
                    pass

            try:
                mv = chess.Move.from_uci(uci)
            except ValueError:
                link.sendtoboard(f"error_invalid_{uci}")
                display.show_invalid(uci)
                continue

            if mv not in brd.legal_moves:
                link.sendtoboard(f"error_illegal_{uci}")
                display.show_illegal(uci, self.d.side_name_from_board(brd))
                continue

            # Submit to Lichess first
            resp = self.client.make_move(game_id, uci)
            if not resp.get("ok"):
                display.send(f"Move rejected\n{resp.get('status','')} {resp.get('text', resp.get('error',''))}")
                time.sleep(2)
                continue

            # Apply locally
            brd.push(mv)
            last_n += 1

            if brd.is_game_over():
                self.d.report_game_over(link, display, brd)
                raise self.d.GoToModeSelect()
