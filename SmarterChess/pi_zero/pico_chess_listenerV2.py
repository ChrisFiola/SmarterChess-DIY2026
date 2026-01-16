import serial
import time
import chess
import chess.engine

# -----------------------------
# UART Setup (Pico → Pi Zero)
# -----------------------------
ser = serial.Serial('/dev/serial0', 115200, timeout=1)

# -----------------------------
# Chess Engine Setup (Pi Zero friendly)
# -----------------------------
engine_path = "/usr/games/stockfish"

# Synchronous initialization avoids asyncio timeouts
try:
    engine = chess.engine.SimpleEngine.popen_uci(engine_path, timeout=None)  # <- None disables timeout
except Exception as e:
    print("Failed to start Stockfish:", e)
    exit(1)

# -----------------------------
# Initialize Board
# -----------------------------
board = chess.Board()
print("Board ready:")
print(board)

player_color = chess.WHITE  # Player is White

# -----------------------------
# Helper: process player moves
# -----------------------------
def process_player_move(move_str):
    move_str = move_str.lower()
    try:
        move_obj = chess.Move.from_uci(move_str)
    except ValueError:
        print("Invalid UCI format:", move_str)
        return False
    if move_obj in board.legal_moves:
        board.push(move_obj)
        print("\nPlayer move accepted:", move_str)
        print(board)
        return True
    else:
        print("Illegal move:", move_str)
        return False

# -----------------------------
# Main Game Loop
# -----------------------------
print("\nListening for moves from Pico keypad...")
while not board.is_game_over():
    if board.turn == player_color:
        # Player's turn
        if ser.in_waiting > 0:
            move = ser.readline().decode('utf-8').strip()
            if move:
                print("\nReceived move:", move)
                success = process_player_move(move)
                if not success:
                    print("Try again.")
        time.sleep(0.05)
    else:
        # Engine's turn
        print("\nStockfish thinking...")
        try:
            result = engine.play(board, chess.engine.Limit(time=0.1))
            board.push(result.move)
            print("Engine plays:", result.move)
            print(board)
        except Exception as e:
            print("Engine failed:", e)
            break

# -----------------------------
# Game Over
# -----------------------------
print("\nGame over!")
if board.is_checkmate():
    print("Checkmate!")
elif board.is_stalemate():
    print("Stalemate!")
elif board.is_insufficient_material():
    print("Draw by insufficient material")
else:
    print("Draw by other reason")

engine.quit()
