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
from collections import Counter
from typing import List, Optional, Tuple

import chess
import chess.pgn

from core.boardlink import BoardLink
from core.game_flow import (
    GameConfig,
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
)
from modes.online.lichess_client import LichessClient
from screen.display import Display

STUDIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "studies.txt")


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
                if not _looks_like_study_id(head):
                    _flush()
                    current_subject = line
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


def _parse_chapters(
    pgn_text: str,
) -> List[Tuple[str, chess.pgn.Game, Optional[chess.Color]]]:
    """Parse all chapters from a study PGN export.

    Returns a list of (chapter_name, game, orientation) tuples.
    orientation is chess.WHITE, chess.BLACK, or None if not specified.
    Chapter name is taken from the PGN Event header.
    Lichess exports use "Study Name: Chapter Name" format; we extract
    just the chapter name (the part after the last ": ").
    """
    chapters: List[Tuple[str, chess.pgn.Game, Optional[chess.Color]]] = []
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
        orientation_str = str(game.headers.get("Orientation") or "").strip().lower()
        if orientation_str == "white":
            orientation: Optional[chess.Color] = chess.WHITE
        elif orientation_str == "black":
            orientation = chess.BLACK
        else:
            orientation = None
        chapters.append((name, game, orientation))
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

    def __init__(
        self,
        client: LichessClient,
        study_id: str,
        study_name: str,
        *,
        ctx=None,
        cfg: Optional[GameConfig] = None,
    ):
        self.client = client
        self.study_id = study_id
        self.study_name = study_name
        self.ctx = ctx  # EngineContext — enables "continue vs computer" at chapter end
        self.cfg = cfg

    # ------------------------------------------------------------------ public

    def run(self, link: BoardLink, display: Display) -> None:
        """Fetch study, show chapter menu, and play selected chapters."""
        display.show_header_panel("Studies", "Loading", self.study_name[:20])
        pgn_text, api_names = run_in_bg(
            lambda: (
                self.client.get_study_pgn(self.study_id),
                self.client.get_study_chapter_names(self.study_id),
            ),
            link,
            display,
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
        # Override PGN Event-derived names with the Lichess API chapter names
        # (imported chapters often have Event: "import" rather than their real name).
        if api_names:
            chapters = [
                (api_names[i] if i < len(api_names) else name, game, orient)
                for i, (name, game, orient) in enumerate(chapters)
            ]
        if not chapters:
            display.show_header_panel("Studies", "No chapters found", footer="OK=Back")
            wait_for_ok(link, display)
            return

        # Build truncated display names (menu prefix "N) " takes ~3 chars,
        # page indicator " 1/4" takes ~4 — leave ~18 chars for the name itself)
        MAX_NAME = 18
        raw_names = [name.strip() for name, _, _ in chapters]
        # Deduplicate names so display_names.index() finds the right chapter.
        # Chapters imported to Lichess without a title are all called "import".
        name_counts = Counter(raw_names)
        name_seen: dict = {}
        display_names = []
        for s in raw_names:
            if name_counts[s] > 1:
                n = name_seen.get(s, 0) + 1
                name_seen[s] = n
                label = f"{s} ({n})"
            else:
                label = s
            display_names.append(label if len(label) <= MAX_NAME else label[: MAX_NAME - 1] + "…")

        # Chapter selection loop — pressing Back returns to study list
        while True:
            choice = _paged_menu(link, display, display_names, header="Studies")
            if choice is None:
                return  # user pressed Back → back to study selection

            try:
                idx = display_names.index(choice)
                chapter_name, chapter_game, chapter_orientation = chapters[idx]
            except (ValueError, IndexError):
                continue

            # Show full chapter name if it was truncated
            if len(chapter_name.strip()) > MAX_NAME:
                if not self._show_chapter_title(link, display, chapter_name):
                    continue  # user pressed OK — return to chapter list

            # Play mode selection — put the Lichess orientation colour first so
            # the user can just press OK to play the intended side.
            if chapter_orientation == chess.WHITE:
                mode_options = ["Play as White", "Play as Black", "Watch"]
            elif chapter_orientation == chess.BLACK:
                mode_options = ["Play as Black", "Play as White", "Watch"]
            else:
                mode_options = ["Play as White", "Play as Black", "Watch"]

            mode_choice = _paged_menu(
                link,
                display,
                mode_options,
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
    def _paginate_lines(lines: List[str], *, per_page: int = 4) -> List[List[str]]:
        return [lines[i : i + per_page] for i in range(0, len(lines), per_page)]

    @staticmethod
    def _render_page(
        display: Display,
        page_lines: List[str],
        *,
        footer: str,
        page_idx: int,
        total_pages: int,
        size_prefix: str = "annotation",
    ) -> None:
        padded = list(page_lines)
        if not padded:
            padded = [""]
        page_tag = f":{page_idx + 1}/{total_pages}" if total_pages > 1 else ""
        display.send("\n".join(padded) + "\n" + footer, size=f"{size_prefix}{page_tag}")

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
                footer="1=Play  OK=Back",
                page_idx=page_idx,
                total_pages=len(pages),
                size_prefix="menu",
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
        """Show annotation text in a scrolling window (line-by-line)."""
        cleaned = _clean_comment(text)
        # Break at sentence boundaries ('. ' not preceded by digit or dot)
        cleaned = re.sub(r"(?<![.\d])\. ", ".\n", cleaned)
        lines = _wrap_text(cleaned, max_chars=20)
        if not lines or lines == [""]:
            return True

        window = 8
        total = len(lines)
        offset = 0

        while True:
            at_top = offset <= 0
            at_bottom = offset + window >= total
            visible = lines[offset : offset + window]

            if total <= window:
                footer = "OK=Done"
            elif at_top:
                footer = "SwipeUp=Scroll  OK=Done"
            elif at_bottom:
                footer = "SwipeDown=Up  OK=Done"
            else:
                footer = "Swipe=Scroll  OK=Done"

            self._render_page(
                display,
                visible,
                footer=footer,
                page_idx=0,
                total_pages=1,
                size_prefix="annotation",
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
                    if confirm_exit_game(
                        link,
                        display,
                        rearm_command="WaitForAnnotationPage",
                    ):
                        return False
                    continue
                if msg in OK_MSGS:
                    return True
                if msg in HINT_MSGS:
                    if total > window:
                        offset = 0 if at_bottom else offset + 1
                        break  # redisplay
                if msg == "delete":
                    if total > window:
                        offset = max(0, offset - 1)
                    break  # redisplay

    def _collect_move(
        self,
        link: BoardLink,
        display: Display,
        board: chess.Board,
        node: chess.pgn.GameNode,
    ) -> Optional[chess.pgn.GameNode]:
        """Wait for the human to enter a move matching one of node's variations.

        Returns the matched variation node, or None if the user backed out.
        Raises ReturnToMenu if the user pressed the back/new-game button and
        confirmed exit.
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
            display.prompt_move(side, force=force)

        _arm()

        while True:
            msg = link.read_from_board()
            if msg is None:
                continue

            if msg == "shutdown":
                shutdown_raspberry_pi(link, display)
                return None

            if msg in NEW_GAME_MSGS:
                if not confirm_exit_game(link, display):
                    _arm()
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
            return matched

    @staticmethod
    def _fork_label(
        node: chess.pgn.GameNode, board_fen: str, *, is_human: bool = True
    ) -> str:
        """Short menu label for a fork: 'You 5: e4/d4' or 'Opp 5: e5/c5'."""
        try:
            brd = chess.Board(board_fen)
            fullmove = brd.fullmove_number
            sans = []
            for var in node.variations[:3]:
                try:
                    sans.append(brd.san(var.move))
                except Exception:
                    sans.append(chess.Move.uci(var.move)[2:4])
            prefix = "You" if is_human else "Opp"
            return f"{prefix} {fullmove}: {'/'.join(sans)}"
        except Exception:
            return "You Fork" if is_human else "Opp Fork"

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

        # Chapters with no moves and no displayable comment are Lichess section
        # dividers or text-only chapters whose prose is not exported in the PGN.
        # Skip board setup entirely and show a brief message.
        has_moves = bool(game.variations)
        cleaned_root_comment = _clean_comment(game.comment) if game.comment else ""
        if not has_moves and not cleaned_root_comment:
            display.show_header_panel(label, "No exercises", footer="OK=Chapters")
            wait_for_ok(link, display)
            return

        # Skip setup for the standard starting position OR an empty board (used by
        # Lichess as the FEN for text-only / section-divider chapters).
        _EMPTY_BOARD = "8/8/8/8/8/8/8/8"
        no_setup_needed = board.board_fen() in (chess.Board().board_fen(), _EMPTY_BOARD)
        if no_setup_needed:
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

        mode_label = (
            "Watch"
            if play_as is None
            else ("Playing White" if play_as == chess.WHITE else "Playing Black")
        )
        print(f"[STUDY] chapter={chapter_name!r} mode={mode_label}", flush=True)

        # fork_history tracks decision points so the user can rewind after reaching
        # the end of a variation. Each entry is (node, fen, is_human_turn).
        fork_history: List[Tuple[chess.pgn.GameNode, str, bool]] = []
        node: chess.pgn.GameNode = game

        while True:  # outer loop: supports rewinding to a fork
            while node.variations:
                current_turn = board.turn
                main_var = node.variation(0)
                move = main_var.move
                uci = chess.Move.uci(move)
                cap = board.is_capture(move)
                is_human_turn = play_as is not None and current_turn == play_as

                if is_human_turn:
                    # Record fork before human chooses so we can rewind here later
                    if len(node.variations) > 1:
                        fork_history.append((node, board.fen(), True))
                    # Human plays — let them choose a variation
                    chosen = self._collect_move(link, display, board, node)
                    if chosen is None:
                        return
                    board.push(chosen.move)
                    send_check_signal(link, board)
                    node = chosen
                else:
                    # Watch or opponent's turn.
                    # If the PGN has multiple variations at this node, let the user
                    # pick which line to follow right now (inline), and record the
                    # fork so they can come back later via the fork menu.
                    if len(node.variations) > 1:
                        fork_history.append((node, board.fen(), False))
                        brd_tmp = chess.Board(board.fen())
                        var_options = []
                        for var in node.variations[:4]:
                            try:
                                var_options.append(brd_tmp.san(var.move))
                            except Exception:
                                var_options.append(chess.Move.uci(var.move)[2:4])
                        var_choice = _paged_menu(
                            link, display, var_options, header="Opp plays",
                            back_label="Main line",
                        )
                        if var_choice is not None:
                            main_var = node.variations[var_options.index(var_choice)]
                            move = main_var.move
                            uci = chess.Move.uci(move)
                            cap = board.is_capture(move)
                        # If user backs out of picker, fall through to main line
                    # Use study_move_ so the Pico stays in the normal message loop
                    # (engine format triggers engine_ack_pending which causes a
                    # deadlock when WaitForOkConfirm is sent for annotations).
                    link.send_to_board(f"study_move_{uci}{'_cap' if cap else ''}")
                    board.push(move)
                    show_received_move(display, board, uci, force=True)
                    pending_check_sq = None
                    if board.is_check():
                        ksq = board.king(board.turn)
                        if ksq is not None:
                            pending_check_sq = chess.square_name(ksq)
                    node = main_var
                    if not wait_for_ok(
                        link,
                        display,
                        allow_exit_menu=True,
                        rearm_command="WaitForOkConfirm",
                    ):
                        return
                    if pending_check_sq is not None:
                        link.send_to_board(f"check_{pending_check_sq}")
                        pending_check_sq = None

                # Show move annotation if present
                if node.comment:
                    if not self._show_annotation(link, display, node.comment):
                        return

            # Chapter complete (or variation end)
            if not fork_history:
                can_vs_cpu = (
                    self.ctx is not None
                    and play_as is not None
                    and not board.is_game_over()
                )
                if can_vs_cpu:
                    display.show_header_panel(
                        label,
                        "Chapter done!",
                        footer="1=vs CPU  OK=Chapters",
                    )
                    link.send_to_board("WaitForOkOrSkipSetup")
                    while True:
                        msg = link.read_from_board()
                        if msg is None:
                            continue
                        if msg == "shutdown":
                            shutdown_raspberry_pi(link, display)
                            return
                        if msg in NEW_GAME_MSGS or msg in OK_MSGS:
                            return  # OK=Chapters
                        if (msg or "").strip() == "1":
                            self._continue_vs_computer(link, display, board, play_as)
                            return
                else:
                    display.show_header_panel(label, "Chapter done!", footer="OK=Chapters")
                    wait_for_ok(link, display)
                return

            # Build fork menu (most recent fork first)
            fork_labels = [
                self._fork_label(n, f, is_human=h)
                for n, f, h in reversed(fork_history)
            ]
            can_vs_cpu = (
                self.ctx is not None
                and play_as is not None
                and not board.is_game_over()
            )
            VS_CPU_LABEL = "Continue VS CPU"
            menu_items = ([VS_CPU_LABEL] if can_vs_cpu else []) + fork_labels
            choice = _paged_menu(
                link,
                display,
                menu_items,
                header="Study Menu",
                can_back=True,
                back_label="Chapters",
            )
            if choice is None:
                return  # OK=Chapters

            if choice == VS_CPU_LABEL:
                display.show_header_panel("vs Computer", "Engine loading...")
                self._continue_vs_computer(link, display, board, play_as)
                return

            # Locate the chosen fork
            rev_idx = fork_labels.index(choice)
            orig_idx = len(fork_history) - 1 - rev_idx
            node, fork_fen, is_human = fork_history[orig_idx]

            # For opponent forks: ask which variation to explore before board setup
            # so backing out is free (no board reset needed).
            opp_chosen_var: Optional[chess.pgn.GameNode] = None
            if not is_human and len(node.variations) > 1:
                brd_tmp = chess.Board(fork_fen)
                var_options = []
                for var in node.variations[:4]:
                    try:
                        var_options.append(brd_tmp.san(var.move))
                    except Exception:
                        var_options.append(chess.Move.uci(var.move)[2:4])
                var_choice = _paged_menu(
                    link, display, var_options, header="Opp variation",
                    back_label="Main line",
                )
                if var_choice is None:
                    # User backed out — leave fork in history and show menu again
                    continue
                opp_chosen_var = node.variations[var_options.index(var_choice)]

            # Discard everything explored after the chosen fork and setup board.
            # For opponent forks, re-insert the fork at the current end of history
            # so the user can come back and try a different variation later.
            fork_history = fork_history[:orig_idx]
            if opp_chosen_var is not None:
                fork_history.append((node, fork_fen, False))
            board = chess.Board(fork_fen)
            if guide_board_setup(link, display, fork_fen, label=label) is None:
                return  # user backed out during setup

            # For opponent forks: play the chosen variation before resuming
            if opp_chosen_var is not None:
                opp_uci = chess.Move.uci(opp_chosen_var.move)
                opp_cap = board.is_capture(opp_chosen_var.move)
                link.send_to_board(
                    f"study_move_{opp_uci}{'_cap' if opp_cap else ''}"
                )
                board.push(opp_chosen_var.move)
                show_received_move(display, board, opp_uci, force=True)
                if not wait_for_ok(
                    link,
                    display,
                    allow_exit_menu=True,
                    rearm_command="WaitForOkConfirm",
                ):
                    return
                node = opp_chosen_var
                if node.comment:
                    if not self._show_annotation(link, display, node.comment):
                        return

    def _continue_vs_computer(
        self,
        link: BoardLink,
        display: Display,
        board: chess.Board,
        play_as: chess.Color,
    ) -> None:
        """Continue the current study position against the engine at max difficulty."""
        from core.game_flow import GameConfig
        from modes.vs_computer.game_controller import GameController, GameDeps
        from modes.vs_computer.stockfish_opponent import StockfishOpponent

        assert self.ctx is not None
        self.ctx.ensure()

        cfg = self.cfg or GameConfig()
        cfg.skill_level = 8
        cfg.move_time_ms = 3000
        cfg.human_is_white = play_as == chess.WHITE

        opponent = StockfishOpponent(
            self.ctx,
            move_time_ms=cfg.move_time_ms,
            skill_level=cfg.skill_level,
        )
        controller = GameController(
            GameDeps(link=link, display=display, opponent=opponent),
            cfg=cfg,
        )
        controller.run_from_position(
            board,
            play_as=play_as,
            move_time_ms=cfg.move_time_ms,
        )
