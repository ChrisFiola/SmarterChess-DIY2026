import subprocess
import time

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

def sync_engine():
    send("isready")
    read_until("readyok")

# -----------------------------
# Game state
# -----------------------------
move_list = []
last_move = None
skill_level = 5
move_time = 200  # ms

# -----------------------------
# Helpers
# -----------------------------
def normalize_move(move):
    move = move.lower()
    if len(move) == 4:
        from_rank = int(move[1])
        to_rank = int(move[3])
        # Auto promotion to queen
        if (from_rank == 7 and to_rank == 8) or (from_rank == 2 and to_rank == 1):
            return move + "q"
    return move

def position_cmd(extra=None):
    moves = move_list[:]
    if extra:
        moves += extra
    if moves:
        return "position startpos moves " + " ".join(moves)
    return "position startpos"

def is_legal_move(move):
    move = normalize_move(move)
    send(position_cmd([move]))
    send("go depth 1 movetime 50")
    reply = read_until("bestmove")
    best = reply.split()[1]
    return best != "(none)"

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
