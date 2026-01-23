#!/home/king/chessenv/bin/python
# -*- coding: utf-8 -*-
"""
SmarterChess (refactored + fixed reset logic)
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
SERIAL_PORT = "/dev/serial0"
BAUD = 115200
SERIAL_TIMEOUT = 2.0

STOCKFISH_PATH = "/usr/games/stockfish"
DEFAULT_SKILL = 5
DEFAULT_MOVE_TIME_MS = 2000

# -----------------------------
# Globals
# -----------------------------
engine: Optional[chess.engine.SimpleEngine] = None
board = chess.Board()

skill_level = DEFAULT_SKILL
move_time_ms = DEFAULT_MOVE_TIME_MS
human_is_white = True
game_started = False


# -----------------------------
# OLED Support
# -----------------------------
def wait_for_display_server_ready():
    while not os.path.exists("/tmp/display_server_ready"):
        time.sleep(0.05)


def restart_display_server():
    PIPE = "/tmp/lcdpipe"
    subprocess.Popen("pkill -f display_server.py", shell=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.2)
    if not os.path.exists(PIPE):
        os.mkfifo(PIPE)
    subprocess.Popen(
        ["python3", "/home/king/SmarterChess-DIY2026/RaspberryPiCode/display_server.py"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def send_to_screen(msg, size="auto"):
    payload = "|".join(msg.split("\n")) + f"|{size}\n"
    with open("/tmp/lcdpipe", "w") as pipe:
        pipe.write(payload)


# -----------------------------
# Serial Helpers
# -----------------------------
def open_serial() -> serial.Serial:
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=SERIAL_TIMEOUT)
    ser.reset_input_buffer()
    return ser


def sendtoboard(ser: serial.Serial, text: str) -> None:
    ser.write(f"heyArduino{text}\n".encode())
    print(f"[Pi→Board] {text}")


def getboard(ser: serial.Serial) -> Optional[str]:
    raw = ser.readline()
    if not raw:
        return None
    try:
        s = raw.decode().strip().lower()
    except UnicodeDecodeError:
        return None

    if s.startswith("heypixshutdown"):
        shutdown_pi(ser)
        return None

    if s.startswith("heypi"):
        return s[5:]

    return None


# -----------------------------
# Chess helpers
# -----------------------------
def reset_game_state():
    global board, game_started
    board = chess.Board()
    game_started = False


def parse_move_payload(p: str) -> Optional[str]:
    if not p:
        return None
    if p.startswith("m"):
        p = p[1:]
    p = "".join(c for c in p if c.isalnum()).lower()
    return p if 4 <= len(p) <= 5 else None


def parse_side_choice(s: str) -> Optional[bool]:
    if s.startswith("s1"):
        return True
    if s.startswith("s2"):
        return False
    if s.startswith("s3"):
        return bool(random.getrandbits(1))
    return None


# -----------------------------
# Stockfish
# -----------------------------
def open_engine(path: str) -> chess.engine.SimpleEngine:
    while True:
        try:
            return chess.engine.SimpleEngine.popen_uci(path)
        except Exception:
            time.sleep(1)


def engine_bestmove(ms: int) -> Optional[str]:
    if board.is_game_over():
        return None
    result = engine.play(board, chess.engine.Limit(time=ms / 1000))
    return result.move.uci() if result.move else None


def send_hint_to_board(ser):
    if board.is_game_over():
        sendtoboard(ser, "hint_gameover")
        return

    try:
        info = engine.analyse(board, chess.engine.Limit(time=move_time_ms / 1000))
        pv = info.get("pv")
        if not pv:
            raise RuntimeError
        best_move = pv[0].uci()
    except Exception:
        best_move = engine_bestmove(move_time_ms)
        if not best_move:
            return

    sendtoboard(ser, f"hint_{best_move}")
    send_to_screen(f"Hint\n{best_move}")


# -----------------------------
# Core gameplay
# -----------------------------
class GoToModeSelect(Exception):
    pass


def hard_reset_board(ser):
    sendtoboard(ser, "ResetBoard")
    time.sleep(0.15)
    ser.reset_input_buffer()


def play_game(ser, mode):
    global game_started
    reset_game_state()

    hard_reset_board(ser)
    sendtoboard(ser, "GameStart")

    # Initial turn sync
    sendtoboard(ser, "turn_white")
    game_started = True

    if mode == "stockfish" and not human_is_white:
        move = engine_bestmove(move_time_ms)
        if move:
            board.push_uci(move)
            sendtoboard(ser, f"m{move}")
            sendtoboard(ser, "turn_white")

    while True:
        if board.is_game_over():
            send_to_screen("Game Over\nPress N")
            msg = getboard(ser)
            if msg and msg.startswith("n"):
                raise GoToModeSelect()
            continue

        msg = getboard(ser)
        if not msg:
            continue

        if msg.startswith("n"):
            raise GoToModeSelect()

        if msg == "hint":
            send_hint_to_board(ser)
            continue

        uci = parse_move_payload(msg)
        if not uci:
            continue

        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            continue

        if move not in board.legal_moves:
            continue

        board.push(move)
        sendtoboard(
            ser,
            f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
        )

        if mode == "stockfish":
            reply = engine_bestmove(move_time_ms)
            if reply:
                board.push_uci(reply)
                sendtoboard(ser, f"m{reply}")
                sendtoboard(
                    ser,
                    f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
                )


# -----------------------------
# Shutdown
# -----------------------------
def shutdown_pi(ser):
    send_to_screen("Shutting down…")
    time.sleep(2)
    subprocess.call("sudo shutdown -h now", shell=True)


# -----------------------------
# Main
# -----------------------------
def main():
    global engine

    restart_display_server()
    wait_for_display_server_ready()

    engine = open_engine(STOCKFISH_PATH)
    ser = open_serial()

    while True:
        try:
            sendtoboard(ser, "ChooseMode")
            send_to_screen("1 PC\n2 Local")

            mode_msg = getboard(ser)
            if not mode_msg:
                continue

            if mode_msg.startswith("1"):
                play_game(ser, "stockfish")
            elif mode_msg.startswith("2"):
                play_game(ser, "local")

        except GoToModeSelect:
            hard_reset_board(ser)
            continue
        except KeyboardInterrupt:
            break
        except Exception:
            traceback.print_exc()
            time.sleep(1)

    if engine:
        engine.quit()


if __name__ == "__main__":
    main()
