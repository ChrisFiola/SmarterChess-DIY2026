import serial
import time
import chess
import chess.engine

# -----------------------------
# UART Setup (from Pico)
# -----------------------------
ser = serial.Serial('/dev/serial0', 115200, timeout=1)

# -----------------------------
# Chess Engine Setup
# -----------------------------
engine_path = "/usr/games/stockfish"  # Adjust if needed
engine = chess.engine.SimpleEngine.popen_uci(engine_path, timeout=60)

# -----------------------------
# Game State
# -----------------------------
board = chess.Board()
opponent = 'H'   # default Human, Pico menu can override
ai_skill = 1     # Stockfish skill level (1–20)
ai_elo = 400     # optional ELO mapping

print("Chess listener ready.")
print("Initial Board:\n", board)

def print_board_moves():
    print("\nCurrent board:")
    print(board)
    print("Legal moves:", [board.san(m) for m in board.legal_moves])
    print("---------------------------\n")

# -----------------------------
# Main Loop
# -----------------------------
while not board.is_game_over():
    if ser.in_waiting:
        raw_line = ser.readline().decode().strip()

        # -------------------------
        # AI difficulty input
        # -------------------------
        if raw_line.startswith("AI_DIFFICULTY:"):
            val = raw_line.split(":")[1]
            try:
                ai_skill = max(1, min(20, int(val)))
                ai_elo = ai_skill * 100  # optional mapping
                opponent = 'A'
                print(f"AI opponent selected, skill level: {ai_skill}, ELO: {ai_elo}")
            except ValueError:
                print("Invalid AI difficulty received:", val)
            continue

        # -------------------------
        # Move input
        # -------------------------
        if len(raw_line) != 4:
            print("Invalid move length:", raw_line)
            continue

        from_sq = raw_line[:2].lower()
        to_sq = raw_line[2:].lower()

        try:
            move = chess.Move.from_uci(from_sq + to_sq)
        except ValueError:
            print("Invalid move format:", raw_line)
            continue

        if move not in board.legal_moves:
            print("Illegal move attempted:", raw_line)
            continue

        # Apply player's move
        board.push(move)
        print("Player move applied:", raw_line)
        print_board_moves()

        # -----------------------------
        # If opponent is AI, generate move
        # -----------------------------
        if opponent == 'A' and not board.is_game_over():
            # Set Stockfish skill level
            engine.configure({"Skill Level": ai_skill})
            result = engine.play(board, chess.engine.Limit(time=0.5))
            ai_move = result.move
            board.push(ai_move)

            # Send AI move back to Pico
            ser.write(str(ai_move) + b"\n")
            print("AI move applied and sent:", ai_move)
            print_board_moves()

print("Game over!")
print("Result:", board.result())

engine.quit()
