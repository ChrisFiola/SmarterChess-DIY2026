from machine import Pin, UART
import time

# -----------------------------
# Configuration
# -----------------------------
BUTTON_PINS = [2,3,4,5,6,7,8,9]   # 8 buttons only
DEBOUNCE_MS = 300
LONG_PRESS_MS = 1000

GAME_IDLE = 0
GAME_SETUP = 1
GAME_RUNNING = 2
GAME_PROMOTION = 3

game_state = GAME_IDLE

uart = UART(
    0,
    baudrate=115200,
    tx=Pin(0),
    rx=Pin(1),
    timeout=10
)

buttons = [Pin(gpio, Pin.IN, Pin.PULL_UP) for gpio in BUTTON_PINS]

# -----------------------------
# Serial helpers
# -----------------------------
def send_to_pi(message_type, payload=""):
    msg = f"heypi{message_type}{payload}\n"
    uart.write(msg.encode())
    print(">>", msg.strip())

def read_from_pi():
    if uart.any():
        line = uart.readline()
        if line:
            try:
                msg = line.decode().strip()
                print("<<", msg)
                return msg
            except:
                return None
    return None

# -----------------------------
# Button handling
# -----------------------------
def detect_button():
    while True:
        for idx, btn in enumerate(buttons):
            if btn.value() == 0:
                time.sleep_ms(DEBOUNCE_MS)
                while btn.value() == 0:
                    pass
                return idx + 1
        time.sleep_ms(10)

def detect_button_with_longpress():
    while True:
        for idx, btn in enumerate(buttons):
            if btn.value() == 0:
                start = time.ticks_ms()
                while btn.value() == 0:
                    if time.ticks_diff(time.ticks_ms(), start) > LONG_PRESS_MS:
                        return idx + 1, True
                time.sleep_ms(DEBOUNCE_MS)
                return idx + 1, False
        time.sleep_ms(10)

def get_coordinate():
    col_btn = detect_button()
    col = chr(ord('a') + col_btn - 1)
    row_btn = detect_button()
    row = str(row_btn)
    return col + row

# -----------------------------
# Main logic
# -----------------------------
def wait_for_mode_request():
    global game_state
    print("Waiting for Pi...")
    while True:
        msg = read_from_pi()
        if not msg:
            continue
        if msg.startswith("heyArduinopromotion_choice_needed"):
            game_state = GAME_PROMOTION
            continue
        elif msg.startswith("heyArduinoChooseMode"):
            return

def select_game_mode():
    print("Select mode:")
    print("1 = Stockfish")
    print("2 = Online")
    print("3 = Local")

    while True:
        btn, longp = detect_button_with_longpress()
        if longp:
            continue
        if btn == 1:
            send_to_pi("btn_mode_pc")
            return
        if btn == 2:
            send_to_pi("btn_mode_online")
            return
        if btn == 3:
            send_to_pi("btn_mode_local")
            return

def wait_for_setup():
    """
    Handles all setup prompts (hint strength, color, time, etc.)
    """
    global game_state
    print("Waiting for setup prompts...")

    while True:
        msg = read_from_pi()
        if not msg:
            time.sleep_ms(10)
            continue

        print("Setup message:", msg)

        if msg.startswith("heyArduinopromotion_choice_needed"):
            game_state = GAME_PROMOTION
            print("[Info] Promotion requested during setup")
            continue

        elif msg.startswith("heyArduinoEngineStrength"):
            game_state = GAME_SETUP
            print("[Info] Pi requests engine strength")
            while True:
                btn, longp = detect_button_with_longpress()
                if longp:
                    send_to_pi("n")
                    return
                if btn == 1:
                    send_to_pi("1")
                    continue
                if btn == 2:
                    send_to_pi("2")
                    continue
                if btn == 3:
                    send_to_pi("3")
                    continue
                if btn == 4:
                    send_to_pi("4")
                    continue
                if btn == 5:
                    send_to_pi("5")
                    continue
                if btn == 6:
                    send_to_pi("6")
                    continue
                if btn == 7:
                    send_to_pi("7")
                    continue
                if btn == 8:
                    send_to_pi("8")
                    continue
            continue

        elif msg.startswith("heyArduinoTimeControl"):
            game_state = GAME_SETUP
            print("[Info] Pi requests time control")
            
            start = time.time()
            while time.time() - start < 1:
                if longp:
                    send_to_pi("n")
                    return
                if btn == 1:
                    send_to_pi("1")
                    continue
                if btn == 2:
                    send_to_pi("2")
                    continue
                if btn == 3:
                    send_to_pi("3")
                    continue
                if btn == 4:
                    send_to_pi("4")
                    continue
                if btn == 5:
                    send_to_pi("5")
                    continue
                if btn == 6:
                    send_to_pi("6")
                    continue
                if btn == 7:
                    send_to_pi("7")
                    continue
                if btn == 8:
                    send_to_pi("8")
                    continue
            continue

        elif msg.startswith("heyArduinoPlayerColor"):
            game_state = GAME_SETUP
            print("[Info] Pi requests player color")
            if longp:
                send_to_pi("n")
                return
            if btn == 1:
                send_to_pi("1")
                continue
            if btn == 2:
                send_to_pi("2")
                continue
            if btn == 3:
                send_to_pi("3")
                continue
            continue

def main_loop():
    global game_state
    print("Entering main loop")

    while True:
        # 1️⃣ Check serial messages first
        msg = read_from_pi()
        if not msg:
            time.sleep_ms(10)
            continue
        if msg:
            if msg.startswith("heyArduinopromotion_choice_needed"):
                print("[Info] Promotion requested by Pi")
                game_state = GAME_PROMOTION
            elif msg.startswith("heyArduinoerror"):
                print("[Error] Illegal move reported by Pi")
            elif msg.startswith("heyArduinom"):
                pi_move = msg[11:]
                print("Pi move:", pi_move)

        # 2️⃣ Handle global long-press commands
        btn, longp = detect_button_with_longpress()
        if longp:
            if btn == 1:
                print("[Command] Request new game")
                send_to_pi("btn_new")
                return
            if btn == 2 and game_state == GAME_RUNNING:
                print("[Command] Request hint")
                send_to_pi("btn_hint")
                continue

        # 3️⃣ Handle promotion if requested
        if game_state == GAME_PROMOTION:
            print("[Prompt] Choose promotion (1=Q, 2=R, 3=B, 4=N)")
            btn, _ = detect_button_with_longpress()
            if btn == 1:
                send_to_pi("btn_q")
            elif btn == 2:
                send_to_pi("btn_r")
            elif btn == 3:
                send_to_pi("btn_b")
            elif btn == 4:
                send_to_pi("btn_n")
            game_state = GAME_RUNNING
            print("[Info] Promotion sent, resuming game")
            continue
        # 4️⃣ Handle normal human move
        if msg.startswith("heyArduinoGameStart"):
            game_state == GAME_RUNNING
            print("[Info] Game starting")
            print("[Prompt] Enter move FROM (column+row)")
            move_from = get_coordinate()
            print("[Prompt] Enter move TO (column+row)")
            move_to = get_coordinate()

            move = move_from + move_to
            send_to_pi("M", move)
            print(f"[Sent] Move {move} sent to Pi")

            # 5️⃣ Wait briefly for Pi to respond with error or promotion request
            start = time.ticks_ms()
            while time.ticks_diff(time.ticks_ms(), start) < 3000:
                msg = read_from_pi()
                if msg:
                    if msg.startswith("heyArduinopromotion_choice_needed"):
                        print("[Info] Promotion requested during move")
                        game_state = GAME_PROMOTION
                        break
                    elif msg.startswith("heyArduinoerror"):
                        print("[Error] Illegal move reported by Pi")
                        break

# -----------------------------
# Entry
# -----------------------------
print("Pico Chess Controller Starting")

wait_for_mode_request()
select_game_mode()
wait_for_setup()
main_loop()


