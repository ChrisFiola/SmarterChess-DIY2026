
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

default_move_time = 2000
default_strength = 5

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

def try_send_hint(btn):
    """If Button 8 is pressed, request a hint from the Pi and return True; else False."""
    if btn == 8:
        send_to_pi("btn_hint")
        # Small debounce to avoid spamming if the button is held
        time.sleep_ms(150)
        return True
    return False


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


def timed_button_choice(timeout_sec, default_value):
    start = time.ticks_ms()
    timeout_ms = timeout_sec * 1000

    while True:
        # 1. CHECK FOR BUTTON PRESS
        btn = detect_button()
        if btn:
            return btn  # user selected a value

        # 2. CHECK TIMEOUT
        elapsed = time.ticks_diff(time.ticks_ms(), start)
        if elapsed >= timeout_ms:
            return default_value  # time expired, use default

        time.sleep_ms(5)


def get_from_square():
    col = None
    row = None

    # Column
    while col is None:
        btn = detect_button()
        if btn:
            # Allow hint during FROM entry
            if try_send_hint(btn):
                continue

            col = chr(ord('a') + btn - 1)
            send_typing_preview("from", col)
        time.sleep_ms(5)

    # Row
    while row is None:
        btn = detect_button()
        if btn:
            # Allow hint during FROM entry
            if try_send_hint(btn):
                continue

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
            # Allow hint during TO entry
            if try_send_hint(btn):
                continue

            col = chr(ord('a') + btn - 1)
            send_typing_preview("to", move_from + " → " + col)
        time.sleep_ms(5)

    # Row
    while row is None:
        btn = detect_button()
        if btn:
            # Allow hint during TO entry
            if try_send_hint(btn):
                continue

            row = str(btn)
            send_typing_preview("to", move_from + " → " + col + row)
        time.sleep_ms(5)


    return col + row


def select_strength_with_buttons(default_value, min_val=0, max_val=20, timeout_sec=5):
    value = default_value
    start = time.ticks_ms()
    timeout_ms = timeout_sec * 1000
    last_sent = None

    # Send initial value
    send_typing_preview("strength", str(value))

    while True:
        # BUTTON HANDLING
        btn = detect_button()
        if btn:
            start = time.ticks_ms()  # reset timer

            if btn == 2:         # increment
                value = min(max_val, value + 1)
            elif btn == 1:       # decrement
                value = max(min_val, value - 1)
            elif btn == 3:       # OK
                return value

            send_typing_preview("strength", str(value))

        # TIMEOUT CHECK
        #elapsed = time.ticks_diff(time.ticks_ms(), start)
        #if elapsed >= timeout_ms:
            #return value

        time.sleep_ms(10)


def select_time_with_buttons(default_value, min_val=100, max_val=20000, timeout_sec=5):
    value = default_value
    start = time.ticks_ms()
    timeout_ms = timeout_sec * 1000

    send_typing_preview("time", str(value))

    while True:
        btn = detect_button()
        if btn:
            start = time.ticks_ms()  # reset timer

            if btn == 2:               # +100
                value = min(max_val, value + 100)
            elif btn == 1:             # -100
                value = max(min_val, value - 100)
            elif btn == 3:             # OK
                return value

            send_typing_preview("time", str(value))

        #elapsed = time.ticks_diff(time.ticks_ms(), start)
        #if elapsed >= timeout_ms:
            #return value

        time.sleep_ms(10)


def reset_buttons():
    for i, btn in enumerate(buttons):
        _last_btn_state[i] = btn.value()


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
    global game_state, default_strength, default_move_time

    while True:
        msg = read_from_pi()
        if not msg:
            time.sleep_ms(10)
            continue
            
        elif msg.startswith("heyArduinodefault_strength_"):
            try:
                default_strength = int(msg.split("_")[-1])
                print("Default strength from Pi:", default_strength)
            except:
                pass

        
        elif msg.startswith("heyArduinoEngineStrength"):
            reset_buttons()
            sel = select_strength_with_buttons(default_strength)
            send_to_pi(str(sel))
            return

        
        elif msg.startswith("heyArduinodefault_time_"):
            try:
                default_move_time = int(msg.split("_")[-1])
                print("Default time from Pi:", default_move_time)
            except:
                pass

        
        elif msg.startswith("heyArduinoTimeControl"):
            reset_buttons()
            sel = select_time_with_buttons(default_move_time)
            send_to_pi(str(sel))
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
