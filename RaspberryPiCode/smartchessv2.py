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
DEFAULT_MOVE_TIME_MS = 2000  # engine think time in ms for engine/hints
OLED_SCRIPT = "/home/king/SmarterChess-DIY2026/RaspberryPiCode/printToOLED.py"

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
            payload = raw[5:]
            print(f"[Board->] {raw}  | payload='{payload}'")
            return payload
        # ignore other noise


# -----------------------------
# Chess helpers
# -----------------------------
def turn_name() -> str:
    return "WHITE" if board.turn == chess.WHITE else "BLACK"


def reset_game_state() -> None:
    global board, game_started
    board = chess.Board()
    game_started = False
    print("[Game] Board reset.")


def parse_move_payload(payload: str) -> Optional[str]:
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
    s = (s or "").strip().lower()
    if s.startswith("s1"):
        return True
    if s.startswith("s2"):
        return False
    if s.startswith("s3"):
        return bool(random.getrandbits(1))
    return None


def timed_input_with_oled(ser, prompt1, prompt2, timeout_sec=5, default=None):
    for remaining in range(timeout_sec, 0, -1):
        send_to_screen(prompt1, prompt2, f"{remaining} sec...")

        start = time.time()
        while time.time() - start < 1:
            msg = getboard(ser)
            if msg is None:
                continue
            if msg.startswith("n"):
                raise GoToModeSelect()
            digits = extract_digits(msg)
            if digits is not None:
                return digits

    return default



def requires_promotion(move: chess.Move, brd: chess.Board) -> bool:
    # 1. Move must be legal
    if move not in brd.legal_moves:
        return False

    # 2. The piece must exist
    piece = brd.piece_at(move.from_square)
    if piece is None:
        return False

    # 3. Piece must be a pawn
    if piece.piece_type != chess.PAWN:
        return False

    # 4. Must reach last rank
    to_rank = chess.square_rank(move.to_square)

    if brd.turn == chess.WHITE and to_rank == 7:
        return move.promotion is None

    if brd.turn == chess.BLACK and to_rank == 0:
        return move.promotion is None

    return False



def ask_promotion_piece(ser) -> str:
    send_to_screen(
        "Promotion!",
        "1=Queen",
        "2=Rook 3=Bishop",
        "4=Knight"
    )
    sendtoboard(ser, "promotion_choice_needed")

    while True:
        msg = getboard(ser)
        if msg is None:
            continue

        if msg.startswith("n"):
            raise GoToModeSelect()

        choice = msg.strip()
        if choice in ("btn_q", "btn_queen"):
            return "q"
        if choice in ("btn_r", "btn_rook"):
            return "r"
        if choice in ("btn_b", "btn_bishop"):
            return "b"
        if choice in ("btn_n", "btn_knight"):
            return "n"

        send_to_screen("Promotion!", "1=Q 2=R", "3=B 4=N", "Try again")


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
    global engine  # REQUIRED for stable Stockfish behavior

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
    """Engine plays one move for the side to move."""
    reply = engine_bestmove(board, move_time_ms)
    if reply is None:
        return

    board.push_uci(reply)

    # Send engine move
    sendtoboard(ser, f"m{reply}")

    # CRITICAL: send turn message
    sendtoboard(
        ser,
        f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
    )

    send_to_screen(
        f"{reply[:2]} → {reply[2:4]}",
        "",
        "Your turn"
    )

    print("[Engine]", reply)
    print(board)


# -----------------------------
# Mode setup
# -----------------------------
def select_mode(ser: serial.Serial) -> str:
    sendtoboard(ser, "ChooseMode")
    send_to_screen("Choose opponent:", "1) PC", "2) Remote", "3) Local")
    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        m = msg.strip().lower()
        if m in ("1", "stockfish", "pc", "btn_mode_pc"):
            return "stockfish"
        if m in ("2", "onlinehuman", "remote", "online", "btn_mode_online"):
            return "online"
        if m in ("3", "local", "human", "btn_mode_local"):
            return "local"
        sendtoboard(ser, "error_unknown_mode")
        send_to_screen("Unknown mode", m, "Send again")


def setup_stockfish(ser: serial.Serial) -> None:
    global skill_level, move_time_ms, human_is_white

    send_to_screen("Choose computer", "difficulty (0-20)")
    sendtoboard(ser, "EngineStrength")

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
        print(f"[Parse] Invalid skill payload '{msg}', waiting...")

    send_to_screen("Choose move time", f"(ms, now {move_time_ms})")
    sendtoboard(ser, "TimeControl")
    val = timed_input_with_oled(
        ser, "Choose move time", f"(now {move_time_ms})", timeout_sec=5, default=move_time_ms
    )
    move_time_ms = max(10, val)

    send_to_screen("Choose side", "1 = White, 2 = Black", "3 = Random")
    sendtoboard(ser, "PlayerColor")

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


def setup_local(ser: serial.Serial) -> None:
    global skill_level, move_time_ms

    send_to_screen("Local 2-Player", "Hints enabled")
    time.sleep(0.5)

    sendtoboard(ser, "EngineStrength")
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
        if msg in ("ok", "skip"):
            break

    sendtoboard(ser, "TimeControl")
    send_to_screen("Hint think time", f"ms (now {move_time_ms})")

    val = timed_input_with_oled(
        ser, "Hint think time", f"(now {move_time_ms})", timeout_sec=5, default=move_time_ms
    )
    move_time_ms = max(10, val)


# -----------------------------
# Core gameplay loop
# -----------------------------
class GoToModeSelect(Exception):
    pass


def play_game(ser: serial.Serial, mode: str) -> None:
    reset_game_state()
    global game_started

    sendtoboard(ser, "GameStart")
    gameover_reported = False

    # Opening behavior (match old version)
    if mode == "stockfish":
        if not human_is_white:
            send_to_screen("You are black", "Engine starts", "Thinking…")
            time.sleep(0.1)
            engine_move_and_send(ser)
            game_started = True
        else:
            send_to_screen("You are white", "Your move…")
            sendtoboard(ser, "turn_white")
            game_started = True
    else:
        sendtoboard(ser, "turn_white")
        send_to_screen("Local Play", "White to move")
        game_started = True

    while True:
        if board.is_game_over():
            if not gameover_reported:
                report_game_over(ser)
                gameover_reported = True
                send_to_screen("Press n", "to start over")

        if mode == "stockfish":
            engine_should_move = (
                (board.turn == chess.WHITE and not human_is_white)
                or (board.turn == chess.BLACK and human_is_white)
            )
            if engine_should_move and not board.is_game_over():
                send_to_screen("Engine", "Thinking…")
                engine_move_and_send(ser)
                continue

        msg = getboard(ser)
        if msg is None:
            continue

        if not game_started:
            sendtoboard(ser, "error_game_not_started")
            continue

        if msg in ("n", "new", "in", "newgame", "btn_new"):
            raise GoToModeSelect()

        if msg in ("hint", "btn_hint"):
            send_hint_to_board(ser)
            continue

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

        if requires_promotion(move, board):
            promo_piece = ask_promotion_piece(ser)
            uci = uci + promo_piece
            move = chess.Move.from_uci(uci)

        if move not in board.legal_moves:
            sendtoboard(ser, f"error_illegal_{uci}")
            send_to_screen("Illegal move!", uci, f"{turn_name()} again")
            continue

        board.push(move)
        next_turn = turn_name()

        send_to_screen(f"{uci[:2]} → {uci[2:4]}", "", f"{next_turn} to move")
        print(board)

        sendtoboard(
            ser,
            f"turn_{'white' if board.turn == chess.WHITE else 'black'}"
        )


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
    if mode == "stockfish":
        setup_stockfish(ser)
        sendtoboard(ser, "SetupComplete")
        play_game(ser, "stockfish")
    elif mode == "local":
        setup_local(ser)
        sendtoboard(ser, "SetupComplete")
        play_game(ser, "local")
    else:
        run_online_mode(ser)
        raise GoToModeSelect()


def main():
    global engine

    restart_display_server()
    wait_for_display_server_ready()

    print("[Init] Opening engine…")
    engine = open_engine(STOCKFISH_PATH)
    print("[Init] Engine OK")

    print(f"[Init] Opening serial {SERIAL_PORT} @ {BAUD}…")
    ser = open_serial()
    print("[Init] Serial OK")

    while True:
        try:
            mode = select_mode(ser)
            mode_dispatch(ser, mode)
        except GoToModeSelect:
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
            continue

    if engine:
        try:
            engine.quit()
            print("[Exit] Engine closed cleanly.")
        except Exception:
            pass


if __name__ == "__main__":
    main()
