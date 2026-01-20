import serial
import chess
import chess.engine
import time

# -----------------------------
# Serial Setup (Pico)
# -----------------------------
ser = serial.Serial("/dev/serial0", baudrate=115200, timeout=0.05)

# -----------------------------
# Stockfish Setup (fast + robust)
# -----------------------------
ENGINE_PATH = "/usr/games/stockfish"

print("Starting Stockfish...")

try:
    engine = chess.engine.SimpleEngine.popen_uci(ENGINE_PATH, setpgrp=True, timeout=60)
except Exception as e:
    print("FATAL: Stockfish failed to start:", e)
    raise SystemExit(1)

# Warm up engine (prevents first-move lag)
engine.configure({"Threads": 1, "Hash": 8, "Ponder": False})
engine.play(chess.Board(), chess.engine.Limit(depth=1))

print("Stockfish ready.")

# -----------------------------
# Game State
# -----------------------------
board = chess.Board()
ai_difficulty = 1
opponent = "AI"

print(board)


# -----------------------------
# Helpers
# -----------------------------
def is_valid_uci(s):
    return (
        len(s) == 4
        and s[0] in "ABCDEFGH"
        and s[1] in "12345678"
        and s[2] in "ABCDEFGH"
        and s[3] in "12345678"
    )


# -----------------------------
# Main Loop
# -----------------------------
try:
    while True:

        if ser.in_waiting:
            raw = ser.readline()
            if not raw:
                continue

            try:
                line = raw.decode(errors="ignore").strip()
            except:
                continue

            # -------------------------
            # AI difficulty
            # -------------------------
            if line.startswith("AI_DIFFICULTY:"):
                try:
                    ai_difficulty = int(line.split(":")[1])
                    continue
                except:
                    continue

            # -------------------------
            # Hint request
            # -------------------------
            if line == "REQUEST_HINT":
                try:
                    hint = engine.play(board, chess.engine.Limit(depth=10))
                    ser.write(f"HINT:{hint.move}\n".encode())
                except:
                    pass
                continue

            # -------------------------
            # Player move
            # -------------------------
            if is_valid_uci(line):
                try:
                    move = chess.Move.from_uci(line.lower())
                    if move not in board.legal_moves:
                        continue

                    board.push(move)

                    if opponent == "AI":
                        result = engine.play(board, chess.engine.Limit(time=0.1))
                        board.push(result.move)
                        ser.write(f"{result.move}\n".encode())

                except:
                    pass

            # Everything else is ignored silently

        time.sleep(0.01)

except KeyboardInterrupt:
    pass

finally:
    try:
        engine.quit()
    except:
        pass
    ser.close()
