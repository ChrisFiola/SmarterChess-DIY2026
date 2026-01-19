
#!/usr/bin/env python3
"""
Rewritten SmartChess hardware-controller
Modern Python‑3 version using python‑chess

Replaces:
- Python 2.7 code
- ChessBoard library
with:
- python-chess
- modern subprocess control of Stockfish

Supports:
- Arduino serial hardware board
- OLED display messages
- Local Stockfish mode
- Online human mode
"""

import chess
import chess.engine
import serial
import subprocess
import time
import sys

# -------------------------
# CONFIGURATION
# -------------------------
SERIAL_PORT = "/dev/pts/8"      # Update for your hardware: /dev/ttyUSB0 etc.
BAUD = 115200
STOCKFISH_PATH = "stockfish"

# -------------------------
# INITIALIZE ENGINE
# -------------------------
try:
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
except FileNotFoundError:
    print("ERROR: Stockfish not found.")
    sys.exit(1)

# -------------------------
# INITIALIZE SERIAL
# -------------------------
ser = serial.Serial(SERIAL_PORT, BAUD, timeout=2)
ser.flush()

# -------------------------
# GAME STATE
# -------------------------
board = chess.Board()
full_move_string = ""       # "e2e4 e7e5 ..."
skill_level = 5
move_time_ms = 1000
remotePlayer = None         # For online mode
colourChoice = None


# ---------------------------------------------------------
# OLED DISPLAY
# ---------------------------------------------------------
def sendToScreen(a, b="", c="", size="14"):
    """Send three lines of text to the OLED script."""
    subprocess.Popen([
        "python3", "/home/king/SmartChess/RaspberryPiCode/printToOLED.py",
        "-a", a, "-b", b, "-c", c, "-s", size
    ])


# ---------------------------------------------------------
# STOCKFISH CONTROL
# ---------------------------------------------------------
def engine_set_skill(level: int):
    lvl = max(0, min(20, level))
    engine.configure({"Skill Level": lvl})


def engine_bestmove():
    """Return best move from Stockfish (UCI format)."""
    if board.is_game_over():
        return None
    result = engine.play(board, chess.engine.Limit(time=move_time_ms / 1000))
    return result.move.uci()


# ---------------------------------------------------------
# SERIAL COMMUNICATION
# ---------------------------------------------------------
def getboard():
    """Wait for Arduino board message beginning with 'heypi'."""
    print("Waiting for command from the board...")
    while True:
        if ser.inWaiting() > 0:
            msg = ser.readline().decode("utf-8").strip().lower()
            if msg.startswith("heypixshutdown"):
                shutdownPi()
                return None
            if msg.startswith("heypi"):
                return msg[len("heypi"):]
            # else ignore noise


def sendtoboard(txt):
    """Send message to Arduino hardware."""
    payload = ("heyArduino" + txt).encode("utf-8")
    time.sleep(0.1)
    ser.write(payload + b"\n")
    print("Sent to board:", txt)


# ---------------------------------------------------------
# ONLINE HUMAN (Adafruit) WRAPPER
# ---------------------------------------------------------
def putAdafruit(command):
    print("Sending to remote:", command)
    remotePlayer.stdin.write(command + "\n")
    remotePlayer.stdin.flush()
    # Wait for remote confirmation
    while True:
        line = remotePlayer.stdout.readline().strip()
        if "piece moved" in line:
            print("Remote ack:", line)
            break


def getAdafruit():
    print("Waiting for remote move...")
    remotePlayer.stdin.write("receive\n")
    remotePlayer.stdin.write(colourChoice + "\n")
    remotePlayer.stdin.flush()
    move = remotePlayer.stdout.readline().strip()
    print("Remote move:", move)
    return move


# ---------------------------------------------------------
# GAME MANAGEMENT
# ---------------------------------------------------------
def reset_game():
    global board, full_move_string
    board = chess.Board()
    full_move_string = ""
    sendToScreen("NEW", "GAME")
    return ""


def apply_player_move(uci_move: str):
    global full_move_string

    try:
        move = chess.Move.from_uci(uci_move)
    except ValueError:
        sendtoboard("error_invalid_" + uci_move)
        return False

    if move not in board.legal_moves:
        sendtoboard("error_illegal_" + uci_move)
        return False

    board.push(move)
    full_move_string += " " + uci_move
    return True


def play_stockfish_reply():
    global full_move_string

    best = engine_bestmove()
    if best is None:
        return None

    move = chess.Move.from_uci(best)
    board.push(move)
    full_move_string += " " + best
    return best


# ---------------------------------------------------------
# MAIN GAMEPLAY LOOP FOR STOCKFISH MODE
# ---------------------------------------------------------
def run_stockfish_mode():
    global skill_level, move_time_ms, full_move_string

    sendtoboard("ReadyStockfish")
    sendToScreen("Choose computer", "difficulty (0-20)")
    skill_level = int(getboard()[1:])
    engine_set_skill(skill_level)

    sendToScreen("Choose move time", "(ms)")
    move_time_ms = int(getboard()[1:])

    full_move_string = reset_game()
    sendToScreen("Your move:")

    while True:
        msg = getboard()
        if not msg:
            continue
        code = msg[0]

        if code == "n":
            full_move_string = reset_game()
            continue

        if code == "m":
            uci = msg[1:5]
            if not apply_player_move(uci):
                continue

            # Send player move to OLED
            sendToScreen(uci[0:2] + "→" + uci[2:4], "", "Thinking...")

            # Engine reply
            reply = play_stockfish_reply()
            if reply:
                sendtoboard("m" + reply)
                sendToScreen(reply[0:2] + "→" + reply[2:4], "", "Your turn")


# ---------------------------------------------------------
# MAIN GAMEPLAY LOOP FOR ONLINE MODE
# ---------------------------------------------------------
def run_online_mode():
    global colourChoice, full_move_string

    updateScript = ["python3", "/home/king/SmartChess/RaspberryPiCode/update-online.py"]
    global remotePlayer
    remotePlayer = subprocess.Popen(updateScript, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, universal_newlines=True)

    sendtoboard("ReadyOnlinePlay")
    sendToScreen("Select colour", "1=White 2=Black")

    colourChoice = getboard()

    putAdafruit("ready")
    full_move_string = reset_game()

    skipFirst = (colourChoice == "cblack")

    while True:
        if skipFirst:
            mv = getAdafruit()
            apply_player_move(mv)
            sendtoboard("m" + mv)
            skipFirst = False

        msg = getboard()
        code = msg[0]

        if code == "n":
            full_move_string = reset_game()

        if code == "m":
            uci = msg[1:5]
            if apply_player_move(uci):
                putAdafruit(uci)
                # Get remote reply
                mv = getAdafruit()
                apply_player_move(mv)
                sendtoboard("m" + mv)


# ---------------------------------------------------------
# SYSTEM SHUTDOWN
# ---------------------------------------------------------
def shutdownPi():
    sendToScreen("Shutting down...", "Please wait 20s")
    time.sleep(3)
    subprocess.call("sudo shutdown -h now", shell=True)


# ---------------------------------------------------------
# GAME MODE SELECTION
# ---------------------------------------------------------
def main():
    sendtoboard("ChooseMode")
    sendToScreen("Choose opponent:", "1) PC", "2) Remote")

    mode = getboard()
    print("Gameplay mode:", mode)

    if mode == "stockfish":
        run_stockfish_mode()
    elif mode == "onlinehuman":
        run_online_mode()
    else:
        sendtoboard("error_unknown_mode")


if __name__ == "__main__":
    main()

