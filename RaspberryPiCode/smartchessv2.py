#!/home/king/chessenv/bin/python
# -*- coding: utf-8 -*-
"""
SmarterChess (refactored)
- Unified game loop for Stockfish & Local modes
- Cleaner parsing and fewer duplicates
- "New Game" returns to mode selection every time
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
SERIAL_PORT = (
    "/dev/serial0"  # <<< Use your real UART when on hardware; e.g. "/dev/ttyUSB0"
)
BAUD = 115200
SERIAL_TIMEOUT = 2.0

STOCKFISH_PATH = "/usr/games/stockfish"
DEFAULT_SKILL = 5  # 0..20
DEFAULT_MOVE_TIME_MS = 800  # engine think time in ms for engine/hints
OLED_SCRIPT = "/home/king/SmarterChess-DIY2026/RaspberryPiCode/printToOLED.py"

# -----------------------------
# Globals
# -----------------------------
engine: Optional[chess.engine.SimpleEngine] = None
board = chess.Board()

skill_level = DEFAULT_SKILL
move_time_ms = DEFAULT_MOVE_TIME_MS

# Who plays white in Stockfish mode (human vs engine).
# In Local mode, both sides are human; this flag is ignored except for hints.
human_is_white = True


# -----------------------------
# OLED Support
# -----------------------------
def wait_for_display_server_ready():
    READY_FLAG = "/tmp/display_server_ready"
    print("[Init] Waiting for display server to become ready...")
    while not os.path.exists(READY_FLAG):
        time.sleep(0.05)
    print("[Init] Display server is ready.")


def restart_display_server():
    PIPE = "/tmp/lcdpipe"
    subprocess.Popen(
        "pkill -f display_server.py",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)

    if not os.path.exists(PIPE):
        os.mkfifo(PIPE)

    subprocess.Popen(
        [
            "python3",
            "/home/king/SmarterChess-DIY2026/RaspberryPiCode/display_server.py",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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
        print(f"[OLED] Warning: {e}", file=sys.stderr)


# -----------------------------
# Serial Helpers
# -----------------------------
def open_serial() -> serial.Serial:
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=SERIAL_TIMEOUT)
    ser.flush()
    return ser


def sendtoboard(ser: serial.Serial, text: str) -> None:
    payload = "heyArduino" + text
    ser.write(payload.encode("utf-8") + b"\n")
    print(f"[-&>Board] {payload}")


def get_raw_from_board(ser: serial.Serial) -> Optional[str]:
    line = ser.readline()
    if not line:
        return None
    try:
        return line.decode("utf-8").strip().lower()
    except UnicodeDecodeError:
        return None


def getboard(ser: serial.Serial) -> Optional[str]:
    """
    Wait for a line starting with 'heypi', strip prefix and return payload.
    - Returns None on timeout.
    - Triggers shutdown on 'heypixshutdown'.
    """
    while True:
        raw = get_raw_from_board(ser)
        if raw is None:
            return None
        if raw.startswith("heypixshutdown"):
            shutdown_pi(ser)
            return None
        if raw.startswith("heypi"):
            payload = raw[5:]  # strip 'heypi'
            print(f"[Board->] {raw}  | payload='{payload}'")
            return payload
        # ignore other noise


# -----------------------------
# Chess helpers
# -----------------------------
def turn_name() -> str:
    return "WHITE" if board.turn == chess.WHITE else "BLACK"


def reset_game_state() -> None:
    global board
    board = chess.Board()
    print("[Game] Board reset.")


def parse_move_payload(payload: str) -> Optional[str]:
    """
    Accept UCI in tolerant forms: e2e4, 'm e2e4', 'e2 e4', 'e2-e4', 'E2:E4', 'e7e8q'.
    Returns normalized UCI or None.
    """
    if not payload:
        return None
    p = payload.strip()
    if p.startswith("m"):
        p = p[1:].strip()
    cleaned = "".join(ch for ch in p if ch.isalnum()).lower()
    if 4 <= len(cleaned) <= 5 and cleaned.isalnum():
        return cleaned
    return None


def extract_digits(s: str) -> Optional[int]:
    digits = "".join(ch for ch in s if ch.isdigit())
    return int(digits) if digits else None


def parse_side_choice(s: str) -> Optional[bool]:
    """
    Returns True if human is white, False if human is black, None if random/invalid.
    Accepts: w/1, b/2, r/3
    """
    s = (s or "").strip().lower()
    if s.startswith("w") or s == "1":
        return True
    if s.startswith("b") or s == "2":
        return False
    if s.startswith("r") or s == "3":
        return bool(random.getrandbits(1))
    return None


# -----------------------------
# Stockfish engine
# -----------------------------
def open_engine(path: str) -> chess.engine.SimpleEngine:
    while True:
        try:
            print(f"[Engine] Launching: {path!r}")
            eng = chess.engine.SimpleEngine.popen_uci(path, stderr=None, timeout=None)
            return eng
        except Exception as e:
            print(f"[Engine] ERROR launching {path!r}")
            print(f"[Engine] Exception: {type(e).__name__} {repr(e)}")
            traceback.print_exc()
            for i in range(5, 0, -1):
                print(f"[Engine] Retry in {i}…")
                time.sleep(1)


def set_engine_skill(eng: chess.engine.SimpleEngine, level: int) -> int:
    lvl = max(0, min(20, level))
    try:
        eng.configure({"Skill Level": lvl})
    except chess.engine.EngineError:
        pass
    return lvl


def engine_bestmove(brd: chess.Board, ms: int) -> Optional[str]:
    if brd.is_game_over():
        return None
    limit = chess.engine.Limit(time=max(0.01, ms / 1000.0))
    result = engine.play(brd, limit)
    return result.move.uci() if result.move else None


def send_hint_to_board(ser: serial.Serial) -> None:
    if board.is_game_over():
        sendtoboard(ser, "hint_gameover")
        send_to_screen("Game Over", "No hints")
        return
    info = engine.analyse(
        board, chess.engine.Limit(time=max(0.01, move_time_ms / 1000.0))
    )
    best_move = info["pv"][0].uci()
    sendtoboard(ser, f"hint_{best_move}")
    send_to_screen("Hint", best_move)
    print(f"[Hint] {best_move}")


# -----------------------------
# Game flow utilities
# -----------------------------
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


def engine_move_and_send(ser: serial.Serial) -> None:
    """Engine plays one move for the side to move, not used in Local mode."""
    reply = engine_bestmove(board, move_time_ms)
    if reply is None:
        return
    board.push_uci(reply)
    sendtoboard(ser, f"m{reply}")
    send_to_screen(f"{reply[0:2]} → {reply[2:4]}", "", "Your turn")
    print("[Engine]", reply)
    print(board)


# -----------------------------
# Mode setup
# -----------------------------
def select_mode(ser: serial.Serial) -> str:
    """
    Ask user to choose a mode. Returns 'stockfish' or 'local' or 'online'.
    """
    sendtoboard(ser, "ChooseMode")
    send_to_screen("Choose opponent:", "1) PC", "2) Remote", "3) Local")
    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        m = msg.strip().lower()
        if m in ("1", "stockfish", "pc"):
            return "stockfish"
        if m in ("2", "onlinehuman", "remote", "online"):
            return "online"
        if m in ("3", "local", "human"):
            return "local"
        sendtoboard(ser, "error_unknown_mode")
        send_to_screen("Unknown mode", m, "Send again")


def setup_stockfish(ser: serial.Serial) -> None:
    global skill_level, move_time_ms, human_is_white

    sendtoboard(ser, "ReadyStockfish")
    send_to_screen("Choose computer", "difficulty (0-20)")

    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        if msg.startswith("n"):
            # go back to mode selection
            raise GoToModeSelect()
        val = extract_digits(msg)
        if val is not None:
            skill_level = set_engine_skill(engine, val)
            break
        print(f"[Parse] Invalid skill payload '{msg}', waiting...")

    send_to_screen("Choose move time", f"(ms, now {move_time_ms})")
    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        if msg.startswith("n"):
            raise GoToModeSelect()
        val = extract_digits(msg)
        if val is not None:
            move_time_ms = max(10, int(val))
            break
        print(f"[Parse] Invalid time payload '{msg}', waiting...")

    send_to_screen("Choose side", "w = White, b = Black", " r = Random")
    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        if msg.startswith("n"):
            raise GoToModeSelect()
        side = parse_side_choice(msg)
        if side is not None:
            human_is_white = side
            break
        print(f"[Parse] Invalid color payload '{msg}', waiting...")

    print(
        f"[Engine] Skill={skill_level} | Time={move_time_ms}ms | HumanWhite={human_is_white}"
    )


def setup_local(ser: serial.Serial) -> None:
    """
    Local 1v1. Engine never moves; used only for hints. We still collect hint params.
    """
    global skill_level, move_time_ms
    sendtoboard(ser, "ReadyLocal")
    send_to_screen("Local 2-Player", "Hints enabled", "Press n to restart")
    time.sleep(0.5)

    send_to_screen("Hint strength", f"0-20 (now {skill_level})")
    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        if msg.startswith("n"):
            raise GoToModeSelect()
        val = extract_digits(msg)
        if val is not None:
            skill_level = set_engine_skill(engine, val)
            break
        # also accept plain 'ok' to skip
        if msg in ("ok", "skip"):
            break
        print(f"[Parse] Invalid skill payload '{msg}', waiting...")

    send_to_screen("Hint think time", f"ms (now {move_time_ms})")
    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        if msg.startswith("n"):
            raise GoToModeSelect()
        val = extract_digits(msg)
        if val is not None:
            move_time_ms = max(10, int(val))
            break
        if msg in ("ok", "skip"):
            break
        print(f"[Parse] Invalid time payload '{msg}', waiting...")

    print(f"[Local] HintSkill={skill_level} | HintTime={move_time_ms}ms")


# -----------------------------
# Core gameplay loop
# -----------------------------
class GoToModeSelect(Exception):
    """Raise this to fully reset and return to mode selection."""


def play_game(ser: serial.Serial, mode: str) -> None:
    """
    Unified game loop for both 'stockfish' and 'local'.
    - 'stockfish': engine responds as needed.
    - 'local': both sides are human; engine used only for hints.
    - 'n' (heypin) anywhere -> GoToModeSelect to re-ask mode.
    """
    reset_game_state()
    gameover_reported = False

    # Opening display hints
    if mode == "stockfish":
        if not human_is_white and board.turn == chess.WHITE:
            # Engine starts
            send_to_screen("Engine starts", "Thinking…")
            engine_move_and_send(ser)
        else:
            send_to_screen(
                "You are white" if human_is_white else "You are black", "Your move…"
            )
    else:
        # local
        sendtoboard(ser, "turn_white")
        send_to_screen("Local Play", "White to move")

    while True:
        # Global "new game" detection (also used at game over)
        if board.is_game_over():
            if not gameover_reported:
                report_game_over(ser)
                gameover_reported = True
                send_to_screen("Press n", "to start over")

        # If engine should play (only in stockfish mode)
        if mode == "stockfish":
            engine_should_move = (board.turn == chess.WHITE and not human_is_white) or (
                board.turn == chess.BLACK and human_is_white
            )
            if engine_should_move and not board.is_game_over():
                send_to_screen("Engine", "Thinking…")
                engine_move_and_send(ser)
                continue

        # Wait for player input
        msg = getboard(ser)
        if msg is None:
            continue

        # NEW GAME -> back to mode selection
        if (
            msg.startswith("n") or msg == "new" or msg == "in"
        ):  # tolerate different 'new' variants
            raise GoToModeSelect()

        # HINT request in any mode
        if msg == "hint" or msg.startswith("hint"):
            send_hint_to_board(ser)
            continue

        # Ignore non-move control in local/stockfish after setup
        # Expect a move from the side to move (always human in local)
        uci = parse_move_payload(msg)
        if not uci:
            sendtoboard(ser, f"error_invalid_{msg}")
            send_to_screen("Invalid", msg, "Try again")
            continue

        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            sendtoboard(ser, f"error_invalid_{uci}")
            send_to_screen("Invalid move", uci, f"{turn_name()} again")
            continue

        if move not in board.legal_moves:
            sendtoboard(ser, f"error_illegal_{uci}")
            send_to_screen("Illegal move!", uci, f"{turn_name()} again")
            continue

        # Apply human move
        board.push(move)
        next_turn = turn_name()
        send_to_screen(f"{uci[0:2]} → {uci[2:4]}", "", f"{next_turn} to move")
        print(board)

        # Optional inform Arduino whose turn (for UI)
        sendtoboard(ser, f"turn_{'white' if board.turn == chess.WHITE else 'black'}")


# -----------------------------
# Online placeholder
# -----------------------------
def run_online_mode(ser: serial.Serial) -> None:
    send_to_screen("Online mode", "Not implemented", "Use Stockfish/Local")
    sendtoboard(ser, "error_online_unimplemented")


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
def mode_dispatch(ser: serial.Serial, mode: str) -> None:
    """
    Prepare and run the selected mode.
    Any 'n' (new game) during setup or gameplay -> GoToModeSelect is raised.
    """
    if mode == "stockfish":
        setup_stockfish(ser)
        play_game(ser, "stockfish")
    elif mode == "local":
        setup_local(ser)
        play_game(ser, "local")
    else:
        run_online_mode(ser)
        # after placeholder, return to mode selection automatically
        raise GoToModeSelect()


def main():
    global engine

    # Start/ensure display server
    restart_display_server()
    wait_for_display_server_ready()

    # Open engine
    print("[Init] Opening engine…")
    engine = open_engine(STOCKFISH_PATH)
    print("[Init] Engine OK")

    # Open serial
    print(f"[Init] Opening serial {SERIAL_PORT} @ {BAUD}…")
    ser = open_serial()
    print("[Init] Serial OK")

    # Mode selection loop
    while True:
        try:
            mode = select_mode(ser)
            mode_dispatch(ser, mode)
        except GoToModeSelect:
            # full reset and re-ask
            reset_game_state()
            send_to_screen("NEW", "GAME")
            time.sleep(0.2)
            continue
        except KeyboardInterrupt:
            print("\n[Exit] KeyboardInterrupt")
            break
        except Exception as e:
            print(f"[Fatal] {e}")
            traceback.print_exc()
            time.sleep(1)
            # try again from mode selection
            continue

    # Cleanup
    if engine:
        try:
            engine.quit()
            print("[Exit] Engine closed cleanly.")
        except Exception:
            pass


if __name__ == "__main__":
    main()
