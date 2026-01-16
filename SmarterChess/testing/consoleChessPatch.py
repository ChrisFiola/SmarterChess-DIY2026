import subprocess

ENGINE_PATH = "stockfish"

# -----------------------------
# Engine startup
# -----------------------------
engine = subprocess.Popen(
    ENGINE_PATH,
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    universal_newlines=True,
    bufsize=1
)

def send(cmd):
    engine.stdin.write(cmd + "\n")
    engine.stdin.flush()

def read_line():
    return engine.stdout.readline().strip()

def read_until(prefix):
    while True:
        line = read_line()
        if line.startswith(prefix):
            return line

# -----------------------------
# Game state
# -----------------------------
move_list = []
last_move = None
skill_level = 5
move_time = 200  # ms
legal_moves_cache = set()  # Cache for fast illegal-move checking

# -----------------------------
# Engine helpers
# -----------------------------
def sync_engine():
    send("isready")
    read_until("readyok")

def new_game():
    global move_list, last_move
    move_list = []
    last_move = None
    send("ucinewgame")
    sync_engine()
    update_legal_moves()
    print("\n=== NEW GAME ===")
    show_board()

def set_skill(level):
    global skill_level
    skill_level = max(0, min(20, level))
    send(f"setoption name Skill Level value {skill_level}")
    sync_engine()
    print(f"Skill level set to {skill_level}")

def position_cmd(extra=None):
    moves = move_list[:]
    if extra:
        moves += extra
    if moves:
        return "position startpos moves " + " ".join(moves)
    return "position startpos"

# -----------------------------
# Move handling
# -----------------------------
def normalize_move(move):
    """Add promotion to queen if pawn reaches last rank"""
    if len(move) == 4:
        from_rank = int(move[1])
        to_rank = int(move[3])
        piece_file = move[0].lower()
        if piece_file == 'p' and (to_rank == 8 or to_rank == 1):
            return move + "q"
        else:
            return move
    return move

def update_legal_moves():
    """Update the legal moves cache from Stockfish"""
    global legal_moves_cache
    sync_engine()  # Make sure Stockfish is ready
    send(position_cmd())
    send("d")
    while True:
        line = read_line()
        if line == "":  # safeguard in case nothing is returned
            continue
        if line.startswith("Legal moves:"):
            legal_moves_cache = set(line[len("Legal moves:"):].strip().split())
            break

def is_legal_move(move):
    """Check if move is legal by asking Stockfish to search only that move"""
    move = normalize_move(move)
    if not move:
        return False
    send(position_cmd([move]))
    send("go depth 1 movetime 50")
    reply = read_until("bestmove")
    best = reply.split()[1]
    # If bestmove is not (none), the move is legal
    return best != "(none)"

def engine_move():
    send(position_cmd())
    send(f"go movetime {move_time}")
    reply = read_until("bestmove")
    best = reply.split()[1]
    return best

def hint():
    send(position_cmd())
    send("go depth 10")
    reply = read_until("bestmove")
    return reply.split()[1]

# -----------------------------
# Board display
# -----------------------------
def show_board():
    send(position_cmd())
    send("d")
    board_lines = []
    capture = False
    while True:
        line = read_line()
        if line.startswith("+---"):
            capture = True
        if capture:
            board_lines.append(line)
        if capture and line.startswith("Fen:"):
            break
    print()
    for row in board_lines[:-1]:
        if last_move and "|" in row:
            a, b = last_move[:2], last_move[2:4]
            if a[1] in row or b[1] in row:
                print(row.replace("|", "║"))
            else:
                print(row)
        else:
            print(row)
    print()

# -----------------------------
# Game-over detection
# -----------------------------
def check_game_over():
    send(position_cmd())
    send("go depth 1")
    reply = read_until("bestmove")
    if "bestmove (none)" in reply:
        print("=== GAME OVER ===")
        return True
    return False

# -----------------------------
# Startup
# -----------------------------
send("uci")
read_until("uciok")
set_skill(skill_level)
send("ucinewgame")
sync_engine()
new_game()

print("Console Chess")
print("Type 'help' for commands")

# -----------------------------
# Main loop
# -----------------------------
while True:
    cmd = input("> ").strip().lower()
    if not cmd:
        continue
    if cmd == "quit":
        break
    if cmd == "help":
        print("""
move e2e4      make a move
move e7e8q     promotion (default = q)
hint           engine hint
undo           undo last full move
new            new game
skill N        engine skill (0–20)
time MS        engine move time
board          show board
moves          show move list
quit           exit
""")
        continue
    if cmd == "new":
        new_game()
        continue
    if cmd.startswith("skill"):
        try:
            set_skill(int(cmd.split()[1]))
        except:
            print("Usage: skill 0–20")
        continue
    if cmd.startswith("time"):
        try:
            move_time = int(cmd.split()[1])
            print(f"Move time set to {move_time} ms")
        except:
            print("Usage: time <ms>")
        continue
    if cmd == "moves":
        if not move_list:
            print("Moves: (none)")
        else:
            for i in range(0, len(move_list), 2):
                w = move_list[i]
                b = move_list[i+1] if i+1 < len(move_list) else ""
                print(f"{i//2+1}. {w} {b}")
        continue
    if cmd == "board":
        show_board()
        continue
    if cmd == "hint":
        print("Hint:", hint())
        continue
    if cmd == "undo":
        if len(move_list) >= 2:
            move_list.pop()
            move_list.pop()
            last_move = None
            update_legal_moves()
            print("Last full move undone")
            show_board()
        else:
            print("Nothing to undo")
        continue
    if cmd.startswith("move"):
        try:
            move = normalize_move(cmd.split()[1])
            if not is_legal_move(move):
                print("Illegal move")
                continue
            move_list.append(move)
            last_move = move
            update_legal_moves()
            show_board()
            if check_game_over():
                continue
            best = engine_move()
            if best != "(none)":
                move_list.append(best)
                last_move = best
                update_legal_moves()
                show_board()
                check_game_over()
            else:
                print("Game over")
        except:
            print("Usage: move e2e4 or e7e8q")
        continue
    print("Unknown command")

# -----------------------------
# Shutdown
# -----------------------------
engine.terminate()
print("Goodbye.")
