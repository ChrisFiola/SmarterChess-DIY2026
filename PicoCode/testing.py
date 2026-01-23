
from machine import Pin, UART
import time
import neopixel

# ============================================================
# CONFIGURATION
# ============================================================

# Button wiring (active‑low)
#  Button 1 = GP2
#  Button 2 = GP3
#  Button 3 = GP4
#  Button 4 = GP5
#  Button 5 = GP6
#  Button 6 = GP7
#  Button 7 = GP8   <-- A1 equivalent (OK)
#  Button 8 = GP9   <-- Hint interrupt source (pin 3 equivalent)

BUTTON_PINS = [2, 3, 4, 5, 6, 7, 8, 9]
DEBOUNCE_MS = 20

# Map special roles
OK_BUTTON_INDEX = 6     # BUTTON_PINS[6] = GP8
HINT_BUTTON_INDEX = 7   # BUTTON_PINS[7] = GP9

# Pico pins
CONTROL_PANEL_LED_PIN = 12
CONTROL_PANEL_LED_COUNT = 22

CHESSBOARD_LED_PIN = 28
BOARD_W, BOARD_H = 8, 8

# ============================================================
# STATE FLAGS (match Arduino behavior EXACTLY)
# ============================================================

confirm_mode = False     # Only TRUE during OK confirmation
in_setup = False         # Setup phase (mode/strength/time/color)
in_input = False         # When entering FROM/TO
hint_irq_flag = False    # Raised by real interrupt
hint_hold_mode = False   # When True, a hint is pinned on the board until OK (Button 7)
hint_waiting = False
showing_hint = False

game_state = 0           # Idle/Setup/Running
GAME_IDLE = 0
GAME_SETUP = 1
GAME_RUNNING = 2

# ============================================================
# UART
# ============================================================

uart = UART(
    0,
    baudrate=115200,
    tx=Pin(0),
    rx=Pin(1),
    timeout=10
)

# ============================================================
# BUTTON OBJECTS
# ============================================================

buttons = [Pin(g, Pin.IN, Pin.PULL_UP) for g in BUTTON_PINS]

BTN_OK = buttons[OK_BUTTON_INDEX]      # GP8 (A1)
BTN_HINT = buttons[HINT_BUTTON_INDEX]  # GP9 (interrupt source)

_last = [1] * len(buttons)

# ============================================================
# IRQ HANDLER — EXACT ARDUINO BEHAVIOR
# ============================================================

def disable_hint_irq():
    BTN_HINT.irq(handler=None)

def enable_hint_irq():
    BTN_HINT.irq(trigger=Pin.IRQ_FALLING, handler=hint_irq)


def hint_irq(pin):
    """
    EXACT Arduino v27 behavior:
    - Interrupt fires when HINT is pressed (falling edge)
    - Inside ISR, Arduino checks A1 (OK button)
    - If A1 LOW → New Game
    - Else → Hint
    """
    global hint_irq_flag
    hint_irq_flag = True

# FALLING EDGE interrupt — same as Arduino pin 3
BTN_HINT.irq(trigger=Pin.IRQ_FALLING, handler=hint_irq)

# ============================================================
# SERIAL HELPERS
# ============================================================

def send_to_pi(kind, payload=""):
    uart.write(f"heypi{kind}{payload}\n".encode())

def read_from_pi():
    if uart.any():
        try:
            return uart.readline().decode().strip()
        except:
            return None
    return None

def send_typing_preview(label, text):
    uart.write(f"heypityping_{label}_{text}\n".encode())

# ============================================================
# LEDS: Control Panel (22 px) + Chessboard (8x8)
# ============================================================

# Orientation flags for matrix mapping (match Arduino NeoMatrix:
# BOTTOM + RIGHT + ROWS + ZIGZAG)
MATRIX_ORIGIN_BOTTOM_RIGHT = True
MATRIX_ZIGZAG = True

# Initialize NeoPixels
cp = neopixel.NeoPixel(Pin(CONTROL_PANEL_LED_PIN, Pin.OUT), CONTROL_PANEL_LED_COUNT)
board_np = neopixel.NeoPixel(Pin(CHESSBOARD_LED_PIN, Pin.OUT), BOARD_W * BOARD_H)

# Colors (RGB)
BLACK   = (0, 0, 0)
WHITE   = (255, 255, 255)
DIMW    = (10, 10, 10)
RED     = (255, 0, 0)
GREEN   = (0, 255, 0)
BLUE    = (0, 0, 255)
CYAN    = (0, 255, 255)
MAGENTA = (255, 0, 255)
YELLOW  = (255, 255, 0)
ORANGE  = (255, 130, 0)

# Control panel pixel roles (mirroring Arduino semantics)
CP_COORD_START = 0   # first 4 pixels: "coordinate lights"
CP_OK_PIX      = 4   # OK indicator
CP_HINT_PIX    = 5   # hint indicator

def cp_fill(color, start=0, count=None):
    if count is None:
        count = CONTROL_PANEL_LED_COUNT - start
    end = min(CONTROL_PANEL_LED_COUNT, start + count)
    for i in range(start, end):
        cp[i] = color
    cp.write()

def cp_set(idx, color):
    if 0 <= idx < CONTROL_PANEL_LED_COUNT:
        cp[idx] = color
        cp.write()

def cp_coord_on():
    cp_fill(WHITE, CP_COORD_START, 4)

def cp_coord_off():
    cp_fill(BLACK, CP_COORD_START, 4)

def cp_ok_on():
    cp_set(CP_OK_PIX, WHITE)

def cp_ok_off():
    cp_set(CP_OK_PIX, BLACK)

def cp_hint_on(color=WHITE):
    cp_set(CP_HINT_PIX, color)

def cp_hint_off():
    cp_set(CP_HINT_PIX, BLACK)

# ---------------- Board (8x8) helpers ----------------

def board_clear(color=BLACK):
    for i in range(BOARD_W * BOARD_H):
        board_np[i] = color
    board_np.write()

def _xy_to_index(x, y):
    """
    Map (x,y) -> NeoPixel index for:
     - origin bottom-right
     - rows zig-zag
     - rows numbered bottom(0) to top(7)
    """
    # y is 0..7 from bottom to top (like rank-1)
    row = y
    if MATRIX_ORIGIN_BOTTOM_RIGHT:
        # columns numbered from RIGHT to LEFT on even rows, then zigzag
        if MATRIX_ZIGZAG:
            if row % 2 == 0:
                col_index = (BOARD_W - 1) - x   # even row: right->left
            else:
                col_index = x                   # odd row: left->right
        else:
            col_index = (BOARD_W - 1) - x
        idx = row * BOARD_W + col_index
    else:
        # fallback (not used here): top-left origin
        row_from_top = (BOARD_H - 1) - y
        if MATRIX_ZIGZAG:
            if row_from_top % 2 == 0:
                col_index = x
            else:
                col_index = (BOARD_W - 1) - x
        else:
            col_index = x
        idx = row_from_top * BOARD_W + col_index
    return idx

def set_square(x, y, color):
    if 0 <= x < BOARD_W and 0 <= y < BOARD_H:
        board_np[_xy_to_index(x, y)] = color

def algebraic_to_xy(sq):
    """
    'a1' -> (0,0) ... 'h8' -> (7,7)
    a-file is left, 1st rank bottom (standard chess coordinates).
    """
    if not sq or len(sq) < 2:
        return None
    f = sq[0].lower()
    r = sq[1]
    if not ('a' <= f <= 'h'):
        return None
    if not ('1' <= r <= '8'):
        return None
    x = ord(f) - ord('a')         # file a..h -> 0..7
    y = int(r) - 1                # rank 1..8 -> 0..7 (bottom to top)
    return (x, y)

def show_chessboard_markings():
    # simple checkered board
    for y in range(BOARD_H):
        for x in range(BOARD_W):
            if (x + y) % 2 == 0:
                color = (80, 80, 80)   # dark
            else:
                color = (160, 160, 160)  # light
            set_square(x, y, color)
    board_np.write()

def show_chessboard_opening_markings():
    # simple diagonal sweep intro animation
    board_clear(BLACK)
    for k in range(BOARD_W + BOARD_H - 1):
        for y in range(BOARD_H):
            x = k - y
            if 0 <= x < BOARD_W:
                set_square(x, y, GREEN)
        board_np.write()
        time.sleep_ms(25)
    time.sleep_ms(150)
    show_chessboard_markings()

def loading_status(count):
    """
    Light squares progressively (bottom->top, right->left) to 64.
    Returns updated count.
    """
    total = BOARD_W * BOARD_H
    if count >= total:
        return count
    idx = count
    y = idx // BOARD_W
    x = (BOARD_W - 1) - (idx % BOARD_W)  # right to left
    set_square(x, y, BLUE)
    board_np.write()
    return count + 1

def error_flash(times=3):
    for _ in range(times):
        # Blue fill + red cross
        board_clear(BLUE)
        for i in range(8):
            set_square(i, 7 - i, RED)
            set_square(i, i, RED)
        board_np.write()
        time.sleep_ms(450)
        show_chessboard_markings()
        time.sleep_ms(450)

def light_up_move(m, mode='Y'):
    """
    m: 'e2e4' or 'e7e8q' (we use first 4 chars)
    mode: 'Y' (human), 'N' (engine), 'H' (hint)
    """
    if not m or len(m) < 4:
        return
    frm = m[:2]
    to = m[2:4]

    if mode == 'Y':
        c_from, c_to = YELLOW, WHITE
    elif mode == 'N':
        c_from, c_to = ORANGE, GREEN
    else:  # 'H'
        c_from, c_to = CYAN, BLUE

    xy_f = algebraic_to_xy(frm)
    xy_t = algebraic_to_xy(to)
    if xy_f:
        set_square(xy_f[0], xy_f[1], c_from)
    if xy_t:
        set_square(xy_t[0], xy_t[1], c_to)
    board_np.write()

# ============================================================
# BUTTON DETECTION + SMALL HELPERS (active‑low)
# ============================================================

def reset_buttons():
    """Synchronize debounced states to current levels, so we don't get stale presses."""
    for i, btn in enumerate(buttons):
        _last[i] = btn.value()

def detect_button():
    """
    Immediate edge detection on PRESS (falling edge), not release.
    Returns button number (1..8) or None.
    """
    for idx, btn in enumerate(buttons):
        cur = btn.value()         # 0 = pressed, 1 = released
        prev = _last[idx]
        _last[idx] = cur
        if prev == 1 and cur == 0:
            time.sleep_ms(DEBOUNCE_MS)
            return idx + 1
    return None

# Arduino-style integer map()
def map_range(x, in_min, in_max, out_min, out_max):
    return int((x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)


# ============================================================
# DEFAULTS (Arduino-like)
# ============================================================
default_strength = 5       # 0..20 (we map 1..8 to 1..20 during setup)
default_move_time = 2000   # milliseconds (we map 1..8 to 3000..12000 during setup)


# ============================================================
# SETUP: Handshake, Mode Select, Strength/Time/Color (Arduino-like)
# ============================================================

def wait_for_mode_request():
    """
    Wait for 'heyArduinoChooseMode' from the Pi.
    Show loading animation and fill the chessboard to 64 squares like Arduino.
    """
    global game_state
    print("Waiting for Pi...")
    show_chessboard_opening_markings()
    chessSquaresLit = 0

    while True:
        # loading pulse every ~1s
        chessSquaresLit = loading_status(chessSquaresLit)
        time.sleep_ms(1000)

        msg = read_from_pi()
        if not msg:
            # Interrupt may set hint flag; we ignore hints/newgame during handshake
            continue

        if msg.startswith("heyArduinoChooseMode"):
            # Finish fill to 64 (Arduino look)
            while chessSquaresLit < 64:
                chessSquaresLit = loading_status(chessSquaresLit)
                time.sleep_ms(15)

            # Turn on control panel coordinate lights (Arduino panel UX)
            cp_fill(WHITE, 0, 5)

            game_state = GAME_SETUP
            return


def select_game_mode():
    """
    1 = PC (Stockfish)
    2 = Online
    3 = Local
    Sends heypibtn_mode_* accordingly. Clears stale edges first to avoid auto-select.
    """
    reset_buttons()
    print("Select mode: 1=PC  2=Online  3=Local")
    while True:
        btn = detect_button()
        if btn == 1:
            send_to_pi("btn_mode_pc")
            # Optional blink ack on chessboard 0,0
            set_square(0, 0, GREEN); board_np.write(); time.sleep_ms(120)
            set_square(0, 0, BLACK); board_np.write(); time.sleep_ms(120)
            set_square(0, 0, GREEN); board_np.write(); time.sleep_ms(120)
            set_square(0, 0, BLACK); board_np.write()
            return
        if btn == 2:
            send_to_pi("btn_mode_online")
            return
        if btn == 3:
            send_to_pi("btn_mode_local")
            return
        time.sleep_ms(5)


def select_strength_singlepress(default_value):
    """
    Arduino-style: one press (1..8) selects strength mapped to 1..20.
    No OK required. Button 7/8 are valid numeric choices here (like Arduino).
    """
    reset_buttons()
    print("Select strength: press 1..8 (maps to 1..20)")
    send_typing_preview("strength", str(default_value))

    while True:
        btn = detect_button()
        if not btn:
            time.sleep_ms(5)
            continue
        if 1 <= btn <= 8:
            strength = map_range(btn, 1, 8, 1, 20)
            send_typing_preview("strength", str(strength))
            return strength


def select_time_singlepress(default_value):
    """
    Arduino-style: one press (1..8) selects time mapped to 3000..12000 ms.
    No OK required. Button 7/8 are valid numeric choices here (like Arduino).
    """
    reset_buttons()
    print("Select time (ms): press 1..8 (maps to 3000..12000)")
    send_typing_preview("time", str(default_value))

    while True:
        btn = detect_button()
        if not btn:
            time.sleep_ms(5)
            continue
        if 1 <= btn <= 8:
            move_ms = map_range(btn, 1, 8, 3000, 12000)
            send_typing_preview("time", str(move_ms))
            return move_ms


def select_color_choice():
    """
    1 = White, 2 = Black, 3 = Random.
    Sends heypis1/s2/s3 (Pi understands parse_side_choice).
    """
    reset_buttons()
    print("Choose side: 1=White  2=Black  3=Random")
    
    while True:
        btn = detect_button()
        if btn == 1:
            send_to_pi("s1"); return
        if btn == 2:
            send_to_pi("s2"); return
        if btn == 3:
            send_to_pi("s3"); return
        time.sleep_ms(5)


def wait_for_setup():
    """
    Full Arduino-style setup phase driven by messages from the Pi:
      - 'heyArduinodefault_strength_X' may arrive anytime
      - 'heyArduinoEngineStrength' => choose strength (1..8 -> 1..20)
      - 'heyArduinodefault_time_Y' may arrive anytime
      - 'heyArduinoTimeControl'    => choose time (1..8 -> 3000..12000 ms)
      - 'heyArduinoPlayerColor'    => choose side (1/2/3)
      - 'heyArduinoSetupComplete'  => switch to GAME_RUNNING
    """
    global in_setup, game_state, default_strength, default_move_time

    in_setup = True
    try:
        while True:
            msg = read_from_pi()
            if not msg:
                time.sleep_ms(10)
                continue

            # Defaults may arrive in any order
            if msg.startswith("heyArduinodefault_strength_"):
                try:
                    default_strength = int(msg.split("_")[-1])
                    print("Default strength from Pi:", default_strength)
                except:
                    pass
                continue

            if msg.startswith("heyArduinodefault_time_"):
                try:
                    default_move_time = int(msg.split("_")[-1])
                    print("Default time from Pi:", default_move_time)
                except:
                    pass
                continue

            # Prompts for actual user selection (Arduino-style: single press)
            if msg.startswith("heyArduinoEngineStrength"):
                sel = select_strength_singlepress(default_strength)
                send_to_pi(str(sel))
                # Return to let the Pi proceed to the next prompt
                return

            if msg.startswith("heyArduinoTimeControl"):
                sel = select_time_singlepress(default_move_time)
                send_to_pi(str(sel))
                return

            if msg.startswith("heyArduinoPlayerColor"):
                select_color_choice()
                return

            if msg.startswith("heyArduinoSetupComplete"):
                game_state = GAME_RUNNING
                break

    finally:
        enable_hint_irq()
        in_setup = False

# ============================================================
# TRUE Arduino v27 Hint/New‑Game handling (via real IRQ on Btn 8)
# ============================================================

def process_hint_irq():
    """
    Emulate Arduino ISR semantics safely:
      - Real IRQ on Button 8 sets hint_irq_flag.
      - Here we consume that flag and run the ISR logic:
           if A1 (Button 7) is LOW  -> NEW GAME
           else                      -> HINT
      - We DO NOT block this by context (works everywhere, like Arduino).
      - We only suppress Hint during setup (Arduino would show "No hint yet").
    Returns:
      'new' if new game triggered
      'hint' if hint requested
      None if no-op
    """
    global hint_irq_flag, hint_waiting

    
    # Ignore hint entirely if not in gameplay
    if game_state != GAME_RUNNING:
        hint_irq_flag = False
        return None

    if not hint_irq_flag:
        return None

    # consume the flag
    hint_irq_flag = False

    # Check A1 (Button 7) level NOW, like Arduino ISR does
    if BTN_OK.value() == 0:
        # NEW GAME
        print("[IRQ] New Game (A1 LOW during Hint IRQ)")
        # Visuals
        cp_hint_off()
        cp_fill(WHITE, 0, 5)
        # Notify Pi
        send_to_pi("n")
        # Loading animation (like Arduino)
        var1 = 0
        while var1 < 64:
            var1 = loading_status(var1)
            time.sleep_ms(25)
        time.sleep_ms(1000)
        show_chessboard_markings()
        return "new"

    # Else: Hint
    print("[IRQ] Hint request")
    
    # Show "thinking" indication on screen
    send_typing_preview("hint", "Hint requested… thinking")

    # During setup, Arduino would do nothing useful; we suppress to avoid noise
    if not in_setup:
        # flash hint pixel
        cp_hint_on(BLUE)
        time.sleep_ms(100)
        cp_hint_on(WHITE)
        # Ask Pi for a hint
        send_to_pi("btn_hint")
        return "hint"

    return None


def enter_hint_hold(best_uci: str):
    """
    Show the hint move and hold it on the LEDs until OK (Button 7) is pressed.
    While holding, another hint press (Button 8) can update the suggestion.
    """
    global hint_hold_mode
    hint_hold_mode = True

    # Render the hint move and light Hint LED
    light_up_move(best_uci, 'H')
    cp_hint_on(BLUE)

    reset_buttons()
    print("[HINT] Holding hint on board. Press Button 7 (OK) to continue.")

    while hint_hold_mode:
        # If a new hint IRQ arrived, process it (may redraw new hint)
        irq = process_hint_irq()
        # process_hint_irq() will NOT automatically draw the hint here,
        # because Pi will also send us heyArduinohint_<uci>, handled below.

        # If Pi sends us a newer hint, we'll display it (see handler below).

        # OK releases the hold
        btn = detect_button()
        if btn == 7:
            # Clear hold
            hint_hold_mode = False
            break

        time.sleep_ms(5)

    # After releasing the hint hold, restore board & coordinate LEDs if we were in input
    show_chessboard_markings()
    cp_hint_on(WHITE)    # Like Arduino: leave hint LED on white after an engine move/hint
    if in_input:
        cp_coord_on()


# ============================================================
# MOVE ENTRY (FROM/TO), identical feel to Arduino humansGo() + OK confirm
# ============================================================

def get_from_square():
    """
    Enter FROM coordinate using buttons 1..6 (due to 8-button test layout).
    Button 7 is reserved for OK/A1, Button 8 reserved for Hint IRQ.
    Hint/New‑Game can still be triggered via Button 8 (IRQ) at any time.
    """
    col = None
    row = None
    global showing_hint

    cp_coord_on()
    cp_ok_off()
    cp_hint_off()
    reset_buttons()

    # Column
    while col is None:
        # Handle any IRQ hint/newgame requests immediately (Arduino-level behavior)
        irq = process_hint_irq()
        if irq == "new":
            # New game started; abort entry
            return None

        btn = detect_button()
        if not btn:
            time.sleep_ms(5)
            continue

        
        # If user pressed any coordinate key while hint is shown → clear hint
        if showing_hint:
            if btn not in (7, 8):
                showing_hint = False
                show_chessboard_markings()
                cp_coord_on()


        # Ignore Button 7 (A1) for coordinate input in 8-button layout
        
        if btn in (7, 8):  # 7=A1 OK, 8=Hint interrupt; both are not coordinates
            continue


        # Map 1..6 -> a..f (test layout; g/h unavailable with 8 buttons)
        col = chr(ord('a') + btn - 1)
        send_typing_preview("from", col)

    # Row
    while row is None:
        irq = process_hint_irq()
        if irq == "new":
            return None

        btn = detect_button()
        if not btn:
            time.sleep_ms(5)
            continue

        if btn in (7, 8):  # 7=A1 OK, 8=Hint interrupt; both are not coordinates
            continue


        # Map 1..6 -> '1'..'6' (test layout; '7','8' reserved)
        row = str(btn)
        send_typing_preview("from", col + row)

    return col + row


def get_to_square(move_from):
    """
    Enter TO coordinate; identical handling to get_from_square().
    """
    global showing_hint
    col = None
    row = None

    cp_coord_on()
    cp_ok_off()
    reset_buttons()

    # Column
    while col is None:
        irq = process_hint_irq()
        if irq == "new":
            return None

        btn = detect_button()
        if not btn:
            time.sleep_ms(5)
            continue

        
        if showing_hint:
            if btn and btn not in (7, 8):
                showing_hint = False
                show_chessboard_markings()
                cp_coord_on()

        if btn in (7, 8):  # 7=A1 OK, 8=Hint interrupt; both are not coordinates
            continue


        col = chr(ord('a') + btn - 1)
        send_typing_preview("to", move_from + " → " + col)

    # Row
    while row is None:
        irq = process_hint_irq()
        if irq == "new":
            return None

        btn = detect_button()
        if not btn:
            time.sleep_ms(5)
            continue


        if btn in (7, 8):  # 7=A1 OK, 8=Hint interrupt; both are not coordinates
            continue


        row = str(btn)
        send_typing_preview("to", move_from + " → " + col + row)

    return col + row


def confirm_move_or_reenter(move_str):
    """
    Hold the move on LEDs and wait for OK (Button 7/A1).
    Exact Arduino semantics:
      - Button 7 inside this loop = OK only
      - Pressing Hint (Button 8) WHILE holding A1 immediately starts NEW GAME,
        because the Hint interrupt fires and sees A1 LOW (ISR behavior).
      - Pressing other buttons cancels and returns 'redo' (re-enter FROM/TO).
    Returns:
      'ok'   -> confirmed
      'redo' -> cancel and re-enter
      None   -> if new game triggered by Hint IRQ (caller should restart outer flow)
    """
    global confirm_mode
    confirm_mode = True
    cp_coord_off()
    cp_ok_on()
    reset_buttons()

    try:
        while True:
            # Process any pending Hint IRQ (may start new game if A1 is held)
            irq = process_hint_irq()
            if irq == "new":
                # New game started; abort confirmation
                return None

            btn = detect_button()
            if not btn:
                time.sleep_ms(5)
                continue

            # OK
            if btn == 7:
                cp_ok_off()
                
                return 'ok'

            # Any other button -> cancel & re-enter FROM/TO
            cp_ok_off()
            show_chessboard_markings()
            return 'redo'
    finally:
        confirm_mode = False

# ============================================================
# MAIN LOOP: Pi message processing, promotion, turns, engine moves
# ============================================================

def handle_promotion_choice():
    """
    Pi requests: 'heyArduinopromotion_choice_needed'
    Buttons:
      1 = Q, 2 = R, 3 = B, 4 = N
    Hint/New‑Game may still be triggered (IRQ), identical to Arduino semantics.
    """
    print("[PROMO] Choose: 1=Q  2=R  3=B  4=N")
    reset_buttons()
    while True:
        # Process hint/newgame interrupt anytime (like Arduino)
        irq = process_hint_irq()
        if irq == "new":
            # Pi will reset flow to mode select; we just stop this handler
            return

        btn = detect_button()
        if not btn:
            time.sleep_ms(5)
            continue

        if btn == 1:
            send_to_pi("btn_q"); return
        if btn == 2:
            send_to_pi("btn_r"); return
        if btn == 3:
            send_to_pi("btn_b"); return
        if btn == 4:
            send_to_pi("btn_n"); return
        # ignore others

def collect_and_send_move():
    """
    Collect a player's move (FROM/TO + OK) and send it to the Pi.
    Mirrors the existing 'turn_*' branch, but callable after errors too.
    """
    global in_input, showing_hint
    in_input = True
    try:
        while True:
            print("[TURN] Your turn — Button 8 = Hint  |  Button 7 = OK (A1)")
            cp_coord_on()
            cp_hint_off()
            cp_ok_off()
            reset_buttons()

            print("Enter move FROM")
            move_from = get_from_square()
            if move_from is None:
                # New game IRQ occurred; abort
                return

            print("Enter move TO")
            move_to = get_to_square(move_from)
            if move_to is None:
                # New game IRQ occurred; abort
                return

            move = move_from + move_to

            # Visual: light and HOLD the human move
            light_up_move(move, 'Y')

            # Confirmation step (A1 = OK only here)
            reset_buttons()
            result = confirm_move_or_reenter(move)
            if result is None:
                # New game via Hint IRQ while confirming
                return
            if result == 'redo':
                # User pressed another button -> re-enter FROM/TO
                cp_coord_on()
                continue

            # Confirmed
            cp_coord_off()
            send_to_pi(move)
            print("[Sent move]", move)
            return
    finally:
        in_input = False


def main_loop():
    """
    Core runtime:
      - process asynchronous hint/newgame requests via IRQ flag
      - react to Pi messages:
          * heyArduinoGameStart         -> ignore (informational)
          * heyArduinom<uci>            -> engine move lighting
          * heyArduinoturn_*            -> collect human move with OK-confirm
          * heyArduinoerror_*           -> show error flash then re-enter move
          * heyArduinopromotion_choice_needed -> ask promotion
    """
    global in_input, hint_hold_mode, hint_irq_flag, showing_hint

    print("Entering main loop")

    while True:
        # Consume any queued IRQ (hint/newgame)
        irq = process_hint_irq()
        if irq == "new":
            # Clear UI & hint states and wait for heyArduinoChooseMode
            showing_hint = False
            hint_hold_mode = False
            hint_irq_flag = False
            disable_hint_irq()
            cp_hint_off()
            cp_coord_off()
            show_chessboard_markings()
            # Start a fresh loop cycle; the next message should be heyArduinoChooseMode
            continue


        msg = read_from_pi()
        if not msg:
            time.sleep_ms(10)
            continue

        
        # -------------------------------------------
        # Re-entry to Mode Selection after 'n'
        # -------------------------------------------
        if msg.startswith("heyArduinoChooseMode"):
            # Clean any residual UI state
            showing_hint = False
            hint_hold_mode = False
            hint_irq_flag = False
            hint_waiting = False

            disable_hint_irq()
            reset_buttons()

            cp_hint_off()
            show_chessboard_markings()
            cp_fill(WHITE, 0, 5)  # control panel coordinate lights per Arduino UX

            # Switch to setup state and re-run the setup flow
            game_state = GAME_SETUP
            select_game_mode()
            while game_state == GAME_SETUP:
                wait_for_setup()

            # We are back to GAME_RUNNING; continue main loop cleanly
            continue


        # -------------------------------------------
        # Info banner from Pi at start of game
        # -------------------------------------------
        if msg.startswith("heyArduinoGameStart"):
            # no visual change needed
            continue

        # -------------------------------------------
        # Pi played a move: heyArduinom<uci>
        # -------------------------------------------
        if msg.startswith("heyArduinom"):
            mv = msg[11:].strip()
            print("Pi move:", mv)
            light_up_move(mv, 'N')
            # light hint indicator (Arduino did this)
            cp_hint_on(WHITE)
            time.sleep_ms(250)
            show_chessboard_markings()
            continue

        # -------------------------------------------
        # Promotion choice needed
        # -------------------------------------------
        if msg.startswith("heyArduinopromotion_choice_needed"):
            handle_promotion_choice()
            continue
        
        # -------------------------------------------
        # Hint from Pi: heyArduinohint_<uci>
        # -------------------------------------------

        # Real hint moves ONLY
        if msg.startswith("heyArduinohint_") and not msg.startswith("heypityping_"):

            best = msg[len("heyArduinohint_"):].strip()

            showing_hint = True
            light_up_move(best, 'H')
            send_typing_preview("hint", f"Hint: {best} — enter move to continue")
            cp_hint_on(BLUE)
            continue
           
        # -------------------------------------------
        # Error from Pi (illegal/invalid move, etc.)
        # -> flash error and then re-enter move for human
        # -------------------------------------------
        if msg.startswith("heyArduinoerror"):
            print("[ERROR from Pi]:", msg)
            error_flash()

            # Reset any hint state so nothing interferes
            hint_hold_mode = False
            hint_irq_flag = False
            showing_hint = False

            # Restore visuals and immediately ask for a new move
            show_chessboard_markings()
            cp_coord_on()

            # IMPORTANT: Re-enter move collection immediately.
            collect_and_send_move()
            continue

        # -------------------------------------------
        # Human's turn (white/black)
        # -------------------------------------------
        if msg.startswith("heyArduinoturn_"):
            collect_and_send_move()
            continue

        # Unrecognized messages can be ignored
        # print("[Unknown Pi message]", msg)


# ============================================================
# PROGRAM ENTRY: init LEDs, handshake/setup, then run
# ============================================================

def run():
    global game_state

    print("Pico Chess Controller Starting")

    # Initial visuals like Arduino setup()
    cp_fill(BLACK)
    board_clear(BLACK)
    show_chessboard_opening_markings()
    reset_buttons()

    # Handshake: wait for mode, then let the user pick
    disable_hint_irq()
    wait_for_mode_request()
    select_game_mode()

    # Pi may call back into us multiple times to get parameters
    while game_state == GAME_SETUP:
        wait_for_setup()

    # Runtime: process messages forever
    while True:
        main_loop()


# Start program
run()
