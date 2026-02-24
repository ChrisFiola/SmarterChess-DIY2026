# -*- coding: utf-8 -*-
"""
Game orchestrator — Protocol v2 JSON
- Minimal, readable state machine
- Human (Pico) vs Stockfish or Local
- Ready for future Lichess adapter without changing Pico protocol again
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import random
import time
import subprocess
import sys

import chess  # type: ignore

from piDisplay import Display
from piSerial import BoardLink
from piEngine import EngineContext, engine_bestmove, engine_hint

# ---------------- Data classes ----------------
@dataclass
class GameConfig:
    skill_level: int = 5
    move_time_ms: int = 2000
    human_is_white: bool = True

@dataclass
class RuntimeState:
    board: chess.Board
    mode: str = "stockfish"  # "stockfish" | "local" | "online"

# --------------- Helpers --------------------

def winner_text_from_result(res: str) -> str:
    res = (res or "").strip()
    if res == "1-0": return "White wins"
    if res == "0-1": return "Black wins"
    return "Draw"


def compute_capture_preview(brd: chess.Board, uci: str) -> bool:
    try:
        from_sq = chess.parse_square(uci[:2])
        to_sq = chess.parse_square(uci[2:4])
    except Exception:
        return False
    target = brd.piece_at(to_sq)
    if target and target.color != brd.turn:
        return True
    mover = brd.piece_at(from_sq)
    if mover and mover.piece_type == chess.PAWN and brd.ep_square == to_sq:
        if abs(chess.square_file(to_sq) - chess.square_file(from_sq)) == 1:
            return True
    return False

# --------------- Mode select + setup --------------------

def select_mode(link: BoardLink, display: Display, state: RuntimeState) -> str:
    link.send('ui', {'kind':'mode_select'})
    display.send("Choose opponent:\n1 PC 2 Online 3 Local")
    while True:
        msg = link.get()
        if msg is None: continue
        if msg['t'] == 'mode_choice':
            m = msg['d'].get('mode')
            if m in ('stockfish','online','local'):
                return m


def setup_stockfish(link: BoardLink, display: Display, cfg: GameConfig) -> None:
    display.send("VS Computer\nHints enabled")
    time.sleep(0.5)
    link.send('ui', {'kind':'setup_strength','default':cfg.skill_level})
    while True:
        m = link.get()
        if m and m['t'] == 'value' and m['d'].get('kind') == 'strength':
            cfg.skill_level = int(m['d']['value'])
            break
    link.send('ui', {'kind':'setup_time','default':cfg.move_time_ms})
    while True:
        m = link.get()
        if m and m['t'] == 'value' and m['d'].get('kind') == 'time':
            cfg.move_time_ms = int(m['d']['value'])
            break
    link.send('ui', {'kind':'setup_color'})
    while True:
        m = link.get()
        if m and m['t'] == 'value' and m['d'].get('kind') == 'color':
            val = m['d']['value']
            if val == 'white': cfg.human_is_white = True
            elif val == 'black': cfg.human_is_white = False
            else: cfg.human_is_white = bool(random.getrandbits(1))
            break


def setup_local(link: BoardLink, display: Display, cfg: GameConfig) -> None:
    display.send("Local 2-Player\nHints enabled")
    time.sleep(0.5)
    cfg.skill_level = 20; cfg.move_time_ms = 1

# --------------- UI helpers --------------------

def handoff_next_turn(link: BoardLink, display: Display, brd: chess.Board, mode: str, cfg: GameConfig, last_uci: Optional[str]):
    if last_uci:
        display.show_arrow(last_uci, suffix=("WHITE" if brd.turn==chess.WHITE else "BLACK")+" to move")
    human_to_move = (mode == 'local' or (mode == 'stockfish' and ((brd.turn==chess.WHITE and cfg.human_is_white) or (brd.turn==chess.BLACK and not cfg.human_is_white))))
    if human_to_move:
        link.send('turn', {'side': 'white' if brd.turn==chess.WHITE else 'black'})
    else:
        display.send("ENGINE thinking…")

# --------------- Hints --------------------

def send_hint_to_board(link: BoardLink, display: Display, ctx: EngineContext, state: RuntimeState, cfg: GameConfig) -> None:
    if state.board.is_game_over():
        link.send('overlay', {'role':'hint','uci':'','isCap':False,'ack':False})
        display.send("Game Over\nNo hints")
        return
    display.show_hint_thinking()
    best = engine_hint(ctx, state.board, cfg.move_time_ms)
    if not best:
        link.send('overlay', {'role':'hint','uci':'','isCap':False,'ack':False})
        return
    is_cap = False
    try:
        mv = chess.Move.from_uci(best)
        is_cap = state.board.is_capture(mv)
    except Exception:
        pass
    link.send('overlay', {'role':'hint','uci':best,'isCap':bool(is_cap),'ack':False})
    display.show_hint_result(best)

# --------------- Engine move --------------------

def engine_move_and_send(link: BoardLink, display: Display, ctx: EngineContext, state: RuntimeState, cfg: GameConfig):
    reply = engine_bestmove(ctx, state.board, cfg.move_time_ms)
    if reply is None:
        return
    mv = chess.Move.from_uci(reply)
    is_cap = state.board.is_capture(mv)
    # Show overlay and wait for pico ack
    link.send('overlay', {'role':'engine','uci':reply,'isCap':bool(is_cap),'ack':True})
    # wait ack
    while True:
        m = link.get()
        if m and m['t'] == 'ack' and m['d'].get('what') == 'engine_move':
            break
    state.board.push(mv)

# --------------- Game loop --------------------

def play_game(link: BoardLink, display: Display, ctx: EngineContext, state: RuntimeState, cfg: GameConfig) -> None:
    state.board = chess.Board()
    display.banner("NEW GAME", delay_s=0.8)

    if state.mode == 'stockfish' and not cfg.human_is_white:
        display.send("Computer starts first.")
        time.sleep(0.3)
        engine_move_and_send(link, display, ctx, state, cfg)
        handoff_next_turn(link, display, state.board, state.mode, cfg, None)
    else:
        handoff_next_turn(link, display, state.board, state.mode, cfg, None)

    while True:
        # 1) Non-blocking: capture probes + typing + shutdown
        peek = link.get_nonblocking()
        if peek is not None:
            if peek['t'] == 'shutdown':
                shutdown_pi(link, display); return
            if peek['t'] == 'typing':
                stage = peek['d'].get('stage')
                text = peek['d'].get('text','')
                if stage == 'from': display.send("Enter from:\n"+text)
                elif stage == 'to': display.send("Enter to:\n"+text)
                elif stage == 'confirm': display.send("Confirm:\n"+text)
            if peek['t'] == 'cap_probe':
                uci = peek['d'].get('uci','')
                cap = compute_capture_preview(state.board, uci)
                link.send('cap_result', {'uci':uci, 'isCap': bool(cap)})

        # 2) Engine turn when needed (stockfish only)
        if state.mode == 'stockfish' and not state.board.is_game_over():
            engine_should_move = ((state.board.turn==chess.WHITE and not cfg.human_is_white) or (state.board.turn==chess.BLACK and cfg.human_is_white))
            if engine_should_move:
                engine_move_and_send(link, display, ctx, state, cfg)
                # after engine move, check game over
                if state.board.is_game_over():
                    res = state.board.result(claim_draw=True)
                    link.send('game_over', {'result': res})
                    display.send(f"GAME OVER\n{winner_text_from_result(res)}\nPress OK")
                    # wait for pico new_game
                    while True:
                        m = link.get()
                        if m and m['t'] == 'new_game':
                            raise GoToModeSelect()
                else:
                    handoff_next_turn(link, display, state.board, state.mode, cfg, state.board.peek().uci() if state.board.move_stack else None)
                continue

        # 3) Blocking wait for pico messages
        msg = link.get()
        if msg is None:
            continue
        if msg['t'] == 'shutdown':
            shutdown_pi(link, display); return

        if msg['t'] == 'hint_request':
            send_hint_to_board(link, display, ctx, state, cfg)
            continue

        if msg['t'] == 'move_submit':
            uci = msg['d'].get('uci','')
            # promotion pre-detection
            if len(uci) == 4:
                try:
                    from_sq, to_sq = uci[:2], uci[2:4]
                    piece = state.board.piece_at(chess.parse_square(from_sq))
                    if piece and piece.piece_type == chess.PAWN:
                        rank = int(to_sq[1])
                        if (piece.color == chess.WHITE and rank == 8) or (piece.color == chess.BLACK and rank == 1):
                            link.send('promotion_needed', {})
                            while True:
                                ans = link.get()
                                if ans and ans['t'] == 'promotion_choice':
                                    uci = uci + ans['d'].get('piece','q')
                                    break
                except Exception:
                    pass
            # Validate & push
            try:
                move = chess.Move.from_uci(uci)
            except Exception:
                display.show_invalid(uci); continue
            if move not in state.board.legal_moves:
                link.send('error', {'type':'illegal','uci':uci}); display.show_illegal(uci, 'side'); continue
            state.board.push(move)
            # Game over?
            if state.board.is_game_over():
                res = state.board.result(claim_draw=True)
                link.send('game_over', {'result': res})
                display.send(f"GAME OVER\n{winner_text_from_result(res)}\nPress OK")
                while True:
                    m2 = link.get()
                    if m2 and m2['t'] == 'new_game':
                        raise GoToModeSelect()
            else:
                handoff_next_turn(link, display, state.board, state.mode, cfg, uci)
            continue

# --------------- Flow control --------------------
class GoToModeSelect(Exception):
    pass

# --------------- Dispatcher --------------------

def mode_dispatch(link: BoardLink, display: Display, ctx: EngineContext, state: RuntimeState, cfg: GameConfig) -> None:
    if state.mode == 'stockfish':
        setup_stockfish(link, display, cfg)
        play_game(link, display, ctx, state, cfg)
    elif state.mode == 'local':
        setup_local(link, display, cfg)
        # Local mode: both sides are human on the same Pico
        play_game(link, display, ctx, state, cfg)
    else:
        display.send("Online mode not implemented\n(yet)")
        raise GoToModeSelect()

# --------------- Shutdown --------------------

def shutdown_pi(link: BoardLink, display: Display) -> None:
    display.send("Shutting down...\nWait 20s then\ndisconnect power.")
    time.sleep(2)
    try:
        subprocess.call("sudo nohup shutdown -h now", shell=True)
    except Exception as e:
        print(f"[Shutdown] {e}", file=sys.stderr)
