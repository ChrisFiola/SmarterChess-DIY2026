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
skill_level = 5
move_time = 200  # ms

# -----------------------------
# Engine helpers
# -----------------------------
def sync_engine():
    send("isready")
    read_until("readyok")

def new_game():
    global move_list
    move_list = []
    send("ucinewgame")
    sync_engine()
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
    return "position startpos moves " + " ".join(moves)

def is_legal_move(move):
    send(position_cmd([move]))
    send("go depth 1")
    reply = read_until("bestmove")
    return "bestmove (none)" not in reply

def engine_move():
    send(position_cmd())
    send(f"go movetime {move_time}")
    reply = read_until("bestmove")
    return reply.split()[1]

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
    for l in board_lines[:-1]:
        print(l)
    print()

# -----------------------------
# Startup
# -----------------------------
send("uci")
read_until("uciok")
set_skill(skill_level)
new_game()

print("Console Chess (UCI only)")
print("Type 'help' for commands")
print("""
move e2e4    make a move
hint         show engine hint
undo         take back last full move
new          start new game
skill N      set engine skill (0–20)
time MS      set engine move time (ms)
moves        show moves played
board        show board
quit         exit
""")
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
move e2e4    make a move
hint         show engine hint
undo         take back last full move
new          start new game
skill N      set engine skill (0–20)
time MS      set engine move time (ms)
moves        show move list
board        show board
quit         exit
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
            print("Usage: time <milliseconds>")
        continue

    if cmd == "moves":
        print("Moves:", " ".join(move_list) or "(none)")
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
            print("Last full move undone")
            show_board()
        else:
            print("Nothing to undo")
        continue

    if cmd.startswith("move"):
        try:
            move = cmd.split()[1]

            if not is_legal_move(move):
                print("Illegal move")
                continue

            move_list.append(move)
            show_board()

            best = engine_move()
            if best != "(none)":
                move_list.append(best)
                print("Engine plays:", best)
                show_board()
            else:
                print("Game over")

        except:
            print("Usage: move e2e4")
        continue

    print("Unknown command")

# -----------------------------
# Shutdown
# -----------------------------
engine.terminate()
print("Goodbye.")
