#!/home/king/chessenv/bin/python
# -*- coding: utf-8 -*-
"""
SmarterChess — FINAL (DIY-style + Local + Random + Live Preview)
- Unified game loop for Stockfish & Local 2-player
- DIY Machines wording preserved while respecting your screen's auto sizing/wrapping ("\n" for new lines)
- Live input preview from Pico:
    * heypityping_from_<text>
    * heypityping_to_<from> → <partial_to>
    * heypityping_confirm_<from> → <to>
- New Game behavior: "NEW GAME" then DIY flow (human white: "Please enter your move:")
- Game-over prompt: "Press n to start over"
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
SERIAL_PORT = "/dev/serial0"   # Adjust if needed, e.g. /dev/ttyUSB0
BAUD = 115200
SERIAL_TIMEOUT = 2.0

STOCKFISH_PATH = "/usr/games/stockfish"
DEFAULT_SKILL = 5  # 0..20
DEFAULT_MOVE_TIME_MS = 2000  # ms for engine/hints

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
# OLED Support (Display Server)
# -----------------------------

def restart_display_server():
    PIPE = "/tmp/lcdpipe"
    subprocess.Popen("pkill -f display_server.py", shell=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.2)
    if not os.path.exists(PIPE):
        os.mkfifo(PIPE)
    subprocess.Popen(["python3",
                      "/home/king/SmarterChess-DIY2026/RaspberryPiCode/display_server.py"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def wait_for_display_server_ready():
    READY_FLAG = "/tmp/display_server_ready"
    while not os.path.exists(READY_FLAG):
        time.sleep(0.05)


def send_to_screen(message: str, size: str = "auto") -> None:
    """Your server expects lines separated by '|' and a trailing size token.
       We keep your exact behavior: we split on '\n' so you can author with line breaks.
    """
    parts = message.split("\n")
    payload = "|".join(parts) + f"|{size}\n"
    with open("/tmp/lcdpipe", "w") as pipe:
        pipe.write(payload)

# -----------------------------
# Serial Helpers (Board link)
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


def getboard_nonblocking(ser: serial.Serial) -> Optional[str]:
    if ser.in_waiting:
        raw = ser.readline()
        if not raw:
            return None
        try:
            s = raw.decode("utf-8").strip().lower()
        except UnicodeDecodeError:
            return None
        if s.startswith("heypixshutdown"):
            shutdown_pi(ser)
            return None
        if s.startswith("heypi"):
            payload = s[5:]
            print(f"[Board->] {s}  | payload='{payload}'")
            return payload
    return None


def getboard(ser: serial.Serial) -> Optional[str]:
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
    global board
    board = chess.Board()


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


def parse_side_choice(s: str) -> Optional[bool]:
    s = (s or "").strip().lower()
    if s.startswith("s1"):  # White
        return True
    if s.startswith("s2"):  # Black
        return False
    if s.startswith("s3"):  # Random
        return bool(random.getrandbits(1))
    return None


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

# -----------------------------
# Promotion
# -----------------------------

def ask_promotion_piece(ser: serial.Serial) -> str:
    send_to_screen("Promotion!\n1=Queen\n2=Rook\n3=Bishop\n4=Knight")
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
        # re-prompt
        send_to_screen("Promotion!\n1=Queen\n2=Rook\n3=Bishop\n4=Knight")

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
            print(f"[Engine] ERROR launching {path!r}: {e}")
            time.sleep(1)


def engine_bestmove(brd: chess.Board, ms: int) -> Optional[str]:
    global engine  # Keep reference alive
    if brd.is_game_over():
        return None
    limit = chess.engine.Limit(time=max(0.01, ms / 1000.0))
    result = engine.play(brd, limit)  # type: ignore
    return result.move.uci() if result.move else None


def send_hint_to_board(ser: serial.Serial) -> None:
    if board.is_game_over():
        sendtoboard(ser, "hint_gameover")
        send_to_screen("Game Over\nNo hints\nPress n to start over")
        return
    best_move: Optional[str] = None
    try:
        info = engine.analyse(  # type: ignore
            board,
            chess.engine.Limit(time=max(0.01, move_time_ms / 1000.0))
        )
        pv = info.get("pv")
        if pv:
            best_move = pv[0].uci()
    except Exception:
        best_move = engine_bestmove(board, move_time_ms)

    if not best_move:
        sendtoboard(ser, "hint_none")
        return

    sendtoboard(ser, f"hint_{best_move}")
    send_to_screen("Hint\n" + best_move)
    print(f"[Hint] {best_move}")

# -----------------------------
# Game flow utilities
# -----------------------------

def report_game_over(ser: serial.Serial) -> None:
    result = board.result(claim_draw=True)
    sendtoboard(ser, f"GameOver:{result}")
    send_to_screen("Game Over\nResult " + result + "\nPress n to start over")


def engine_move_and_send(ser: serial.Serial) -> None:
    reply = engine_bestmove(board, move_time_ms)
    if reply is None:
        return
    board.push_uci(reply)
    sendtoboard(ser, f"m{reply}")
    # hand off turn
    sendtoboard(ser, f"turn_{'white' if board.turn == chess.WHITE else 'black'}")
    send_to_screen(f"{reply[:2]} → {reply[2:4]}\nYour go...")
    print("[Engine]", reply)
    print(board)

# -----------------------------
# Mode setup
# -----------------------------

def select_mode(ser: serial.Serial) -> str:
    sendtoboard(ser, "ChooseMode")
    send_to_screen("Choose opponent:\n1) Against PC\n2) Remote human\n3) Local 2-player")
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
        send_to_screen("Unknown mode\n" + m + "\nSend again")


def setup_stockfish(ser: serial.Serial) -> None:
    global skill_level, move_time_ms, human_is_white

    # Difficulty (DIY wording)
    send_to_screen("Choose computer\ndifficulty level:\n(0 -> 8)")
    sendtoboard(ser, "EngineStrength")
    sendtoboard(ser, f"default_strength_{skill_level}")
    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        if msg.startswith("n"):
            raise GoToModeSelect()
        if msg.isdigit():
            skill_level = max(0, min(int(msg), 20))
            break

    # Time
    send_to_screen("Choose computer\nmove time:\n(0 -> 8)")
    sendtoboard(ser, "TimeControl")
    sendtoboard(ser, f"default_time_{move_time_ms}")
    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        if msg.startswith("n"):
            raise GoToModeSelect()
        if msg.isdigit():
            move_time_ms = max(10, int(msg))
            break

    # Color selection with Random
    send_to_screen("Select a colour:\n1 = White/First\n2 = Black/Second\n3 = Random")
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


def setup_local(ser: serial.Serial) -> None:
    global skill_level, move_time_ms
    send_to_screen("Local 2-Player\nHints enabled")
    time.sleep(2)

    # Use same hint strength/time knobs for Local
    send_to_screen("Choose computer\ndifficulty level:\n(0 -> 8)")
    sendtoboard(ser, "EngineStrength")
    sendtoboard(ser, f"default_strength_{skill_level}")
    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        if msg.isdigit():
            skill_level = max(0, min(int(msg), 20))
            break

    send_to_screen("Choose computer\nmove time:\n(0 -> 8)")
    sendtoboard(ser, "TimeControl")
    sendtoboard(ser, f"default_time_{move_time_ms}")
    while True:
        msg = getboard(ser)
        if msg is None:
            continue
        if msg.isdigit():
            move_time_ms = max(10, int(msg))
            break

# -----------------------------
# Core gameplay loop
# -----------------------------
class GoToModeSelect(Exception):
    pass


def play_game(ser: serial.Serial, mode: str) -> None:
    global game_started
    reset_game_state()
    sendtoboard(ser, "GameStart")

    # DIY Reset flow shows NEW GAME first
    send_to_screen("NEW GAME")
    time.sleep(0.5)

    if mode == "stockfish":
        # Opening behavior
        if not human_is_white:
            send_to_screen("You are black\nEngine starts\nThinking...")
            time.sleep(0.1)
            engine_move_and_send(ser)
            game_started = True
        else:
            send_to_screen("Please enter\nyour move:")
            sendtoboard(ser, "turn_white")
            game_started = True
    else:
        # Local 2-Player
        sendtoboard(ser, "turn_white")
        send_to_screen("Please enter\nyour move:")
        game_started = True

    while True:
        # Live typing preview from Pico (FROM/TO/CONFIRM only)
        msg = getboard_nonblocking(ser)
        if msg is not None and msg.startswith("typing_"):
            try:
                _, label, text = msg.split("_", 2)
                label = label.lower()
                if label == "from":
                    send_to_screen("Enter from:\n" + text)
                elif label == "to":
                    send_to_screen("Enter to:\n" + text)
                elif label == "confirm":
                    send_to_screen("Confirm move:\n" + text + "\nPress OK or re-enter")
            except Exception:
                pass
            # do not continue; allow other events same cycle

        # Game-over reporting
        if board.is_game_over():
            # Show only once then continue to allow 'n'
            send_to_screen("Game Over\nResult " + board.result(claim_draw=True) + "\nPress n to start over")

        # Engine move when needed (stockfish mode only)
        if mode == "stockfish":
            engine_should_move = (
                (board.turn == chess.WHITE and not human_is_white) or
                (board.turn == chess.BLACK and human_is_white)
            )
            if engine_should_move and not board.is_game_over():
                send_to_screen("Engine Thinking...")
                engine_move_and_send(ser)
                continue

        # Blocking read for next board message
        msg = getboard(ser)
        if msg is None:
            continue

        # Mode switch
        if msg in ("n", "new", "in", "newgame", "btn_new"):
            raise GoToModeSelect()

        # Hints
        if msg in ("hint", "btn_hint"):
            send_hint_to_board(ser)
            continue

        # Ignore typing echoes caught here; handled above
        if msg.startswith("typing_"):
            continue

        # Move processing
        uci = parse_move_payload(msg)
        if not uci:
            sendtoboard(ser, f"error_invalid_{msg}")
            send_to_screen("Invalid\n" + msg + "\nTry again")
            continue

        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            sendtoboard(ser, f"error_invalid_{uci}")
            send_to_screen("Invalid move\n" + uci + f"\n{turn_name()} again")
            continue

        if requires_promotion(move, board):
            promo = ask_promotion_piece(ser)
            uci = uci + promo
            move = chess.Move.from_uci(uci)

        if move not in board.legal_moves:
            sendtoboard(ser, f"error_illegal_{uci}")
            send_to_screen("Illegal move!\nEnter new\nmove...")
            continue

        # Accept player's move
        board.push(move)
        next_turn = turn_name()
        send_to_screen(f"{uci[:2]} → {uci[2:4]}\n{next_turn} to move")
        print(board)

        # Hand off to Pico for next turn
        sendtoboard(ser, f"turn_{'white' if board.turn == chess.WHITE else 'black'}")

        # If it's human to move (local or human side in stockfish), show DIY prompt
        if (mode == 'local') or (mode == 'stockfish' and
           ((board.turn == chess.WHITE and human_is_white) or
            (board.turn == chess.BLACK and not human_is_white))):
            send_to_screen("Please enter\nyour move:")

# -----------------------------
# Online placeholder
# -----------------------------

def run_online_mode(ser: serial.Serial) -> None:
    send_to_screen("Online mode not implemented\nUse Stockfish/Local")
    sendtoboard(ser, "error_online_unimplemented")

# -----------------------------
# Shutdown
# -----------------------------

def shutdown_pi(ser: Optional[serial.Serial]) -> None:
    send_to_screen("Shutting down...\nWait 20s then\ndisconnect power.")
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

    engine = open_engine(STOCKFISH_PATH)

    ser = open_serial()

    while True:
        try:
            mode = select_mode(ser)
            mode_dispatch(ser, mode)
        except GoToModeSelect:
            # DIY: NEW GAME then restart loop
            reset_game_state()
            send_to_screen("NEW GAME")
            time.sleep(0.4)
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
