#!/home/king/chessenv/bin/python
"""
Console Chess using python-chess + Stockfish

Features:
- Move validation (illegal moves rejected)
- Board display in console
- Takeback support
- Engine hints
- Stockfish skill level and move time settings
- Game over detection
"""

import chess
import chess.engine
import sys

# -----------------------------
# Configuration
# -----------------------------
STOCKFISH_PATH = "stockfish"  # adjust path if necessary
DEFAULT_SKILL = 5
DEFAULT_MOVE_TIME_MS = 200

# -----------------------------
# Game state
# -----------------------------
board = chess.Board()
move_list = []
last_move = None
engine_skill = DEFAULT_SKILL
move_time = DEFAULT_MOVE_TIME_MS

# -----------------------------
# Engine setup
# -----------------------------
try:
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
except FileNotFoundError:
    print("Stockfish not found at", STOCKFISH_PATH)
    sys.exit(1)


def set_skill(level: int):
    global engine_skill
    engine_skill = max(0, min(20, level))
    engine.configure({"Skill Level": engine_skill})
    print(f"Skill level set to {engine_skill}")


def set_move_time(ms: int):
    global move_time
    move_time = max(10, ms)
    print(f"Engine move time set to {move_time} ms")


# -----------------------------
# Board display
# -----------------------------
def show_board():
    print()
    print(board.unicode(borders=True))
    print()


# -----------------------------
# Move handling
# -----------------------------
def make_move(move_str: str):
    global last_move
    try:
        move = board.parse_san(move_str)
    except ValueError:
        try:
            move = board.parse_uci(move_str)
        except ValueError:
            print("Illegal move:", move_str)
            return False

    if move not in board.legal_moves:
        print("Illegal move:", move_str)
        return False

    board.push(move)
    last_move = move
    move_list.append(move)
    return True


def takeback():
    if board.move_stack:
        board.pop()
        last_move = board.peek() if board.move_stack else None
        move_list.pop()
        print("Last move undone")
    else:
        print("No moves to undo")


def engine_move():
    """Get Stockfish move using current move time and return SAN string."""
    if board.is_game_over():
        return None
    result = engine.play(board, chess.engine.Limit(time=move_time / 1000))
    move_san = board.san(result.move)  # get SAN while move is still legal
    board.push(result.move)  # now push it
    move_list.append(result.move)
    return move_san


def hint():
    """Get a hint from Stockfish without pushing it."""
    if board.is_game_over():
        print("Game over")
        return None
    info = engine.analyse(board, chess.engine.Limit(time=move_time / 1000))
    print("Hint:", info["pv"][0])


def show_moves():
    if not move_list:
        print("Moves: (none)")
        return
    for i in range(0, len(move_list), 2):
        w = move_list[i]
        b = move_list[i + 1] if i + 1 < len(move_list) else ""
        print(f"{i//2+1}. {board.san(w)} {board.san(b) if b else ''}")


# -----------------------------
# Main loop
# -----------------------------
def main():
    set_skill(DEFAULT_SKILL)
    show_board()
    print("Console Chess")
    print("Type 'help' for commands\n")

    while True:
        cmd = input("> ").strip().lower()
        if not cmd:
            continue

        if cmd == "quit":
            break
        elif cmd == "help":
            print(
                """
Commands:
move <move>     Make a move (e2e4 or Nf3 or e7e8q)
hint            Show best move from engine
undo            Undo last move
new             Start new game
skill <0-20>    Set engine skill
time <ms>       Set engine move time in milliseconds
board           Show board
moves           Show move list
quit            Exit
"""
            )
        elif cmd.startswith("move"):
            parts = cmd.split()
            if len(parts) != 2:
                print("Usage: move e2e4 or Nf3")
                continue
            if not make_move(parts[1]):
                continue
            if board.is_game_over():
                print("Game over:", board.result())
                show_board()
                continue
            m = engine_move()
            if m:
                print("Engine plays:", m)
            show_board()
            if board.is_game_over():
                print("Game over:", board.result())
        elif cmd == "hint":
            hint()
        elif cmd == "undo":
            takeback()
            show_board()
        elif cmd == "new":
            board.reset()
            move_list.clear()
            show_board()
        elif cmd.startswith("skill"):
            try:
                set_skill(int(cmd.split()[1]))
            except:
                print("Usage: skill 0-20")
        elif cmd.startswith("time"):
            try:
                set_move_time(int(cmd.split()[1]))
            except:
                print("Usage: time <ms>")
        elif cmd == "board":
            show_board()
        elif cmd == "moves":
            show_moves()
        else:
            print("Unknown command")

    engine.quit()
    print("Goodbye.")


if __name__ == "__main__":
    main()
