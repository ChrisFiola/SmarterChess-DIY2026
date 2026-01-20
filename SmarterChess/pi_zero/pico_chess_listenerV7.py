import serial
import chess
import chess.engine
import time

# -----------------------------
# Serial Setup (from Pico)
# -----------------------------
ser = serial.Serial("/dev/serial0", 115200, timeout=1)

# -----------------------------
# Chess Engine Setup with Safe Fallback
# -----------------------------
engine_path = "/usr/games/stockfish"
engine = None

print("Starting Stockfish engine...")

while engine is None:
    try:
        engine = chess.engine.SimpleEngine.popen_uci(
            engine_path, setpgrp=True, timeout=60
        )
        print("Stockfish engine ready!")
    except Exception as e:
        print("Error starting Stockfish:", e)
        print("Retrying in 5 seconds...")
        time.sleep(5)

# -----------------------------
# Notify Pico repeatedly until acknowledged
# -----------------------------
ready_acknowledged = False
print("Sending ready signal to Pico...")

while not ready_acknowledged:
    ser.write(b"CHESS_LISTENER_READY\n")
    # Check for any response from Pico (optional: could be extended to send an ACK back)
    time.sleep(0.5)
    if ser.in_waiting:
        line = ser.readline()
        if line:
            try:
                line = line.decode("utf-8", errors="ignore").strip()
                if line == "READY_ACK":
                    ready_acknowledged = True
                    print("Pico acknowledged ready signal.")
            except:
                pass
    # To avoid spamming too fast
    time.sleep(0.5)

# -----------------------------
# Game State
# -----------------------------
board = chess.Board()
ai_difficulty = 1
opponent = "AI"

print("Initial Board:")
print(board)

# -----------------------------
# Main Loop
# -----------------------------
try:
    while True:
        if ser.in_waiting:
            raw_line = ser.readline()
            if not raw_line:
                continue

            try:
                line = raw_line.decode("utf-8", errors="ignore").strip()
            except Exception as e:
                print("UART decode error:", e)
                continue

            # Handle AI difficulty command
            if line.startswith("AI_DIFFICULTY:"):
                try:
                    ai_difficulty = int(line.split(":")[1])
                    print("AI difficulty set to:", ai_difficulty)
                except:
                    print("Invalid AI difficulty received.")
                continue

            # Handle hint request
            if line == "REQUEST_HINT":
                try:
                    hint_move = engine.play(board, chess.engine.Limit(depth=10))
                    ser.write(("HINT:" + str(hint_move.move) + "\n").encode("utf-8"))
                    print("Hint sent:", hint_move.move)
                except Exception as e:
                    print("Error generating hint:", e)
                continue

            # Handle moves
            move_str = line.upper()
            if len(move_str) == 4:
                try:
                    move = chess.Move.from_uci(move_str.lower())
                    if move in board.legal_moves:
                        board.push(move)
                        print("Move received:", move)
                        print(board)

                        if opponent == "AI":
                            result = engine.play(board, chess.engine.Limit(time=0.1))
                            board.push(result.move)
                            print("AI move:", result.move)
                            print(board)
                            ser.write((str(result.move) + "\n").encode("utf-8"))
                    else:
                        print("Illegal move received:", move)
                except Exception as e:
                    print("Error processing move:", e)
            else:
                print("Invalid move length:", move_str)

        time.sleep(0.05)

except KeyboardInterrupt:
    print("\nExiting...")

finally:
    if engine:
        engine.quit()
    ser.close()
