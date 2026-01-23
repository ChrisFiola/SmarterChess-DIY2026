
# Pico Chess Controller — Refactored (behavior identical)
# --------------------------------------------------------
# - Preserves Arduino v27 semantics for buttons/IRQ (Hint/New-Game via ISR).
# - Cleans structure: LEDs wrapped in small classes, button helpers, shared pickers.
# - All UART messages and game flow remain unchanged.

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

# Special roles
OK_BUTTON_INDEX = 6     # BUTTON_PINS[6] = GP8 (Button 7)
HINT_BUTTON_INDEX = 7   # BUTTON_PINS[7] = GP9 (Button 8 / IRQ)

# Pico pins
CONTROL_PANEL_LED_PIN = 12
CONTROL_PANEL_LED_COUNT = 22

CHESSBOARD_LED_PIN = 28
BOARD_W, BOARD_H = 8, 8

# Orientation flags for matrix mapping (match Arduino NeoMatrix: BOTTOM+RIGHT+ROWS+ZIGZAG)
MATRIX_ORIGIN_BOTTOM_RIGHT = True
MATRIX_ZIGZAG = True

# ============================================================
# COLORS
# ============================================================
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

# Control panel pixel roles
CP_COORD_START = 0   # first 4 pixels: "coordinate lights"
CP_OK_PIX      = 4   # OK indicator
CP_HINT_PIX    = 5   # hint indicator

# ============================================================
# GAME STATE
# ============================================================
confirm_mode = False     # Only TRUE during OK confirmation
in_setup = False         # Setup phase (mode/strength/time/color)
in_input = False         # When entering FROM/TO
hint_irq_flag = False    # Raised by real interrupt
hint_hold_mode = False   # When True, a hint is pinned on the board until OK (Button 7)
hint_waiting = False
showing_hint = False

game_state = 0
GAME_IDLE = 0
GAME_SETUP = 1
GAME_RUNNING = 2

# Defaults (Arduino-like)
default_strength = 5       # 0..20 (we map 1..8 to 1..20 during setup)
default_move_time = 2000   # milliseconds (we map 1..8 to 3000..12000 during setup)

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
# LED WRAPPERS
# ============================================================

class ControlPanel:
    def __init__(self, pin, count):
        self.np = neopixel.NeoPixel(Pin(pin, Pin.OUT), count)
        self.count = count

    def set(self, idx, color):
        if 0 <= idx < self.count:
            self.np[idx] = color
            self.np.write()

    def fill(self, color, start=0, count=None):
        if count is None:
            count = self.count - start
        end = min(self.count, start + count)
        for i in range(start, end):
            self.np[i] = color
        self.np.write()

    def coord(self, on=True):
        self.fill(WHITE if on else BLACK, CP_COORD_START, 4)

    def ok(self, on=True):
        self.set(CP_OK_PIX, WHITE if on else BLACK)

    def hint(self, on=True, color=WHITE):
        self.set(CP_HINT_PIX, color if on else BLACK)


class Chessboard:
    def __init__(self, pin, w, h, origin_bottom_right=True, zigzag=True):
        self.w, self.h = w, h
        self.origin_bottom_right = origin_bottom_right
        self.zigzag = zigzag
        self.np = neopixel.NeoPixel(Pin(pin, Pin.OUT), w * h)

    def clear(self, color=BLACK):
        for i in range(self.w * self.h):
            self.np[i] = color
        self.np.write()

    def _xy_to_index(self, x, y):
        # Map (x,y) with origin bottom-right + zigzag rows (Arduino NeoMatrix compatibility)
        row = y
        if self.origin_bottom_right:
            if self.zigzag:
                if row % 2 == 0:
                    col_index = (self.w - 1) - x   # even row: right->left
                else:
                    col_index = x                   # odd row: left->right
            else:
                col_index = (self.w - 1) - x
            idx = row * self.w + col_index
        else:
            row_from_top = (self.h - 1) - y
            if self.zigzag:
                if row_from_top % 2 == 0:
                    col_index = x
                else:
                    col_index = (self.w - 1) - x
            else:
                col_index = x
            idx = row_from_top * self.w + col_index
        return idx

    def set_square(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.np[self._xy_to_index(x, y)] = color

    def write(self):
        self.np.write()

    def algebraic_to_xy(self, sq):
        if not sq or len(sq) < 2:
            return None
        f, r = sq[0].lower(), sq[1]
        if not ('a' <= f <= 'h'):
            return None
        if not ('1' <= r <= '8'):
            return None
        x = ord(f) - ord('a')     # a..h -> 0..7
        y = int(r) - 1            # 1..8 -> 0..7
        return (x, y)

    def show_markings(self):
        # simple checkered board
        for y in range(self.h):
            for x in range(self.w):
                color = (80, 80, 80) if ((x + y) % 2 == 0) else (160, 160, 160)
                self.set_square(x, y, color)
        self.write()

    def opening_markings(self):
        # diagonal sweep intro animation
        self.clear(BLACK)
        for k in range(self.w + self.h - 1):
            for y in range(self.h):
                x = k - y
                if 0 <= x < self.w:
                    self.set_square(x, y, GREEN)
            self.write()
            time.sleep_ms(25)
        time.sleep_ms(150)
        self.show_markings()

    def loading_status(self, count):
        """
        Light squares progressively (bottom->top, right->left) to 64.
        Returns updated count.
        """
        total = self.w * self.h
        if count >= total:
            return count
        idx = count
        y = idx // self.w
        x = (self.w - 1) - (idx % self.w)  # right to left
        self.set_square(x, y, BLUE)
        self.write()
        return count + 1

    def error_flash(self, times=3):
        for _ in range(times):
            # Blue fill + red cross
            self.clear(BLUE)
            for i in range(8):
                self.set_square(i, 7 - i, RED)
                self.set_square(i, i, RED)
            self.write()
            time.sleep_ms(450)
            self.show_markings()
            time.sleep_ms(450)

    def light_up_move(self, m, mode='Y'):
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

        xy_f = self.algebraic_to_xy(frm)
        xy_t = self.algebraic_to_xy(to)
        if xy_f:
            self.set_square(xy_f[0], xy_f[1], c_from)
        if xy_t:
            self.set_square(xy_t[0], xy_t[1], c_to)
        self.write()


# ============================================================
# BUTTONS
# ============================================================

class ButtonManager:
    def __init__(self, pins):
        self.pins = [Pin(g, Pin.IN, Pin.PULL_UP) for g in pins]
        self._last = [1] * len(self.pins)

    def btn(self, index):
        return self.pins[index]

    def reset(self):
        """Synchronize debounced states to current levels."""
        for i, p in enumerate(self.pins):
            self._last[i] = p.value()

    def detect_press(self):
        """
        Immediate edge detection on PRESS (falling edge), not release.
        Returns button number (1..8) or None.
        """
        for idx, p in enumerate(self.pins):
            cur = p.value()         # 0 = pressed, 1 = released
            prev = self._last[idx]
            self._last[idx] = cur
            if prev == 1 and cur == 0:
                time.sleep_ms(DEBOUNCE_MS)
                return idx + 1
        return None

    @staticmethod
    def is_non_coord_button(btn):
        # 7 = A1 OK, 8 = Hint interrupt; not coordinates in 8-button layout
        return btn in (7, 8)


# Instantiate hardware wrappers
cp = ControlPanel(CONTROL_PANEL_LED_PIN, CONTROL_PANEL_LED_COUNT)
board = Chessboard(CHESSBOARD_LED_PIN, BOARD_W, BOARD_H,
                   origin_bottom_right=MATRIX_ORIGIN_BOTTOM_RIGHT,
                   zigzag=MATRIX_ZIGZAG)
buttons = ButtonManager(BUTTON_PINS)

# Aliases for special buttons (raw Pin objects)
BTN_OK = buttons.btn(OK_BUTTON_INDEX)        # GP8 (A1 / Button 7)
BTN_HINT = buttons.btn(HINT_BUTTON_INDEX)    # GP9 (Hint / Button 8 / IRQ)

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
    We emulate by setting a flag; logic is handled in process_hint_irq().
    """
    global hint_irq_flag
    hint_irq_flag = True

# Enable FALLING EDGE interrupt — same as Arduino pin 3
BTN_HINT.irq(trigger=Pin.IRQ_FALLING, handler=hint_irq)

# ============================================================
# SMALL HELPERS
# ============================================================

def map_range(x, in_min, in_max, out_min, out_max):
    return int((x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)

def hard_reset_board():
    global in_input, in_setup, confirm_mode
    global hint_irq_flag, hint_hold_mode, showing_hint

    print("[RESET] Hard board reset")

    in_input = False
    in_setup = False
    confirm_mode = False

    hint_irq_flag = False
    hint_hold_mode = False
    showing_hint = False

    disable_hint_irq()
    buttons.reset()

    cp.fill(BLACK)
    board.clear(BLACK)
    board.show_markings()

# ============================================================
# SETUP / MODE SELECTION
# ============================================================

def wait_for_mode_request():
    """
    Wait for 'heyArduinoChooseMode' from the Pi.
    Show loading animation and fill the chessboard to 64 squares like Arduino.
    """
    global game_state
    print("Waiting for Pi...")
    board.opening_markings()
    chessSquaresLit = 0

    while True:
        # loading pulse every ~1s
        chessSquaresLit = board.loading_status(chessSquaresLit)
        time.sleep_ms(1000)

        msg = read_from_pi()
        if not msg:
            # Interrupt may set hint flag; we ignore hints/newgame during handshake
            continue

        if msg.startswith("heyArduinoChooseMode"):
            # Finish fill to 64 (Arduino look)
            while chessSquaresLit < 64:
                chessSquaresLit = board.loading_status(chessSquaresLit)
                time.sleep_ms(15)

            # Turn on control panel coordinate lights (Arduino panel UX)
            cp.fill(WHITE, 0, 5)

            game_state = GAME_SETUP
            return

def select_game_mode():
    """
    1 = PC (Stockfish)
    2 = Online
    3 = Local
    Sends heypibtn_mode_* accordingly. Clears stale edges first to avoid auto-select.
    """
    buttons.reset()
    print("Select mode: 1=PC  2=Online  3=Local")
    while True:
        btn = buttons.detect_press()
        if btn == 1:
            send_to_pi("btn_mode_pc")
            # Optional blink ack on chessboard 0,0
            board.set_square(0, 0, GREEN); board.write(); time.sleep_ms(120)
            board.set_square(0, 0, BLACK); board.write(); time.sleep_ms(120)
            board.set_square(0, 0, GREEN); board.write(); time.sleep_ms(120)
            board.set_square(0, 0, BLACK); board.write()
            return
        if btn == 2:
            send_to_pi("btn_mode_online")
            return
        if btn == 3:
            send_to_pi("btn_mode_local")
            return
        time.sleep_ms(5)

def select_singlepress(label, default_value, out_min, out_max):
    """
    Arduino-style: one press (1..8) selects mapped value.
    No OK required. Button 7/8 are valid numeric choices here (like Arduino).
    """
    buttons.reset()
    print(f"Select {label}: press 1..8 (maps to {out_min}..{out_max})")
    send_typing_preview(label, str(default_value))

    while True:
        btn = buttons.detect_press()
        if not btn:
            time.sleep_ms(5)
            continue
        if 1 <= btn <= 8:
            mapped = map_range(btn, 1, 8, out_min, out_max)
            send_typing_preview(label, str(mapped))
            return mapped

def select_strength_singlepress(default_value):
    return select_singlepress("strength", default_value, 1, 20)

def select_time_singlepress(default_value):
    return select_singlepress("time", default_value, 3000, 12000)

def select_color_choice():
    """
    1 = White, 2 = Black, 3 = Random.
    Sends heypis1/s2/s3 (Pi understands parse_side_choice).
    """
    buttons.reset()
    print("Choose side: 1=White  2=Black  3=Random")

    while True:
        btn = buttons.detect_press()
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
                return

    finally:
        enable_hint_irq()
        # in_setup flag remains True until runtime starts

# ============================================================
# TRUE Arduino v27 Hint/New‑Game handling (via IRQ on Btn 8)
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
        cp.hint(False)
        cp.fill(WHITE, 0, 5)
        # Notify Pi
        send_to_pi("n")
        # Loading animation (like Arduino)
        var1 = 0
        while var1 < 64:
            var1 = board.loading_status(var1)
            time.sleep_ms(25)
        time.sleep_ms(1000)
        board.show_markings()
        return "new"

    # Else: Hint
    print("[IRQ] Hint request")
    # Show "thinking" indication on screen
    send_typing_preview("hint", "Hint requested… thinking")

    # During setup, Arduino would do nothing useful; we suppress to avoid noise
    if not in_setup:
        # flash hint pixel
        cp.hint(True, BLUE)
        time.sleep_ms(100)
        cp.hint(True, WHITE)
        # Ask Pi for a hint
        send_to_pi("btn_hint")
        return "hint"

    return None

def enter_hint_hold(best_uci: str):
    """
    Show the hint move and hold it on the LEDs until OK (Button 7) is pressed.
    While holding, another hint press (Button 8) can update the suggestion.
    """
    global hint_hold_mode, in_input, showing_hint
    hint_hold_mode = True

    # Render the hint move and light Hint LED
    board.light_up_move(best_uci, 'H')
    cp.hint(True, BLUE)

    buttons.reset()
    print("[HINT] Holding hint on board. Press Button 7 (OK) to continue.")

    while hint_hold_mode:
        # If a new hint IRQ arrived, process it (may redraw new hint)
        irq = process_hint_irq()
        # process_hint_irq() will NOT automatically draw the hint here;
        # the Pi will also send heyArduinohint_<uci>, handled in the main loop.

        # OK releases the hold
        btn = buttons.detect_press()
        if btn == 7:
            hint_hold_mode = False
            break

        time.sleep_ms(5)

    # After releasing the hint hold, restore board & coordinate LEDs if we were in input
    board.show_markings()
    cp.hint(True, WHITE)  # Like Arduino: leave hint LED on white after an engine move/hint
    if in_input:
        cp.coord(True)

# ============================================================
# MOVE ENTRY (FROM/TO) + OK confirm
# ============================================================

def _maybe_clear_hint_on_coord_press(btn):
    global showing_hint
    if showing_hint and btn and not ButtonManager.is_non_coord_button(btn):
        showing_hint = False
        board.show_markings()
        cp.coord(True)

def _read_coord_part(kind, label, prefix=""):
    """
    kind: "file" or "rank"
    Returns 'a'..'f' or '1'..'6' depending on kind (8-button layout).
    """
    while True:
        irq = process_hint_irq()
        if irq == "new":
            return None

        btn = buttons.detect_press()
        if not btn:
            time.sleep_ms(5)
            continue

        if ButtonManager.is_non_coord_button(btn):
            continue

        if kind == "file":
            col = chr(ord('a') + btn - 1)  # 1..6 -> a..f
            send_typing_preview(label, prefix + col)
            return col
        else:
            row = str(btn)  # 1..6 -> '1'..'6'
            send_typing_preview(label, prefix + row)
            return row

def enter_from_square():
    """
    Enter FROM coordinate using buttons 1..6 (8-button layout).
    """
    global showing_hint
    col = row = None

    cp.coord(True)
    cp.ok(False)
    cp.hint(False)
    buttons.reset()

    # Column
    while col is None:
        irq = process_hint_irq()
        if irq == "new":
            return None

        btn = buttons.detect_press()
        if not btn:
            time.sleep_ms(5)
            continue

        _maybe_clear_hint_on_coord_press(btn)
        if ButtonManager.is_non_coord_button(btn):
            continue

        col = chr(ord('a') + btn - 1)
        send_typing_preview("from", col)

    # Row
    while row is None:
        part = _read_coord_part("rank", "from", prefix=col)
        if part is None:
            return None
        row = part

    return col + row

def enter_to_square(move_from):
    """
    Enter TO coordinate; identical handling to FROM.
    """
    global showing_hint
    col = row = None

    cp.coord(True)
    cp.ok(False)
    buttons.reset()

    # Column
    while col is None:
        irq = process_hint_irq()
        if irq == "new":
            return None

        btn = buttons.detect_press()
        if not btn:
            time.sleep_ms(5)
            continue

        _maybe_clear_hint_on_coord_press(btn)
        if ButtonManager.is_non_coord_button(btn):
            continue

        col = chr(ord('a') + btn - 1)
        send_typing_preview("to", move_from + " → " + col)

    # Row
    while row is None:
        irq = process_hint_irq()
        if irq == "new":
            return None

        btn = buttons.detect_press()
        if not btn:
            time.sleep_ms(5)
            continue

        if ButtonManager.is_non_coord_button(btn):
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
    cp.coord(False)
    cp.ok(True)
    buttons.reset()

    try:
        while True:
            # Process any pending Hint IRQ (may start new game if A1 is held)
            irq = process_hint_irq()
            if irq == "new":
                # New game started; abort confirmation
                return None

            btn = buttons.detect_press()
            if not btn:
                time.sleep_ms(5)
                continue

            # OK
            if btn == 7:
                cp.ok(False)
                return 'ok'

            # Any other button -> cancel & re-enter FROM/TO
            cp.ok(False)
            board.show_markings()
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
    buttons.reset()
    while True:
        # Process hint/newgame interrupt anytime (like Arduino)
        irq = process_hint_irq()
        if irq == "new":
            # Pi will reset flow to mode select; we just stop this handler
            return

        btn = buttons.detect_press()
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
    global in_input
    in_input = True
    try:
        while True:
            print("[TURN] Your turn — Button 8 = Hint  |  Button 7 = OK (A1)")
            cp.coord(True)
            cp.hint(False)
            cp.ok(False)
            buttons.reset()

            print("Enter move FROM")
            move_from = enter_from_square()
            if move_from is None:
                # New game IRQ occurred; abort
                return

            print("Enter move TO")
            move_to = enter_to_square(move_from)
            if move_to is None:
                # New game IRQ occurred; abort
                return

            move = move_from + move_to

            # Visual: light and HOLD the human move
            board.light_up_move(move, 'Y')

            # Confirmation step (A1 = OK only here)
            buttons.reset()
            result = confirm_move_or_reenter(move)
            if result is None:
                # New game via Hint IRQ while confirming
                return
            if result == 'redo':
                # User pressed another button -> re-enter FROM/TO
                cp.coord(True)
                continue

            # Confirmed
            cp.coord(False)
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
    global hint_hold_mode, hint_irq_flag, showing_hint, hint_waiting

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
            cp.hint(False)
            cp.coord(False)
            board.show_markings()
            # Next loop iteration will process incoming messages again
            continue

        msg = read_from_pi()
        if not msg:
            time.sleep_ms(10)
            continue

        if msg.startswith("heyArduinoResetBoard"):
            hard_reset_board()
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
            buttons.reset()

            cp.hint(False)
            board.show_markings()
            cp.fill(WHITE, 0, 5)  # control panel coordinate lights per Arduino UX

            # Switch to setup state and re-run the setup flow
            global game_state
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
            board.light_up_move(mv, 'N')
            # light hint indicator (Arduino did this)
            cp.hint(True, WHITE)
            time.sleep_ms(250)
            board.show_markings()
            continue

        # -------------------------------------------
        # Promotion choice needed
        # -------------------------------------------
        if msg.startswith("heyArduinopromotion_choice_needed"):
            handle_promotion_choice()
            continue

        # -------------------------------------------
        # Hint from Pi: heyArduinohint_<uci> (not typing echoes)
        # -------------------------------------------
        if msg.startswith("heyArduinohint_") and not msg.startswith("heypityping_"):
            best = msg[len("heyArduinohint_"):].strip()
            showing_hint = True
            board.light_up_move(best, 'H')
            send_typing_preview("hint", f"Hint: {best} — enter move to continue")
            cp.hint(True, BLUE)
            continue

        # -------------------------------------------
        # Error from Pi (illegal/invalid move, etc.) -> flash error and re-enter move
        # -------------------------------------------
        if msg.startswith("heyArduinoerror"):
            print("[ERROR from Pi]:", msg)
            board.error_flash()

            # Reset any hint state so nothing interferes
            hint_hold_mode = False
            hint_irq_flag = False
            showing_hint = False

            # Restore visuals and immediately ask for a new move
            board.show_markings()
            cp.coord(True)

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

# ============================================================
# PROGRAM ENTRY
# ============================================================

def run():
    global game_state

    print("Pico Chess Controller Starting")

    # Initial visuals like Arduino setup()
    cp.fill(BLACK)
    board.clear(BLACK)
    board.opening_markings()
    buttons.reset()

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
