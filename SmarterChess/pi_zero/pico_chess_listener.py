import serial
import time
import chess
import chess.engine

# -----------------------------
# UART Setup
# -----------------------------
# Adjust this to your Pico connection
# e.g., '/dev/ttyAMA0' for GPIO UART, '/dev/ttyACM0' for USB
# My device serial: /dev/serial0 -> ttyS0
ser = serial.Serial('/dev/serial0', 115200, timeout=1)

# -----------------------------
# Chess Engine Setup
# -----------------------------
engine_path = "/usr/games/stockfish"  # adjust path to your Stockfish binary
engine = chess.engine.SimpleEngine.popen_uci(engine_path)

board = chess.Board()
print("Board ready:")
print(board)

# -----------------------------
# Helper: process move from Pico
# -----------------------------
def process_move(move_str):
    move_str = move_str.lower()  # Stockfish expects lowercase UCI
    try:
        move_obj = chess.Move.from_uci(move_str)
    except ValueError:
        print("Invalid UCI format:", move_str)
        return

    if move_obj in board.legal_moves:
        board.push(move_obj)
        print("\nMove accepted:", move_str)
        print(board)
    else:
        print("Illegal move:", move_str)

# -----------------------------
# Main Loop
# -----------------------------
print("\nListening for moves from Pico (e.g., E2E4)...")
while True:
    if ser.in_waiting > 0:
        move = ser.readline().decode('utf-8').strip()
        if move:
            print("\nReceived move:", move)
            process_move(move)
