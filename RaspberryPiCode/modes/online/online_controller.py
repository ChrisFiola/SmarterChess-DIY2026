# -*- coding: utf-8 -*-
"""
Online (Lichess) controller — full submenu structure.

Menu tree:
  Online Main
  ├── New Game
  │   ├── Challenge Friend  (select from following list, then time control)
  │   ├── Quick Pairing     (10+0 / 10+5 / 15+10 / 30+0 / 30+20)
  │   └── Correspondence    (challenge a friend, casual, 3-day clock)
  ├── Ongoing Games         (resume any active game; board setup if needed)
  └── Challenge Received    (accept a pending incoming challenge)

During active play:
  - OK + Hint  →  "Leave game?" paged menu (Resign / Exit to menu)
  - Hold Hint  →  offer draw
  - Serial checked every keepalive (~1 s) even during opponent's turn
"""

from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, Optional, Set
import threading
import time
import chess

from core.boardlink import BoardLink
from screen.display import Display
from modes.online.lichess_client import LichessClient
from modes.online.lichess_game import (
    extract_clocks,
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
)
from core.game_flow import (
    GameConfig,
    ReturnToMenu,
    _paged_menu,
    run_in_bg,
    wait_for_ok,
    handle_typing_message,
    handle_capq_message,
    notify_game_over,
    handle_illegal_move,
    resolve_uci_promotion,
    send_check_signal,
    send_turn_notification,
    shutdown_raspberry_pi,
    guide_board_setup,
)

# ── Time-control definitions ──────────────────────────────────────────────────

# Quick pairing options.
_QUICK_PAIRING_OPTIONS = [
    {"label": "10+0 Rapid", "kind": "seek", "time": 10, "increment": 0},
    {"label": "10+5 Rapid", "kind": "seek", "time": 10, "increment": 5},
    {"label": "15+10 Rapid", "kind": "seek", "time": 15, "increment": 10},
    {"label": "30+0 Classical", "kind": "seek", "time": 30, "increment": 0},
    {"label": "30+20 Classical", "kind": "seek", "time": 30, "increment": 20},
    {"label": "3d Corr Random", "kind": "open_correspondence", "days": 3},
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
        self._event_queue: Deque[Dict[str, Any]] = deque()
        self._event_ready = threading.Condition()
        self._event_stop = threading.Event()
        self._event_thread: Optional[threading.Thread] = None
        self._game_color_hints: Dict[str, str] = {}

    def _remember_game_color(self, game_id: Optional[str], color: Optional[str]) -> None:
        game_id = (game_id or "").strip()
        color = (color or "").strip().lower()
        if game_id and color in ("white", "black"):
            self._game_color_hints[game_id] = color

    def _ensure_event_stream(self) -> None:
        """Keep a background Lichess event stream running for this session."""
        if self._event_thread and self._event_thread.is_alive():
            return

        self._event_stop.clear()

        def _watch_events() -> None:
            while not self._event_stop.is_set():
                try:
                    for ev in self.client.stream_events(timeout_s=30):
                        if self._event_stop.is_set():
                            return
                        if not ev:
                            continue
                        with self._event_ready:
                            self._event_queue.append(ev)
                            while len(self._event_queue) > 64:
                                self._event_queue.popleft()
                            self._event_ready.notify_all()
                except Exception:
                    if not self._event_stop.is_set():
                        time.sleep(0.5)

        self._event_thread = threading.Thread(target=_watch_events, daemon=True)
        self._event_thread.start()

    def _stop_event_stream(self) -> None:
        """Request shutdown of the background Lichess event watcher."""
        self._event_stop.set()

    def _consume_pending_game_start(
        self,
        *,
        exclude_game_ids: Optional[Set[str]] = None,
    ) -> Optional[str]:
        """Pop queued gameStart events and return the first unseen game ID."""
        exclude = {gid for gid in (exclude_game_ids or set()) if gid}

        with self._event_ready:
            while self._event_queue:
                ev = self._event_queue.popleft()
                if ev.get("type") != "gameStart":
                    continue
                game = ev.get("game") or {}
                game_id = (game.get("id") or game.get("gameId") or "").strip()
                if game_id and game_id not in exclude:
                    self._remember_game_color(game_id, game.get("color"))
                    return game_id

        return None

    def _current_ongoing_game_ids(self) -> Set[str]:
        """Fetch the set of currently active game IDs for this account."""
        data = run_in_bg(
            lambda: self.client.get_ongoing_games(timeout_s=3),
            self.link,
            self.display,
            on_cancel=self._cancel_to_menu,
        )
        if not data or data.get("_error"):
            return set()

        game_ids: Set[str] = set()
        for game in data.get("nowPlaying") or []:
            game_id = (game.get("gameId") or game.get("id") or "").strip()
            if game_id:
                self._remember_game_color(game_id, game.get("color"))
                game_ids.add(game_id)
        return game_ids

    def _find_new_ongoing_game(
        self,
        *,
        exclude_game_ids: Optional[Set[str]] = None,
    ) -> Optional[str]:
        """Fallback lookup when a game starts but the event was missed."""
        exclude = {gid for gid in (exclude_game_ids or set()) if gid}
        for game_id in self._current_ongoing_game_ids():
            if game_id not in exclude:
                return game_id
        return None

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

    def _wait_for_game_start(
        self,
        *,
        exclude_game_ids: Optional[Set[str]] = None,
    ) -> Optional[str]:
        """Poll the Lichess event stream until a gameStart event arrives.

        Returns the game ID string, or None if cancelled / error.
        Raises ReturnToMenu on user cancel.
        """
        link, display = self.link, self.display
        self._ensure_event_stream()
        exclude = {gid for gid in (exclude_game_ids or set()) if gid}

        last_banner_ms = 0
        next_ongoing_poll = time.monotonic()
        while True:
            game_id = self._consume_pending_game_start(exclude_game_ids=exclude)
            if game_id:
                return game_id

            msg = link.try_read_from_board()
            if msg == "shutdown":
                shutdown_raspberry_pi(link, display)
                return None
            if msg and msg in OK_MSGS | NEW_GAME_MSGS:
                self._cancel_to_menu()

            now_monotonic = time.monotonic()
            if now_monotonic >= next_ongoing_poll:
                game_id = self._find_new_ongoing_game(exclude_game_ids=exclude)
                if game_id:
                    return game_id
                next_ongoing_poll = now_monotonic + 3.0

            now = int(time.time() * 1000)
            if now - last_banner_ms > 1500:
                display.send("Waiting for\ngame to start...\nOK = cancel")
                last_banner_ms = now
            time.sleep(0.05)

    # ── New game flows ────────────────────────────────────────────────────────

    def _run_quick_pairing(self) -> Optional[str]:
        """Show time-control selector, create seek, return game ID or None."""
        link, display = self.link, self.display

        labels = [opt["label"] for opt in _QUICK_PAIRING_OPTIONS]
        choice = _paged_menu(link, display, labels)
        if choice is None:
            return None

        selected = next((o for o in _QUICK_PAIRING_OPTIONS if o["label"] == choice), None)
        if not selected:
            return None
        existing_game_ids = self._current_ongoing_game_ids()

        display.send(f"Seeking {choice}\nOK = cancel")
        link.send_to_board("ok_cancel_enable")

        seek_done = threading.Event()
        if selected["kind"] == "seek":
            # Start seek in background; game appears via event stream.
            def _do_seek():
                self.client.create_seek(selected["time"], selected["increment"])
                seek_done.set()

            threading.Thread(target=_do_seek, daemon=True).start()
        else:
            resp = run_in_bg(
                lambda: self.client.create_open_challenge(days=selected["days"]),
                link,
                display,
                on_cancel=self._cancel_to_menu,
            )
            if not resp or resp.get("_error"):
                err = (resp or {}).get("_error") or "Creation failed"
                display.send(f"Challenge error\n{err[:18]}\nOK = back")
                wait_for_ok(link, display)
                return None
            seek_done.set()

        game_id = self._wait_for_game_start(exclude_game_ids=existing_game_ids)
        seek_done.set()  # signal seek thread to stop if still running
        return game_id

    def _run_challenge_friend(self) -> Optional[str]:
        """Fetch friends list, let user pick one, select time control, challenge."""
        link, display = self.link, self.display

        chosen_name = self._pick_friend_name()
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
        existing_game_ids = self._current_ongoing_game_ids()

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

        return self._wait_for_game_start(exclude_game_ids=existing_game_ids)

    def _run_correspondence(self) -> Optional[str]:
        """Challenge a friend to a 3-day correspondence game."""
        link, display = self.link, self.display

        chosen_name = self._pick_friend_name()
        if not chosen_name:
            return None
        existing_game_ids = self._current_ongoing_game_ids()

        display.send(f"Challenging\n{chosen_name}...\nOK = cancel")
        link.send_to_board("ok_cancel_enable")

        resp = run_in_bg(
            lambda: self.client.challenge_user_correspondence(chosen_name, days=3),
            link, display,
            on_cancel=self._cancel_to_menu,
        )
        if not resp or resp.get("_error"):
            err = (resp or {}).get("_error") or "Creation failed"
            display.send(f"Challenge error\n{err[:18]}\nOK = back")
            wait_for_ok(link, display)
            return None

        return self._wait_for_game_start(exclude_game_ids=existing_game_ids)

    def _pick_friend_name(self) -> Optional[str]:
        """Load followed users and return one selected username/id."""
        link, display = self.link, self.display

        display.send("Loading friends...")
        friends = run_in_bg(
            self.client.get_following, link, display,
            on_cancel=self._cancel_to_menu,
        ) or []

        full_names = [
            (f.get("username") or f.get("id") or "")
            for f in friends
            if f.get("username") or f.get("id")
        ]
        if not full_names:
            display.send("No friends found\nOK = back")
            wait_for_ok(link, display)
            return None

        # Keep a label -> full username mapping so long usernames can still be
        # challenged correctly via API while menu labels stay LCD-friendly.
        labels = []
        label_to_full = {}
        for name in full_names:
            base = name[:18]
            label = base
            n = 2
            while label in label_to_full and label_to_full[label] != name:
                suffix = f"~{n}"
                label = f"{base[:max(0, 18 - len(suffix))]}{suffix}"
                n += 1
            labels.append(label)
            label_to_full[label] = name

        chosen_label = _paged_menu(link, display, labels)
        if not chosen_label:
            return None

        return label_to_full.get(chosen_label)

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
        existing_game_ids = self._current_ongoing_game_ids()

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

        return self._wait_for_game_start(exclude_game_ids=existing_game_ids)

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

        selected = game_list[idx]
        game_id = (selected.get("gameId") or selected.get("id") or "").strip() or None
        self._remember_game_color(game_id, selected.get("color"))
        return game_id

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
        except Exception:
            display.send("Stream error\nOK = back")
            wait_for_ok(link, display)
            return None

        first = {}
        deadline = time.time() + 20.0
        while not first:
            try:
                first = run_in_bg(
                    lambda: next(stream),
                    link,
                    display,
                    on_cancel=self._cancel_to_menu,
                ) or {}
            except StopIteration:
                display.send("Lichess ended\nOK = back")
                wait_for_ok(link, display)
                return None
            except Exception:
                display.send("Stream error\nOK = back")
                wait_for_ok(link, display)
                return None

            if not first and time.time() >= deadline:
                display.send("Lichess error\nStream stalled\nOK = back")
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

        # Transition Pico from setup-menu loop to RUNNING state so that
        # puzzle_setup_begin / setup_place_* are handled by _main_loop.
        link.send_to_board("SetupComplete")

        # If board is still at starting position, skip setup
        current_pieces = board.fen().split(" ")[0]
        if not moves or current_pieces == _STARTING_FEN_PIECES:
            display.send("Board at start\nNo setup needed\nOK = continue")
            if not wait_for_ok(link, display):
                return None
            return board

        # Guide user through physical board setup
        setup_choice = guide_board_setup(
            link, display, board.fen(), label="Current position"
        )
        if setup_choice is None:
            return None
        return board

    # ── Main entry point ──────────────────────────────────────────────────────

    def run(self) -> None:
        """Connect to Lichess, show menus, launch game."""
        link, display = self.link, self.display

        try:
            username = self._connect_and_get_account()
            if not username:
                raise ReturnToMenu()
            self._ensure_event_stream()

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

                if game_id not in self._game_color_hints:
                    self._current_ongoing_game_ids()

                display.send("Lichess\nLoading game...")
                link.send_to_board("ok_back_disable")
                link.send_to_board("GameStart")
                link.send_to_board("hint_disable")
                try:
                    self._play_game(game_id, username, pre_loaded_board=pre_loaded_board)
                finally:
                    display.clear_online_clock()
                    link.send_to_board("wait_exit_disable")
                    link.send_to_board("hint_enable")
                # _play_game raises ReturnToMenu when the game ends
        finally:
            self._stop_event_stream()

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
        wait_exit_ui_enabled = False
        clock_white_ms: Optional[int] = None
        clock_black_ms: Optional[int] = None
        clock_sync_t = time.monotonic()
        last_clock_refresh = 0.0

        def send_turn_if_human():
            if board.turn != your_color:
                return
            send_turn_notification(link, board)

        def commit_elapsed(active_color: chess.Color) -> None:
            nonlocal clock_white_ms, clock_black_ms, clock_sync_t
            if clock_white_ms is None or clock_black_ms is None:
                return
            elapsed_ms = max(0, int((time.monotonic() - clock_sync_t) * 1000))
            if active_color == chess.WHITE:
                clock_white_ms = max(0, clock_white_ms - elapsed_ms)
            else:
                clock_black_ms = max(0, clock_black_ms - elapsed_ms)
            clock_sync_t = time.monotonic()

        def refresh_clock_display(force: bool = False) -> None:
            nonlocal last_clock_refresh
            if clock_white_ms is None or clock_black_ms is None:
                return
            now = time.monotonic()
            if not force and now - last_clock_refresh < 0.2:
                return

            white_ms = clock_white_ms
            black_ms = clock_black_ms
            elapsed_ms = max(0, int((now - clock_sync_t) * 1000))
            if not board.is_game_over():
                if board.turn == chess.WHITE:
                    white_ms = max(0, white_ms - elapsed_ms)
                else:
                    black_ms = max(0, black_ms - elapsed_ms)

            display.set_online_clock(
                white_ms=white_ms,
                black_ms=black_ms,
                you_are_white=you_are_white,
                active_color="white" if board.turn == chess.WHITE else "black",
            )
            last_clock_refresh = now

        def sync_clock_from_payload(payload) -> None:
            nonlocal clock_white_ms, clock_black_ms, clock_sync_t
            white_ms, black_ms = extract_clocks(payload)
            if white_ms is None or black_ms is None:
                return
            clock_white_ms = int(white_ms)
            clock_black_ms = int(black_ms)
            clock_sync_t = time.monotonic()
            refresh_clock_display(force=True)

        awaiting_ok_ack = False
        in_move_entry = False
        pending_check_sq: Optional[str] = None

        def set_waiting_exit_ui(enabled: bool) -> None:
            nonlocal wait_exit_ui_enabled
            if wait_exit_ui_enabled == enabled:
                return
            wait_exit_ui_enabled = enabled
            link.send_to_board("wait_exit_enable" if enabled else "wait_exit_disable")

        def apply_new_moves(move_list, announce_new: bool = True):
            nonlocal last_move_count, awaiting_ok_ack, in_move_entry, pending_check_sq
            set_waiting_exit_ui(False)
            for uci in move_list[last_move_count:]:
                try:
                    mv = chess.Move.from_uci(uci)
                except Exception:
                    last_move_count += 1
                    continue
                is_cap = board.is_capture(mv)
                commit_elapsed(board.turn)
                board.push(mv)
                last_move_count += 1
                pending_check_sq = None
                if board.is_check():
                    ksq = board.king(board.turn)
                    if ksq is not None:
                        pending_check_sq = chess.square_name(ksq)
                if announce_new:
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

        # Read the initial game state from the stream.
        #
        # Lichess sends empty keepalive frames ({}) on stream connections.
        # We need the first non-empty payload for reliable side detection, but
        # this startup wait must stay cancellable and bounded.
        first = {}
        startup_deadline = time.time() + 20.0
        while not first:
            payload_box = [None]
            error_box = [None]
            ready = threading.Event()

            def _fetch_initial_event():
                try:
                    payload_box[0] = next(stream)
                except StopIteration:
                    error_box[0] = "stop"
                except Exception as exc:
                    error_box[0] = str(exc) or "error"
                finally:
                    ready.set()

            threading.Thread(target=_fetch_initial_event, daemon=True).start()

            while not ready.wait(timeout=0.05):
                smsg = link.try_read_from_board()
                if smsg == "shutdown":
                    shutdown_raspberry_pi(link, display)
                    raise ReturnToMenu()
                if smsg and smsg in OK_MSGS | NEW_GAME_MSGS:
                    self._confirm_resign_or_exit(game_id)
                if time.time() >= startup_deadline:
                    display.send("Lichess error\nStream stalled\nOK = menu")
                    wait_for_ok(link, display)
                    raise ReturnToMenu()

            if error_box[0]:
                display.send("Lichess error\nGame stream\nOK = menu")
                wait_for_ok(link, display)
                raise ReturnToMenu()

            first = payload_box[0] or {}

        color_hint = self._game_color_hints.get(game_id)
        if color_hint == "black":
            you_are_white = False
        elif color_hint == "white":
            you_are_white = True
        else:
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

        sync_clock_from_payload(first)
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
                    set_waiting_exit_ui(False)
                    self._confirm_resign_or_exit(game_id)
                    # Returned → user pressed Back; game continues
                elif peek in ("draw", "btn_draw"):
                    set_waiting_exit_ui(False)
                    self._offer_draw(game_id)

            if board.is_game_over():
                set_waiting_exit_ui(False)
                notify_game_over(link, display, board)
                raise ReturnToMenu()

            # ── Opponent's turn — poll stream in background ───────────────────
            if board.turn != your_color:
                set_waiting_exit_ui(True)
                now = int(time.time() * 1000)
                if now - last_wait_banner_ms > 1500:
                    display.send("Waiting for\nopponent...\nOK+Hint = menu")
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
                    refresh_clock_display()
                    smsg = link.try_read_from_board()
                    if smsg:
                        if smsg == "shutdown":
                            set_waiting_exit_ui(False)
                            shutdown_raspberry_pi(link, display)
                            raise ReturnToMenu()
                        if smsg in NEW_GAME_MSGS:
                            set_waiting_exit_ui(False)
                            self._confirm_resign_or_exit(game_id)
                            last_wait_banner_ms = 0  # re-show banner immediately
                        elif smsg in ("draw", "btn_draw"):
                            set_waiting_exit_ui(False)
                            self._offer_draw(game_id)
                        self._handle_common(smsg, board)

                if error_box[0] == "stop":
                    set_waiting_exit_ui(False)
                    display.send("Lichess ended\nOK = menu")
                    wait_for_ok(link, display)
                    raise ReturnToMenu()
                if error_box[0]:
                    set_waiting_exit_ui(False)
                    display.send("Lichess error\nStream lost\nOK = menu")
                    wait_for_ok(link, display)
                    raise ReturnToMenu()

                payload = payload_box[0]
                sync_clock_from_payload(payload)
                move_list = extract_moves(payload)
                if len(move_list) > last_move_count:
                    apply_new_moves(move_list, announce_new=True)
                    prompted_for_this_turn = False
                    continue

                status = extract_status(payload)
                if status and status != "started":
                    set_waiting_exit_ui(False)
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
            set_waiting_exit_ui(False)
            send_turn_if_human()
            refresh_clock_display()
            if not prompted_for_this_turn and not awaiting_ok_ack and not in_move_entry:
                side = "WHITE" if your_color == chess.WHITE else "BLACK"
                display.prompt_move(side)
                prompted_for_this_turn = True

            msg = link.try_read_from_board()
            if not msg:
                time.sleep(0.05)
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
                if pending_check_sq is not None:
                    link.send_to_board(f"check_{pending_check_sq}")
                    pending_check_sq = None
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
            commit_elapsed(not board.turn)
            send_check_signal(link, board)
            send_turn_if_human()
            refresh_clock_display(force=True)
            prompted_for_this_turn = False
            in_move_entry = False
            awaiting_ok_ack = False
