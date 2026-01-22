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


# ---- Non-blocking button state (for countdown screens) ----
_last_state = [1] * len(BUTTON_PINS)        # 1 = not pressed (pull-up)
_press_start_ms = [None] * len(BUTTON_PINS) # None or start time in ms
_longpress_fired = [False] * len(BUTTON_PINS)


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

    now = time.ticks_ms()

    for idx, btn in enumerate(buttons):
        cur = btn.value()  # 0 = pressed, 1 = released (PULL_UP)
        prev = _last_state[idx]

        # Edge: just pressed
        if prev == 1 and cur == 0:
            _press_start_ms[idx] = now
            _longpress_fired[idx] = False

        # Held
        if cur == 0 and _press_start_ms[idx] is not None:
            held_ms = time.ticks_diff(now, _press_start_ms[idx])
            if (held_ms > LONG_PRESS_MS) and not _longpress_fired[idx]:
                _longpress_fired[idx] = True
                # Emit immediately on long hold (no wait for release)
                _last_state[idx] = cur
                return (idx + 1, True)

        # Edge: just released
        if prev == 0 and cur == 1:
            if _press_start_ms[idx] is not None:
                held_ms = time.ticks_diff(now, _press_start_ms[idx])
                was_long = held_ms > LONG_PRESS_MS
                # Only emit short press on release if not already emitted as long press
                if not was_long and not _longpress_fired[idx]:
                    # Debounce *after* emitting by ignoring future bounce naturally
                    _press_start_ms[idx] = None
                    _longpress_fired[idx] = False
                    _last_state[idx] = cur
                    return (idx + 1, False)
                # reset after long or whatever
                _press_start_ms[idx] = None
                _longpress_fired[idx] = False

        _last_state[idx] = cur

    return (None, None)


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

def select_engine_strength():
    print("[Info] Pi requests engine strength")

    while True:
        btn, longp = detect_button_with_longpress()
        if longp:
            send_to_pi("n")
            return
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

        btn, longp = detect_button_with_longpress()
        if longp:
            send_to_pi("n")
            return
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
        btn, longp = detect_button_with_longpress()
        if longp:
            send_to_pi("n")
            return
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

        # --- Promotion ---
        if msg.startswith("heyArduinopromotion_choice_needed"):
            print("[Info] Promotion requested by Pi")
            game_state = GAME_PROMOTION

            print("[Prompt] Choose promotion (1=Q,2=R,3=B,4=N)")
            btn = detect_button()          # short press only!
            if btn == 1: send_to_pi("btn_q")
            if btn == 2: send_to_pi("btn_r")
            if btn == 3: send_to_pi("btn_b")
            if btn == 4: send_to_pi("btn_n")

            game_state = GAME_RUNNING
            break

        elif msg.startswith("heyArduinoEngineStrength"):
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
        msg = read_from_pi()
        if not msg:
            time.sleep_ms(10)
            continue

        # --- GLOBAL ACTIONS ---
        # Fully non-blocking button check (no longpress)
        btn, longp = detect_button_with_longpress()
        if longp:
            if btn == 1:
                send_to_pi("btn_new")
                return
            if btn == 2:
                send_to_pi("btn_hint")
                continue

        # --- Promotion ---
        if msg.startswith("heyArduinopromotion_choice_needed"):
            print("[Info] Promotion requested by Pi")
            game_state = GAME_PROMOTION

            print("[Prompt] Choose promotion (1=Q,2=R,3=B,4=N)")
            btn = detect_button()          # short press only!
            if btn == 1: send_to_pi("btn_q")
            if btn == 2: send_to_pi("btn_r")
            if btn == 3: send_to_pi("btn_b")
            if btn == 4: send_to_pi("btn_n")

            game_state = GAME_RUNNING
            break

        elif msg.startswith("heyArduinoGameStart"):
            game_state = GAME_RUNNING
            return    

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


