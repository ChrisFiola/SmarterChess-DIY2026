# -*- coding: utf-8 -*-
"""
Game flow, parsing, setup, and unified play loop (modular version).
Preserves Pico<->Pi UART protocol strings and display behavior.
"""
from __future__ import annotations

import hashlib
import random
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from urllib.parse import quote

import chess

from core.boardlink import BoardLink
from core.engine import EngineContext
from core.protocol import (
    HINT_MSGS,
    IGNORED_MSGS,
    NEW_GAME_MSGS,
    OK_MSGS,
    format_capture_reply,
    format_hint_move,
    parse_uci_move,
    piece_name,
    send_lcd_ack_for_payload,
)
from screen.display import Display

# -------------------- Data classes --------------------


@dataclass
class GameConfig:
    skill_level: int = 5
    move_time_ms: int = 2000
    human_is_white: bool = True
    brightness: int = 5


@dataclass
class GameState:
    board: chess.Board
    mode: str = "stockfish"  # "stockfish" | "local" | "online" | "puzzle"


# -------------------- Parsing & helpers --------------------


def wait_for_ok(link: BoardLink, display: Display, *, send_prompt: bool = True) -> bool:
    """Wait for Pico OK acknowledgement (btn_ok/ok).

    Sends WaitForOkConfirm so the Pico enters a dedicated OK-confirm state
    regardless of whether it is in SETUP (setup loop) or RUNNING (main loop)
    state, unless the caller already put the Pico into an OK-waiting state.

    Returns False if the user exits to menu (new game) or shutdown is triggered.
    """
    if send_prompt:
        link.send_to_board("WaitForOkConfirm")

    while True:
        m = link.read_from_board()
        if m is None:
            continue

        if m == "shutdown":
            shutdown_raspberry_pi(link, display)
            return False

        if m in NEW_GAME_MSGS:
            return False

        if m in OK_MSGS:
            return True

        # ignore chatter
        if m.startswith("typing_") or m.startswith("capq_") or m in HINT_MSGS:
            continue


def wait_for_ok_or_skip_setup(link: BoardLink, display: Display):
    """Wait for setup-entry confirmation."""
    link.send_to_board("WaitForOkOrSkipSetup")

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
            return "ok"

        if (m or "").strip().lower() == "1":
            return "skip"

        if m.startswith("typing_") or m.startswith("capq_") or m in HINT_MSGS:
            continue


def confirm_exit_game(
    link: BoardLink,
    display: Display,
    options: Optional[List[str]] = None,
) -> bool:
    """Show a paged 'Leave game?' confirmation menu.

    Wakes the Pico from its suspended-after-OK+Hint state via ChooseMode,
    then shows the menu.  If the user presses Back (OK), the Pico is
    re-armed for gameplay by sending SetupComplete.

    Returns True  – user confirmed exit; caller should raise ReturnToMenu.
    Returns False – user pressed Back; caller should re-prompt and continue.
    """
    choice = _paged_menu(
        link,
        display,
        options or ["Exit to menu"],
        wake_command="ChooseMode",
        resend_timeout=3.0,
    )
    if choice is None:
        # User pressed Back — put Pico back into RUNNING state
        link.send_to_board("SetupComplete")
        return False
    return True


def run_in_bg(
    fn: Callable,
    link: BoardLink,
    display: Display,
    *,
    on_cancel: Optional[Callable] = None,
):
    """Run *fn()* in a daemon thread while polling serial every 50 ms.

    This keeps the UI responsive during slow blocking calls (HTTP, sleep).
    If the user presses OK or back while *fn* is running, *on_cancel()* is
    called if provided (it must raise ReturnToMenu), otherwise ReturnToMenu
    is raised directly.  Returns *fn()*'s return value on normal completion.
    """
    result_box = [None]
    exc_box: list = [None]
    done = threading.Event()

    def _worker():
        try:
            result_box[0] = fn()
        except Exception as e:
            exc_box[0] = e
        finally:
            done.set()

    threading.Thread(target=_worker, daemon=True).start()

    while not done.wait(timeout=0.05):
        msg = link.try_read_from_board()
        if msg == "shutdown":
            shutdown_raspberry_pi(link, display)
            return None
        if msg and msg in OK_MSGS | NEW_GAME_MSGS:
            if on_cancel is not None:
                on_cancel()  # expected to raise ReturnToMenu
            raise ReturnToMenu()

    if exc_box[0] is not None:
        raise exc_box[0]
    return result_box[0]


# ─────────────────────────────────────────────────────────────────────────────
# Shared single-call helpers used by every game mode
# ─────────────────────────────────────────────────────────────────────────────


def handle_typing_message(
    link: BoardLink,
    display: Display,
    payload: str,
    board: Optional["chess.Board"] = None,
    *,
    log_prefix: str = "[ACK]",
) -> None:
    """Update the typing-preview display and send the matching ACK in one call.

    Called identically in every mode — keeping this centralised guarantees
    that typing-preview behaviour stays in sync everywhere.
    """
    _update_typing_display(display, payload, board)
    send_lcd_ack_for_payload(link, payload, log_prefix=log_prefix)


def handle_capq_message(link: BoardLink, board: "chess.Board", msg: str) -> bool:
    """If *msg* is a capture-query from the Pico, answer it and return True."""
    if not msg.startswith("capq_"):
        return False
    uci = msg[5:].strip()
    try:
        cap = check_move_captures(board, uci)
    except Exception:
        cap = False
    link.send_to_board(format_capture_reply(cap))
    return True


def send_turn_notification(link: BoardLink, board: "chess.Board") -> None:
    """Send the current turn colour to the Pico (turn_white or turn_black)."""
    link.send_to_board(f"turn_{'white' if board.turn == chess.WHITE else 'black'}")


def send_check_signal(link: BoardLink, board: "chess.Board") -> None:
    """If the side to move is in check, tell the Pico which king square to blink.

    Safe to call after any push — does nothing when the position is not in check.
    """
    try:
        if board.is_check():
            ksq = board.king(board.turn)
            if ksq is not None:
                link.send_to_board(f"check_{chess.square_name(ksq)}")
    except Exception:
        pass


def resolve_uci_promotion(
    link: BoardLink,
    display: Display,
    board: "chess.Board",
    uci: str,
) -> Optional[str]:
    """Append a promotion letter to *uci* if the move requires one.

    Returns the (possibly extended) uci string, or None if the user backed out.
    Raises ReturnToMenu if the user pressed the back/new-game button.
    """
    if len(uci) == 4:
        try:
            piece = board.piece_at(chess.parse_square(uci[:2]))
            if piece and piece.piece_type == chess.PAWN:
                rank = int(uci[3])
                if (piece.color == chess.WHITE and rank == 8) or (
                    piece.color == chess.BLACK and rank == 1
                ):
                    promo = _prompt_promotion_choice(link, display)
                    return uci + promo
        except ReturnToMenu:
            raise
        except Exception:
            pass

    try:
        move = chess.Move.from_uci(uci)
        if _move_needs_promotion(move, board):
            promo = _prompt_promotion_choice(link, display)
            return uci + promo
    except (ValueError, Exception):
        pass

    return uci


def validate_and_push_move(
    *,
    link: BoardLink,
    display: Display,
    board: "chess.Board",
    uci: str,
) -> Optional["chess.Move"]:
    """Parse, promote, validate, and push a human move in one call.

    Returns the pushed Move on success, None on failure.
    On failure the appropriate error is already sent to Pico + display.
    """
    # 1) Promotion
    try:
        uci = resolve_uci_promotion(link, display, board, uci) or uci
    except ReturnToMenu:
        raise

    # 2) Parse
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        link.send_to_board(f"error_invalid_{uci}")
        display.show_invalid(uci)
        return None

    # 3) Legality
    if move not in board.legal_moves:
        handle_illegal_move(
            link=link, display=display, board=board, uci=uci, label="ILLEGAL"
        )
        return None

    # 4) Push
    board.push(move)

    # 5) Check signal
    send_check_signal(link, board)

    return move


def handle_illegal_move(
    *,
    link: BoardLink,
    display: Display,
    board: chess.Board,
    uci: str,
    label: str = "Illegal",
) -> bool:
    """Standard illegal-move UX: show put-back target + red trail; wait OK.

    Pico handler expects: `puzzle_wrong_{to}{from}` (trail from TO back to FROM).
    """
    u = (uci or "").strip().lower()
    if u.startswith("m"):
        u = u[1:]
    u = "".join(ch for ch in u if ch.isalnum())

    side = "WHITE" if board.turn == chess.WHITE else "BLACK"
    side_prefix = f"You are {side}"

    frm, to = (u[:2], u[2:4]) if len(u) >= 4 else ("", "")
    piece_txt = "PIECE"
    try:
        if frm:
            p = board.piece_at(chess.parse_square(frm))
            if p:
                piece_txt = piece_name(p.symbol())
    except Exception:
        pass

    if frm and to:
        display.send(f"{label} move:\n{piece_txt} {frm}->{to}\nPut it back + OK")
        link.send_to_board(f"puzzle_wrong_{to}{frm}")
    else:
        display.send(f"{side_prefix}\n{label} move\nPress OK")

    ok = wait_for_ok(link, display, send_prompt=False)
    if not ok:
        return False

    # Deterministic re-entry: Pi commands turn_ and then waits for a move.
    send_turn_notification(link, board)
    try:
        display.prompt_move(side)
    except Exception:
        display.send(f"{side_prefix}\nEnter move:")
    return True


def _parse_color_choice(s: str) -> Optional[bool]:
    s = (s or "").strip().lower()
    if s.startswith("s1"):
        return True
    if s.startswith("s2"):
        return False
    if s.startswith("s3"):
        return bool(random.getrandbits(1))
    return None


def check_move_captures(brd: chess.Board, uci: str) -> bool:
    """
    Return True if moving side would capture something on 'to' square
    in current position, including en passant. Does not validate legality.
    """
    try:
        from_sq = chess.parse_square(uci[:2])
        to_sq = chess.parse_square(uci[2:4])
    except Exception:
        return False

    # If there's an opponent piece on 'to', that's a capture
    target = brd.piece_at(to_sq)
    if target and target.color != brd.turn:
        return True

    # En passant: pawn moves diagonally to ep square which is empty
    mover = brd.piece_at(from_sq)
    if mover and mover.piece_type == chess.PAWN and brd.ep_square == to_sq:
        # ensure diagonal direction
        if abs(chess.square_file(to_sq) - chess.square_file(from_sq)) == 1:
            return True

    return False


# -------------------- Promotion --------------------


def _move_needs_promotion(move: chess.Move, brd: chess.Board) -> bool:
    if move not in brd.legal_moves:
        return False
    piece = brd.piece_at(move.from_square)
    if piece is None or piece.piece_type != chess.PAWN:
        return False
    to_rank = chess.square_rank(move.to_square)
    if brd.turn == chess.WHITE and to_rank == 7:
        return move.promotion is None
    if brd.turn == chess.BLACK and to_rank == 0:
        return move.promotion is None
    return False


def _prompt_promotion_choice(link: BoardLink, display: Display) -> str:
    """
    Ask Pico to collect promotion choice:
      1=Queen, 2=Rook, 3=Bishop, 4=Knight  -> 'q','r','b','n'
    """
    display.send("Promotion!\n1=Queen\n2=Rook\n3=Bishop\n4=Knight")
    link.send_to_board("promotion_choice_needed")
    while True:
        msg = link.read_from_board()
        if msg is None:
            continue
        if msg.startswith("n"):
            # Signal to caller to restart mode selection via exception
            raise ReturnToMenu()
        m = msg.strip().lower()
        if m in ("btn_q", "btn_queen"):
            return "q"
        if m in ("btn_r", "btn_rook"):
            return "r"
        if m in ("btn_b", "btn_bishop"):
            return "b"
        if m in ("btn_n", "btn_knight"):
            return "n"
        display.send("Promotion!\n1=Queen\n2=Rook\n3=Bishop\n4=Knight")


# -------------------- UI helpers & engine handoff --------------------


def _show_new_game_banner(display: Display):
    display.banner("New Game", delay_s=1.0)


# -------------------- Hints & game-over --------------------


def send_move_hint(
    link: BoardLink,
    display: Display,
    ctx: EngineContext,
    state: GameState,
    cfg: GameConfig,
) -> None:
    if state.board.is_game_over():
        link.send_to_board("hint_gameover")
        display.send("Game over\nNo hints available\nOK = menu")
        return

    display.send("Engine thinking...")
    best = ctx.hint(state.board, cfg.move_time_ms)
    if not best:
        link.send_to_board("hint_none")
        return

    # Mark capture for hint if applicable
    try:
        mv = chess.Move.from_uci(best)
        is_cap = state.board.is_capture(mv)
    except Exception:
        is_cap = False

    # Send to Pico and update OLED with arrow format
    link.send_to_board(format_hint_move(best, is_cap))
    display.show_hint_result(best)
    print(f"[Hint] {best}")


def _result_to_winner_text(res: str) -> str:
    res = (res or "").strip()
    if res == "1-0":
        return "White wins"
    if res == "0-1":
        return "Black wins"
    return "Draw"


LICHESS_ANALYSIS_PGN_URL = "https://lichess.org/analysis/pgn/"
LICHESS_BASE_URL = "https://lichess.org"


def _export_game_pgn(board: "chess.Board") -> str:
    import chess.pgn

    try:
        game = chess.pgn.Game.from_board(board)
        exporter = chess.pgn.StringExporter(
            headers=False, variations=False, comments=False
        )
        # Collapse line wrapping so the QR payload is a compact, browser-safe URL.
        pgn = " ".join(game.accept(exporter).split())
    except Exception:
        return ""

    if not pgn or pgn == "*":
        return ""

    return pgn


def _build_lichess_analysis_url_from_pgn(pgn: str) -> str:
    pgn = (pgn or "").strip()
    if not pgn or pgn == "*":
        return ""
    return f"{LICHESS_ANALYSIS_PGN_URL}{quote(pgn, safe='')}"


def _extract_imported_game_url(resp: dict) -> str:
    url = str((resp or {}).get("url") or "").strip()
    if url:
        return url

    game = (resp or {}).get("game") or {}
    url = str(game.get("url") or "").strip()
    if url:
        return url

    game_id = str((resp or {}).get("id") or game.get("id") or "").strip()
    if game_id:
        return f"{LICHESS_BASE_URL}/{game_id}"

    return ""


def _build_post_game_analysis_url(pgn: str) -> Tuple[str, str]:
    from modes.online.lichess_client import import_game_pgn

    imported = import_game_pgn(pgn)
    imported_url = _extract_imported_game_url(imported)
    if imported_url:
        return imported_url, "import"

    fallback_url = _build_lichess_analysis_url_from_pgn(pgn)
    if fallback_url:
        err = str((imported or {}).get("_error") or "unknown error").strip()
        print(f"[QR ANALYSIS] import failed, using PGN URL: {err}", flush=True)
        return fallback_url, "pgn"

    return "", "none"


def notify_game_over(link: BoardLink, display: Display, brd: chess.Board) -> str:
    result = brd.result(claim_draw=True)
    winner = _result_to_winner_text(result)
    link.send_to_board(f"GameOver:{result}")
    display.send(f"Game over\n{winner}")
    return result


def offer_analysis_qr(link: BoardLink, display: Display, board: "chess.Board") -> None:
    """Generate a Lichess analysis URL for the completed game and show it as a QR code."""
    pgn = _export_game_pgn(board)

    if not pgn:
        display.send("No moves yet\nNo analysis link\nOK = back")
        link.send_to_board("MenuPaged")
        wait_for_ok(link, display)
        return

    display.send("Uploading to\nLichess...")
    analysis_url, url_source = run_in_bg(
        lambda: _build_post_game_analysis_url(pgn),
        link,
        display,
    )

    if not analysis_url:
        display.send("No analysis link\navailable\nOK = back")
        link.send_to_board("MenuPaged")
        wait_for_ok(link, display)
        return

    print(
        f"[QR ANALYSIS] source={url_source} length={len(analysis_url)} url={analysis_url!r}",
        flush=True,
    )
    display.show_qr(analysis_url)  # full-screen, no caption
    link.send_to_board("MenuPaged")
    wait_for_ok(link, display)


def post_game_menu(
    link: BoardLink, display: Display, board: "chess.Board"
) -> None:
    """Show post-game flow: OK to view analysis QR, then return to menu.

    Always raises ReturnToMenu. Call this after notify_game_over().
    """
    time.sleep(1.5)  # let the game-over display settle
    display.send("Press OK\nto view analysis")
    link.send_to_board("ChooseMode")
    link.send_to_board("only_ok_cancel")
    wait_for_ok(link, display)
    offer_analysis_qr(link, display, board)
    raise ReturnToMenu()


def _get_draw_reason(brd: chess.Board) -> Optional[str]:
    """Return a short reason string if we should auto-declare a draw.

    We auto-declare:
      - 5-fold repetition (automatic by rules)
      - 75-move rule (automatic by rules)
      - 3-fold repetition (claimable)  [QoL, like many online UIs]
      - 50-move rule (claimable)       [QoL]
    """
    try:
        if brd.is_fivefold_repetition():
            return "5-fold repetition"
        if brd.is_seventyfive_moves():
            return "75-move rule"
        if brd.can_claim_threefold_repetition():
            return "Repetition"
        if brd.can_claim_fifty_moves():
            return "50-move rule"
    except Exception:
        pass
    return None


def _check_and_handle_draw(link: BoardLink, display: Display, brd: chess.Board) -> bool:
    """If draw condition met, notify Pico + LCD and wait for new game.

    Returns True if we handled a draw and the caller should stop current game loop.
    """
    reason = _get_draw_reason(brd)
    if not reason:
        return False

    # Result string in UCI/Pico protocol format
    result = "1/2-1/2"
    link.send_to_board(f"GameOver:{result}")
    try:
        move_no = max(1, (len(brd.move_stack) + 1) // 2)
    except Exception:
        move_no = 0
    display.show_draw(reason, move_no)

    # Wait for Pico to acknowledge by sending 'n' (new game / back)
    while True:
        msg2 = link.read_from_board()
        if msg2 is None:
            continue
        if msg2 == "shutdown":
            shutdown_raspberry_pi(link, display)
            return True
        if msg2 in NEW_GAME_MSGS:
            # caller typically raises ReturnToMenu
            return True
        if msg2.startswith("typing_") or msg2 in HINT_MSGS | OK_MSGS:
            continue


# -------------------- Flow control --------------------


class ReturnToMenu(Exception):
    pass


# -------------------- Board setup guidance --------------------


def compute_board_setup_steps(fen: str):
    """Return placement steps for a physical board based on a FEN position.

    Each step is a (side_char, square, piece_symbol) tuple:
      side_char: 'w' or 'b'
      square:    'e4'
      piece_sym: like 'P', 'n', etc.

    Steps are sorted white-first, with piece priority: K Q R B N P.
    Used by both puzzle setup and ongoing-game resume to guide piece placement.
    """
    brd = chess.Board(fen)
    steps = []
    for sq in chess.SQUARES:
        p = brd.piece_at(sq)
        if not p:
            continue
        side = "w" if p.color == chess.WHITE else "b"
        steps.append((side, chess.square_name(sq), p.symbol()))

    order_pt = {
        chess.KING: 0,
        chess.QUEEN: 1,
        chess.ROOK: 2,
        chess.BISHOP: 3,
        chess.KNIGHT: 4,
        chess.PAWN: 5,
    }

    def _key(t):
        s, square, sym = t
        ptype = chess.Piece.from_symbol(sym).piece_type
        return (0 if s == "w" else 1, order_pt.get(ptype, 9), square)

    steps.sort(key=_key)
    return steps


def guide_board_setup(
    link: BoardLink,
    display: Display,
    fen: str,
    label: str = "Position",
) -> str | None:
    """Guide the user through placing pieces for a given FEN position.

    Sends puzzle_setup_begin/done and LED square hints to the Pico.
    Returns "ok" if setup completed, "skip" if skipped, or None if user backed out.
    The caller is responsible for confirming the board is EMPTY before calling.
    """
    from core.protocol import piece_name  # local import to avoid circular

    steps = compute_board_setup_steps(fen)
    try:
        link.clear_input()
    except Exception:
        pass

    link.send_to_board("hint_disable")
    link.send_to_board("puzzle_setup_begin")
    try:
        display.send(f"{label}\nSetup position\nOK=setup 1=skip")
        time.sleep(0.3)
        link.send_to_board("setup_clear")

        choice = wait_for_ok_or_skip_setup(link, display)
        if choice is None:
            return None
        if choice == "skip":
            return "skip"

        for side, sq, sym in steps:
            display.send(
                f"PLACE {'WHITE' if side == 'w' else 'BLACK'}\n"
                f"{piece_name(sym)} {sq}\nOK = next"
            )
            link.send_to_board(f"setup_place_{sq}_{side}")
            if not wait_for_ok(link, display):
                return None

        display.send(f"{label}\nSetup done!")
        time.sleep(0.5)
        return "ok"
    finally:
        link.send_to_board("hint_enable")
        link.send_to_board("puzzle_setup_done")
        try:
            link.clear_input()
        except Exception:
            pass


# -------------------- Setup & mode selection --------------------


_TOP_MENU_OPTIONS: List[Tuple[str, Optional[str]]] = [
    ("Play Chess!", "play"),
    ("Puzzles", "puzzle"),
    ("Studies", "studies"),
    ("Settings", "settings"),
]

_PLAY_CHESS_MENU_OPTIONS: List[Tuple[str, Optional[str]]] = [
    ("Against PC", "stockfish"),
    ("Local 2-player", "local"),
    ("Lichess Online", "online"),
]


# -------------------- Settings menu --------------------


def _configure_brightness(link: BoardLink, display: Display, cfg: GameConfig) -> bool:
    """Show the 1-8 brightness picker and apply the chosen level.

    Returns to the settings menu on cancel. After a confirmed brightness change,
    the Pico reboots and the caller should return to the main menu flow.
    """
    def _parse_brightness_msg(msg: str) -> Optional[int]:
        if not msg.startswith("brightness_"):
            return None
        try:
            return max(1, min(int(msg.split("_")[-1]), 8))
        except Exception:
            return None

    def _sync_brightness_control(*, wake: bool) -> None:
        if wake:
            link.send_to_board("ChooseMode")
        link.send_to_board("BrightnessControl")

    display.send("Loading...")
    link.clear_input()
    _sync_brightness_control(wake=False)

    current: Optional[int] = None
    started = time.monotonic()
    last_sync = started
    wake_after = started + 3.0
    deadline = started + 10.0
    while current is None:
        msg = link.read_from_board()
        if msg is not None:
            m = msg.strip().lower()
            level = _parse_brightness_msg(m)
            if level is not None:
                current = level
                cfg.brightness = level
                break
            if m in OK_MSGS | NEW_GAME_MSGS | HINT_MSGS:
                continue

        now = time.monotonic()
        if now >= deadline:
            display.send("Brightness menu\nnot responding")
            time.sleep(1.5)
            return False
        if now - last_sync >= 2.5:
            _sync_brightness_control(wake=now >= wake_after)
            last_sync = now

    display.send(
        f"LED Brightness\n1=dim  8=bright\nCurrent: {current}\nOK = cancel"
    )

    while True:
        msg = link.read_from_board()
        if msg is None:
            continue
        m = msg.strip().lower()
        level = _parse_brightness_msg(m)
        if level is not None:
            cfg.brightness = level
            display.send(
                f"LED Brightness\n1=dim  8=bright\nCurrent: {cfg.brightness}\nOK = cancel"
            )
            continue
        if m in OK_MSGS or m.startswith("n"):
            return False
        if m.isdigit():
            val = max(1, min(int(m), 8))
            display.send(f"New brightness: {val}\nReloading...")
            link.send_to_board(f"SetBrightness_{val}")
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                ack = link.read_from_board()
                if ack is None:
                    continue
                ack_msg = ack.strip().lower()
                level = _parse_brightness_msg(ack_msg)
                if level is not None:
                    cfg.brightness = level
                    continue
                if ack_msg.startswith("brightness_set_"):
                    try:
                        applied = max(1, min(int(ack_msg.split("_")[-1]), 8))
                    except Exception:
                        applied = val
                    cfg.brightness = applied
                    time.sleep(0.5)
                    return True
                if ack_msg in OK_MSGS | NEW_GAME_MSGS | HINT_MSGS:
                    continue

            cfg.brightness = val
            time.sleep(0.5)
            return True


def _run_settings_menu(link: BoardLink, display: Display, cfg: GameConfig) -> None:
    """Paged settings submenu."""
    while True:
        choice = _paged_menu(
            link,
            display,
            ["Brightness", "Update"],
            resend_timeout=3.0,
        )
        if choice is None:
            return
        if choice == "Brightness":
            if _configure_brightness(link, display, cfg):
                return
            continue
        if choice == "Update":
            _run_update(link, display)
            return


def _configure_vs_computer(link: BoardLink, display: Display, cfg: GameConfig) -> None:
    """
    DIY-like setup flow:
      - Difficulty (skill)
      - Player color
    Move time is fixed at 2s — difficulty is controlled by the level's depth
    cap and blunder chance instead.
    """
    display.send("vs Computer\nHints enabled")
    time.sleep(0.5)

    # Difficulty
    display.send("Difficulty level:\n1 to 8\nOK = cancel")
    link.send_to_board("EngineStrength")
    link.send_to_board(f"default_strength_{cfg.skill_level}")
    while True:
        msg = link.read_from_board()
        if msg is None:
            continue
        if msg in OK_MSGS or msg.startswith("n"):
            raise ReturnToMenu()
        if msg.isdigit():
            cfg.skill_level = max(1, min(int(msg), 8))
            break

    cfg.move_time_ms = 2000  # used for hint calculations

    # Color
    display.send("Select a colour:\n1=White 2=Black\n3=Random\nOK = cancel")
    link.send_to_board("PlayerColor")
    while True:
        msg = link.read_from_board()
        if msg is None:
            continue
        if msg in OK_MSGS or msg.startswith("n"):
            raise ReturnToMenu()
        side = _parse_color_choice(msg)
        if side is not None:
            cfg.human_is_white = side
            break


def _configure_local_game(link: BoardLink, display: Display, cfg: GameConfig) -> None:
    display.send("Local 2-player\nHints enabled")
    time.sleep(0.5)
    cfg.skill_level = 8  # max hint skill for local
    cfg.move_time_ms = 1  # fastest think time for local


def prompt_next_turn(
    link: BoardLink,
    display: Display,
    brd: chess.Board,
    mode: str,
    cfg: GameConfig,
    last_uci: str,
) -> None:
    """Update the display and Pico after a move has been pushed.

    For human-to-move situations: sends turn_{color} to the Pico and shows
    the last-move arrow with the side label. For engine-to-move (stockfish
    mode only): shows the last-move arrow with "ENGINE thinking" so the
    player knows to wait.
    """
    print(brd)

    human_to_move = mode == "local" or (
        mode == "stockfish"
        and (
            (brd.turn == chess.WHITE and cfg.human_is_white)
            or (brd.turn == chess.BLACK and not cfg.human_is_white)
        )
    )
    if human_to_move:
        send_turn_notification(link, brd)
        promo_letter = (
            last_uci[4].lower()
            if isinstance(last_uci, str) and len(last_uci) >= 5
            else ""
        )
        promo_line = (
            f"{display.format_promo_line(promo_letter)}\n"
            if promo_letter in ("q", "r", "b", "n")
            else ""
        )

        display.show_arrow(
            last_uci,
            suffix=f"{promo_line}{'WHITE' if brd.turn == chess.WHITE else 'BLACK'} to move",
            force=True,
        )
    else:
        display.show_arrow(last_uci, suffix="Engine thinking", force=True)


# -------------------- Typing preview --------------------


def _format_piece_name(piece: "chess.Piece") -> str:
    """Return a short label like 'White Pawn' suitable for a small LCD."""
    try:
        color = "White" if piece.color == chess.WHITE else "Black"
        p = {
            chess.PAWN: "Pawn",
            chess.KNIGHT: "Knight",
            chess.BISHOP: "Bishop",
            chess.ROOK: "Rook",
            chess.QUEEN: "Queen",
            chess.KING: "King",
        }.get(piece.piece_type, "Piece")
        return f"{color} {p}"
    except Exception:
        return "Piece"


def _is_valid_square(s: str) -> bool:
    if len(s) != 2:
        return False
    f, r = s[0].lower(), s[1]
    return f in "abcdefgh" and r in "12345678"


def _get_piece_label(board: Optional["chess.Board"], sq: str) -> Optional[str]:
    """Return a label for the piece currently on sq, or None if not resolvable."""
    if board is None or not _is_valid_square(sq):
        return None
    try:
        piece = board.piece_at(chess.parse_square(sq))
        if piece is None:
            return "Empty"
        return _format_piece_name(piece)
    except Exception:
        return None


def _update_typing_display(
    display: Display, payload: str, board: Optional["chess.Board"] = None
) -> None:
    """
    payload is the '<after heypityping_...>' part, e.g.:
      'from_e'
      'to_e2 → e'
      'confirm_e2 → e4'
    Displays short contextual prompts.
    """
    try:
        # label, text
        parts = payload.split("_", 1)
        if len(parts) != 2:
            return
        label, text = parts[0], parts[1]
        label = label.lower()
        if label == "from":
            # When a full square is entered (e.g. e2), show which piece is on that square.
            # If the user deletes back to 0/1 chars, we revert to the generic prompt.
            if _is_valid_square(text):
                piece_lbl = _get_piece_label(board, text)
                if piece_lbl:
                    display.send(f"{piece_lbl}\n{text} →\nEnter to:")
                else:
                    display.send("Enter from:\n" + text)
            else:
                display.send("Enter from:\n" + text)

        elif label == "to":
            # text format: "e2 → e" (partial) or "e2 → e4"
            frm = ""
            partial_to = text
            if "→" in text:
                left, right = text.split("→", 1)
                frm = left.strip()
                partial_to = right.strip()
            piece_lbl = _get_piece_label(board, frm)
            if piece_lbl:
                display.send(f"{piece_lbl}\n{frm} → {partial_to}")
            else:
                display.send("Enter to:\n" + text)

        elif label == "confirm":
            # text format: "e2 → e4"
            frm = ""
            to = ""
            if "→" in text:
                left, right = text.split("→", 1)
                frm = left.strip()
                to = right.strip()
            piece_lbl = _get_piece_label(board, frm)
            if piece_lbl:
                display.send(f"{piece_lbl}\n{frm} → {to}\nOK to send")
            else:
                display.send("Confirm move:\n" + text + "\nPress OK or re-enter")
    except Exception:
        # swallow malformed previews quietly
        pass


# -------------------- Human move processing (extracted) --------------------


# -------------------- Unified play loop --------------------


def _run_local_game(
    link: BoardLink,
    display: Display,
    ctx: EngineContext,
    state: GameState,
    cfg: GameConfig,
) -> None:
    """Local 2-player game loop.

    Both sides are human; the engine context is kept available for hints only.
    """
    state.board = chess.Board()
    try:
        link.clear_input()
    except Exception:
        pass
    link.send_to_board("GameStart")
    _show_new_game_banner(display)
    time.sleep(0.7)

    link.send_to_board("turn_white")
    display.prompt_move("WHITE")

    while True:
        msg = link.read_from_board()
        if msg is None:
            continue

        if msg == "shutdown":
            shutdown_raspberry_pi(link, display)
            return

        # Actionable signals — must be checked before the ignore filter below
        if msg in NEW_GAME_MSGS:
            if confirm_exit_game(link, display):
                raise ReturnToMenu()
            side = "WHITE" if state.board.turn == chess.WHITE else "BLACK"
            send_turn_notification(link, state.board)
            display.prompt_move(side, force=True)
            continue

        if msg in HINT_MSGS:
            send_move_hint(link, display, ctx, state, cfg)
            continue

        if msg in OK_MSGS:
            side = "WHITE" if state.board.turn == chess.WHITE else "BLACK"
            send_turn_notification(link, state.board)
            display.prompt_move(side, force=True)
            continue

        # Silently ignore lingering navigation/system messages
        if msg == "menu_ready" or msg in {"draw", "btn_draw"}:
            continue

        if handle_capq_message(link, state.board, msg):
            continue

        if msg.startswith("typing_"):
            handle_typing_message(link, display, msg[len("typing_") :], state.board)
            continue

        uci = parse_uci_move(msg)
        if not uci:
            link.send_to_board(f"error_invalid_{msg}")
            display.show_invalid(msg)
            continue

        move = validate_and_push_move(
            link=link, display=display, board=state.board, uci=uci
        )
        if move is None:
            continue

        if _check_and_handle_draw(link, display, state.board):
            raise ReturnToMenu()

        if state.board.is_game_over():
            notify_game_over(link, display, state.board)
            post_game_menu(link, display, state.board)
            # unreachable — post_game_menu always raises ReturnToMenu
        else:
            prompt_next_turn(
                link, display, state.board, state.mode, cfg, chess.Move.uci(move)
            )


# -------------------- Online placeholder --------------------


def _run_online_game(link: BoardLink, display: Display, cfg: GameConfig) -> None:
    from modes.online.online_controller import OnlineController

    OnlineController(link, display, cfg).run()


# ── Puzzle phase tags (lichess.org/training/themes) ──────────────────────────
# Each entry is (lichess_tag, display_label).
PHASE_THEMES: List[Tuple[str, str]] = [
    ("opening", "Opening"),
    ("middlegame", "Middlegame"),
    ("endgame", "Endgame"),
    ("rookEndgame", "Rook endgame"),
    ("bishopEndgame", "Bishop endgame"),
    ("pawnEndgame", "Pawn endgame"),
    ("knightEndgame", "Knight endgame"),
    ("queenEndgame", "Queen endgame"),
]

# Opening names from lichess.org/training/openings, grouped alphabetically.
# Used by /api/puzzle/next?angle=<opening_name>.
OPENING_GROUPS: List[Tuple[str, List[str]]] = [
    (
        "A to E",
        [
            "Alekhine Defense",
            "Amar Opening",
            "Amazon Attack",
            "Anderssen's Opening",
            "Barnes Defense",
            "Barnes Opening",
            "Benko Gambit",
            "Benko Gambit Accepted",
            "Benko Gambit Declined",
            "Benoni Defense",
            "Bird Opening",
            "Bishop's Opening",
            "Blackmar Gambit",
            "Blackmar Gambit Accepted",
            "Blackmar Gambit Declined",
            "Blumenfeld Countergambit",
            "Bogo-Indian Defense",
            "Borg Defense",
            "Canard Opening",
            "Caro-Kann Defense",
            "Carr Defense",
            "Catalan Opening",
            "Center Game",
            "Center Counter",
            "Clemenz Opening",
            "Czech Defense",
            "Danish Gambit",
            "Danish Gambit Accepted",
            "Danish Gambit Declined",
            "Dutch Defense",
            "East Indian Defense",
            "Elephant Gambit",
            "English Defense",
            "English Opening",
            "Englund Gambit",
            "Englund Gambit Declined",
        ],
    ),
    (
        "F to I",
        [
            "French Defense",
            "Fried Fox Defense",
            "Goldsmith Defense",
            "Grob Opening",
            "Grunfeld Defense",
            "Gunderam Defense",
            "Hippopotamus Defense",
            "Horwitz Defense",
            "Hungarian Opening",
            "Indian Defense",
            "Italian Game",
        ],
    ),
    (
        "K to N",
        [
            "Kangaroo Defense",
            "King's Gambit",
            "King's Gambit Accepted",
            "King's Gambit Declined",
            "King's Indian Attack",
            "King's Indian Defense",
            "King's Knight Opening",
            "King's Pawn Game",
            "King's Pawn Opening",
            "Kadas Opening",
            "Lasker Simul Special",
            "Latvian Gambit",
            "Latvian Gambit Accepted",
            "Lemming Defense",
            "Lion Defense",
            "London System",
            "Mexican Defense",
            "Mieses Opening",
            "Mikenas Defense",
            "Modern Defense",
            "Neo-Grunfeld Defense",
            "Nimzo-Indian Defense",
            "Nimzo-Larsen Attack",
            "Nimzowitsch Defense",
        ],
    ),
    (
        "O to R",
        [
            "Old Indian Defense",
            "Owen Defense",
            "Paleface Attack",
            "Petrov's Defense",
            "Philidor Defense",
            "Pirc Defense",
            "Polish Defense",
            "Polish Opening",
            "Ponziani Opening",
            "Portuguese Defense",
            "Pseudo-Queen's Indian Defense",
            "Pterodactyl Defense",
            "Queen's Gambit",
            "Queen's Gambit Accepted",
            "Queen's Gambit Declined",
            "Queen's Indian Accelerated",
            "Queen's Indian Defense",
            "Queen's Pawn Game",
            "Rapport-Jobava System",
            "Rat Defense",
            "Richter-Veresov Attack",
            "Robatsch Defense",
            "Rubinstein Opening",
            "Ruy Lopez",
            "Réti Opening",
        ],
    ),
    (
        "S to V",
        [
            "Saragossa Opening",
            "Scandinavian Defense",
            "Scotch Game",
            "Semi-Slav Defense",
            "Sicilian Defense",
            "Slav Defense",
            "Slav Indian",
            "Sodium Attack",
            "St. George Defense",
            "Tarrasch Defense",
            "Three Knights Game",
            "Torre Attack",
            "Trompowsky Attack",
            "Van Geet Opening",
            "Van't Kruijs Opening",
            "Vienna Gambit",
            "Vienna Game",
        ],
    ),
    (
        "W to Z",
        [
            "Wade Defense",
            "Ware Defense",
            "Ware Opening",
            "Yusupov-Rubinstein System",
            "Zukertort Opening",
        ],
    ),
]


def _render_paged_menu(
    page: int,
    pages: int,
    items: List[str],
    *,
    can_back: bool,
    per_page: int = 3,
) -> str:
    """Format a paged menu for the graphical LCD display.

    The display server handles uppercase and word-wrap, so we just pass
    clean text without character-count constraints.
    """

    def _fmt(i: int, s: str) -> str:
        return f"{i}) {(s or '').strip()}"

    lines = [_fmt(i + 1, opt) for i, opt in enumerate(items[:per_page])]
    if lines and pages > 1:
        lines[0] = f"{lines[0]} {page + 1}/{pages}"

    while len(lines) < 3:
        lines.append("")

    has_next = (page + 1) < pages
    if can_back and has_next:
        footer = "OK=Back  Hint=Next"
    elif can_back:
        footer = "OK=Back"
    elif has_next:
        footer = "Hint=Next"
    else:
        footer = ""
    lines.append(footer)
    return "\n".join(lines)


def _paged_menu(
    link: BoardLink,
    display: Display,
    options: List[str],
    *,
    can_back: bool = True,
    wake_command: Optional[str] = None,
    resend_timeout: Optional[float] = None,
    per_page: int = 3,
) -> Optional[str]:
    """Show a scrollable menu and return the user's selection.

    ``wake_command`` is used by startup/setup menus that need to recover from a
    Pico reboot before reopening the paged menu UI.
    """
    opts = list(options or [])
    if not opts:
        return None

    pages = (len(opts) + per_page - 1) // per_page
    page = 0
    last_sync = 0.0
    menu_ready = False

    def _sync_menu() -> None:
        nonlocal last_sync, menu_ready
        if wake_command:
            link.send_to_board(wake_command)
        has_hint = 1 if pages > 1 else 0
        has_back = 1 if can_back else 0
        link.send_to_board(f"MenuPaged_{has_hint}_{has_back}")
        last_sync = time.monotonic()
        menu_ready = False

    link.clear_input()
    _sync_menu()

    while True:
        chunk = opts[page * per_page : page * per_page + per_page]
        display.send(
            _render_paged_menu(
                page,
                pages,
                chunk,
                can_back=can_back,
                per_page=per_page,
            ),
            size="menu",
        )
        msg = link.read_from_board()
        if msg is None:
            if (
                not menu_ready
                and resend_timeout
                and time.monotonic() - last_sync >= resend_timeout
            ):
                _sync_menu()
            continue

        m = msg.strip().lower()
        if m == "menu_ready":
            menu_ready = True
            last_sync = time.monotonic()
            continue
        if not menu_ready:
            continue

        last_sync = time.monotonic()
        if m in OK_MSGS | NEW_GAME_MSGS:
            if can_back:
                return None
            continue
        if m in HINT_MSGS:
            page = (page + 1) % pages
            _sync_menu()
            continue
        if m in ("1", "2", "3", "4"):
            idx = int(m) - 1
            if idx < len(chunk) and chunk[idx]:
                return chunk[idx]
            continue


def wait_for_mode_selection(
    link: BoardLink, display: Display, state: GameState, cfg: GameConfig
) -> str:
    del state
    while True:
        top_choice = _paged_menu(
            link,
            display,
            [label for label, _ in _TOP_MENU_OPTIONS],
            can_back=False,
            wake_command="ChooseMode",
            resend_timeout=3.0,
        )
        if top_choice is None:
            continue
        if top_choice == "Settings":
            _run_settings_menu(link, display, cfg)
            continue

        top_action = next(
            (mode for label, mode in _TOP_MENU_OPTIONS if label == top_choice),
            None,
        )
        if top_action == "puzzle":
            return "puzzle"
        if top_action == "studies":
            return "studies"
        if top_action != "play":
            continue

        play_choice = _paged_menu(
            link,
            display,
            [label for label, _ in _PLAY_CHESS_MENU_OPTIONS],
            wake_command="ChooseMode",
            resend_timeout=3.0,
        )
        if play_choice is None:
            continue

        selected_mode = next(
            (mode for label, mode in _PLAY_CHESS_MENU_OPTIONS if label == play_choice),
            None,
        )
        if selected_mode:
            return selected_mode


def _git_head(repo: Path) -> Optional[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _restart_smartchess_service(display: Display, message: str) -> None:
    display.send(message)
    time.sleep(1)
    subprocess.Popen(["sudo", "systemctl", "restart", "smartChess.service"])


def _run_update(link: BoardLink, display: Display) -> None:
    """git pull on the Pi, upload new Pico firmware only if needed, then restart."""
    import base64

    repo = Path(__file__).resolve().parent.parent.parent
    pico_dir = repo / "PicoCode" / "main"
    pico_files = [
        pico_dir / "main.py",
        pico_dir / "pico_hw.py",
    ]

    def _pico_signature() -> tuple[tuple[str, Optional[str]], ...]:
        return tuple(
            (path.name, _file_sha256(path) if path.exists() else None) for path in pico_files
        )

    try:
        before_head = _git_head(repo)
        before_pico_hash = _pico_signature()
    except Exception as exc:
        display.send(f"Update error\n{exc.__class__.__name__}")
        time.sleep(3)
        return

    display.send("Checking for\nupdates...")
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "pull"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:
        display.send(f"Git error:\n{exc}")
        time.sleep(3)
        return

    if result.returncode != 0:
        print(
            f"[UPDATE] git pull failed:\nstdout={result.stdout}\nstderr={result.stderr}",
            flush=True,
        )
        display.send("Git pull failed\nSee logs")
        time.sleep(3)
        return

    try:
        after_head = _git_head(repo)
        after_pico_hash = _pico_signature()
    except Exception as exc:
        display.send(f"Update error\n{exc.__class__.__name__}")
        time.sleep(3)
        return

    combined_output = f"{result.stdout}\n{result.stderr}"
    repo_changed = (
        before_head is not None and after_head is not None and before_head != after_head
    )
    already_up_to_date = (
        "Already up to date." in combined_output
        or "Already up-to-date." in combined_output
    )
    if not repo_changed and not already_up_to_date:
        repo_changed = bool(result.stdout.strip() or result.stderr.strip())

    if not repo_changed:
        display.send("Already up\nto date!")
        time.sleep(2)
        return

    if before_pico_hash == after_pico_hash:
        _restart_smartchess_service(display, "Pi updated\nRestarting...")
        return

    def _sync_update_mode() -> None:
        link.send_to_board("ChooseMode")
        link.send_to_board("UpdateMode")

    display.send("Uploading to\nPico...")
    link.clear_input()
    _sync_update_mode()

    deadline = time.monotonic() + 15
    last_sync = time.monotonic()
    while True:
        msg = link.read_from_board()
        if msg == "updateready":
            break
        if time.monotonic() - last_sync >= 3.0:
            _sync_update_mode()
            last_sync = time.monotonic()
        if time.monotonic() > deadline:
            display.send("Pico timeout\nAbort.")
            link.send_to_board("UpdateAbort")
            time.sleep(2)
            return

    # Conservative timing to avoid overflowing the Pico's UART receive
    # buffer during GC pauses and flash sector-erase operations.
    chunk_size = 32
    chunk_delay = 0.12
    for pico_file in [path for path in pico_files if path.exists()]:
        encoded = base64.b64encode(pico_file.read_bytes()).decode()
        link.send_to_board(f"UpdateFile_{pico_file.name}")
        time.sleep(chunk_delay)
        for i in range(0, len(encoded), chunk_size):
            link.send_to_board(f"UpdateChunk_{encoded[i:i + chunk_size]}")
            time.sleep(chunk_delay)
        link.send_to_board("UpdateFileDone")
        time.sleep(chunk_delay)

    link.send_to_board("UpdateDone")
    display.send("Waiting for\nPico...")

    deadline = time.time() + 30
    while True:
        msg = link.read_from_board()
        if msg == "updatecomplete":
            break
        if msg and msg.startswith("updateerror"):
            reason = "failed"
            if "_" in msg:
                reason = msg.split("_", 1)[1] or reason
            display.send(f"Pico update\n{reason[:16]}")
            time.sleep(3)
            return
        if time.time() > deadline:
            display.send("Pico timeout!")
            time.sleep(2)
            break

    _restart_smartchess_service(display, "Update done!\nRestarting...")


def _run_study_mode(link: BoardLink, display: Display) -> None:
    """Study mode: select a Lichess study and play through its chapters."""
    from modes.online.lichess_client import LichessClient
    from modes.studies.study_controller import StudyController, load_studies

    try:
        client = LichessClient()
    except RuntimeError as e:
        display.send(f"Lichess error\n{str(e)[:20]}\nOK = back")
        wait_for_ok(link, display)
        raise ReturnToMenu()

    studies = load_studies()
    if not studies:
        display.send("No studies found\nAdd IDs to\nstudies.txt\nOK = back")
        wait_for_ok(link, display)
        raise ReturnToMenu()

    while True:
        study_names = [name for _, name in studies]
        choice = _paged_menu(link, display, study_names)
        if choice is None:
            raise ReturnToMenu()

        study = next((s for s in studies if s[1] == choice), None)
        if not study:
            continue

        study_id, study_name = study
        ctrl = StudyController(client, study_id, study_name)
        ctrl.run(link, display)
        # After run() returns (user pressed Back from chapter list), loop to study selection


def _run_puzzle_game(link: BoardLink, display: Display) -> None:
    """Puzzle mode: show a submenu then launch the selected puzzle type."""
    display.send("Loading...")
    from modes.online.lichess_client import LichessClient
    from modes.puzzles.puzzle_controller import PuzzleController

    client = LichessClient()

    def menu(options: List[str]) -> Optional[str]:
        return _paged_menu(link, display, options)

    while True:
        top = menu(["Daily Puzzle", "Mix and match", "Themes"])
        if top is None:
            raise ReturnToMenu()

        if top.startswith("Daily"):
            PuzzleController(client, mode="daily").run(link, display)
            return

        if top.startswith("Mix"):
            PuzzleController(client, mode="mix").run(link, display)
            return

        if not top.startswith("Themes"):
            continue

        while True:
            themes_top = menu(["Phases", "Openings"])
            if themes_top is None:
                break

            if themes_top.startswith("Phases"):
                label = menu([v for _, v in PHASE_THEMES])
                if label is None:
                    continue
                tag = next((k for k, v in PHASE_THEMES if v == label), None)
                if not tag:
                    continue
                PuzzleController(client, mode="theme", theme=tag, theme_label=label).run(
                    link, display
                )
                return

            if themes_top.startswith("Openings"):
                grp = menu([g for g, _ in OPENING_GROUPS])
                if grp is None:
                    continue
                opts = next((items for g, items in OPENING_GROUPS if g == grp), None)
                if not opts:
                    continue
                label = menu(opts)
                if label is None:
                    continue
                PuzzleController(
                    client, mode="theme", theme=label, theme_label=label
                ).run(link, display)
                return


def run_selected_mode(
    link: BoardLink,
    display: Display,
    ctx: EngineContext,
    state: GameState,
    cfg: GameConfig,
) -> None:
    """Dispatch to the correct game loop based on state.mode."""
    display.send("Loading...")
    if state.mode in ("stockfish", "pc", "btn_mode_pc", "vs_computer", "vs"):
        _configure_vs_computer(link, display, cfg)
        link.send_to_board("SetupComplete")
        display.send("Engine loading...")
        ctx.ensure()

        from modes.vs_computer.game_controller import GameController, GameDeps
        from modes.vs_computer.stockfish_opponent import StockfishOpponent

        opponent = StockfishOpponent(
            ctx,
            move_time_ms=cfg.move_time_ms,
            skill_level=cfg.skill_level,
        )
        controller = GameController(
            GameDeps(link=link, display=display, opponent=opponent),
            cfg=cfg,
        )
        controller.run_stockfish_game(move_time_ms=cfg.move_time_ms)

    elif state.mode in ("local", "btn_mode_local", "local_2p"):
        _configure_local_game(link, display, cfg)
        link.send_to_board("SetupComplete")
        _run_local_game(link, display, ctx, state, cfg)

    elif state.mode in ("puzzle", "puzzles", "btn_mode_puzzle", "btn_mode_puzzles"):
        link.send_to_board("SetupComplete")
        _run_puzzle_game(link, display)
        raise ReturnToMenu()

    elif state.mode in ("studies", "study"):
        link.send_to_board("SetupComplete")
        _run_study_mode(link, display)
        raise ReturnToMenu()

    elif state.mode == "online":
        _run_online_game(link, display, cfg)

    else:
        print(f"[MODE DISPATCH] unknown mode={state.mode!r}", flush=True)
        try:
            link.send_to_board("error_unknown_mode")
        except Exception:
            pass
        display.send("Unknown mode\n" + str(state.mode)[:18] + "\nOK=menu")
        while True:
            msg = link.read_from_board()
            if msg is None:
                continue
            if msg.strip().lower() in IGNORED_MSGS:
                raise ReturnToMenu()


# -------------------- Shutdown --------------------


def shutdown_raspberry_pi(link: BoardLink, display: Display) -> None:
    if display:
        display.send("Shutting Down...", force=True)
    time.sleep(0.5)

    commands = [
        ["sudo", "systemctl", "poweroff", "--no-wall"],
        ["sudo", "shutdown", "-h", "now"],
    ]

    for cmd in commands:
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            raise SystemExit(0)
        except SystemExit:
            raise
        except Exception as e:
            print(f"[Shutdown] {' '.join(cmd)} failed: {e}", file=sys.stderr)

    raise SystemExit(1)
