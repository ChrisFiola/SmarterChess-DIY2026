
#/home/king/chessenv/bin/python
# -*- coding: utf-8 -*-
"""
Hardware Chess (Arduino + OLED) using python-chess + Stockfish
Option A: Add Arduino/Serial board support to the modern engine.

Protocol (serial):
- Arduino -> Pi:  lines starting with 'heypi...'
- Pi -> Arduino:  lines starting with 'heyArduino...'

Expected Arduino payloads (examples):
- heypistockfish         # choose vs engine mode
- heypi5                 # skill level (0-20)
- heypi1000              # move time in ms
- heypim e2e4            # player move (with space)
- heypime2e4             # player move (no space) also supported
- heypin                 # new game
- heypixshutdown         # shutdown signal

Pi responses to Arduino:
- heyArduinoChooseMode
- heyArduinoReadyStockfish
- heyArduinom<uci>       # engine move (e.g. "heyArduinome7e5")
- heyArduinoerror_illegal_<uci>
- heyArduinoerror_invalid_<payload>
- heyArduinoGameOver:<result>
"""

import sys
import time
import subprocess
from typing import Optional

import serial
import chess
import chess.engine

# -----------------------------
# Configuration
# -----------------------------
SERIAL_PORT = "/dev/pts/4"     # e.g. '/dev/ttyUSB0' on real hardware
BAUD = 115200
SERIAL_TIMEOUT = 2.0

STOCKFISH_PATH = "stockfish"   # full path if needed, e.g. '/usr/bin/stockfish'
#ENGINE_TIMEOUT = 10.0          # seconds for UCI init
DEFAULT_SKILL = 5              # 0..20
DEFAULT_MOVE_TIME_MS = 800     # engine think time in ms
OLED_SCRIPT = "/home/king/SmartChess/RaspberryPiCode/printToOLED.py"

# -----------------------------
# Globals
# -----------------------------
engine: Optional[chess.engine.SimpleEngine] = None
board = chess.Board()
skill_level = DEFAULT_SKILL
move_time_ms = DEFAULT_MOVE_TIME_MS

# -----------------------------
# OLED Support
# -----------------------------
def send_to_screen(line1: str, line2: str = "", line3: str = "", size: str = "14") -> None:
    """Fire-and-forget update to OLED (non-blocking)."""
    try:
        subprocess.Popen([
            "python3", OLED_SCRIPT,
            "-a", line1, "-b", line2, "-c", line3, "-s", size
        ])
    except Exception as e:
        # Don't crash if OLED script is missing; just log.
        print(f"[OLED] Warning: {e}", file=sys.stderr)

# -----------------------------
# Serial Helpers
# -----------------------------
def open_serial() -> serial.Serial:
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=SERIAL_TIMEOUT)
    ser.flush()
    return ser

def sendtoboard(ser: serial.Serial, text: str) -> None:
    """Send a single line to Arduino, prefixed."""
    payload = "heyArduino" + text
    ser.write(payload.encode("utf-8") + b"\n")
    print(f"[->Board] {payload}")

def get_raw_from_board(ser: serial.Serial) -> Optional[str]:
    """Return raw lowercased line or None on timeout."""
    line = ser.readline()
    if not line:
        return None
    try:
        return line.decode("utf-8").strip().lower()
    except UnicodeDecodeError:
        return None

def getboard(ser: serial.Serial) -> Optional[str]:
    """
    Wait for a line starting with 'heypi', strip the prefix and return payload.
    - Returns None on timeout (so callers can decide to retry or continue).
    - Triggers shutdown on 'heypixshutdown'.
    """
    while True:
        raw = get_raw_from_board(ser)
        if raw is None:
            return None  # timeout, let caller handle
        if raw.startswith("heypixshutdown"):
            shutdown_pi(ser)
            return None
        if raw.startswith("heypi"):
            payload = raw[5:]  # strip 'heypi'
            print(f"[Board->] {raw}  | payload='{payload}'")
            return payload
        # Ignore noise/other traffic

# -----------------------------
# Engine Helpers
# -----------------------------
def open_engine(STOCKFISH_PATH) -> chess.engine.SimpleEngine:
    try:
        eng = chess.engine.SimpleEngine.popen_uci(
            path,
            timeout=ENGINE_TIMEOUT,
            stderr=subprocess.DEVNULL  # avoid banner/warning deadlocks
        )
        return eng
    except Exception as e:
        print(f"[Engine] ERROR launching '{path}': {e}", file=sys.stderr)
        sys.exit(1)

def set_engine_skill(eng: chess.engine.SimpleEngine, level: int) -> int:
    lvl = max(0, min(20, level))
    try:
        eng.configure({"Skill Level": lvl})
    except chess.engine.EngineError:
        # Some builds may not support Skill Level; continue anyway.
        pass
    return lvl

def engine_bestmove(eng: chess.engine.SimpleEngine, brd: chess.Board, ms: int) -> Optional[str]:
    if brd.is_game_over():
        return None
    limit = chess.engine.Limit(time=max(0.01, ms / 1000.0))
    result = eng.play(brd, limit)
    return result.move.uci() if result.move else None


def send_hint_to_board(ser):
    """Compute a hint using Stockfish and send it to the Arduino."""
    if board.is_game_over():
        sendtoboard(ser, "hint_gameover")
        send_to_screen("Game Over", "No hints", "")
        return

    # Ask Stockfish for a suggested move without committing it
    info = engine.analyse(board, chess.engine.Limit(time=move_time_ms / 1000))
    best_move = info["pv"][0].uci()

    sendtoboard(ser, f"hint_{best_move}")
    send_to_screen("Hint", best_move, "")
    print(f"[Hint] {best_move}")


# -----------------------------
# Game Management
# -----------------------------
def reset_game() -> None:
    global board
    board = chess.Board()
    send_to_screen("NEW", "GAME", "", "30")
    time.sleep(0.2)
    send_to_screen("Please enter", "your move:", "")

def parse_move_payload(payload: str) -> Optional[str]:
    """
    Accepts 'e2e4' or 'm e2e4' or 'me2e4' (tolerant).
    Returns UCI string of length 4 or 5 (promotion).
    """
    p = payload.strip()
    # Remove leading code letters and spaces (e.g., 'm e2e4', 'me2e4')
    if p.startswith("m"):
        p = p[1:].strip()
    p = p.replace(" ", "")
    # Now expect uci like 'e2e4' or 'e7e8q'
    if 4 <= len(p) <= 5 and all(ch.isalnum() for ch in p):
        return p
    return None

def apply_player_move(uci: str) -> bool:
    """Validate and push player's move."""
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        return False
    if move not in board.legal_moves:
        return False
    board.push(move)
    return True

def report_game_over(ser: serial.Serial) -> None:
    result = board.result(claim_draw=True)
    reason = "checkmate" if board.is_checkmate() else \
             "stalemate" if board.is_stalemate() else \
             "draw" if board.is_insufficient_material() or board.can_claim_draw() else \
             "gameover"
    sendtoboard(ser, f"GameOver:{result}")
    send_to_screen("Game Over", f"Result {result}", reason.upper())

# -----------------------------
# Mode: Player vs Stockfish
# -----------------------------
def run_stockfish_mode(ser: serial.Serial) -> None:
    global skill_level, move_time_ms

    sendtoboard(ser, "ReadyStockfish")
    send_to_screen("Choose computer", "difficulty (0-20)", "")
    # Read skill (tolerant: accept '', non-digits, timeouts)
    while True:
        msg = getboard(ser)
        if msg is None:
            continue  # timeout -> keep waiting
        if msg.isdigit():
            skill_level = int(msg)
            break
        # If Arduino sends something like 's5', extract digits:
        digits = "".join(ch for ch in msg if ch.isdigit())
        if digits:
            skill_level = int(digits)
            break
        print(f"[Parse] Invalid skill payload '{msg}', waiting...")

    skill_level = set_engine_skill(engine, skill_level)
    print(f"[Engine] Skill set to {skill_level}")

    send_to_screen("Choose move time", f"(ms, now {move_time_ms})", "")
    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        digits = "".join(ch for ch in msg if ch.isdigit())
        if digits:
            move_time_ms = max(10, int(digits))
            break
        print(f"[Parse] Invalid time payload '{msg}', waiting...")
    print(f"[Engine] Move time set to {move_time_ms} ms")

    reset_game()

    # Gameplay loop
    while True:
        if board.is_game_over():
            report_game_over(ser)
            # Wait for new game or power-cycle
            msg = getboard(ser)
            if msg and msg.startswith("n"):
                reset_game()
                continue
            time.sleep(0.1)
            continue

        # Wait for a command from board
        msg = getboard(ser)
        if msg is None:
            # timeout; continue listening
            continue

        code = msg[:1]
        if code == "n":
            reset_game()
            continue

        
        if code == "h":   # request for hint
            send_hint_to_board(ser)
            continue

        # Expect a move
        uci = parse_move_payload(msg)
        if not uci:
            sendtoboard(ser, f"error_invalid_{msg}")
            continue

        # Player move
        if not apply_player_move(uci):
            sendtoboard(ser, f"error_illegal_{uci}")
            send_to_screen("Illegal move!", "Enter new move…", "", "14")
            continue

        # Visual feedback
        send_to_screen(f"{uci[0:2]} → {uci[2:4]}", "", "Thinking…", "20")
        print(board)

        # Engine reply
        reply = engine_bestmove(engine, board, move_time_ms)
        if reply is None:
            # No reply means game over
            report_game_over(ser)
            continue

        # Push engine move on the board state
        board.push_uci(reply)
        print(f"[Engine] {reply}")
        print(board)

        # Notify Arduino and OLED
        sendtoboard(ser, f"m{reply}")
        send_to_screen(f"{reply[0:2]} → {reply[2:4]}", "", "Your turn", "20")

# -----------------------------
# Mode: Online Human (Placeholder Hook)
# -----------------------------
def run_online_mode(ser: serial.Serial) -> None:
    send_to_screen("Online mode", "Not implemented", "Use Stockfish mode")
    sendtoboard(ser, "error_online_unimplemented")
    # You can plug in your update-online.py + protocol here.
    # Reuse parse_move_payload, apply_player_move, and sendtoboard helpers.

# -----------------------------
# Shutdown
# -----------------------------
def shutdown_pi(ser: Optional[serial.Serial]) -> None:
    send_to_screen("Shutting down…", "Wait 20s then", "disconnect power")
    time.sleep(2)
    try:
        subprocess.call("sudo nohup shutdown -h now", shell=True)
    except Exception as e:
        print(f"[Shutdown] {e}", file=sys.stderr)

# -----------------------------
# Main
# -----------------------------
def main():
    global engine
    print("[Init] Opening engine…")
    engine = open_engine(STOCKFISH_PATH)
    print("[Init] Engine OK")

    print(f"[Init] Opening serial {SERIAL_PORT} @ {BAUD}…")
    ser = open_serial()
    print("[Init] Serial OK")

    # Mode selection
    sendtoboard(ser, "ChooseMode")
    send_to_screen("Choose opponent:", "1) PC", "2) Remote")

    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        mode = msg.strip()
        print(f"[Mode] Requested: {mode}")
        if mode == "stockfish" or mode == "1":
            run_stockfish_mode(ser)
        elif mode == "onlinehuman" or mode == "2":
            run_online_mode(ser)
        else:
            sendtoboard(ser, "error_unknown_mode")
            send_to_screen("Unknown mode", mode, "Send again")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Exit] KeyboardInterrupt")
    finally:
        try:
            if engine:
                engine.quit()
        except Exception:
            pass
