#!/home/king/chessenv/bin/python
# -*- coding: utf-8 -*-
"""
SmarterChess — CLEAN REWRITE (2026)
-----------------------------------
Goals:
  - Keep UART protocol identical with Pico firmware.
  - Support Stockfish and Local modes with a unified, clear pipeline.
  - Show responsive typing previews:
       heypityping_from_<text>
       heypityping_to_<from> → <partial_to>
       heypityping_confirm_<from> → <to>
  - "New Game" => banner then DIY-style flow.
  - Illegal moves validated on Pi only when a UCI arrives (Pico no longer pre-checks).
  - Hints: 'Thinking...' shown immediately; hint arrow displayed on completion.
  - Code is modular in structure (even in single-file form) with distinct sections.

NOTE:
  - This file is the SINGLE-FILE version.
  - After completing this, a MODULAR version will be provided (split into modules).
"""

from __future__ import annotations

import os
import sys
import time
import random
import traceback
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple, List, Callable

# Third-party libs (installed in your venv/system as before)
import serial  # type: ignore
import chess  # type: ignore
import chess.engine  # type: ignore

# ============================================================
# =============== CONSTANTS & PATHS ==========================
# ============================================================

SERIAL_PORT: str = "/dev/serial0"
BAUD: int = 115200
SERIAL_TIMEOUT: float = 2.0

STOCKFISH_PATH: str = "/usr/games/stockfish"  # Keep same path unless configured outside

# Display server IPC endpoints
PIPE_PATH: str = "/tmp/lcdpipe"
READY_FLAG_PATH: str = "/tmp/display_server_ready"
DISPLAY_SERVER_SCRIPT: str = "/home/king/SmarterChess-DIY2026/RaspberryPiCode/display_server.py"

DEFAULT_SKILL: int = 5
DEFAULT_MOVE_TIME_MS: int = 2000

# ============================================================
# =============== DATA STRUCTURES ============================
# ============================================================

@dataclass
class GameConfig:
    """Configuration that can be set during setup prompts."""
    skill_level: int = DEFAULT_SKILL
    move_time_ms: int = DEFAULT_MOVE_TIME_MS
    human_is_white: bool = True  # true => human plays White in Stockfish mode

@dataclass
class EngineContext:
    """Holds the Stockfish engine instance and helper methods."""
    engine: Optional[chess.engine.SimpleEngine] = None

    def ensure(self, path: str) -> chess.engine.SimpleEngine:
        if self.engine is not None:
            return self.engine
        while True:
            try:
                self.engine = chess.engine.SimpleEngine.popen_uci(path, stderr=None, timeout=None)
                return self.engine
            except Exception:
                time.sleep(1)

    def quit(self):
        if self.engine:
            try:
                self.engine.quit()
            except Exception:
                pass
            self.engine = None

@dataclass
class RuntimeState:
    """Mutable game state."""
    board: chess.Board
    mode: str = "stockfish"  # "stockfish" | "local" | "online"
    # UI handler to push typing previews even during blocking calls
    on_typing_preview: Optional[Callable[[str, str], None]] = None

# ============================================================
# =============== DISPLAY (OLED via PIPE) ====================
# ============================================================

class Display:
    """
    Minimal abstraction around your display_server.py IPC.
    We always write 'line1|line2|...|auto\n' to PIPE_PATH.
    """

    def __init__(self, pipe_path: str = PIPE_PATH, ready_flag: str = READY_FLAG_PATH):
        self.pipe_path = pipe_path
        self.ready_flag = ready_flag

    def restart_server(self):
        """Kill old server, create FIFO, start new server."""
        subprocess.Popen("pkill -f display_server.py", shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.2)
        if not os.path.exists(self.pipe_path):
            try:
                os.mkfifo(self.pipe_path)
            except FileExistsError:
                pass
        subprocess.Popen(["python3", DISPLAY_SERVER_SCRIPT],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_ready(self, timeout_s: float = 10.0):
        """Wait for display server to create its ready flag."""
        start = time.time()
        while not os.path.exists(self.ready_flag):
            if time.time() - start > timeout_s:
                break
            time.sleep(0.05)

    def send(self, message: str, size: str = "auto") -> None:
        """
        Write to the named pipe. The display server parses segments by '|'.
        """
        parts = message.split("\n")
        payload = "|".join(parts) + f"|{size}\n"
        with open(self.pipe_path, "w") as pipe:
            pipe.write(payload)

    # UI conveniences

    def banner(self, text: str, delay_s: float = 0.0):
        self.send(text)
        if delay_s > 0:
            time.sleep(delay_s)

    def show_arrow(self, uci: str, suffix: str = ""):
        arrow = f"{uci[:2]} → {uci[2:4]}"
        if suffix:
            self.send(f"{arrow}\n{suffix}")
        else:
            self.send(arrow)

    def prompt_move(self, side: str):
        # side is human-friendly descriptor: "WHITE" or "BLACK"
        self.send(f"You are {side.lower()}\nEnter move:")

    def show_hint_thinking(self):
        self.send("Hint\nThinking...")

    def show_hint_result(self, uci: str):
        self.show_arrow(uci)

    def show_invalid(self, text: str):
        self.send(f"Invalid\n{text}\nTry again")

    def show_illegal(self, uci: str, side_name: str):
        self.send(f"Illegal move!\nEnter new\nmove...")

    def show_gameover(self, result: str):
        self.send(f"Game Over\nResult {result}\nPress n to start over")

# ============================================================
# =============== SERIAL (UART to PICO) ======================
# ============================================================

class BoardLink:
    """
    Wraps serial link with helper methods:
      - sendtoboard("Text") => send 'heyArduinoText\n'
      - getboard_nonblocking() => payload after 'heypi'
      - getboard() => blocking wait for payload after 'heypi'
    """

    def __init__(self, port: str = SERIAL_PORT, baud: int = BAUD, timeout: float = SERIAL_TIMEOUT):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.ser.flush()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    # ---------- write ----------
    def send_raw(self, text: str) -> None:
        """Low-level write with newline; no 'heyArduino' prefix."""
        self.ser.write(text.encode("utf-8") + b"\n")

    def sendtoboard(self, text: str) -> None:
        """Protocol-preserving send: 'heyArduino' + <text> + '\\n'."""
        payload = "heyArduino" + text
        self.ser.write(payload.encode("utf-8") + b"\n")
        print(f"[-→Board] {payload}")

    # ---------- read ----------
    def _readline(self) -> Optional[str]:
        line = self.ser.readline()
        if not line:
            return None
        try:
            return line.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None

    def get_raw_from_board(self) -> Optional[str]:
        """Blocking raw line; handles shutdown command."""
        raw = self._readline()
        if raw is None:
            return None
        low = raw.lower()
        if low.startswith("heypixshutdown"):
            return "heypixshutdown"
        return low

    def getboard_nonblocking(self) -> Optional[str]:
        """Non-blocking 'heypi' payload read; returns payload or None."""
        if self.ser.in_waiting:
            raw = self._readline()
            if not raw:
                return None
            low = raw.lower()
            if low.startswith("heypixshutdown"):
                return "shutdown"
            if low.startswith("heypi"):
                payload = low[5:]
                print(f"[Board→] {low}  | payload='{payload}'")
                return payload
        return None

    def getboard(self) -> Optional[str]:
        """Blocking 'heypi' payload; handles shutdown."""
        while True:
            raw = self.get_raw_from_board()
            if raw is None:
                return None
            if raw.startswith("heypixshutdown"):
                return "shutdown"
            if raw.startswith("heypi"):
                payload = raw[5:]
                print(f"[Board→] {raw}  | payload='{payload}'")
                return payload

# ============================================================
# =============== UTILS: PARSING & HELPERS ===================
# ============================================================

def parse_move_payload(payload: str) -> Optional[str]:
    """
    Accept 'm<uci>' or '<uci>' (4-5 alnum chars).
    Returns lower-case UCI or None.
    """
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
    """
    's1' => True (human white)
    's2' => False (human black)
    's3' => random boolean
    """
    s = (s or "").strip().lower()
    if s.startswith("s1"): return True
    if s.startswith("s2"): return False
    if s.startswith("s3"): return bool(random.getrandbits(1))
    return None

def side_name_from_board(brd: chess.Board) -> str:
    return "WHITE" if brd.turn == chess.WHITE else "BLACK"

def uci_arrow(uci: str) -> str:
    return f"{uci[:2]} → {uci[2:4]}"

# ============================================================
# =============== TYPING PREVIEW HANDLER =====================
# ============================================================

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

# ============================================================
# =============== ENGINE (STOCKFISH) =========================
# ============================================================

def engine_bestmove(ctx: EngineContext, brd: chess.Board, ms: int) -> Optional[str]:
    if brd.is_game_over():
        return None
    engine = ctx.ensure(STOCKFISH_PATH)
    limit = chess.engine.Limit(time=max(0.01, ms / 1000.0))
    result = engine.play(brd, limit)  # type: ignore
    return result.move.uci() if result.move else None

def engine_hint(ctx: EngineContext, brd: chess.Board, ms: int) -> Optional[str]:
    """
    Try analyse() to get principal variation; fallback to a single best move.
    """
    try:
        engine = ctx.ensure(STOCKFISH_PATH)
        info = engine.analyse(brd, chess.engine.Limit(time=max(0.01, ms / 1000.0)))  # type: ignore
        pv = info.get("pv")
        if pv:
            return pv[0].uci()
    except Exception:
        pass
    return engine_bestmove(ctx, brd, ms)

# ============================================================
# =============== PROMOTION FLOW =============================
# ============================================================

def requires_promotion(move: chess.Move, brd: chess.Board) -> bool:
    """
    Determine if promotion is required: pawn reaching back rank without a promotion set.
    """
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

# ============================================================
# =============== HINTS & NEW GAME ===========================
# ============================================================

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

# ============================================================
# =============== ERROR / GAME OVER ==========================
# ============================================================

def report_game_over(link: BoardLink, display: Display, brd: chess.Board) -> None:
    result = brd.result(claim_draw=True)
    link.sendtoboard(f"GameOver:{result}")
    display.show_gameover(result)

# ============================================================
# =============== FLOW CONTROL EXCEPTIONS ====================
# ============================================================

class GoToModeSelect(Exception):
    """Signal to jump out to top-level mode selection (e.g., user pressed New Game)."""
    pass

# ============================================================
# =============== SETUP & MODE SELECTION =====================
# ============================================================

def select_mode(link: BoardLink, display: Display, state: RuntimeState) -> str:
    """
    Ask Pico for mode via:
      - sendtoboard("ChooseMode")
      - Wait for heypi ... btn_mode_pc / btn_mode_online / btn_mode_local
    """
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


def setup_stockfish(link: BoardLink, display: Display, cfg: GameConfig):
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


def setup_local(link: BoardLink, display: Display, cfg: GameConfig):
    """
    Local 2-player setup (mirrors DIY style, though engine params are placeholders
    for uniformity with Pico prompts).
    """
    display.send("Local 2-Player\nHints enabled")
    time.sleep(2)

    # Difficulty proxy
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

    # Move time proxy
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

# ============================================================
# =============== UNIFIED PLAY LOOP (SKELETON) ===============
# ============================================================

def ui_new_game_banner(display: Display):
    display.banner("NEW GAME", delay_s=1.0)

def ui_engine_thinking(display: Display):
    display.send("Engine Thinking...")

def handoff_next_turn(link: BoardLink, display: Display, brd: chess.Board, mode: str, cfg: GameConfig, last_uci: str):
    """
    After a valid push, notify Pico whose turn it is and show arrow prompt.
    """
    link.sendtoboard(f"turn_{'white' if brd.turn == chess.WHITE else 'black'}")

    # Show last move arrow and indicate whose turn
    display.show_arrow(last_uci, suffix=f"{side_name_from_board(brd)} to move")

def engine_move_and_send(link: BoardLink, display: Display, ctx: EngineContext, state: RuntimeState, cfg: GameConfig):
    """
    Trigger engine to move (Stockfish mode only), push it, send to Pico, then hand off.
    """
    reply = engine_bestmove(ctx, state.board, cfg.move_time_ms)
    if reply is None:
        return
    state.board.push_uci(reply)
    link.sendtoboard(f"m{reply}")
    handoff_next_turn(link, display, state.board, state.mode, cfg, reply)


# ============================================================
# =============== PLAY GAME (UNIFIED LOOP) ===================
# ============================================================

def play_game(link: BoardLink, display: Display, ctx: EngineContext, state: RuntimeState, cfg: GameConfig) -> None:
    """
    Consistent, centralized UI flow:
      - Resets board
      - Sends 'GameStart'
      - Handles engine-first (stockfish) vs human-first
      - Main loop:
          * Non-blocking typing previews (typing_from/to/confirm)
          * Engine move when it's engine turn (stockfish)
          * Blocking read for Pico messages (moves, hints, new game)
          * Promotion handling
          * Legality check after OK (Pico does not pre-check)
    """
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
        handoff_next_turn(link, display, state.board, state.mode, cfg, uci)

        # 11) Game over?
        if state.board.is_game_over():
            report_game_over(link, display, state.board)
            # Wait for new game command (back to mode select)
            # The Pico UX expects user to press 'n' => GoToModeSelect
            raise GoToModeSelect()

# ============================================================
# =============== ONLINE MODE PLACEHOLDER ====================
# ============================================================

def run_online_mode(link: BoardLink, display: Display):
    display.send("Online mode not implemented\nUse Stockfish/Local")
    link.sendtoboard("error_online_unimplemented")
    # Bounce to mode select
    raise GoToModeSelect()

# ============================================================
# =============== MODE DISPATCHER ============================
# ============================================================

def mode_dispatch(link: BoardLink, display: Display, ctx: EngineContext, state: RuntimeState, cfg: GameConfig):
    """
    Enter setup based on selected mode, then start game loop.
    """
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

# ============================================================
# =============== SHUTDOWN HANDLER ===========================
# ============================================================

def shutdown_pi(link: Optional[BoardLink], display: Optional[Display]) -> None:
    if display:
        display.send("Shutting down...\nWait 20s then\ndisconnect power.")
    time.sleep(2)
    try:
        subprocess.call("sudo nohup shutdown -h now", shell=True)
    except Exception as e:
        print(f"[Shutdown] {e}", file=sys.stderr)

# ============================================================
# =============== MAIN ======================================= 
# ============================================================

def main():
    display = Display()
    display.restart_server()
    display.wait_ready()

    ctx = EngineContext()
    # Engine is lazy-opened on first use; can be pre-warmed by uncommenting:
    # ctx.ensure(STOCKFISH_PATH)

    link = BoardLink()
    cfg = GameConfig()
    state = RuntimeState(board=chess.Board(), mode="stockfish")

    # Top-level loop: choose mode -> run -> back to select on GoToModeSelect
    while True:
        try:
            # Mode select (Pico)
            selected = select_mode(link, display, state)
            state.mode = selected

            # Dispatch to setup and then game loop
            mode_dispatch(link, display, ctx, state, cfg)

        except GoToModeSelect:
            # Return to "SMARTCHESS" banner then choose mode again
            state.board = chess.Board()
            display.send("SMARTCHESS")
            time.sleep(2.5)
            continue
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[Fatal] {e}")
            traceback.print_exc()
            time.sleep(1)
            # Continue loop to allow recovery / reselection

    # Cleanup
    try:
        link.close()
    except Exception:
        pass
    try:
        ctx.quit()
    except Exception:
        pass

if __name__ == "__main__":
    main()