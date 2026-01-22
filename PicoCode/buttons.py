from machine import Pin, UART
import time

# -----------------------------
# Configuration
# -----------------------------
BUTTON_PINS = [2,3,4,5,6,7,8,9]   # 8 buttons only
DEBOUNCE_MS = 30
LONG_PRESS_MS = 1000

GAME_IDLE = 0
GAME_SETUP = 1
GAME_RUNNING = 2
GAME_PROMOTION = 3


# ---- Non-blocking button state (for countdown screens) ----
_last_btn_state = [1] * len(BUTTON_PINS)        # 1 = not pressed (pull-up)
_press_detected = [False] * len(BUTTON_PINS) # None or start time in ms


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


def send_typing_preview(text: str):
    uart.write(f"heyArduinotyping_{text}\n".encode())


# -----------------------------
# Button handling
# -----------------------------
def detect_button():
    """
    Non-blocking short press detector.
    Returns:
        button_number (1–8) when a short press is completed (pressed → released)
        None if no complete press event yet.
    """
    for idx, btn in enumerate(buttons):
        cur = btn.value()   # 0 = pressed, 1 = released
        prev = _last_btn_state[idx]

        # --- EDGE: PRESSED ---
        if prev == 1 and cur == 0:
            _press_detected[idx] = True
            _press_start = time.ticks_ms()

        # --- EDGE: RELEASED AFTER PRESS ---
        if prev == 0 and cur == 1 and _press_detected[idx]:
            _press_detected[idx] = False
            _last_btn_state[idx] = cur
            return idx + 1

        _last_btn_state[idx] = cur

    return None

def get_coordinate():
    col = None
    row = None

    # Wait for column
    while col is None:
        btn = detect_button()
        if btn:
            col = chr(ord('a') + btn - 1)
            send_typing_preview(col)  # <‑‑ LIVE UPDATE
        time.sleep_ms(5)

    # Wait for row
    while row is None:
        btn = detect_button()
        if btn:
            row = str(btn)
            send_typing_preview(col + row)  # <‑‑ LIVE UPDATE
        time.sleep_ms(5)

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
            break
        elif msg.startswith("heyArduinoChooseMode"):
            game_state = GAME_SETUP
            return

def select_game_mode():
    print("Select mode:")
    print("1 = Stockfish")
    print("2 = Online")
    print("3 = Local")

    while True:
        btn = detect_button()
        if btn == 1:
            send_to_pi("btn_mode_pc")
            return
        if btn == 2:
            send_to_pi("btn_mode_online")
            return
        if btn == 3:
            send_to_pi("btn_mode_local")
            return

def select_engine_strength():
    print("[Info] Pi requests engine strength")

    while True:
        btn = detect_button()
        if btn == 1:
            send_to_pi("1")
            return
        if btn == 2:
            send_to_pi("2")
            return
        if btn == 3:
            send_to_pi("3")
            return
        if btn == 4:
            send_to_pi("4")
            return
        if btn == 5:
            send_to_pi("5")
            return
        if btn == 6:
            send_to_pi("6")
            return
        if btn == 7:
            send_to_pi("7")
            return
        if btn == 8:
            send_to_pi("8")
            return

def select_time_control():
    print("[Info] Pi requests time control")  

    while True:

        btn = detect_button()
        if btn == 1:
            send_to_pi("1")
            return
        if btn == 2:
            send_to_pi("2000")
            return
        if btn == 3:
            send_to_pi("3")
            return
        if btn == 4:
            send_to_pi("4")
            return
        if btn == 5:
            send_to_pi("5")
            return
        if btn == 6:
            send_to_pi("6")
            return
        if btn == 7:
            send_to_pi("7")
            return
        if btn == 8:
            send_to_pi("8")
            return
    # Timeout reached

def select_color_choice():
    print("[Info] Pi requests player color")

    while True:
        btn = detect_button()
        if btn == 1:
            send_to_pi("s1")
            return
        if btn == 2:
            send_to_pi("s2")
            return
        if btn == 3:
            send_to_pi("s3")
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

        if msg.startswith("heyArduinoEngineStrength"):
            game_state = GAME_SETUP
            select_engine_strength()
            return    

        elif msg.startswith("heyArduinoTimeControl"):
            game_state = GAME_SETUP
            select_time_control()
            return

        elif msg.startswith("heyArduinoPlayerColor"):
            game_state = GAME_SETUP
            select_color_choice()
            return
        
        elif msg.startswith("heyArduinoSetupComplete"):
            game_state = GAME_RUNNING
            break


def main_loop():
    global game_state

    print("Entering main loop")

    while True:

        # --- GLOBAL ACTIONS ---
        # Fully non-blocking button check (no longpress)
        btn = detect_button()

        msg = read_from_pi()
        if not msg:
            time.sleep_ms(10)
            continue

        
        elif msg.startswith("heyArduinopromotion_choice_needed"):
            print("[Info] Promotion requested by Pi")
            print("[Prompt] Choose promotion (1=Q,2=R,3=B,4=N)")

            while True:
                btn = detect_button()
                if btn == 1:
                    send_to_pi("btn_q")
                    break
                elif btn == 2:
                    send_to_pi("btn_r")
                    break
                elif btn == 3:
                    send_to_pi("btn_b")
                    break
                elif btn == 4:
                    send_to_pi("btn_n")
                    break
                time.sleep_ms(5)

            continue  # back to main loop


        elif msg.startswith("heyArduinoturn_") or msg.startswith("heyArduinoerror"):
            game_state = GAME_RUNNING

            if msg.startswith("heyArduinoerror"):
                print("[Error] Illegal move reported by Pi")
            
             # --- TURN MESSAGES: ALWAYS REQUEST A MOVE ---
            print("[Info] Your turn now")

            print("[Prompt] Enter move FROM")
            move_from = get_coordinate()

            print("[Prompt] Enter move TO")
            move_to = get_coordinate()

            move = move_from + move_to
            send_to_pi(move)
            print("[Sent] Move:", move)
            return

        
        elif msg.startswith("heyArduinom"):
            game_state = GAME_RUNNING
            pi_move = msg[11:]
            print("Pi move:", pi_move)
            break

# -----------------------------
# Entry
# -----------------------------
print("Pico Chess Controller Starting")

wait_for_mode_request()
select_game_mode()
while game_state == GAME_SETUP:
    wait_for_setup()
while game_state == GAME_RUNNING:
    main_loop()


