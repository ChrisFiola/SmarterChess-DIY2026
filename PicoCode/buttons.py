
from machine import Pin, UART
import time

# -----------------------------
# Configuration
# -----------------------------
BUTTON_PINS = [2,3,4,5,6,7,8,9]   # 8 buttons
DEBOUNCE_MS = 20  # fast & responsive

GAME_IDLE = 0
GAME_SETUP = 1
GAME_RUNNING = 2
GAME_PROMOTION = 3

# Button state tracking
_last_btn_state = [1] * len(BUTTON_PINS)  # 1 = released
_press_detected = [False] * len(BUTTON_PINS)

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

def send_typing_preview(label: str, text: str):
    """
    Sends:
      heyArduinotyping_FROM_e2
      heyArduinotyping_TO_e2 → e4
    """
    uart.write(f"heypityping_{label}_{text}\n".encode())


# -----------------------------
# Non-blocking button detection (INSTANT PRESS)
# -----------------------------
def detect_button():
    """
    Immediate edge detection on PRESS, not release.
    Returns button number (1–8) or None.
    """
    for idx, btn in enumerate(buttons):
        cur = btn.value()  # 0 = pressed, 1 = released
        prev = _last_btn_state[idx]

        _last_btn_state[idx] = cur

        # PRESSED NOW
        if prev == 1 and cur == 0:
            time.sleep_ms(DEBOUNCE_MS)
            return idx + 1

    return None



def get_from_square():
    col = None
    row = None

    # Column
    while col is None:
        btn = detect_button()
        if btn:
            col = chr(ord('a') + btn - 1)
            send_typing_preview("from", col)
        time.sleep_ms(5)

    # Row
    while row is None:
        btn = detect_button()
        if btn:
            row = str(btn)
            send_typing_preview("from", col + row)
        time.sleep_ms(5)

    return col + row


def get_to_square(move_from):
    col = None
    row = None

    # Column
    while col is None:
        btn = detect_button()
        if btn:
            col = chr(ord('a') + btn - 1)
            send_typing_preview("to", move_from + " → " + col)
        time.sleep_ms(5)

    # Row
    while row is None:
        btn = detect_button()
        if btn:
            row = str(btn)
            send_typing_preview("to", move_from + " → " + col + row)
        time.sleep_ms(5)

    return col + row

# -----------------------------
# Setup mode (unchanged)
# -----------------------------
def wait_for_mode_request():
    global game_state
    print("Waiting for Pi...")
    while True:
        msg = read_from_pi()
        if not msg:
            continue
        if msg.startswith("heyArduinoChooseMode"):
            game_state = GAME_SETUP
            return


def select_game_mode():
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
    while True:
        btn = detect_button()
        if btn:
            send_to_pi(str(btn))
            return


def select_time_control():
    while True:
        btn = detect_button()
        if btn:
            if btn == 2:
                send_to_pi("2000")
            else:
                send_to_pi(str(btn))
            return


def select_color_choice():
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
    global game_state

    while True:
        msg = read_from_pi()
        if not msg:
            time.sleep_ms(10)
            continue

        if msg.startswith("heyArduinoEngineStrength"):
            select_engine_strength()
            return

        elif msg.startswith("heyArduinoTimeControl"):
            select_time_control()
            return

        elif msg.startswith("heyArduinoPlayerColor"):
            select_color_choice()
            return

        elif msg.startswith("heyArduinoSetupComplete"):
            game_state = GAME_RUNNING
            break


# -----------------------------
# Main Loop
# -----------------------------
def main_loop():
    global game_state

    print("Entering main loop")

    while True:
        msg = read_from_pi()
        if not msg:
            time.sleep_ms(10)
            continue

        # ----- Promotion -----
        if msg.startswith("heyArduinopromotion_choice_needed"):
            print("[PROMO] Choose Q/R/B/N")
            while True:
                btn = detect_button()
                if btn == 1:
                    send_to_pi("btn_q"); break
                if btn == 2:
                    send_to_pi("btn_r"); break
                if btn == 3:
                    send_to_pi("btn_b"); break
                if btn == 4:
                    send_to_pi("btn_n"); break
                time.sleep_ms(5)
            continue

        # ----- Turn / error → ask for move -----
        if msg.startswith("heyArduinoturn_") or msg.startswith("heyArduinoerror"):
            print("[TURN] Your turn")

            print("Enter move FROM")
            move_from = get_from_square()

            print("Enter move TO")
            move_to = get_to_square(move_from)

            move = move_from + move_to
            send_to_pi(move)
            print("[Sent move]", move)
            return

        # ----- Pi move -----
        if msg.startswith("heyArduinom"):
            print("Pi move:", msg[11:])
            break


# -----------------------------
# Program Entry
# -----------------------------
print("Pico Chess Controller Starting")

wait_for_mode_request()
select_game_mode()

while game_state == GAME_SETUP:
    wait_for_setup()

while game_state == GAME_RUNNING:
    main_loop()
