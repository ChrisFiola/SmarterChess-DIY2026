# -*- coding: utf-8 -*-
"""Lichess Study mode controller for SmartChess.

Loads study chapters from Lichess, guides board setup, and lets the user
play through the game tree with annotation display and variation navigation.

studies.txt format (one per line):
    study_id  Display Name
Lines starting with # are comments.
"""

from __future__ import annotations

import io
import os
import re
import time
from typing import List, Optional, Tuple

import chess
import chess.pgn

from core.boardlink import BoardLink
from core.game_flow import (
    ReturnToMenu,
    _paged_menu,
    confirm_exit_game,
    guide_board_setup,
    handle_capq_message,
    handle_illegal_move,
    handle_typing_message,
    resolve_uci_promotion,
    run_in_bg,
    show_hint_move,
    show_received_move,
    send_check_signal,
    shutdown_raspberry_pi,
    wait_for_ok,
)
from core.protocol import (
    HINT_MSGS,
    NEW_GAME_MSGS,
    OK_MSGS,
    parse_uci_move,
    piece_name_for_side,
)
from modes.online.lichess_client import LichessClient
from screen.display import Display

STUDIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "studies.txt")


class BackToChoice(Exception):
    """Raised when the user wants to return to the last study variation choice."""


def _looks_like_study_id(token: str) -> bool:
    token = (token or "").strip()
    return bool(re.fullmatch(r"[A-Za-z0-9]{8}", token))


def load_study_subjects() -> List[Tuple[str, List[Tuple[str, str]]]]:
    """Return grouped studies from studies.txt.

    Subject headings are plain lines that do not start with a Lichess study ID.
    Study rows keep the existing format:
        study_id  Display Name

    If the file is flat with no subject headings, entries are grouped under
    a default `Studies` subject so older files still work.
    """
    groups: List[Tuple[str, List[Tuple[str, str]]]] = []
    current_subject = "Studies"
    current_items: List[Tuple[str, str]] = []

    def _flush() -> None:
        nonlocal current_items
        if current_items:
            groups.append((current_subject, current_items))
            current_items = []

    try:
        with open(STUDIES_PATH, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                parts = line.split(None, 1)
                head = parts[0].strip() if parts else ""
                if len(parts) == 1 and not _looks_like_study_id(head):
                    _flush()
                    current_subject = head
                    continue

                if not _looks_like_study_id(head):
                    continue

                study_id = head
                name = parts[1].strip() if len(parts) > 1 else study_id
                current_items.append((study_id, name))
    except FileNotFoundError:
        return []

    _flush()
    return groups


def load_studies() -> List[Tuple[str, str]]:
    """Return a flat list of (study_id, display_name) from studies.txt."""
    studies: List[Tuple[str, str]] = []
    for _, items in load_study_subjects():
        studies.extend(items)
    return studies


def _parse_chapters(pgn_text: str) -> List[Tuple[str, chess.pgn.Game]]:
    """Parse all chapters from a study PGN export.

    Returns a list of (chapter_name, game) tuples.
    Chapter name is taken from the PGN Event header.
    Lichess exports use "Study Name: Chapter Name" format; we extract
    just the chapter name (the part after the last ": ").
    """
    chapters: List[Tuple[str, chess.pgn.Game]] = []
    reader = io.StringIO(pgn_text)
    idx = 0
    while True:
        game = chess.pgn.read_game(reader)
        if game is None:
            break
        idx += 1
        event = str(game.headers.get("Event") or "").strip()
        # Lichess uses "Study Name: Chapter Name" — keep only the chapter part
        if ": " in event:
            event = event.rsplit(": ", 1)[1].strip() or event
        name = event or f"Chapter {idx}"
        chapters.append((name, game))
    return chapters


def _clean_comment(text: str) -> str:
    """Strip Lichess command annotations and emoji from comment text.

    Removes [%cal ...], [%csl ...], and any other [%...] Lichess markup.
    Strips characters outside basic ASCII + Latin Extended (emoji, symbols, etc.).
    """
    text = re.sub(r"\[%[^\]]*\]", "", text)
    return "".join(
        ch for ch in text if ord(ch) < 0x0250  # ASCII + Latin Extended A/B
    ).strip()


def _wrap_text(text: str, max_chars: int = 20) -> List[str]:
    """Word-wrap text into lines of at most max_chars characters."""
    lines: List[str] = []
    for para in text.strip().split("\n"):
        para = para.strip()
        if not para:
            continue
        words = para.split()
        current = ""
        for word in words:
            word = word[:max_chars]  # hard-truncate monster words
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= max_chars:
                current += " " + word
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines or [""]


class StudyController:
    """Run a Lichess study: chapter selection, board setup, and game tree play."""

    def __init__(self, client: LichessClient, study_id: str, study_name: str):
        self.client = client
        self.study_id = study_id
        self.study_name = study_name

    # ------------------------------------------------------------------ public

    def run(self, link: BoardLink, display: Display) -> None:
        """Fetch study, show chapter menu, and play selected chapters."""
        display.show_header_panel("Studies", "Loading", self.study_name[:20])
        pgn_text = run_in_bg(
            lambda: self.client.get_study_pgn(self.study_id), link, display
        )
        if not pgn_text:
            display.show_header_panel(
                "Study error",
                "Could not load",
                footer="OK=Back",
            )
            wait_for_ok(link, display)
            return

        chapters = _parse_chapters(pgn_text)
        if not chapters:
            display.show_header_panel("Studies", "No chapters found", footer="OK=Back")
            wait_for_ok(link, display)
            return

        # Build truncated display names (menu prefix "N) " takes ~3 chars,
        # page indicator " 1/4" takes ~4 — leave ~18 chars for the name itself)
        MAX_NAME = 18
        display_names = []
        for name, _ in chapters:
            s = name.strip()
            display_names.append(s if len(s) <= MAX_NAME else s[: MAX_NAME - 1] + "…")

        # Chapter selection loop — pressing Back returns to study list
        while True:
            choice = _paged_menu(link, display, display_names, header="Studies")
            if choice is None:
                return  # user pressed Back → back to study selection

            try:
                idx = display_names.index(choice)
                chapter_name, chapter_game = chapters[idx]
            except (ValueError, IndexError):
                continue

            # Show full chapter name if it was truncated
            if len(chapter_name.strip()) > MAX_NAME:
                if not self._show_chapter_title(link, display, chapter_name):
                    continue  # user pressed OK — return to chapter list

            # Play mode selection
            mode_choice = _paged_menu(
                link,
                display,
                ["Play as White", "Play as Black", "Watch"],
                header="Studies",
            )
            if mode_choice is None:
                continue  # back to chapter selection

            if mode_choice == "Play as White":
                play_as: Optional[chess.Color] = chess.WHITE
            elif mode_choice == "Play as Black":
                play_as = chess.BLACK
            else:
                play_as = None  # Watch mode

            self._play_chapter(link, display, chapter_game, play_as, chapter_name)
            # After chapter finishes, loop back to chapter selection

    # ----------------------------------------------------------------- private

    @staticmethod
    def _paginate_lines(lines: List[str], *, per_page: int = 3) -> List[List[str]]:
        return [lines[i : i + per_page] for i in range(0, len(lines), per_page)]

    @staticmethod
    def _render_page(
        display: Display,
        page_lines: List[str],
        *,
        footer: str,
        page_idx: int,
        total_pages: int,
    ) -> None:
        padded = list(page_lines)
        while len(padded) < 3:
            padded.append("")
        page_tag = f":{page_idx + 1}/{total_pages}" if total_pages > 1 else ""
        display.send("\n".join(padded) + "\n" + footer, size=f"annotation{page_tag}")

    def _show_chapter_title(
        self, link: BoardLink, display: Display, title: str
    ) -> bool:
        """Show full chapter title with 1=continue and OK=back semantics.

        Returns True if user pressed 1 (continue), False if OK (back) or exit.
        """
        lines = _wrap_text(title, max_chars=20)
        pages = self._paginate_lines(lines)

        for page_idx, page_lines in enumerate(pages):
            is_last = page_idx == len(pages) - 1
            self._render_page(
                display,
                page_lines,
                footer="1 = play   OK = back",
                page_idx=page_idx,
                total_pages=len(pages),
            )
            link.send_to_board("WaitForOkOrSkipSetup")

            while True:
                msg = link.read_from_board()
                if msg is None:
                    continue
                if msg == "shutdown":
                    shutdown_raspberry_pi(link, display)
                    return False
                if msg in NEW_GAME_MSGS or msg in OK_MSGS:
                    return False  # OK = back
                if (msg or "").strip() == "1":
                    if is_last:
                        return True  # 1 = continue
                    break  # next page
                if (
                    msg.startswith("typing_")
                    or msg.startswith("capq_")
                    or msg in HINT_MSGS
                ):
                    continue

        return True

    def _show_annotation(self, link: BoardLink, display: Display, text: str) -> bool:
        """Show paginated annotation text (3 lines/page).

        Hint = next page (cycles back from last), OK = done.
        Returns False if user backs out to menu.
        """
        lines = _wrap_text(_clean_comment(text), max_chars=20)
        if not lines or lines == [""]:
            return True

        pages = self._paginate_lines(lines)
        page_idx = 0

        while True:
            is_last = page_idx == len(pages) - 1
            self._render_page(
                display,
                pages[page_idx],
                footer=(
                    "Hint = back   OK = done"
                    if is_last
                    else "Hint = next   OK = skip"
                ),
                page_idx=page_idx,
                total_pages=len(pages),
            )
            link.send_to_board("WaitForAnnotationPage")

            while True:
                msg = link.read_from_board()
                if msg is None:
                    continue
                if msg == "shutdown":
                    shutdown_raspberry_pi(link, display)
                    return False
                if msg in NEW_GAME_MSGS:
                    return False
                if msg in OK_MSGS:
                    return True
                if msg in HINT_MSGS:
                    page_idx = 0 if is_last else page_idx + 1
                    break  # redisplay

    def _study_nav_action(
        self,
        link: BoardLink,
        display: Display,
        *,
        can_go_back: bool,
    ) -> str:
        """Return 'continue', 'back', or 'exit' from the in-study nav menu."""
        options = ["Back to choice", "Exit to menu"] if can_go_back else ["Exit to menu"]
        choice = _paged_menu(
            link,
            display,
            options,
            header="Studies",
            wake_command="ChooseMode",
            resend_timeout=3.0,
        )
        if choice is None:
            link.send_to_board("SetupComplete")
            return "continue"
        if choice == "Back to choice":
            return "back"
        return "exit"

    def _rewind_to_choice(
        self,
        link: BoardLink,
        display: Display,
        board: chess.Board,
        *,
        target_ply: int,
    ) -> bool:
        """Undo moves on the physical board until the last variation-choice node."""
        while len(board.move_stack) > target_ply:
            mv = board.peek()
            uci = chess.Move.uci(mv)
            piece = board.piece_at(mv.to_square)
            piece_line = ""
            if piece is not None:
                piece_line = piece_name_for_side(
                    piece.symbol(),
                    "w" if piece.color == chess.WHITE else "b",
                )

            body_lines = [ln for ln in (piece_line, f"{uci[2:4]} → {uci[:2]}") if ln]
            display.show_header_panel(
                "Back to choice",
                *body_lines,
                footer="OK=Undo",
            )
            link.send_to_board(f"puzzle_wrong_{uci[2:4]}{uci[:2]}")
            if not wait_for_ok(link, display, send_prompt=False):
                return False
            board.pop()

        display.show_header_panel("Studies", "Back at last choice")
        time.sleep(0.4)
        return True

    def _collect_move(
        self,
        link: BoardLink,
        display: Display,
        board: chess.Board,
        node: chess.pgn.GameNode,
        *,
        can_go_back: bool = False,
    ) -> Optional[chess.pgn.GameNode]:
        """Wait for the human to enter a move matching one of node's variations.

        Returns the matched variation node, or None if the user backed out.
        Raises ReturnToMenu if the user pressed the back/new-game button and
        confirmed exit. Raises BackToChoice if the user wants to revisit the
        previous variation branch.
        """
        variations = node.variations
        if not variations:
            return None

        main_var = variations[0]
        main_uci = chess.Move.uci(main_var.move)
        side = "WHITE" if board.turn == chess.WHITE else "BLACK"

        def _arm(*, force: bool = False) -> None:
            """Arm the Pico for move entry and update the LCD."""
            if len(variations) > 1:
                ucis = "|".join(chess.Move.uci(v.move) for v in variations[:4])
                link.send_to_board(f"study_vars_{ucis}")
            link.send_to_board(
                f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
            )
            if len(variations) == 1:
                display.show_header_panel(
                    f"You are {side}",
                    "Play move",
                    footer="Hint = see move",
                    force=force,
                )
            else:
                dests = " / ".join(chess.Move.uci(v.move)[2:4] for v in variations[:4])
                display.show_header_panel(
                    f"You are {side}",
                    "Play move",
                    f"→{dests}",
                    footer="Hint = main line",
                    force=force,
                )

        _arm()

        while True:
            msg = link.read_from_board()
            if msg is None:
                continue

            if msg == "shutdown":
                shutdown_raspberry_pi(link, display)
                return None

            if msg in NEW_GAME_MSGS:
                if can_go_back:
                    action = self._study_nav_action(
                        link,
                        display,
                        can_go_back=True,
                    )
                    if action == "back":
                        raise BackToChoice()
                    if action == "exit":
                        raise ReturnToMenu()
                    _arm()
                    link.send_to_board(f"hint_{main_uci}")
                    continue
                if not confirm_exit_game(link, display):
                    _arm()
                    link.send_to_board(f"hint_{main_uci}")
                    continue
                raise ReturnToMenu()

            if msg in HINT_MSGS:
                link.send_to_board(f"hint_{main_uci}")
                show_hint_move(display, board, main_uci, force=True)
                continue

            if msg in OK_MSGS:
                _arm(force=True)
                continue

            if msg.startswith("typing_"):
                handle_typing_message(link, display, msg[len("typing_") :], board)
                continue

            if handle_capq_message(link, board, msg):
                continue

            uci = parse_uci_move(msg)
            if not uci:
                continue

            # Promotion handling
            try:
                uci = resolve_uci_promotion(link, display, board, uci) or uci
            except ReturnToMenu:
                raise

            # Parse move
            try:
                user_mv = chess.Move.from_uci(uci)
            except ValueError:
                continue

            if user_mv not in board.legal_moves:
                ok = handle_illegal_move(
                    link=link, display=display, board=board, uci=uci, label="Illegal"
                )
                if not ok:
                    return None
                continue

            # Match against any variation
            matched: Optional[chess.pgn.GameNode] = None
            for var in variations:
                var_uci = chess.Move.uci(var.move)
                if uci[:4] == var_uci[:4]:
                    # promotion suffix must match if present
                    if len(var_uci) < 5 or (len(uci) >= 5 and uci[4] == var_uci[4]):
                        matched = var
                        break

            if matched is None:
                # Legal but not in study variations
                ok = handle_illegal_move(
                    link=link,
                    display=display,
                    board=board,
                    uci=uci,
                    label="Not in study",
                )
                if not ok:
                    return None
                continue

            # Correct move
            display.show_header_panel("Good move!", f"{uci[:2]} → {uci[2:4]}")
            time.sleep(1.0)
            return matched

    def _play_chapter(
        self,
        link: BoardLink,
        display: Display,
        game: chess.pgn.Game,
        play_as: Optional[chess.Color],
        chapter_name: str,
    ) -> None:
        """Guide board setup and play through the chapter's main game tree."""
        board = game.board()
        fen = board.fen()
        label = chapter_name[:15]

        # Skip setup if the chapter starts from the standard starting position
        if board.board_fen() == chess.Board().board_fen():
            display.show_header_panel(label, "Standard start", "Board ready")
            link.send_to_board("hint_disable")
            link.send_to_board("puzzle_setup_begin")
            time.sleep(0.2)
            link.send_to_board("puzzle_setup_done")
            link.send_to_board("hint_enable")
        else:
            result = guide_board_setup(link, display, fen, label=label)
            if result is None:
                return  # user backed out during setup

        link.send_to_board("SetupComplete")

        # Show root annotation (chapter intro text) if present
        if game.comment:
            if not self._show_annotation(link, display, game.comment):
                return

        node: chess.pgn.GameNode = game
        variation_history: List[Tuple[chess.pgn.GameNode, int]] = []
        mode_label = (
            "Watch"
            if play_as is None
            else ("Playing White" if play_as == chess.WHITE else "Playing Black")
        )
        print(f"[STUDY] chapter={chapter_name!r} mode={mode_label}", flush=True)

        while node.variations:
            current_turn = board.turn
            main_var = node.variation(0)
            move = main_var.move
            uci = chess.Move.uci(move)
            cap = board.is_capture(move)
            side_label = "White" if current_turn == chess.WHITE else "Black"
            is_human_turn = play_as is not None and current_turn == play_as

            if is_human_turn:
                # Human plays — let them choose a variation
                try:
                    chosen = self._collect_move(
                        link,
                        display,
                        board,
                        node,
                        can_go_back=bool(variation_history),
                    )
                except BackToChoice:
                    branch_node, branch_ply = variation_history.pop()
                    if not self._rewind_to_choice(
                        link,
                        display,
                        board,
                        target_ply=branch_ply,
                    ):
                        return
                    node = branch_node
                    continue
                if chosen is None:
                    return
                if len(node.variations) > 1:
                    variation_history.append((node, len(board.move_stack)))
                board.push(chosen.move)
                send_check_signal(link, board)
                node = chosen
            else:
                # Watch or opponent's turn — auto-play main line
                # Use hint_ instead of engine move format so the Pico stays in
                # the normal message loop (engine format triggers engine_ack_pending
                # which auto-calls _collect_and_submit_move on OK, causing a deadlock
                # when we then send WaitForOkConfirm for annotations).
                link.send_to_board(f"study_move_{uci}{'_cap' if cap else ''}")
                board.push(move)
                show_received_move(display, board, uci, force=True)
                send_check_signal(link, board)
                node = main_var
                if not wait_for_ok(link, display):
                    return

            # Show move annotation if present
            if node.comment:
                if not self._show_annotation(link, display, node.comment):
                    return

        # Chapter complete
        display.show_header_panel(label, "Chapter done!", footer="OK=Chapters")
        wait_for_ok(link, display)
