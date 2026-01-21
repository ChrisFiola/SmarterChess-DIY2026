#!/home/king/chessenv/bin/python
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
import traceback
import random
import os

import serial  # type: ignore
import chess  # type: ignore
import chess.engine  # type: ignore

# -----------------------------
# Configuration
# -----------------------------
SERIAL_PORT = "/dev/serial0"  # e.g. '/dev/ttyUSB0' on real hardware
BAUD = 115200
SERIAL_TIMEOUT = 2.0

STOCKFISH_PATH = (
    "/usr/games/stockfish"  # full path if needed, e.g. '/usr/bin/stockfish'
)
DEFAULT_SKILL = 5  # 0..20
DEFAULT_MOVE_TIME_MS = 800  # engine think time in ms
OLED_SCRIPT = "/home/king/SmarterChess-DIY2026/RaspberryPiCode/printToOLED.py"

# -----------------------------
# Globals
# -----------------------------
engine: Optional[chess.engine.SimpleEngine] = None
board = chess.Board()
skill_level = DEFAULT_SKILL
move_time_ms = DEFAULT_MOVE_TIME_MS
human_is_white = True

# -----------------------------
# OLED Support
# -----------------------------


def wait_for_display_server_ready():
    READY_FLAG = "/tmp/display_server_ready"

    print("[Init] Waiting for display server to become ready...")

    # Loop forever until ready file appears
    while not os.path.exists(READY_FLAG):
        time.sleep(0.05)
    print("[Init] Display server is ready.")


def restart_display_server():
    PIPE = "/tmp/lcdpipe"

    # Kill any existing display_server.py processes
    subprocess.Popen(
        "pkill -f display_server.py",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Give the OS a moment to release GPIO / SPI
    time.sleep(0.2)

    # Ensure pipe exists
    if not os.path.exists(PIPE):
        os.mkfifo(PIPE)

    # Start fresh display server
    subprocess.Popen(
        [
            "python3",
            "/home/king/SmarterChess-DIY2026/RaspberryPiCode/display_server.py",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_display_server(timeout=5.0):
    """Wait until display_server.py is fully running and pipe is ready."""
    PIPE = "/tmp/lcdpipe"
    start_time = time.time()

    # Wait for process to appear
    while time.time() - start_time < timeout:
        ps = subprocess.Popen(
            "ps aux | grep display_server.py | grep -v grep",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out = ps.stdout.read().decode().strip()
        if "display_server.py" in out:
            break
        time.sleep(0.05)

    # Wait for pipe to exist
    while not os.path.exists(PIPE):
        if time.time() - start_time > timeout:
            print("[Init] ERROR: LCD pipe not created")
            return
        time.sleep(0.05)

    # Wait until pipe can be opened for writing
    while True:
        try:
            with open(PIPE, "w") as f:
                pass
            break
        except Exception:
            if time.time() - start_time > timeout:
                print("[Init] ERROR: LCD pipe cannot be opened")
                break
            time.sleep(0.05)

    print("[Init] Display server ready")


def send_to_screen(
    line1: str, line2: str = "", line3: str = "", line4: str = "", size: str = ""
) -> None:
    """Fire-and-forget update to OLED (non-blocking)."""
    try:
        subprocess.Popen(
            [
                "python3",
                OLED_SCRIPT,
                "-a",
                line1,
                "-b",
                line2,
                "-c",
                line3,
                "-d",
                line4,
                "-s",
                size,
            ]
        )
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


def turn_name() -> str:
    return "WHITE" if board.turn == chess.WHITE else "BLACK"


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
def open_engine(path: str) -> chess.engine.SimpleEngine:
    """Try repeatedly to open Stockfish until it succeeds."""
    while True:
        try:
            print(f"[Engine] Launching: {path!r}")
            eng = chess.engine.SimpleEngine.popen_uci(
                path, stderr=None, timeout=None  # avoid banner/warning deadlocks
            )
            return eng

        except Exception as e:
            print(f"[Engine] ERROR launching {path!r}")
            print(f"[Engine] Exception type: {type(e).__name__}")
            print(f"[Engine] Exception repr: {repr(e)}")
            traceback.print_exc()

            # Retry delay
            for i in range(5, 0, -1):
                print(f"[Engine] Retry in {i}…")
                time.sleep(1)


def set_engine_skill(eng: chess.engine.SimpleEngine, level: int) -> int:
    lvl = max(0, min(20, level))
    try:
        eng.configure({"Skill Level": lvl})
    except chess.engine.EngineError:
        # Some builds may not support Skill Level; continue anyway.
        pass
    return lvl


def is_engine_turn() -> bool:
    """Returns True if it's the engine's turn to move."""
    return (board.turn == chess.WHITE and not human_is_white) or (
        board.turn == chess.BLACK and human_is_white
    )


def engine_move_and_send(ser: serial.Serial) -> None:
    """Make the engine play one move, push it, and notify the Arduino + OLED."""
    reply = engine_bestmove(board, move_time_ms)
    if reply is None:
        return
    board.push_uci(reply)
    sendtoboard(ser, f"m{reply}")
    send_to_screen(f"{reply[0:2]} → {reply[2:4]}", "", "Your turn")
    print("[Engine]", reply)
    print(board)


def engine_bestmove(brd: chess.Board, ms: int) -> Optional[str]:
    if brd.is_game_over():
        return None
    limit = chess.engine.Limit(time=max(0.01, ms / 1000.0))
    result = engine.play(brd, limit)  # Uses global engine
    return result.move.uci() if result.move else None


def send_hint_to_board(ser):
    """Compute a hint using Stockfish and send it to the Arduino."""
    if board.is_game_over():
        sendtoboard(ser, "hint_gameover")
        send_to_screen("Game Over", "No hints")
        return

    # Ask Stockfish for a suggested move without committing it
    info = engine.analyse(board, chess.engine.Limit(time=move_time_ms / 1000))
    best_move = info["pv"][0].uci()

    sendtoboard(ser, f"hint_{best_move}")
    print(board)
    time.sleep(1)
    send_to_screen("Hint", best_move)
    print(f"[Hint] {best_move}")


# -----------------------------
# Game Management
# -----------------------------
def reset_game() -> None:
    global board
    board = chess.Board()
    send_to_screen("NEW", "GAME")
    time.sleep(0.2)


def parse_move_payload(payload: str) -> Optional[str]:
    """
    Accepts UCI like:
      e2e4, E2E4, e7e8q
    and tolerant forms:
      'm e2e4', 'me2e4', 'm e2 e4', 'e2 e4', 'E2:E4', 'e2-e4'
    Returns: normalized UCI string ('e2e4' or 'e7e8q') or None if invalid.
    """
    if payload is None:
        return None

    p = payload.strip()

    # Remove optional leading 'm' / 'M' used by some senders
    if p.lower().startswith("m"):
        # tolerate "m e2e4" or "m   e2 e4"
        p = p[1:].strip()

    # Normalize separators and spaces (e.g., 'e2 e4', 'e2-e4', 'e2:e4')
    # Keep only alphanumerics and drop the rest
    cleaned = "".join(ch for ch in p if ch.isalnum())

    # Lowercase for UCI
    cleaned = cleaned.lower()

    # Now expect a UCI move of length 4 (e.g., e2e4) or 5 (promotion, e7e8q)
    if 4 <= len(cleaned) <= 5 and cleaned.isalnum():
        return cleaned

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
    reason = (
        "checkmate"
        if board.is_checkmate()
        else (
            "stalemate"
            if board.is_stalemate()
            else (
                "draw"
                if board.is_insufficient_material() or board.can_claim_draw()
                else "gameover"
            )
        )
    )
    sendtoboard(ser, f"GameOver:{result}")
    send_to_screen("Game Over", f"Result {result}", reason.upper())


# -----------------------------
# Mode: Player vs Stockfish
# -----------------------------


def run_local_mode(ser: serial.Serial) -> None:
    """
    Two humans play locally.
    - Pi validates moves and updates OLED.
    - Hints are provided using Stockfish for the side to move.
    """
    global skill_level, move_time_ms

    sendtoboard(ser, "ReadyLocal")
    send_to_screen("Local 2-Player", "Hints enabled", "Press n=new game")

    # Optional: allow setting hint strength/time (reusing your existing UI flow)
    send_to_screen("Hint strength", "0-20")

    while True:  # short window to set (optional)
        msg = getboard(ser)
        if msg is None:
            continue
        if msg.startswith("n"):
            reset_game()
            break
        if msg.isdigit():
            skill_level = int(msg)
            break
        digits = "".join(ch for ch in msg if ch.isdigit())
        if digits:
            skill_level = int(digits)
            break
        print(f"[Parse] Invalid skill payload '{msg}', waiting...")

    skill_level = set_engine_skill(engine, skill_level)
    print(f"[Engine] Skill set to {skill_level}")

    send_to_screen("Hint think time", f"ms (now {move_time_ms})")
    while True:  # short window to set (optional)
        msg = getboard(ser)
        if msg is None:
            continue
        if msg.startswith("n"):
            reset_game()
            break
        digits = "".join(ch for ch in msg if ch.isdigit())
        if digits:
            move_time_ms = max(10, int(digits))
            break
        print(f"[Parse] Invalid time payload '{msg}', waiting...")
    print(f"[Engine] Move time set to {move_time_ms} ms")

    reset_game()
    gameover_reported = False

    # Let Arduino know starting turn (optional)
    sendtoboard(ser, "turn_white")
    send_to_screen("Local Play", "White to move")

    while True:
        if board.is_game_over():
            if not gameover_reported:
                report_game_over(ser)
                gameover_reported = True

            # wait for new game
            msg = getboard(ser)
            if msg and msg.startswith("n"):
                reset_game()
                gameover_reported = False
                sendtoboard(ser, "turn_white")
                send_to_screen("Local Play", "White to move")
            continue

        msg = getboard(ser)
        if msg is None:
            continue

        # New game
        if msg.startswith("n"):
            reset_game()
            gameover_reported = False
            sendtoboard(ser, "turn_white")
            send_to_screen("Local Play", "White to move")
            continue

        # Hint request
        if msg == "hint" or msg.startswith("hint"):
            send_hint_to_board(ser)
            continue

        # Move
        uci = parse_move_payload(msg)
        if not uci:
            sendtoboard(ser, f"error_invalid_{msg}")
            send_to_screen("Invalid input", msg, "Try again")
            continue

        if not apply_player_move(uci):
            sendtoboard(ser, f"error_illegal_{uci}")
            send_to_screen("Illegal move!", uci, f"{turn_name()} again")
            continue

        # Show move + next turn
        next_turn = turn_name()
        send_to_screen(f"{uci[0:2]} → {uci[2:4]}", "", f"{next_turn} to move")
        print(board)

        # Optional: inform Arduino whose turn (if you want Arduino UI updates)
        sendtoboard(ser, f"turn_{'white' if board.turn == chess.WHITE else 'black'}")


def run_stockfish_mode(ser: serial.Serial) -> None:
    global skill_level, move_time_ms, human_is_white

    sendtoboard(ser, "ReadyStockfish")

    # Difficulty
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

    # Time
    send_to_screen("Choose move time", f"(ms, now {move_time_ms})")
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

    # Side selection w / b / r
    send_to_screen("Choose side", "w = White, b = Black", " r = Random")
    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        m = msg.strip().lower()
        if m.startswith("w") or m == "1":
            human_is_white = True
            break
        if m.startswith("b") or m == "2":
            human_is_white = False
            break
        if m.startswith("r") or m == "3":
            human_is_white = bool(random.getrandbits(1))
            break

        print(f"[Parse] Invalid color payload '{msg}', waiting...")

    # NEW GAME
    reset_game()
    gameover_reported = False

    # If engine starts (human chose black), let engine move now
    if is_engine_turn():
        print("You are Black\n")
        send_to_screen("Engine Starts", "Thinking…")
        time.sleep(1)
        engine_move_and_send(ser)
        print(board)
    else:
        send_to_screen("You are white", "Your move...")
        print(board)

    # Gameplay loop
    while True:
        if board.is_game_over():
            if not gameover_reported:
                report_game_over(ser)
                gameover_reported = True
            # Wait for new game or power-cycle
            msg = getboard(ser)
            if msg and msg.startswith("n"):
                reset_game()
                gameover_reported = False

                # Check if engine turn
                if is_engine_turn():
                    send_to_screen("Engine", "Thinking…")
                    engine_move_and_send(ser)

                continue

        # If it's engine's turn, move automatically
        if is_engine_turn():
            send_to_screen("Engine", "Thinking…")
            engine_move_and_send(ser)
            continue

        # Wait for a command from board
        msg = getboard(ser)
        if msg is None:
            # timeout; continue listening
            continue

        if msg == "new" or msg.startswith("n"):
            reset_game()
            gameover_reported = False

            # Check if engine turn
            if is_engine_turn():
                send_to_screen("Engine", "Thinking…")
                engine_move_and_send(ser)

            continue

        if msg == "hint" or msg.startswith("hint"):  # request for hint
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
            send_to_screen("Illegal move!", uci, f"{turn_name()} again")
            continue

        # Visual feedback
        send_to_screen(f"{uci[0:2]} → {uci[2:4]}", "", "Thinking…")
        print(board)

        # Engine reply
        engine_move_and_send(ser)


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

    # Starting the display server for persistent image
    # print("[Init] Starting display server")
    # restart_display_server()
    # print("[Init] Display server running")

    # Starting the stockfish engine
    global engine
    print("[Init] Opening engine…")
    engine = open_engine(STOCKFISH_PATH)
    print("[Init] Engine OK")

    # Opening serial connection
    print(f"[Init] Opening serial {SERIAL_PORT} @ {BAUD}…")
    ser = open_serial()
    print("[Init] Serial OK")

    # Mode selection
    sendtoboard(ser, "ChooseMode")
    send_to_screen("Choose opponent:", "1) PC", "2) Remote", "3) Local")
    time.sleep(1)

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
        elif mode == "local" or mode == "3":
            run_local_mode(ser)
        else:
            sendtoboard(ser, "error_unknown_mode")
            send_to_screen("Unknown mode", mode, "Send again")


if __name__ == "__main__":
    restart_display_server()
    wait_for_display_server_ready()
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Exit] KeyboardInterrupt")
    finally:
        try:
            if engine:
                engine.quit()
                print("[Exit] Engine closed cleanly. ")
        except Exception:
            pass
