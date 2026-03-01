# -*- coding: utf-8 -*-
"""
Game flow, parsing, setup, and unified play loop (modular version).
Preserves Pico<->Pi UART protocol strings and display behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import random
import time
import traceback
import subprocess
import sys

# Allow importing sibling packages (RaspberryPiCode/app) when running from
# RaspberryPiCode/main under systemd.
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import chess  # type: ignore

from piDisplay import Display
from piSerial import BoardLink
from piEngine import EngineContext, engine_bestmove, engine_hint

# Phase 1: daily puzzle controller
from app.lichess_client import LichessClient
from app.puzzle_controller import DailyPuzzleController

# -------------------- Data classes --------------------


@dataclass
class GameConfig:
    skill_level: int = 5
    move_time_ms: int = 2000
    human_is_white: bool = True


@dataclass
class RuntimeState:
    board: chess.Board
    mode: str = "stockfish"  # "stockfish" | "local" | "online" | "puzzle"


# -------------------- Parsing & helpers --------------------


RESERVED_NON_MOVES = {"ok", "btnok", "btn_ok", "draw", "btn_draw", "hint", "btn_hint", "n", "new", "in", "newgame", "btn_new"}


def parse_move_payload(payload: str) -> Optional[str]:
    if not payload:
        return None
    p = payload.strip().lower()
    if p.startswith("m"):
        p = p[1:].strip()
    cleaned = "".join(ch for ch in p if ch.isalnum())
    if 4 <= len(cleaned) <= 5 and cleaned.isalnum():
        if cleaned in RESERVED_NON_MOVES:
            return None
        return cleaned
    return None


def parse_side_choice(s: str) -> Optional[bool]:
    s = (s or "").strip().lower()
    if s.startswith("s1"):
        return True
    if s.startswith("s2"):
        return False
    if s.startswith("s3"):
        return bool(random.getrandbits(1))
    return None


def compute_capture_preview(brd: chess.Board, uci: str) -> bool:
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


def requires_promotion(move: chess.Move, brd: chess.Board) -> bool:
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


def ask_promotion_piece(link: BoardLink, display: Display) -> str:
    """
    Ask Pico to collect promotion choice:
      1=Queen, 2=Rook, 3=Bishop, 4=Knight  -> 'q','r','b','n'
    """
    display.send("Promotion!\n1=Queen\n2=Rook\n3=Bishop\n4=Knight")
    link.sendtoboard("promotion_choice_needed")
    while True:
        msg = link.getboard()
        if msg is None:
            continue
        if msg.startswith("n"):
            # Signal to caller to restart mode selection via exception
            raise GoToModeSelect()
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


# -------------------- Hints & game-over --------------------


def send_hint_to_board(
    link: BoardLink,
    display: Display,
    ctx: EngineContext,
    state: RuntimeState,
    cfg: GameConfig,
) -> None:
    if state.board.is_game_over():
        link.sendtoboard("hint_gameover")
        display.send("Game Over\nNo hints\nPress n to start over")
        return

    display.show_hint_thinking()
    best = engine_hint(ctx, state.board, cfg.move_time_ms)
    if not best:
        link.sendtoboard("hint_none")
        return

    # Mark capture for hint if applicable
    try:
        mv = chess.Move.from_uci(best)
        is_cap = state.board.is_capture(mv)
    except Exception:
        is_cap = False

    # Send to Pico and update OLED with arrow format
    link.sendtoboard(f"hint_{best}{'_cap' if is_cap else ''}")
    display.show_hint_result(best)
    print(f"[Hint] {best}")


def side_name_from_board(brd: chess.Board) -> str:
    return "WHITE" if brd.turn == chess.WHITE else "BLACK"


def report_game_over(link: BoardLink, display: Display, brd: chess.Board) -> str:
    result = brd.result(claim_draw=True)
    winner = winner_text_from_result(result)
    link.sendtoboard(f"GameOver:{result}")
    display.send(f"GAME OVER\n{winner}\nStart new game?")
    return result



def _auto_draw_reason(brd: chess.Board) -> Optional[str]:
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


def _handle_auto_draw(link: BoardLink, display: Display, brd: chess.Board) -> bool:
    """If draw condition met, notify Pico + LCD and wait for new game.

    Returns True if we handled a draw and the caller should stop current game loop.
    """
    reason = _auto_draw_reason(brd)
    if not reason:
        return False

    # Result string in UCI/Pico protocol format
    result = "1/2-1/2"
    link.sendtoboard(f"GameOver:{result}")
    try:
        move_no = max(1, (len(brd.move_stack) + 1) // 2)
    except Exception:
        move_no = 0
    display.show_draw(reason, move_no)

    # Wait for Pico to acknowledge by sending 'n' (new game / back)
    while True:
        msg2 = link.getboard()
        if msg2 is None:
            continue
        if msg2 == "shutdown":
            shutdown_pi(link, display)
            return True
        if msg2 in ("n", "new", "in", "newgame", "btn_new"):
            # caller typically raises GoToModeSelect
            return True
        if msg2.startswith("typing_") or msg2 in ("hint", "btn_hint", "btn_ok", "ok"):
            continue


# -------------------- Flow control --------------------


class GoToModeSelect(Exception):
    pass


# -------------------- Setup & mode selection --------------------


def select_mode(link: BoardLink, display: Display, state: RuntimeState) -> str:
    link.sendtoboard("ChooseMode")
    display.send(
        "Choose mode:\n1) Against PC\n2) Lichess Online\n3) Local 2-player\n4) Puzzles"
    )
    while True:
        msg = link.getboard()
        if msg is None:
            continue
        m = msg.strip().lower()
        if m in ("1", "stockfish", "pc", "btn_mode_pc"):
            return "stockfish"
        if m in ("2", "onlinehuman", "remote", "online", "btn_mode_online"):
            return "online"
        if m in ("3", "local", "human", "btn_mode_local"):
            return "local"
        if m in ("4", "puzzle", "daily", "btn_mode_puzzle"):
            return "puzzle"
        link.sendtoboard("error_unknown_mode")
        display.send("Unknown mode\n" + m + "\nSend again")


def setup_stockfish(link: BoardLink, display: Display, cfg: GameConfig) -> None:
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
    display.send("Choose computer\ndifficulty level:\n(0 -> 8)")
    link.sendtoboard("EngineStrength")
    link.sendtoboard(f"default_strength_{cfg.skill_level}")
    while True:
        msg = link.getboard()
        if msg is None:
            continue
        if msg.startswith("n"):
            raise GoToModeSelect()
        if msg.isdigit():
            cfg.skill_level = max(0, min(int(msg), 20))
            break

    # Move time
    display.send("Choose computer\nmove time:\n(0 -> 8)")
    link.sendtoboard("TimeControl")
    link.sendtoboard(f"default_time_{cfg.move_time_ms}")
    while True:
        msg = link.getboard()
        if msg is None:
            continue
        if msg.startswith("n"):
            raise GoToModeSelect()
        if msg.isdigit():
            cfg.move_time_ms = max(10, int(msg))
            break

    # Color
    display.send("Select a colour:\n1 = White/First\n2 = Black/Second\n3 = Random")
    link.sendtoboard("PlayerColor")
    while True:
        msg = link.getboard()
        if msg is None:
            continue
        if msg.startswith("n"):
            raise GoToModeSelect()
        side = parse_side_choice(msg)
        if side is not None:
            cfg.human_is_white = side
            break


def setup_local(link: BoardLink, display: Display, cfg: GameConfig) -> None:
    display.send("Local 2-Player\nHints enabled")
    time.sleep(2)
    cfg.skill_level = 20  # max hint skill for local
    cfg.move_time_ms = 1  # fastest think time for local

    """
    display.send("Choose computer\ndifficulty level:\n(0 -> 8)")
    link.sendtoboard("EngineStrength")
    link.sendtoboard(f"default_strength_{cfg.skill_level}")
    while True:
        msg = link.getboard()
        if msg is None:
            continue
        if msg.isdigit():
            cfg.skill_level = max(0, min(int(msg), 20))
            break

    display.send("Choose computer\nmove time:\n(0 -> 8)")
    link.sendtoboard("TimeControl")
    link.sendtoboard(f"default_time_{cfg.move_time_ms}")
    while True:
        msg = link.getboard()
        if msg is None:
            continue
        if msg.isdigit():
            cfg.move_time_ms = max(10, int(msg))
            break
    """


# -------------------- UI helpers & engine handoff --------------------


def ui_new_game_banner(display: Display):
    display.banner("NEW GAME", delay_s=1.0)


def ui_engine_thinking(display: Display):
    display.send("Engine Thinking...")


def handoff_next_turn(
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
        link.sendtoboard(f"turn_{'white' if brd.turn == chess.WHITE else 'black'}")
        display.show_arrow(
            last_uci,
            suffix=f"{'WHITE' if brd.turn == chess.WHITE else 'BLACK'} to move",
        )
    else:
        display.show_arrow(last_uci, suffix="ENGINE thinking")


def engine_move_and_send(
    link: BoardLink,
    display: Display,
    ctx: EngineContext,
    state: RuntimeState,
    cfg: GameConfig,
):
    reply = engine_bestmove(ctx, state.board, cfg.move_time_ms)
    if reply is None:
        return

    # Compute capture BEFORE pushing
    mv = chess.Move.from_uci(reply)
    is_cap = state.board.is_capture(mv)

    # Send with _cap if capture, then push
    link.sendtoboard(f"m{reply}{'_cap' if is_cap else ''}")
    state.board.push(mv)
    # Promotion banner (engine)
    if mv.promotion:
        try:
            display.show_promotion("Computer", chess.piece_symbol(mv.promotion))
            time.sleep(0.8)
        except Exception:
            pass

    # Auto-draw after engine move
    if _handle_auto_draw(link, display, state.board):
        raise GoToModeSelect()


    if state.board.is_game_over():
        _res = report_game_over(link, display, state.board)
        while True:
            msg2 = link.getboard()
            if msg2 is None:
                continue
            if msg2 in ("n", "new", "in", "newgame", "btn_new"):
                raise GoToModeSelect()
            if msg2.startswith("typing_") or msg2 in ("hint", "btn_hint"):
                continue
        # no handoff needed because game ended
    else:
        handoff_next_turn(link, display, state.board, state.mode, cfg, reply)


def winner_text_from_result(res: str) -> str:
    res = (res or "").strip()
    if res == "1-0":
        return "White wins"
    if res == "0-1":
        return "Black wins"
    return "Draw"


# -------------------- Typing preview --------------------


def _piece_pretty_name(piece: "chess.Piece") -> str:
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


def _looks_like_square(s: str) -> bool:
    if len(s) != 2:
        return False
    f, r = s[0].lower(), s[1]
    return f in "abcdefgh" and r in "12345678"


def _piece_label_from_square(board: Optional["chess.Board"], sq: str) -> Optional[str]:
    """Return a label for the piece currently on sq, or None if not resolvable."""
    if board is None or not _looks_like_square(sq):
        return None
    try:
        piece = board.piece_at(chess.parse_square(sq))
        if piece is None:
            return "Empty"
        return _piece_pretty_name(piece)
    except Exception:
        return None


def handle_typing_preview(display: Display, payload: str, board: Optional["chess.Board"] = None) -> None:
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
            if _looks_like_square(text):
                piece_lbl = _piece_label_from_square(board, text)
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
            piece_lbl = _piece_label_from_square(board, frm)
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
            piece_lbl = _piece_label_from_square(board, frm)
            if piece_lbl:
                display.send(f"{piece_lbl}\n{frm} → {to}\nOK to send")
            else:
                display.send("Confirm move:\n" + text + "\nPress OK or re-enter")
    except Exception:
        # swallow malformed previews quietly
        pass


# -------------------- Human move processing (extracted) --------------------


def process_human_move(
    *, link: BoardLink, display: Display, board: chess.Board, uci: str
) -> None:
    """Validate, handle promotion, push, and report/handoff.

    Extracted from the previous monolithic play loop to make the core loop
    easier to read and extend (Lichess later).

    Protocol + display behavior are preserved:
      - invalid -> heyArduinoerror_invalid_* + OLED invalid
      - illegal -> heyArduinoerror_illegal_* + OLED illegal
      - game over -> heyArduinoGameOver:* + OLED game over
    """

    # 1) Parse UCI
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        link.sendtoboard(f"error_invalid_{uci}")
        display.show_invalid(uci)
        return

    # 2) Promotion pre-detection if user did not include promotion letter
    if len(uci) == 4:
        try:
            from_sq = uci[:2]
            to_sq = uci[2:4]
            piece = board.piece_at(chess.parse_square(from_sq))
            if piece and piece.piece_type == chess.PAWN:
                rank = int(to_sq[1])
                if (piece.color == chess.WHITE and rank == 8) or (
                    piece.color == chess.BLACK and rank == 1
                ):
                    promo = ask_promotion_piece(link, display)
                    uci = uci + promo
                    move = chess.Move.from_uci(uci)
        except GoToModeSelect:
            raise
        except Exception:
            # fall through to normal validation
            pass

    # 3) If still requires promotion (legal but missing promotion)
    if requires_promotion(move, board):
        promo = ask_promotion_piece(link, display)
        uci = uci + promo
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            link.sendtoboard(f"error_invalid_{uci}")
            display.show_invalid(uci)
            return

    # 4) Legality check
    if move not in board.legal_moves:
        link.sendtoboard(f"error_illegal_{uci}")
        display.show_illegal(uci, side_name_from_board(board))
        return

    # 5) Push
    board.push(move)

    # 6) Game over or handoff
    if board.is_game_over():
        report_game_over(link, display, board)
        return

    # Keep your existing "arrow + whose turn" messaging
    dummy_cfg = GameConfig(skill_level=5, move_time_ms=2000, human_is_white=True)
    handoff_next_turn(link, display, board, "stockfish", dummy_cfg, uci)


# -------------------- Unified play loop --------------------


def play_game(
    link: BoardLink,
    display: Display,
    ctx: EngineContext,
    state: RuntimeState,
    cfg: GameConfig,
) -> None:
    # Reset and banner
    state.board = chess.Board()
    link.sendtoboard("GameStart")
    ui_new_game_banner(display)
    time.sleep(0.3)

    # Initial side to move
    if state.mode == "stockfish":
        if not cfg.human_is_white:
            display.send("Computer starts first.")
            time.sleep(0.4)
            engine_move_and_send(link, display, ctx, state, cfg)
        else:
            link.sendtoboard("turn_white")
            display.prompt_move("WHITE")
    else:
        # Local 2-player always starts with White
        link.sendtoboard("turn_white")
        display.prompt_move("WHITE")

    while True:
        # 1) Non-blocking: show typing previews if any
        peek = link.getboard_nonblocking()
        if peek is not None:
            if peek == "shutdown":
                shutdown_pi(link, display)
                return
            if peek.startswith("typing_"):
                handle_typing_preview(display, peek[len("typing_") :], state.board)
            # do not 'continue' to still allow engine turn same cycle

            # Pico asks: "capq_<uci>" -> answer quickly with "capr_0/1"
            if peek.startswith("capq_"):
                uci = peek[5:].strip()
                try:
                    cap = compute_capture_preview(state.board, uci)
                except Exception:
                    cap = False
                link.sendtoboard(f"capr_{1 if cap else 0}")

        # 2) Engine turn (Stockfish mode)
        if state.mode == "stockfish" and not state.board.is_game_over():
            engine_should_move = (
                state.board.turn == chess.WHITE and not cfg.human_is_white
            ) or (state.board.turn == chess.BLACK and cfg.human_is_white)
            if engine_should_move:
                ui_engine_thinking(display)
                engine_move_and_send(link, display, ctx, state, cfg)
                # After engine move, loop continues to check for human input
                continue

        # 3) Blocking read for next Pico message
        msg = link.getboard()
        if msg is None:
            # serial timeout; loop to allow engine step or previews again
            continue
        if msg == "shutdown":
            shutdown_pi(link, display)
            return

        # 4) Also handle typing previews in the blocking path (to be consistent)
        if msg.startswith("typing_"):
            handle_typing_preview(display, msg[len("typing_") :], state.board)
            continue

        # --- NEW: capture preview probe (blocking path) ---
        if msg.startswith("capq_"):
            uci = msg[5:].strip()
            try:
                cap = compute_capture_preview(state.board, uci)
            except Exception:
                cap = False
            link.sendtoboard(f"capr_{1 if cap else 0}")
            continue

        # 5) New game request
        if msg in ("n", "new", "in", "newgame", "btn_new"):
            raise GoToModeSelect()

        # 6) Hint request
        if msg in ("hint", "btn_hint"):
            send_hint_to_board(link, display, ctx, state, cfg)
            continue

        # 7) OK acknowledgement / 'enter move' trigger (Pico sends this before typing_ begins)
        if msg in ("ok", "btnok", "btn_ok"):
            # Keep OLED aligned with Pico's UX: OK takes you to the move entry prompt.
            display.prompt_move("WHITE" if state.board.turn == chess.WHITE else "BLACK")
            continue

        # 7) Try parsing a move
        uci = parse_move_payload(msg)
        if not uci:
            link.sendtoboard(f"error_invalid_{msg}")
            display.show_invalid(msg)
            continue

        # === PROMOTION PRE-DETECTION ===
        # If the pawn move ends on rank 8 (white) or rank 1 (black),
        # and the UCI has no promotion letter, trigger promotion.
        from_sq = uci[:2]
        to_sq = uci[2:4]

        if len(uci) == 4:
            # we need board state BEFORE including this move
            piece = state.board.piece_at(chess.parse_square(from_sq))
            if piece and piece.piece_type == chess.PAWN:
                rank = int(to_sq[1])
                if (piece.color == chess.WHITE and rank == 8) or (
                    piece.color == chess.BLACK and rank == 1
                ):
                    # ask promotion piece BEFORE creating the move
                    promo = ask_promotion_piece(link, display)
                    uci = uci + promo

        # 8) Validate UCI and handle promotion if needed
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            link.sendtoboard(f"error_invalid_{uci}")
            display.show_invalid(uci)
            continue

        # Promotion needed?
        if requires_promotion(move, state.board):
            promo = ask_promotion_piece(link, display)
            uci = uci + promo
            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                link.sendtoboard(f"error_invalid_{uci}")
                display.show_invalid(uci)
                continue

        # 9) Legality check (AFTER OK) — Pico only sends after OK now
        if move not in state.board.legal_moves:
            link.sendtoboard(f"error_illegal_{uci}")
            display.show_illegal(uci, side_name_from_board(state.board))
            continue

        # 10) Accept and push
        state.board.push(move)

        # 10.5) Auto-draw (repetition / 50-move etc.)
        if _handle_auto_draw(link, display, state.board):
            raise GoToModeSelect()

        # 11) Game over?
        if state.board.is_game_over():
            _res = report_game_over(link, display, state.board)
            # Wait for Pico to acknowledge by sending 'n' (OK)
            while True:
                msg2 = link.getboard()
                if msg2 is None:
                    continue
                if msg2 in ("n", "new", "in", "newgame", "btn_new"):
                    # Return to mode select
                    raise GoToModeSelect()
                # swallow typing/hint during game over
                if msg2.startswith("typing_") or msg2 in ("hint", "btn_hint"):
                    continue
        else:
            handoff_next_turn(link, display, state.board, state.mode, cfg, uci)


# -------------------- Online placeholder --------------------


def run_online_mode(link: BoardLink, display: Display, cfg: GameConfig) -> None:
    """Online mode (manual start) — thin wrapper.

    Phase 1: implementation moved to app.online_controller.OnlineController.
    """
    from app.online_controller import OnlineController, OnlineDeps

    deps = OnlineDeps(
        link=link,
        display=display,
        cfg=cfg,
        parse_move_payload=parse_move_payload,
        compute_capture_preview=compute_capture_preview,
        ask_promotion_piece=ask_promotion_piece,
        side_name_from_board=side_name_from_board,
        handle_typing_preview=handle_typing_preview,
        report_game_over=report_game_over,
        shutdown_pi=shutdown_pi,
        GoToModeSelect=GoToModeSelect,
    )
    OnlineController(deps).run()


def run_puzzle_mode(link: BoardLink, display: Display) -> None:
    """Puzzle mode.

    Submenu:
      1) Daily puzzle (Lichess daily)
      2) Mix & Match (random from optional local list; falls back to daily)
    """
    client = LichessClient()

    link.sendtoboard("ChoosePuzzle")
    display.send("PUZZLES\n1) Daily\n2) Mix & Match\n(n=back)")
    while True:
        msg = link.getboard()
        if msg is None:
            continue
        m = msg.strip().lower()
        if m in ("n", "new", "in", "newgame", "btn_new"):
            raise GoToModeSelect()
        if m in ("1", "daily", "btn_puzzle_daily"):
            DailyPuzzleController(client, mode="daily").run(link, display)
            return
        if m in ("2", "mix", "random", "btn_puzzle_mix"):
            DailyPuzzleController(client, mode="mix").run(link, display)
            return
        display.send("PUZZLES\n1) Daily\n2) Mix & Match\n(n=back)")


def mode_dispatch(
    link: BoardLink,
    display: Display,
    ctx: EngineContext,
    state: RuntimeState,
    cfg: GameConfig,
) -> None:
    if state.mode == "stockfish":
        setup_stockfish(link, display, cfg)
        link.sendtoboard("SetupComplete")
        # Refactored: run through the explicit GameController state machine.
        from app.game_controller import GameController, LoopDeps
        from app.stockfish_opponent import StockfishOpponent

        opponent = StockfishOpponent(
    ctx,
    move_time_ms=cfg.move_time_ms,
    skill_level=cfg.skill_level,
    use_elo=False,   # <-- turn on Elo limiting
)
        controller = GameController(
            LoopDeps(link=link, display=display, opponent=opponent),
            human_is_white=cfg.human_is_white,
        )
        controller.play_stockfish(move_time_ms=cfg.move_time_ms)
    elif state.mode == "local":
        setup_local(link, display, cfg)
        link.sendtoboard("SetupComplete")
        play_game(link, display, ctx, state, cfg)
    elif state.mode == "puzzle":
        # No Pico setup screens for puzzle yet.
        link.sendtoboard("SetupComplete")
        run_puzzle_mode(link, display)
        raise GoToModeSelect()
    else:
        run_online_mode(link, display, cfg)


# -------------------- Shutdown --------------------


def shutdown_pi(link: BoardLink, display: Display) -> None:
    if display:
        display.send("Shutting down...\nWait 20s then\ndisconnect power.")
    time.sleep(2)
    try:
        subprocess.call("sudo nohup shutdown -h now", shell=True)
    except Exception as e:
        print(f"[Shutdown] {e}", file=sys.stderr)
