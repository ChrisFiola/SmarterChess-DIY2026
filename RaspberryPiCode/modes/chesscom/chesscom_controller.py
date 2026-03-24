# -*- coding: utf-8 -*-
"""
Chess.com controller for SmartChess Connected Board.

Menu tree:
  Chess.com
  ├── My Turn        (games where it's your move)
  ├── All Games      (browse all ongoing daily games)
  └── Change User    (enter a different Chess.com username)

Game flow (Connected Board API configured):
  1. Select a correspondence game
  2. Board is set up to the current position (guided LED placement)
  3. Your turn → enter move on physical board → submitted directly to Chess.com
  4. Opponent's turn → review position, OK = back

Game flow (API not yet configured — fallback):
  Same as above, but step 3 shows the move on the LCD for manual submission.

Credentials:
  CHESSCOM_USERNAME — player username (required)
  CHESSCOM_TOKEN    — Connected Board API token (enables direct move submission)
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

import chess

from core.boardlink import BoardLink
from screen.display import Display
from modes.chesscom.chesscom_client import (
    ConnectedBoardAPI,
    extract_game_id,
    extract_opponent,
    extract_player_color,
    is_my_turn,
    parse_pgn_moves,
)
from core.protocol import (
    NEW_GAME_MSGS,
    OK_MSGS,
    HINT_MSGS,
)
from core.game_flow import (
    GameConfig,
    ReturnToMenu,
    _paged_menu,
    confirm_board_ready_or_setup,
    run_in_bg,
    wait_for_ok,
    handle_typing_message,
    handle_capq_message,
    resolve_uci_promotion,
    send_check_signal,
    send_turn_notification,
    shutdown_raspberry_pi,
)

_USERNAME_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".chesscom_username"
)

def _load_username() -> Optional[str]:
    """Load Chess.com username from env var or file."""
    env = (os.environ.get("CHESSCOM_USERNAME") or "").strip()
    if env:
        return env.lower()
    try:
        with open(_USERNAME_FILE, "r") as f:
            return f.read().strip().lower() or None
    except Exception:
        return None


def _save_username(username: str) -> None:
    try:
        with open(_USERNAME_FILE, "w") as f:
            f.write(username.strip().lower())
    except Exception:
        pass


class ChessComController:
    """Manages Chess.com game sessions via the Connected Board API."""

    def __init__(self, link: BoardLink, display: Display, cfg: GameConfig):
        self.link = link
        self.display = display
        self.cfg = cfg
        self.api: Optional[ConnectedBoardAPI] = None
        self.username: Optional[str] = None

    # ── Username ──────────────────────────────────────────────────────────────

    def _get_username(self) -> Optional[str]:
        link, display = self.link, self.display

        saved = _load_username()
        if saved:
            display.send(f"Chess.com user:\n{saved}\nOK=continue 1=change")
            link.send_to_board("WaitForOkOrSkipSetup")
            while True:
                m = link.read_from_board()
                if m is None:
                    continue
                if m == "shutdown":
                    shutdown_raspberry_pi(link, display)
                    return None
                if m in OK_MSGS:
                    return saved
                if m in NEW_GAME_MSGS:
                    return None
                if (m or "").strip() == "1":
                    break
                if m in HINT_MSGS or m.startswith("typing_") or m.startswith("capq_"):
                    continue

        display.send(
            "Set username via\nSSH then restart:\nexport CHESSCOM_\nUSERNAME=yourname"
        )
        time.sleep(3)
        display.send("Or type on board:\na-h rows 1-5\nOK=done Hint=del")
        return self._read_username_from_board()

    def _read_username_from_board(self) -> Optional[str]:
        """Read a username character by character using board squares."""
        link, display = self.link, self.display

        chars = "abcdefghijklmnopqrstuvwxyz0123456789_-"
        square_map: Dict[str, str] = {}
        for idx, ch in enumerate(chars):
            sq = chr(ord("a") + idx % 8) + str(idx // 8 + 1)
            square_map[sq] = ch

        name: List[str] = []
        link.send_to_board("SetupComplete")
        link.send_to_board("GameStart")

        def _show():
            current = "".join(name) or "_"
            display.send(f"Username:\n{current}\nOK=done Hint=del")

        _show()

        while True:
            m = link.read_from_board()
            if m is None:
                continue
            if m == "shutdown":
                shutdown_raspberry_pi(link, display)
                return None
            if m in NEW_GAME_MSGS:
                return None
            if m in OK_MSGS:
                result = "".join(name).strip()
                if result:
                    return result
                display.send("Name empty!\nTry again")
                time.sleep(1)
                _show()
                continue
            if m in HINT_MSGS:
                if name:
                    name.pop()
                _show()
                continue
            if m.startswith("typing_"):
                parts = m.split("_")
                if len(parts) >= 3 and parts[1] == "confirm":
                    sq = parts[2][:2].lower()
                    if sq in square_map:
                        name.append(square_map[sq])
                        _show()
                handle_typing_message(link, display, m)
                continue
            if m.startswith("capq_"):
                handle_capq_message(link, chess.Board(), m)
                continue

    # ── Game selection ────────────────────────────────────────────────────────

    def _fetch_games(self) -> List[Dict]:
        link, display = self.link, self.display
        display.send("Fetching games...")
        games = run_in_bg(
            self.api.get_ongoing_games,
            link, display,
            on_cancel=self._cancel_to_menu,
        )
        return games or []

    def _show_game_list(self, games: List[Dict]) -> Optional[Dict]:
        link, display = self.link, self.display

        if not games:
            display.send("No games found\nOK = back")
            wait_for_ok(link, display)
            return None

        labels = []
        for g in games[:12]:
            opp = extract_opponent(g, self.username)
            color = extract_player_color(g, self.username)
            turn_marker = "*" if is_my_turn(g, self.username) else ""
            side = color[0].upper()
            labels.append(f"{side} {opp[:14]}{turn_marker}")

        choice = _paged_menu(link, display, labels, wake_command="ChooseMode")
        if choice is None:
            return None
        try:
            idx = labels.index(choice)
        except ValueError:
            return None
        return games[idx]

    # ── Board setup ───────────────────────────────────────────────────────────

    def _setup_board_for_game(self, game: Dict) -> Optional[chess.Board]:
        link, display = self.link, self.display

        display.send("Loading position...")
        pgn = game.get("pgn") or ""
        uci_moves = parse_pgn_moves(pgn)

        board = chess.Board()
        for uci in uci_moves:
            try:
                board.push(chess.Move.from_uci(uci))
            except Exception:
                break

        link.send_to_board("SetupComplete")

        opp = extract_opponent(game, self.username)
        if not confirm_board_ready_or_setup(
            link,
            display,
            board,
            label=f"vs {opp}",
            start_message="Starting position\nNo setup needed\nOK = continue",
        ):
            return None
        return board

    # ── Active game play ──────────────────────────────────────────────────────

    def _play_position(self, game: Dict, board: chess.Board) -> None:
        link, display = self.link, self.display

        my_color = extract_player_color(game, self.username)
        my_turn = is_my_turn(game, self.username)
        opp = extract_opponent(game, self.username)
        game_id = extract_game_id(game)

        link.send_to_board("GameStart")
        send_turn_notification(link, board)
        send_check_signal(link, board)

        if my_turn:
            display.send(f"vs {opp}\nYour turn ({my_color})\nEnter your move")
            link.send_to_board("hint_enable")
            self._enter_move_loop(board, game_id)
        else:
            display.send(f"vs {opp}\nOpponent's turn\nOK = back")
            link.send_to_board("hint_disable")
            self._wait_for_exit(board)

    def _enter_move_loop(
        self, board: chess.Board, game_id: Optional[str]
    ) -> None:
        """Accept moves on the board, confirm, and submit to Chess.com."""
        link, display = self.link, self.display
        move_prompted = False
        can_submit = self.api and self.api.is_configured and game_id

        while True:
            if not move_prompted:
                display.prompt_move(
                    "WHITE" if board.turn == chess.WHITE else "BLACK"
                )
                link.send_to_board("wait_exit_enable")
                move_prompted = True

            m = link.read_from_board()
            action = self._handle_active_game_message(board, m, ignore_hint=True)
            if action is not None:
                if action:
                    continue
                return

            uci = m.strip()
            if not uci or len(uci) < 4:
                continue

            try:
                uci = resolve_uci_promotion(link, display, board, uci) or uci
            except ReturnToMenu:
                raise

            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                link.send_to_board(f"error_invalid_{uci}")
                display.show_invalid(uci)
                move_prompted = False
                continue

            if move not in board.legal_moves:
                link.send_to_board(f"error_invalid_{uci}")
                display.show_invalid(uci)
                move_prompted = False
                continue

            # Valid move — push and show
            board.push(move)
            send_check_signal(link, board)
            display.show_arrow(uci, suffix="")
            time.sleep(0.5)

            # Confirm before submitting
            choice = _paged_menu(
                link, display,
                [f"Play {uci}", "Try another", "Exit"],
                wake_command="ChooseMode",
            )

            if choice is not None and choice.startswith("Play"):
                if can_submit:
                    self._submit_move(game_id, uci)
                else:
                    display.send(f"Move: {uci}\nAPI not configured\nSubmit on chess.com")
                    wait_for_ok(link, display)
                link.send_to_board("GameEnd")
                return

            if choice == "Exit" or choice is None:
                board.pop()
                link.send_to_board("GameEnd")
                return

            # "Try another" — undo and re-enter
            board.pop()
            send_turn_notification(link, board)
            send_check_signal(link, board)
            move_prompted = False

    def _submit_move(self, game_id: str, uci: str) -> None:
        """Submit the move to Chess.com via the Connected Board API."""
        link, display = self.link, self.display

        display.send(f"Sending {uci}...")
        result = run_in_bg(
            lambda: self.api.make_move(game_id, uci),
            link, display,
            on_cancel=self._cancel_to_menu,
        )

        if result and result.get("ok"):
            display.send(f"Move sent!\n{uci}")
            time.sleep(1.5)
        else:
            err = (result or {}).get("text") or (result or {}).get("error") or "Unknown"
            print(f"[CHESSCOM] Move failed: {result}", flush=True)
            display.send(f"Move failed\n{err[:18]}\nOK = back")
            wait_for_ok(link, display)

    def _handle_active_game_message(
        self,
        board: chess.Board,
        msg: Optional[str],
        *,
        ignore_hint: bool = False,
    ) -> Optional[bool]:
        """Handle common serial messages during an active Chess.com position.

        Returns:
          - True when the message was handled and the caller should keep looping
          - False when the caller should exit the current position
          - None when the caller should continue with move-specific processing
        """
        link, display = self.link, self.display
        if msg is None:
            return True
        if msg == "shutdown":
            shutdown_raspberry_pi(link, display)
            return False
        if msg in NEW_GAME_MSGS or msg in OK_MSGS:
            link.send_to_board("GameEnd")
            return False
        if msg.startswith("typing_"):
            handle_typing_message(link, display, msg, board)
            return True
        if msg.startswith("capq_"):
            handle_capq_message(link, board, msg)
            return True
        if ignore_hint and msg in HINT_MSGS:
            return True
        return None

    def _wait_for_exit(self, board: chess.Board) -> None:
        while True:
            action = self._handle_active_game_message(board, self.link.read_from_board())
            if action is None:
                continue
            if not action:
                return

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _cancel_to_menu(self) -> None:
        raise ReturnToMenu()

    def _verify_current_account(self, *, include_error_detail: bool) -> bool:
        """Validate the active Chess.com account and show a consistent error flow."""
        link, display = self.link, self.display

        display.send(f"Checking\n{self.username}...")
        profile = run_in_bg(
            self.api.get_account,
            link,
            display,
            on_cancel=self._cancel_to_menu,
        )
        if profile and not profile.get("_error"):
            return True

        err = str((profile or {}).get("_error") or "Unknown error")
        if include_error_detail:
            display.send(f"User not found\n{err[:18]}\nOK = back")
        else:
            display.send("User not found\nOK = back")
        wait_for_ok(link, display)
        return False

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self) -> None:
        link, display = self.link, self.display

        try:
            username = self._get_username()
            if not username:
                raise ReturnToMenu()

            self.username = username.lower()
            self.api = ConnectedBoardAPI(self.username)

            # Show API status
            if self.api.is_configured:
                display.send(f"Chess.com\n{self.username}\nConnected Board")
            else:
                display.send(f"Chess.com\n{self.username}\nRead-only mode")
            time.sleep(1)

            if not self._verify_current_account(include_error_detail=True):
                raise ReturnToMenu()

            _save_username(self.username)

            link.send_to_board("ok_back_disable")

            while True:
                menu_opts = ["My Turn", "All Games", "Change User"]
                if self.api.is_configured:
                    menu_opts = ["My Turn", "All Games", "Resign", "Change User"]

                choice = _paged_menu(
                    link, display, menu_opts,
                    wake_command="ChooseMode",
                    resend_timeout=3.0,
                )
                if choice is None:
                    raise ReturnToMenu()

                if choice == "Change User":
                    new_name = self._read_username_from_board()
                    if new_name:
                        self.username = new_name.lower()
                        self.api = ConnectedBoardAPI(self.username)
                        if not self._verify_current_account(
                            include_error_detail=False
                        ):
                            old = _load_username()
                            if old:
                                self.username = old
                                self.api = ConnectedBoardAPI(old)
                        else:
                            _save_username(self.username)
                            display.send(f"Switched to\n{self.username}")
                            time.sleep(1)
                    continue

                games = self._fetch_games()

                if choice == "My Turn":
                    games = [g for g in games if is_my_turn(g, self.username)]

                selected = self._show_game_list(games)
                if selected is None:
                    continue

                board = self._setup_board_for_game(selected)
                if board is None:
                    continue

                link.send_to_board("SetupComplete")
                link.send_to_board("GameStart")
                link.send_to_board("hint_disable")
                try:
                    self._play_position(selected, board)
                finally:
                    display.clear_online_clock()
                    link.send_to_board("wait_exit_disable")
                    link.send_to_board("hint_enable")

        except ReturnToMenu:
            raise
