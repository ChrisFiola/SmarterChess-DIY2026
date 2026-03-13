# -*- coding: utf-8 -*-
"""
Online (Lichess) controller — full submenu structure.

Menu tree:
  Online Main
  ├── New Game
  │   ├── Challenge Friend  (select from following list, then time control)
  │   ├── Quick Pairing     (10+0 / 10+5 / 15+10 / 30+0 / 30+20)
  │   └── Correspondence    (open challenge, casual, 3-day clock)
  ├── Ongoing Games         (resume any active game; board setup if needed)
  └── Challenge Received    (accept a pending incoming challenge)

During active play:
  - OK + Hint  →  "Leave game?" paged menu (Resign / Exit to menu)
  - Hold Hint  →  offer draw
  - Serial checked every keepalive (~1 s) even during opponent's turn
"""

from __future__ import annotations

from typing import Optional
import threading
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
    _paged_menu,
    run_in_bg,
    wait_for_ok,
    handle_typing_message,
    handle_capq_message,
    validate_and_push_move,
    notify_game_over,
    handle_illegal_move,
    resolve_uci_promotion,
    send_check_signal,
    send_turn_notification,
    shutdown_raspberry_pi,
    guide_board_setup,
)

# ── Time-control definitions ──────────────────────────────────────────────────

# Quick pairing options (label, time_minutes, increment_seconds)
_QUICK_PAIRING_OPTIONS = [
    ("10+0 Rapid",      10, 0),
    ("10+5 Rapid",      10, 5),
    ("15+10 Rapid",     15, 10),
    ("30+0 Classical",  30, 0),
    ("30+20 Classical", 30, 20),
]

# Time controls offered when challenging a friend
# (label, limit_seconds, increment_seconds)
_CHALLENGE_TIME_OPTIONS = [
    ("3+0 Blitz",       180,  0),
    ("5+0 Blitz",       300,  0),
    ("5+3 Blitz",       300,  3),
    ("10+0 Rapid",      600,  0),
    ("10+5 Rapid",      600,  5),
    ("15+10 Rapid",     900, 10),
    ("30+0 Classical", 1800,  0),
]

# Starting FEN piece-placement string — used to detect untouched boards
_STARTING_FEN_PIECES = chess.STARTING_FEN.split(" ")[0]


class OnlineController:
    """Manages one complete Lichess online game session.

    Covers the full lifecycle: WiFi check, account auth, submenu navigation,
    game creation / ongoing-game selection, optional board setup, and active
    play (move entry, typing preview, draw offers, resignation).
    """

    def __init__(self, link: BoardLink, display: Display, cfg: GameConfig):
        self.link = link
        self.display = display
        self.cfg = cfg
        self.client = LichessClient()

    # ── Per-game actions ─────────────────────────────────────────────────────

    def _resign_and_exit(self, game_id: str) -> None:
        """Resign the active game on Lichess and return to the main menu."""
        self.display.send("Resigning...")
        try:
            self.client.resign_game(game_id)
        except Exception:
            pass
        raise ReturnToMenu()

    def _offer_draw(self, game_id: str) -> None:
        """Send a draw offer to the opponent on Lichess."""
        self.display.send("Offering draw...")
        try:
            self.client.offer_draw(game_id)
        except Exception:
            pass

    def _cancel_to_menu(self) -> None:
        """Show 'Cancelling...', disable back button, raise ReturnToMenu."""
        self.display.send("Cancelling...")
        self.link.send_to_board("ok_back_disable")
        time.sleep(1.0)
        raise ReturnToMenu()

    def _confirm_resign_or_exit(self, game_id: str) -> None:
        """Show 'Leave game?' menu after OK+Hint is pressed during active play.

        Uses the paged-menu mechanism (wake Pico via ChooseMode then MenuPaged)
        so the user can choose between resigning and exiting without resigning.

        Raises ReturnToMenu if the user exits or resigns.
        Returns normally (without raising) if the user presses Back to continue.
        In that case the Pico is re-armed via SetupComplete.
        """
        choice = _paged_menu(
            self.link,
            self.display,
            ["Resign", "Exit to menu"],
            wake_command="ChooseMode",
            resend_timeout=3.0,
        )
        if choice == "Resign":
            self._resign_and_exit(game_id)  # raises ReturnToMenu
        if choice == "Exit to menu":
            raise ReturnToMenu()
        # choice is None — user pressed Back to stay in game
        self.link.send_to_board("SetupComplete")

    # ── Common Pico message handling ──────────────────────────────────────────

    def _handle_common(self, msg: str, board: chess.Board) -> bool:
        """Handle messages processed identically in every state.

        Returns True if the message was consumed.
        """
        if msg == "shutdown":
            shutdown_raspberry_pi(self.link, self.display)
            return True

        if msg.startswith("typing_"):
            handle_typing_message(
                self.link,
                self.display,
                msg[len("typing_"):],
                board,
                log_prefix="[ONLINE ACK]",
            )
            return True

        if handle_capq_message(self.link, board, msg):
            return True

        if msg in HINT_MSGS:
            self.display.send("Online mode\nHints disabled")
            return True

        return False

    # ── Connection & account fetch ────────────────────────────────────────────

    def _connect_and_get_account(self) -> Optional[str]:
        """Check WiFi, fetch Lichess account, return username or raise.

        Returns the lowercase username string on success.
        Raises ReturnToMenu on error or user cancel.
        """
        link, display = self.link, self.display

        link.send_to_board("SetupComplete")
        link.send_to_board("GameStart")
        link.send_to_board("ok_cancel_enable")
        display.send("Lichess\nConnecting...\nOK = cancel")

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
                    return None
                if m in OK_MSGS | NEW_GAME_MSGS:
                    self._cancel_to_menu()

        # Retry account fetch up to 3 times in background (serial stays live)
        acct = None
        for _ in range(3):
            acct = run_in_bg(
                self.client.get_account, link, display,
                on_cancel=self._cancel_to_menu,
            )
            if acct and not acct.get("_error"):
                break
            run_in_bg(
                lambda: time.sleep(1.0), link, display,
                on_cancel=self._cancel_to_menu,
            )

        if not acct or acct.get("_error"):
            display.send("Lichess offline\nWiFi/DNS error\nOK = menu")
            while True:
                m = link.read_from_board()
                if not m:
                    continue
                if m == "shutdown":
                    shutdown_raspberry_pi(link, display)
                    return None
                if m in OK_MSGS | NEW_GAME_MSGS:
                    link.send_to_board("ok_back_disable")
                    raise ReturnToMenu()

        return (acct.get("username") or acct.get("id") or "").strip().lower()

    # ── Waiting for gameStart ─────────────────────────────────────────────────

    def _wait_for_game_start(self) -> Optional[str]:
        """Poll the Lichess event stream until a gameStart event arrives.

        Returns the game ID string, or None if cancelled / error.
        Raises ReturnToMenu on user cancel.
        """
        link, display = self.link, self.display
        game_id_box = [None]
        stream_done = threading.Event()

        def _stream_watcher():
            while not stream_done.is_set():
                try:
                    for ev in self.client.stream_events(timeout_s=5):
                        if stream_done.is_set():
                            return
                        if ev.get("type") == "gameStart":
                            game = ev.get("game") or {}
                            game_id_box[0] = game.get("id") or game.get("gameId")
                            stream_done.set()
                            return
                except Exception:
                    if not stream_done.is_set():
                        time.sleep(0.5)

        threading.Thread(target=_stream_watcher, daemon=True).start()

        last_banner_ms = 0
        while not stream_done.wait(timeout=0.05):
            msg = link.try_read_from_board()
            if msg == "shutdown":
                stream_done.set()
                shutdown_raspberry_pi(link, display)
                return None
            if msg and msg in OK_MSGS | NEW_GAME_MSGS:
                stream_done.set()
                self._cancel_to_menu()
            now = int(time.time() * 1000)
            if now - last_banner_ms > 1500:
                display.send("Waiting for\ngame to start...\nOK = cancel")
                last_banner_ms = now

        return game_id_box[0]

    # ── New game flows ────────────────────────────────────────────────────────

    def _run_quick_pairing(self) -> Optional[str]:
        """Show time-control selector, create seek, return game ID or None."""
        link, display = self.link, self.display

        labels = [opt[0] for opt in _QUICK_PAIRING_OPTIONS]
        choice = _paged_menu(link, display, labels)
        if choice is None:
            return None

        selected = next((o for o in _QUICK_PAIRING_OPTIONS if o[0] == choice), None)
        if not selected:
            return None
        _, time_min, inc_sec = selected

        display.send(f"Seeking {choice}\nOK = cancel")
        link.send_to_board("ok_cancel_enable")

        # Start seek in background; game appears via event stream
        seek_done = threading.Event()

        def _do_seek():
            self.client.create_seek(time_min, inc_sec)
            seek_done.set()

        threading.Thread(target=_do_seek, daemon=True).start()

        game_id = self._wait_for_game_start()
        seek_done.set()  # signal seek thread to stop if still running
        return game_id

    def _run_challenge_friend(self) -> Optional[str]:
        """Fetch friends list, let user pick one, select time control, challenge."""
        link, display = self.link, self.display

        display.send("Loading friends...")
        friends = run_in_bg(
            self.client.get_following, link, display,
            on_cancel=self._cancel_to_menu,
        ) or []

        if not friends:
            display.send("No friends found\nOK = back")
            wait_for_ok(link, display)
            return None

        names = [
            (f.get("username") or f.get("id") or "")[:18]
            for f in friends
            if f.get("username") or f.get("id")
        ]
        if not names:
            display.send("No friends found\nOK = back")
            wait_for_ok(link, display)
            return None

        chosen_name = _paged_menu(link, display, names)
        if not chosen_name:
            return None

        # Time control selection
        tc_labels = [o[0] for o in _CHALLENGE_TIME_OPTIONS]
        chosen_tc = _paged_menu(link, display, tc_labels)
        if not chosen_tc:
            return None

        tc = next((o for o in _CHALLENGE_TIME_OPTIONS if o[0] == chosen_tc), None)
        if not tc:
            return None
        _, limit_sec, inc_sec = tc

        display.send(f"Challenging\n{chosen_name}...\nOK = cancel")
        link.send_to_board("ok_cancel_enable")

        resp = run_in_bg(
            lambda: self.client.challenge_user(chosen_name, limit_sec, inc_sec),
            link, display,
            on_cancel=self._cancel_to_menu,
        )
        if not resp or resp.get("_error"):
            err = (resp or {}).get("_error") or "Challenge failed"
            display.send(f"Challenge error\n{err[:18]}\nOK = back")
            wait_for_ok(link, display)
            return None

        return self._wait_for_game_start()

    def _run_correspondence(self) -> Optional[str]:
        """Create an open correspondence challenge and wait for an opponent."""
        link, display = self.link, self.display

        display.send("Creating\ncorrespondence...\nOK = cancel")
        link.send_to_board("ok_cancel_enable")

        resp = run_in_bg(
            lambda: self.client.create_open_challenge(days=3),
            link, display,
            on_cancel=self._cancel_to_menu,
        )
        if not resp or resp.get("_error"):
            err = (resp or {}).get("_error") or "Creation failed"
            display.send(f"Challenge error\n{err[:18]}\nOK = back")
            wait_for_ok(link, display)
            return None

        # Show challenge URL via QR if possible
        url = ((resp.get("challenge") or {}).get("url") or "").strip()
        if url:
            if hasattr(display, "show_qr"):
                display.show_qr(url, "Share link:", "OK = cancel")
            else:
                display.send(f"Challenge ready\n{url[:18]}\nOK = cancel")

        return self._wait_for_game_start()

    def _run_challenge_received(self) -> Optional[str]:
        """Fetch pending incoming challenges, let user accept one, return game ID."""
        link, display = self.link, self.display

        display.send("Loading\nchallenges...")
        challenges = run_in_bg(
            self.client.get_incoming_challenges, link, display,
            on_cancel=self._cancel_to_menu,
        ) or []

        # Filter out error sentinel
        challenges = [c for c in challenges if isinstance(c, dict) and not c.get("_error")]

        if not challenges:
            display.send("No challenges\nOK = back")
            wait_for_ok(link, display)
            return None

        # Build labels: "Challenger (time control)"
        def _tc_label(c: dict) -> str:
            tc = c.get("timeControl") or {}
            kind = tc.get("type", "")
            if kind == "clock":
                mins = tc.get("limit", 0) // 60
                inc = tc.get("increment", 0)
                return f"{mins}+{inc}"
            if kind == "correspondence":
                days = tc.get("daysPerTurn", "?")
                return f"{days}d"
            return "?"

        labels = []
        challenge_ids = []
        for c in challenges[:10]:
            challenger = (
                (c.get("challenger") or {}).get("name")
                or (c.get("challenger") or {}).get("id")
                or "Unknown"
            )
            tc = _tc_label(c)
            labels.append(f"{challenger[:12]} {tc}")
            challenge_ids.append(c.get("id") or "")

        choice = _paged_menu(link, display, labels)
        if choice is None:
            return None

        try:
            idx = labels.index(choice)
        except ValueError:
            return None

        challenge_id = challenge_ids[idx]
        challenger_name = labels[idx]

        display.send(f"Accepting\n{challenger_name}...\nOK = cancel")
        link.send_to_board("ok_cancel_enable")

        resp = run_in_bg(
            lambda: self.client.accept_challenge(challenge_id),
            link, display,
            on_cancel=self._cancel_to_menu,
        )
        if not resp or not resp.get("ok"):
            err = (resp or {}).get("_error") or "Accept failed"
            display.send(f"Challenge error\n{err[:18]}\nOK = back")
            wait_for_ok(link, display)
            return None

        return self._wait_for_game_start()

    def _run_new_game(self) -> Optional[str]:
        """New Game submenu → returns game ID or None."""
        link, display = self.link, self.display
        choice = _paged_menu(
            link, display,
            ["Challenge Friend", "Quick Pairing", "Correspondence"],
        )
        if choice is None:
            return None
        if choice == "Challenge Friend":
            return self._run_challenge_friend()
        if choice == "Quick Pairing":
            return self._run_quick_pairing()
        if choice == "Correspondence":
            return self._run_correspondence()
        return None

    # ── Ongoing games flow ────────────────────────────────────────────────────

    def _run_ongoing_games(self) -> Optional[str]:
        """Fetch ongoing games, let user select one, return game ID or None."""
        link, display = self.link, self.display

        display.send("Loading games...")
        data = run_in_bg(
            self.client.get_ongoing_games, link, display,
            on_cancel=self._cancel_to_menu,
        )
        if not data or data.get("_error"):
            display.send("No ongoing games\nOK = back")
            wait_for_ok(link, display)
            return None

        game_list = data.get("nowPlaying") or []
        if not game_list:
            display.send("No active games\nOK = back")
            wait_for_ok(link, display)
            return None

        # Build short labels: "W vs Opponent" or "B vs Opponent"
        labels = []
        for g in game_list[:10]:
            color = g.get("color", "?")[0].upper()
            opp = (g.get("opponent") or {}).get("username") or "Unknown"
            labels.append(f"{color} vs {opp[:15]}")

        choice = _paged_menu(link, display, labels, wake_command="ChooseMode")
        if choice is None:
            return None

        try:
            idx = labels.index(choice)
        except ValueError:
            return None

        return (game_list[idx].get("gameId") or game_list[idx].get("id") or "").strip() or None

    def _setup_ongoing_board(self, game_id: str) -> Optional[chess.Board]:
        """Open the game stream, replay moves, and guide board setup if needed.

        Returns the pre-loaded Board (with all moves replayed) on success,
        or None if the user backed out or an error occurred.
        The returned board is used to skip replaying moves in _play_game.
        """
        link, display = self.link, self.display

        display.send("Loading game...")
        try:
            stream = self.client.stream_game(game_id)
            first = next(stream)
        except Exception:
            display.send("Stream error\nOK = back")
            wait_for_ok(link, display)
            return None

        # Replay all past moves to get current position
        moves = extract_moves(first)
        board = chess.Board()
        for uci in moves:
            try:
                board.push(chess.Move.from_uci(uci))
            except Exception:
                break

        # If board is still at starting position, skip setup
        current_pieces = board.fen().split(" ")[0]
        if not moves or current_pieces == _STARTING_FEN_PIECES:
            display.send("Board at start\nNo setup needed\nOK = continue")
            if not wait_for_ok(link, display):
                return None
            return board

        # Guide user through physical board setup
        ok = guide_board_setup(link, display, board.fen(), label="Current position")
        if not ok:
            return None
        return board

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self) -> None:
        """Connect to Lichess, show menus, launch game."""
        link, display = self.link, self.display

        username = self._connect_and_get_account()
        if not username:
            raise ReturnToMenu()

        # Disable cancel button before showing main menu
        link.send_to_board("ok_back_disable")

        while True:
            choice = _paged_menu(
                link, display,
                ["New Game", "Ongoing Games", "Challenge Received"],
            )
            if choice is None:
                raise ReturnToMenu()

            pre_loaded_board: Optional[chess.Board] = None
            game_id: Optional[str] = None

            if choice == "New Game":
                game_id = self._run_new_game()
            elif choice == "Ongoing Games":
                game_id = self._run_ongoing_games()
                if game_id:
                    pre_loaded_board = self._setup_ongoing_board(game_id)
                    if pre_loaded_board is None:
                        # User backed out of board setup
                        continue
            elif choice == "Challenge Received":
                game_id = self._run_challenge_received()

            if not game_id:
                continue

            display.send("Lichess\nLoading game...")
            link.send_to_board("ok_back_disable")
            link.send_to_board("GameStart")
            self._play_game(game_id, username, pre_loaded_board=pre_loaded_board)
            # _play_game raises ReturnToMenu when the game ends

    # ── Active game loop ──────────────────────────────────────────────────────

    def _play_game(
        self,
        game_id: str,
        username: str,
        *,
        pre_loaded_board: Optional[chess.Board] = None,
    ) -> None:
        """Run the active game loop for a Lichess board-API game.

        pre_loaded_board: when resuming an ongoing game, the board state is
        already advanced to the current position. We still open a fresh stream
        so we receive new moves, but skip LED animations for past moves.
        """
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
                if announce_new:
                    send_check_signal(link, board)
                    if board.is_check():
                        time.sleep(1.6)
                    link.send_to_board(format_engine_move(uci, is_cap))
                    time.sleep(0.3)
                    send_turn_if_human()
                    side_to_move = "WHITE" if board.turn == chess.WHITE else "BLACK"
                    promo_line = ""
                    if mv.promotion:
                        pl = chess.piece_symbol(mv.promotion)
                        promo_line = display.format_promo_line(pl)
                    display.show_arrow(
                        uci,
                        suffix=(
                            f"{promo_line}\n{side_to_move} to move"
                            if promo_line
                            else f"{side_to_move} to move"
                        ),
                    )
                    awaiting_ok_ack = True
                    in_move_entry = False

        # Read the initial game state from the stream
        try:
            first = next(stream)
        except Exception:
            display.send("Lichess error\nGame stream\nOK = menu")
            wait_for_ok(link, display)
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

        # For ongoing game resumes: board state already set up physically.
        # Replay past moves silently (no LED) to sync the Python board object.
        if pre_loaded_board is not None:
            display.send(f"You are {'WHITE' if you_are_white else 'BLACK'}")
            # Use pre_loaded_board as the starting board state and skip old moves
            board_moves = extract_moves(first)
            for uci in board_moves:
                try:
                    board.push(chess.Move.from_uci(uci))
                    last_move_count += 1
                except Exception:
                    break
        else:
            display.send(f"Connected\nYou are {'WHITE' if you_are_white else 'BLACK'}")
            apply_new_moves(extract_moves(first), announce_new=False)

        send_turn_if_human()

        prompted_for_this_turn = False
        last_wait_banner_ms = 0

        while True:
            # ── Non-blocking peek for resign/draw/common events ───────────────
            peek = link.try_read_from_board()
            if peek:
                if self._handle_common(peek, board):
                    if peek.startswith("typing_"):
                        awaiting_ok_ack = False
                        in_move_entry = True
                elif peek in NEW_GAME_MSGS:
                    self._confirm_resign_or_exit(game_id)
                    # Returned → user pressed Back; game continues
                elif peek in ("draw", "btn_draw"):
                    self._offer_draw(game_id)

            if board.is_game_over():
                notify_game_over(link, display, board)
                raise ReturnToMenu()

            # ── Opponent's turn — poll stream in background ───────────────────
            if board.turn != your_color:
                now = int(time.time() * 1000)
                if now - last_wait_banner_ms > 1500:
                    display.send("Waiting for\nopponent...")
                    last_wait_banner_ms = now

                # Read ONE stream event in a background thread so the main
                # thread can poll serial every 50 ms for resign/draw/etc.
                payload_box: list = [None]
                error_box: list = [None]
                ready = threading.Event()

                def _fetch_one():
                    try:
                        payload_box[0] = next(stream)
                    except StopIteration:
                        error_box[0] = "stop"
                    except Exception as exc:
                        error_box[0] = str(exc) or "error"
                    finally:
                        ready.set()

                threading.Thread(target=_fetch_one, daemon=True).start()

                while not ready.wait(timeout=0.05):
                    smsg = link.try_read_from_board()
                    if smsg:
                        if smsg == "shutdown":
                            shutdown_raspberry_pi(link, display)
                            raise ReturnToMenu()
                        if smsg in NEW_GAME_MSGS:
                            self._confirm_resign_or_exit(game_id)
                            last_wait_banner_ms = 0  # re-show banner immediately
                        elif smsg in ("draw", "btn_draw"):
                            self._offer_draw(game_id)
                        self._handle_common(smsg, board)

                if error_box[0] == "stop":
                    display.send("Lichess ended\nOK = menu")
                    wait_for_ok(link, display)
                    raise ReturnToMenu()
                if error_box[0]:
                    display.send("Lichess error\nStream lost\nOK = menu")
                    wait_for_ok(link, display)
                    raise ReturnToMenu()

                payload = payload_box[0]
                move_list = extract_moves(payload)
                if len(move_list) > last_move_count:
                    apply_new_moves(move_list, announce_new=True)
                    prompted_for_this_turn = False
                    continue

                status = extract_status(payload)
                if status and status != "started":
                    winner = extract_winner(payload)
                    result = "1/2-1/2"
                    if winner == "white":
                        result = "1-0"
                    elif winner == "black":
                        result = "0-1"
                    link.send_to_board(f"GameOver:{result}")
                    display.send(f"GAME OVER\n{result}\nOK = menu")
                    raise ReturnToMenu()

                continue  # keepalive or unrecognised event — loop back

            # ── Your turn ─────────────────────────────────────────────────────
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
                self._confirm_resign_or_exit(game_id)
                # Returned → user pressed Back; re-prompt and continue
                prompted_for_this_turn = False
                continue

            if msg in ("draw", "btn_draw"):
                self._offer_draw(game_id)
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
                handle_illegal_move(
                    link=link, display=display, board=board, uci=uci, label="ILLEGAL"
                )
                continue

            # Submit to Lichess before pushing locally
            resp = self.client.make_move(game_id, uci)
            if not resp.get("ok"):
                display.send("Move rejected\nOK = retry")
                if not wait_for_ok(link, display):
                    self._resign_and_exit(game_id)
                continue

            board.push(move)
            last_move_count += 1
            send_check_signal(link, board)
            send_turn_if_human()
            prompted_for_this_turn = False
            in_move_entry = False
            awaiting_ok_ack = False
