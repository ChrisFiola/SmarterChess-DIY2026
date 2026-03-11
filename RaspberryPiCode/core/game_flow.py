# -*- coding: utf-8 -*-
"""
Game flow, parsing, setup, and unified play loop (modular version).
Preserves Pico<->Pi UART protocol strings and display behavior.
"""
from __future__ import annotations

import random
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Optional

import chess

from screen.display import Display
from core.boardlink import BoardLink
from core.engine import EngineContext

# Phase 1: daily puzzle controller
from modes.online.lichess_client import LichessClient
from core.protocol import (
    send_lcd_ack_for_payload,
    parse_uci_move,
    piece_name,
    NEW_GAME_MSGS,
    OK_MSGS,
    HINT_MSGS,
    IGNORED_MSGS,
)

# -------------------- Data classes --------------------


@dataclass
class GameConfig:
    skill_level: int = 5
    move_time_ms: int = 2000
    human_is_white: bool = True


@dataclass
class GameState:
    board: chess.Board
    mode: str = "stockfish"  # "stockfish" | "local" | "online" | "puzzle"


# -------------------- Parsing & helpers --------------------


def wait_for_ok(link: BoardLink, display: Display) -> bool:
    """Wait for Pico OK acknowledgement (btn_ok/ok).

    Returns False if the user exits to menu (new game) or shutdown is triggered.
    """
    while True:
        m = link.read_from_board()
        if m is None:
            continue

        if m == "shutdown":
            shutdown_raspberry_pi(link, display)
            return False

        if m in NEW_GAME_MSGS:
            return False

        if m in ("btn_ok", "ok"):
            return True

        # ignore chatter
        if m.startswith("typing_") or m.startswith("capq_") or m in HINT_MSGS:
            continue


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
    link.send_to_board(f"capr_{1 if cap else 0}")
    return True


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
    try:
        if board.is_check():
            ksq = board.king(board.turn)
            if ksq is not None:
                link.send_to_board(f"check_{chess.square_name(ksq)}")
    except Exception:
        pass

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

    ok = wait_for_ok(link, display)
    if not ok:
        return False

    # Deterministic re-entry: Pi commands turn_ and then waits for a move.
    link.send_to_board(f"turn_{'white' if board.turn == chess.WHITE else 'black'}")
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
    display.banner("NEW GAME", delay_s=1.0)


def _show_engine_thinking(display: Display):
    display.send("Engine Thinking...")


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
        display.send("Game Over\nNo hints\nPress n to start over")
        return

    _show_engine_thinking(display)
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
    link.send_to_board(f"hint_{best}{'_cap' if is_cap else ''}")
    display.show_hint_result(best)
    print(f"[Hint] {best}")


def _result_to_winner_text(res: str) -> str:
    res = (res or "").strip()
    if res == "1-0":
        return "White wins"
    if res == "0-1":
        return "Black wins"
    return "Draw"


def notify_game_over(link: BoardLink, display: Display, brd: chess.Board) -> str:
    result = brd.result(claim_draw=True)
    winner = _result_to_winner_text(result)
    link.send_to_board(f"GameOver:{result}")
    display.send(f"GAME OVER\n{winner}\nStart new game?")
    return result


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
        if msg2.startswith("typing_") or msg2 in ("hint", "btn_hint", "btn_ok", "ok"):
            continue


# -------------------- Flow control --------------------


class ReturnToMenu(Exception):
    pass


# -------------------- Setup & mode selection --------------------


def wait_for_mode_selection(link: BoardLink, display: Display, state: GameState) -> str:
    link.send_to_board("ChooseMode")
    display.send(
        "Choose mode:\n1) Against PC\n2) Lichess Online\n3) Local 2-player\n4) Puzzles"
    )
    while True:
        msg = link.read_from_board()
        if msg is None:
            continue
        # Debug for mode-select mismatches (view in journalctl)
        # Helps diagnose when the Pico sends an unexpected token.
        print(f"[MODE SELECT] raw={msg!r}", flush=True)
        m = msg.strip().lower()

        # Robustness: the Pico can emit control / navigation tokens (e.g. OK+HINT
        # sends 'n' to request a return to the main menu). If we treat those as
        # "unknown mode" we end up replacing the menu on the LCD with an error
        # screen even though we're already *in* the main menu.
        #
        # In mode-select, simply ignore non-selection tokens.
        if not m or m in IGNORED_MSGS or m.startswith("typing_"):
            continue

        if m in ("1", "stockfish", "pc", "btn_mode_pc"):
            return "stockfish"
        if m in ("2", "onlinehuman", "remote", "online", "btn_mode_online"):
            return "online"
        if m in ("3", "local", "human", "btn_mode_local"):
            return "local"
        # Puzzles: accept both historical token variants (singular/plural)
        # because the Pico menu firmware has used both.
        if m in (
            "4",
            "puzzle",
            "puzzles",
            "daily",
            "btn_mode_puzzle",
            "btn_mode_puzzles",
        ):
            return "puzzle"
        link.send_to_board("error_unknown_mode")
        display.send("Unknown mode\n" + m + "\nSend again")


def _configure_vs_computer(link: BoardLink, display: Display, cfg: GameConfig) -> None:
    """
    DIY-like setup flow:
      - Difficulty (skill)
      - Move time
      - Player color
    All values sent back to Pico unchanged (protocol preserved).
    """
    display.send("VS Computer\nHints enabled")
    time.sleep(2)

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
            cfg.skill_level = max(0, min(int(msg), 20))
            break

    # Move time
    display.send("Computer\nmove time:\n1 to 8\nOK = cancel")
    link.send_to_board("TimeControl")
    link.send_to_board(f"default_time_{cfg.move_time_ms}")
    while True:
        msg = link.read_from_board()
        if msg is None:
            continue
        if msg in OK_MSGS or msg.startswith("n"):
            raise ReturnToMenu()
        if msg.isdigit():
            cfg.move_time_ms = max(10, int(msg))
            break

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
    display.send("Local 2-Player\nHints enabled")
    time.sleep(2)
    cfg.skill_level = 20  # max hint skill for local
    cfg.move_time_ms = 1  # fastest think time for local


def prompt_next_turn(
    link: BoardLink,
    display: Display,
    brd: chess.Board,
    mode: str,
    cfg: GameConfig,
    last_uci: str,
):
    print(brd)

    human_to_move = mode == "local" or (
        mode == "stockfish"
        and (
            (brd.turn == chess.WHITE and cfg.human_is_white)
            or (brd.turn == chess.BLACK and not cfg.human_is_white)
        )
    )
    if human_to_move:
        link.send_to_board(f"turn_{'white' if brd.turn == chess.WHITE else 'black'}")
        promo_letter = (
            last_uci[4].lower()
            if isinstance(last_uci, str) and len(last_uci) >= 5
            else ""
        )
        promo_line = ""
        if promo_letter in ("q", "r", "b", "n"):
            promo_name = (
                display.promo_name(promo_letter)
                if hasattr(display, "promo_name")
                else promo_letter.upper()
            )
            promo_line = f"Promoted to {promo_name}\n"

        display.show_arrow(
            last_uci,
            suffix=f"{promo_line}{'WHITE' if brd.turn == chess.WHITE else 'BLACK'} to move",
            force=True,
        )
    else:
        display.show_arrow(last_uci, suffix="ENGINE thinking", force=True)


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


def apply_human_move(
    *, link: BoardLink, display: Display, board: chess.Board, uci: str
) -> None:
    """Validate, handle promotion, push, and report/handoff."""
    move = validate_and_push_move(link=link, display=display, board=board, uci=uci)
    if move is None:
        return

    if board.is_game_over():
        notify_game_over(link, display, board)
        return

    dummy_cfg = GameConfig(skill_level=5, move_time_ms=2000, human_is_white=True)
    prompt_next_turn(link, display, board, "local", dummy_cfg, chess.Move.uci(move))


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
    link.send_to_board("GameStart")
    _show_new_game_banner(display)
    time.sleep(0.3)

    link.send_to_board("turn_white")
    display.prompt_move("WHITE")

    while True:
        msg = link.read_from_board()
        if msg is None:
            continue

        if msg == "shutdown":
            shutdown_raspberry_pi(link, display)
            return

        if handle_capq_message(link, state.board, msg):
            continue

        if msg.startswith("typing_"):
            handle_typing_message(link, display, msg[len("typing_") :], state.board)
            continue

        if msg in NEW_GAME_MSGS:
            raise ReturnToMenu()

        if msg in HINT_MSGS:
            send_move_hint(link, display, ctx, state, cfg)
            continue

        if msg in OK_MSGS:
            side = "WHITE" if state.board.turn == chess.WHITE else "BLACK"
            display.prompt_move(side, force=True)
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
            while True:
                msg2 = link.read_from_board()
                if msg2 is None:
                    continue
                if msg2 in NEW_GAME_MSGS:
                    raise ReturnToMenu()
                if msg2.startswith("typing_") or msg2 in HINT_MSGS:
                    continue
        else:
            prompt_next_turn(
                link, display, state.board, state.mode, cfg, chess.Move.uci(move)
            )


# -------------------- Online placeholder --------------------


def _run_online_game(link: BoardLink, display: Display, cfg: GameConfig) -> None:
    from modes.online.online_controller import OnlineController

    OnlineController(link, display, cfg).run()


def _run_puzzle_game(link: BoardLink, display: Display) -> None:
    """Puzzle mode.

    Submenu:
      1) Daily puzzle (Lichess daily)
      2) Mix & Match (random from optional local list; falls back to daily)
    """
    client = LichessClient()
    from modes.puzzles.puzzle_controller import PuzzleController

    def _short(s: str, n: int) -> str:
        s = (s or "").strip()
        return s if len(s) <= n else (s[: max(0, n - 1)] + "…")

    def _render_paged(title: str, page: int, pages: int, items4):
        # 20x4-friendly: one option per line (readable).
        # We prioritize readability over showing the header/footer at all times.
        # - Up to 4 options displayed: 1) .. 4) ..
        # - If <4 options, we use remaining lines for help / page info.
        def fmt_opt(i: int, s: str) -> str:
            s = _short(s or "", 18)  # leave room for "1)" prefix
            return f"{i}) {s}"[:20].rstrip()

        lines = []
        n = len(items4)

        # If we have a full 4 options, use all 4 lines for options.
        # Include page info on line 1 suffix when multiple pages.
        if n >= 4:
            l1 = fmt_opt(1, items4[0])
            if pages > 1:
                # add "p/x" at end if it fits
                suffix = f" {page+1}/{pages}"
                if len(l1) + len(suffix) <= 20:
                    l1 = l1 + suffix
                else:
                    l1 = l1[: max(0, 20 - len(suffix))] + suffix
            lines = [
                l1,
                fmt_opt(2, items4[1]),
                fmt_opt(3, items4[2]),
                fmt_opt(4, items4[3]),
            ]
            return "\n".join([x[:20] for x in lines])

        # Otherwise, show a compact header then one-option-per-line, plus help.
        header = (
            f"{_short(title, 14)} {page+1}/{pages}" if pages > 1 else _short(title, 20)
        )
        lines.append(header[:20].rstrip())
        for i, opt in enumerate(items4, start=1):
            lines.append(fmt_opt(i, opt))
        # Fill remaining lines with help text
        while len(lines) < 4:
            # Put help on the last line
            if len(lines) == 3:
                lines.append("H=next OK=back"[:20])
            else:
                lines.append("")
        return "\n".join([x[:20] for x in lines])

    from typing import Optional, List

    def _paged_menu(title: str, options: "List[str]") -> "Optional[str]":
        # Returns selected option string, or None if back.
        opts = list(options or [])
        if not opts:
            return None
        per_page = 4
        pages = (len(opts) + per_page - 1) // per_page
        page = 0

        # Tell the Pico we are entering a paged menu (1-4 + HINT next + OK back)
        link.send_to_board("MenuPaged")

        while True:
            chunk = opts[page * per_page : page * per_page + per_page]
            display.send(_render_paged(title, page, pages, chunk))
            msg = link.read_from_board()
            if msg is None:
                continue
            m = msg.strip().lower()
            if m in ("ok", "btn_ok", "btnok", "n", "new", "in", "newgame", "btn_new"):
                return None
            if m in HINT_MSGS:
                page = (page + 1) % pages
                continue
            if m in ("1", "2", "3", "4"):
                idx = int(m) - 1
                if idx < len(chunk) and chunk[idx]:
                    return chunk[idx]
                continue

    # -------------------- Menu definitions --------------------

    from typing import Tuple

    PHASE_THEMES: "List[Tuple[str, str]]" = [
        ("opening", "Opening"),
        ("middlegame", "Middlegame"),
        ("endgame", "Endgame"),
        ("rookEndgame", "Rook endgame"),
        ("bishopEndgame", "Bishop endgame"),
        ("pawnEndgame", "Pawn endgame"),
        ("knightEndgame", "Knight endgame"),
        ("queenEndgame", "Queen endgame"),
    ]

    # Opening angles (names) for /api/puzzle/next?angle=<opening name>.
    # These are *not* the same as the theme tags under /training/themes.
    OPENING_GROUPS: "List[Tuple[str, List[str]]]" = [
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
    ALL_OPENINGS: "List[str]" = [
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
        "Wade Defense",
        "Ware Defense",
        "Ware Opening",
        "Yusupov-Rubinstein System",
        "Zukertort Opening",
    ]

    # -------------------- Top-level puzzle menu --------------------

    # link.send_to_board("ChoosePuzzle")
    top = _paged_menu("PUZZLES", ["Daily Puzzle", "Mix and match", "Themes"])
    if top is None:
        raise ReturnToMenu()

    if top.startswith("Daily"):
        PuzzleController(client, mode="daily").run(link, display)
        return

    if top.startswith("Mix"):
        PuzzleController(client, mode="mix").run(link, display)
        return

    # -------------------- Themes submenu --------------------

    themes_top = _paged_menu("THEMES", ["Phases", "Openings"])
    if themes_top is None:
        raise ReturnToMenu()

    if themes_top.startswith("Phases"):
        label = _paged_menu("PHASES", [t[1] for t in PHASE_THEMES])
        if label is None:
            raise ReturnToMenu()
        tag = None
        for k, v in PHASE_THEMES:
            if v == label:
                tag = k
                break
        if not tag:
            raise ReturnToMenu()

        # IMPORTANT:
        # "Phases -> Opening" must request the PHASE tag 'opening' (lichess training/themes),
        # NOT a random opening name (lichess training/openings).
        PuzzleController(
            client,
            mode="theme",
            theme=tag,  # tag is e.g. 'opening', 'middlegame', 'endgame', ...
            theme_label=label,  # label is "Opening", "Middlegame", ...
        ).run(link, display)
        return

    if themes_top.startswith("Openings"):
        grp = _paged_menu("OPENINGS", [g[0] for g in OPENING_GROUPS])
        if grp is None:
            raise ReturnToMenu()
        opts: Optional[List[str]] = None
        for gname, glist in OPENING_GROUPS:
            if gname == grp:
                opts = glist
                break
        if not opts:
            raise ReturnToMenu()

        label = _paged_menu(grp.upper(), opts)
        if label is None:
            raise ReturnToMenu()

        # For openings, pass the opening label as the angle; lichess_client will slugify.
        PuzzleController(client, mode="theme", theme=label, theme_label=label).run(
            link, display
        )
        return

    raise ReturnToMenu()


def run_selected_mode(
    link: BoardLink,
    display: Display,
    ctx: EngineContext,
    state: GameState,
    cfg: GameConfig,
) -> None:
    if state.mode in ("stockfish", "pc", "btn_mode_pc", "vs_computer", "vs"):
        _configure_vs_computer(link, display, cfg)
        link.send_to_board("SetupComplete")

        display.send("Engine loading...")
        ctx.ensure()  # uses default STOCKFISH_PATH

        # Refactored: run through the explicit GameController state machine.
        from modes.vs_computer.game_controller import GameController, GameDeps
        from modes.vs_computer.stockfish_opponent import StockfishOpponent

        opponent = StockfishOpponent(
            ctx,
            move_time_ms=cfg.move_time_ms,
            skill_level=cfg.skill_level,
            use_elo=False,  # <-- turn on Elo limiting
        )
        controller = GameController(
            GameDeps(link=link, display=display, opponent=opponent),
            human_is_white=cfg.human_is_white,
        )
        controller.run_stockfish_game(move_time_ms=cfg.move_time_ms)
    elif state.mode in ("local", "btn_mode_local", "local_2p"):
        _configure_local_game(link, display, cfg)
        link.send_to_board("SetupComplete")
        _run_local_game(link, display, ctx, state, cfg)
    elif state.mode in ("puzzle", "puzzles", "btn_mode_puzzle", "btn_mode_puzzles"):
        # No Pico setup screens for puzzle yet.
        link.send_to_board("SetupComplete")
        _run_puzzle_game(link, display)
        raise ReturnToMenu()
    elif state.mode == "online":
        _run_online_game(link, display, cfg)
    else:
        # Don't silently fall back to online; it hides mode-token bugs.
        print(f"[MODE DISPATCH] unknown mode={state.mode!r}", flush=True)
        try:
            link.send_to_board("error_unknown_mode")
        except Exception:
            pass
        display.send("Unknown mode\n" + str(state.mode)[:18] + "\nOK=menu")
        # Wait for OK or New (OK+HINT) then return to mode select
        while True:
            msg = link.read_from_board()
            if msg is None:
                continue
            m = msg.strip().lower()
            if m in IGNORED_MSGS:
                raise ReturnToMenu()


# -------------------- Shutdown --------------------


def shutdown_raspberry_pi(link: BoardLink, display: Display) -> None:
    if display:
        display.send("Shutting down...\nWait 20s then\ndisconnect power.")
    time.sleep(2)
    try:
        subprocess.call("sudo nohup shutdown -h now", shell=True)
    except Exception as e:
        print(f"[Shutdown] {e}", file=sys.stderr)
