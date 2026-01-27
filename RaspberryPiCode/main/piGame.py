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

import chess  # type: ignore

from piDisplay import Display
from piSerial import BoardLink
from piEngine import EngineContext, engine_bestmove, engine_hint

# -------------------- Data classes --------------------

@dataclass
class GameConfig:
    skill_level: int = 5
    move_time_ms: int = 2000
    human_is_white: bool = True

@dataclass
class RuntimeState:
    board: chess.Board
    mode: str = "stockfish"  # "stockfish" | "local" | "online"

# -------------------- Parsing & helpers --------------------

def parse_move_payload(payload: str) -> Optional[str]:
    if not payload:
        return None
    p = payload.strip().lower()
    if p.startswith("m"):
        p = p[1:].strip()
    cleaned = "".join(ch for ch in p if ch.isalnum())
    if 4 <= len(cleaned) <= 5 and cleaned.isalnum():
        return cleaned
    return None

def parse_side_choice(s: str) -> Optional[bool]:
    s = (s or "").strip().lower()
    if s.startswith("s1"): return True
    if s.startswith("s2"): return False
    if s.startswith("s3"): return bool(random.getrandbits(1))
    return None

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
        if m in ("btn_q", "btn_queen"): return "q"
        if m in ("btn_r", "btn_rook"):  return "r"
        if m in ("btn_b", "btn_bishop"):return "b"
        if m in ("btn_n", "btn_knight"):return "n"
        display.send("Promotion!\n1=Queen\n2=Rook\n3=Bishop\n4=Knight")

# -------------------- Hints & game-over --------------------

def send_hint_to_board(link: BoardLink, display: Display, ctx: EngineContext, state: RuntimeState, cfg: GameConfig) -> None:
    if state.board.is_game_over():
        link.sendtoboard("hint_gameover")
        display.send("Game Over\nNo hints\nPress n to start over")
        return

    display.show_hint_thinking()
    best = engine_hint(ctx, state.board, cfg.move_time_ms)
    if not best:
        link.sendtoboard("hint_none")
        return

    # Send to Pico and update OLED with arrow format
    link.sendtoboard(f"hint_{best}")
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

# -------------------- Flow control --------------------

class GoToModeSelect(Exception):
    pass

# -------------------- Setup & mode selection --------------------

def select_mode(link: BoardLink, display: Display, state: RuntimeState) -> str:
    link.sendtoboard("ChooseMode")
    display.send("Choose opponent:\n1) Against PC\n2) Remote human\n3) Local 2-player")
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

def handoff_next_turn(link: BoardLink, display: Display, brd: chess.Board, mode: str, cfg: GameConfig, last_uci: str):
    print(brd)

    link.sendtoboard(f"turn_{'white' if brd.turn == chess.WHITE else 'black'}")
    display.show_arrow(last_uci, suffix=f"{'WHITE' if brd.turn == chess.WHITE else 'BLACK'} to move")


def engine_move_and_send(link: BoardLink, display: Display, ctx: EngineContext, state: RuntimeState, cfg: GameConfig):
    reply = engine_bestmove(ctx, state.board, cfg.move_time_ms)
    if reply is None:
        return
    state.board.push_uci(reply)
    link.sendtoboard(f"m{reply}")

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

def handle_typing_preview(display: Display, payload: str) -> None:
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
            display.send("Enter from:\n" + text)
        elif label == "to":
            display.send("Enter to:\n" + text)
        elif label == "confirm":
            display.send("Confirm move:\n" + text + "\nPress OK or re-enter")
    except Exception:
        # swallow malformed previews quietly
        pass

# -------------------- Unified play loop --------------------

def play_game(link: BoardLink, display: Display, ctx: EngineContext, state: RuntimeState, cfg: GameConfig) -> None:
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
                handle_typing_preview(display, peek[len("typing_"):])
            # do not 'continue' to still allow engine turn same cycle

        # 2) Engine turn (Stockfish mode)
        if state.mode == "stockfish" and not state.board.is_game_over():
            engine_should_move = (
                (state.board.turn == chess.WHITE and not cfg.human_is_white) or
                (state.board.turn == chess.BLACK and cfg.human_is_white)
            )
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
            handle_typing_preview(display, msg[len("typing_"):])
            continue

        # 5) New game request
        if msg in ("n", "new", "in", "newgame", "btn_new"):
            raise GoToModeSelect()

        # 6) Hint request
        if msg in ("hint", "btn_hint"):
            send_hint_to_board(link, display, ctx, state, cfg)
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
                if (piece.color == chess.WHITE and rank == 8) or \
                (piece.color == chess.BLACK and rank == 1):
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

def run_online_mode(link: BoardLink, display: Display) -> None:
    display.send("Online mode not implemented\nUse Stockfish/Local")
    link.sendtoboard("error_online_unimplemented")
    # Bounce to mode select
    raise GoToModeSelect()

# -------------------- Dispatcher --------------------

def mode_dispatch(link: BoardLink, display: Display, ctx: EngineContext, state: RuntimeState, cfg: GameConfig) -> None:
    if state.mode == "stockfish":
        setup_stockfish(link, display, cfg)
        link.sendtoboard("SetupComplete")
        play_game(link, display, ctx, state, cfg)
    elif state.mode == "local":
        setup_local(link, display, cfg)
        link.sendtoboard("SetupComplete")
        play_game(link, display, ctx, state, cfg)
    else:
        run_online_mode(link, display)

# -------------------- Shutdown --------------------

def shutdown_pi(link: BoardLink, display: Display) -> None:
    if display:
        display.send("Shutting down...\nWait 20s then\ndisconnect power.")
    time.sleep(2)
    try:
        subprocess.call("sudo nohup shutdown -h now", shell=True)
    except Exception as e:
        print(f"[Shutdown] {e}", file=sys.stderr)