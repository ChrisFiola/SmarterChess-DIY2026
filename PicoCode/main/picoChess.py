# main.py — Pico Chess Controller (Single-File, Performance-Optimized)
# -------------------------------------------------------------------
# - Preserves exact Arduino v27 semantics for Hint/New-Game ISR and OK-confirm.
# - 8-button layout: 1..6 are coordinates (a..f, 1..6), 7=OK (A1), 8=Hint (IRQ).
# - Fully compatible with your Pi-side protocol (SmarterChess refactor you provided).
# - Keeps LED animations (opening sweep, error flash, loading fill, move lighting).
# - One file: no filesystem imports during runtime -> avoids UART misses/hangs.
# - Conservative on allocations and writes; uses short helper calls.
#
# Messages expected from Pi (RX):
#   heyArduinoChooseMode
#   heyArduinoEngineStrength | heyArduinodefault_strength_X
#   heyArduinoTimeControl    | heyArduinodefault_time_X
#   heyArduinoPlayerColor
#   heyArduinoSetupComplete
#   heyArduinoGameStart
#   heyArduinom<uci>
#   heyArduinohint_<uci>
#   heyArduinopromotion_choice_needed
#   heyArduinoerror_...
#   heyArduinoturn_<white|black>
#
# Messages we send to Pi (TX):
#   heypibtn_mode_pc | heypibtn_mode_online | heypibtn_mode_local
#   heypi<digits> (e.g., heypi5 for skill, heypi3000 for ms)
#   heypis1|heypis2|heypis3 (color)
#   heypityping_<label>_<value> (from/to/strength/time/hint)
#   heypi<uci> (e.g., heypie2e4)
#   heypibtn_q|_r|_b|_n for promotion
#   heypin (single 'n' payload) for New Game after Hint+OK
#
# Safe-Boot: Hold OK (GP8) on power-up ~2.5s to skip app and keep REPL free.

# ---------------------
# 0) Safe-Boot Guard
# ---------------------
import time
from machine import Pin, UART
try:
    _guard_ok = Pin(8, Pin.IN, Pin.PULL_UP)
    _t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), _t0) < 2500:
        if _guard_ok.value() == 0:  # pressed (active-low)
            print("SAFE MODE – main.py skipped.")
            raise SystemExit
        time.sleep_ms(20)
except Exception:
    pass

# ---------------------
# 1) Imports & Constants
# ---------------------
import neopixel

# Button wiring (active‑low)
# 1=GP2, 2=GP3, 3=GP4, 4=GP5, 5=GP6, 6=GP7, 7=GP8 (OK/A1), 8=GP9 (Hint/IRQ)
BUTTON_PINS = [2, 3, 4, 5, 6, 7, 8, 9]
DEBOUNCE_MS = 20

OK_BUTTON_INDEX = 6     # index in BUTTON_PINS -> GP8
HINT_BUTTON_INDEX = 7   # index in BUTTON_PINS -> GP9

# LEDs
CONTROL_PANEL_LED_PIN = 12
CONTROL_PANEL_LED_COUNT = 22

CHESSBOARD_LED_PIN = 28
BOARD_W, BOARD_H = 8, 8

# Matrix orientation (Arduino NeoMatrix compatible)
MATRIX_ORIGIN_BOTTOM_RIGHT = True
MATRIX_ZIGZAG = True

# Control Panel pixel roles
CP_COORD_START = 0
CP_OK_PIX      = 4
CP_HINT_PIX    = 5

# Colors (RGB)
BLACK   = (0, 0, 0)
WHITE   = (255, 255, 255)
RED     = (255, 0, 0)
GREEN   = (0, 255, 0)
BLUE    = (0, 0, 255)
CYAN    = (0, 255, 255)
MAGENTA = (255, 0, 255)
YELLOW  = (255, 255, 0)
ORANGE  = (255, 130, 0)

# Game phases
GAME_IDLE = 0
GAME_SETUP = 1
GAME_RUNNING = 2

# -------------------------------
# 2) Hardware Abstractions (fast)
# -------------------------------
class ButtonManager:
    """Debounced falling-edge detection. Active-low inputs with PULL_UP."""
    def __init__(self, pins):
        self.pins = [Pin(g, Pin.IN, Pin.PULL_UP) for g in pins]
        self._last = [1] * len(self.pins)

    def btn(self, index):
        return self.pins[index]

    def reset(self):
        for i, p in enumerate(self.pins):
            self._last[i] = p.value()

    def detect_press(self):
        for idx, p in enumerate(self.pins):
            cur = p.value()  # 0=pressed, 1=released
            prev = self._last[idx]
            self._last[idx] = cur
            if prev == 1 and cur == 0:
                time.sleep_ms(DEBOUNCE_MS)
                return idx + 1
        return None

    @staticmethod
    def is_non_coord_button(btn):
        # In 8-button layout, 7=A1 OK, 8=Hint/IRQ are not coordinates
        return btn in (7, 8)


class ControlPanel:
    """22-pixel control panel strip:
    - 0..3: coordinate LEDs
    - 4: OK indicator
    - 5: Hint indicator
    """
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
    """8x8 NeoPixel matrix with Arduino NeoMatrix-compatible mapping.
    Optimized to minimize writes (batch set_* then write()).
    """
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
        row = y
        if self.origin_bottom_right:
            if self.zigzag:
                if row % 2 == 0:
                    col_index = (self.w - 1) - x
                else:
                    col_index = x
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
        x = ord(f) - ord('a')
        y = int(r) - 1
        return (x, y)

    def show_markings(self):
        for y in range(self.h):
            for x in range(self.w):
                color = (80, 80, 80) if ((x + y) % 2 == 0) else (160, 160, 160)
                self.set_square(x, y, color)
        self.write()

    def opening_markings(self):
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
        total = self.w * self.h
        if count >= total:
            return count
        idx = count
        y = idx // self.w
        x = (self.w - 1) - (idx % self.w)
        self.set_square(x, y, BLUE)
        self.write()
        return count + 1

    def error_flash(self, times=3):
        for _ in range(times):
            self.clear(BLUE)
            for i in range(8):
                self.set_square(i, 7 - i, RED)
                self.set_square(i, i, RED)
            self.write()
            time.sleep_ms(450)
            self.show_markings()
            time.sleep_ms(450)

    def light_up_move(self, m, mode='Y'):
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

# -------------------------------------------
# 3) Global State + Arduino Behavior Flags
# -------------------------------------------
confirm_mode = False
in_setup = False
in_input = False
hint_irq_flag = False
hint_hold_mode = False
hint_waiting = False
showing_hint = False

game_state = GAME_IDLE

# Defaults (Arduino-like)
default_strength = 5
default_move_time = 2000

# ---------------------------------
# 4) UART Protocol Helper Routines
# ---------------------------------
# UART0 on GP0/GP1 @115200 (match Pi side)
_uart = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1), timeout=10)

def send_to_pi(kind, payload=""):
    # Keep this tiny and allocation-light
    _uart.write(b"heypi" + kind.encode() + payload.encode() + b"\n")

def read_from_pi():
    if _uart.any():
        try:
            return _uart.readline().decode().strip()
        except Exception:
            return None
    return None

def send_typing_preview(label, text):
    # heypityping_<label>_<text>
    _uart.write(b"heypityping_" + label.encode() + b"_" + text.encode() + b"\n")

# -------------------------------------------
# 5) Hint/New‑Game IRQ Handler (Arduino-accurate)
# -------------------------------------------
buttons = ButtonManager(BUTTON_PINS)
BTN_OK = buttons.btn(OK_BUTTON_INDEX)
BTN_HINT = buttons.btn(HINT_BUTTON_INDEX)

cp = ControlPanel(CONTROL_PANEL_LED_PIN, CONTROL_PANEL_LED_COUNT)
board = Chessboard(CHESSBOARD_LED_PIN, BOARD_W, BOARD_H,
                   origin_bottom_right=MATRIX_ORIGIN_BOTTOM_RIGHT,
                   zigzag=MATRIX_ZIGZAG)

def disable_hint_irq():
    BTN_HINT.irq(handler=None)

def hint_irq(_pin):
    global hint_irq_flag
    hint_irq_flag = True

def enable_hint_irq():
    from machine import Pin as _Pin
    BTN_HINT.irq(trigger=_Pin.IRQ_FALLING, handler=hint_irq)


def process_hint_irq():
    """Emulate Arduino ISR semantics safely:
    - Flag set in real IRQ.
    - If OK (A1) is LOW at processing -> NEW GAME (send 'n').
    - Else -> Hint request (send 'btn_hint').
    Works only when GAME_RUNNING (ignored during handshake/setup).
    Returns: 'new' | 'hint' | None
    """
    global hint_irq_flag
    if game_state != GAME_RUNNING:
        hint_irq_flag = False
        return None
    if not hint_irq_flag:
        return None

    hint_irq_flag = False  # consume

    if BTN_OK.value() == 0:
        # NEW GAME
        cp.hint(False)
        cp.fill(WHITE, 0, 5)
        send_to_pi("n")  # tell Pi to go back to mode selection
        # Smooth loading like Arduino
        var1 = 0
        while var1 < 64:
            var1 = board.loading_status(var1)
            time.sleep_ms(25)
        time.sleep_ms(1000)
        board.show_markings()
        return "new"

    # Else Hint
    send_typing_preview("hint", "Hint requested… thinking")
    if not in_setup:
        cp.hint(True, BLUE)
        time.sleep_ms(100)
        cp.hint(True, WHITE)
        send_to_pi("btn_hint")
        return "hint"
    return None

# ------------------
# 6) LED Animations
# ------------------

def hard_reset_board():
    global in_input, in_setup, confirm_mode
    global hint_irq_flag, hint_hold_mode, showing_hint

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

# -----------------------------
# 7) User Input (FROM/TO, OK)
# -----------------------------

def _maybe_clear_hint_on_coord_press(btn):
    global showing_hint
    if showing_hint and btn and not ButtonManager.is_non_coord_button(btn):
        showing_hint = False
        board.show_markings()
        cp.coord(True)

def _read_coord_part(kind, label, prefix=""):
    """kind: 'file' or 'rank'. Maps 1..6 -> a..f or '1'..'6'."""
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
    cp.coord(True); cp.ok(False); cp.hint(False); buttons.reset()
    # Column
    col = None
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
    row = _read_coord_part("rank", "from", prefix=col)
    if row is None:
        return None
    return col + row

def enter_to_square(move_from):
    cp.coord(True); cp.ok(False); buttons.reset()
    # Column
    col = None
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
    row = None
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
    """Hold move; wait for OK (btn 7). Any other button -> redo. Hint+OK -> new.
    Returns: 'ok' | 'redo' | None(new game)
    """
    global confirm_mode
    confirm_mode = True
    cp.coord(False); cp.ok(True); buttons.reset()
    try:
        while True:
            irq = process_hint_irq()
            if irq == "new":
                return None
            btn = buttons.detect_press()
            if not btn:
                time.sleep_ms(5)
                continue
            if btn == 7:
                cp.ok(False)
                return 'ok'
            cp.ok(False)
            board.show_markings()
            return 'redo'
    finally:
        confirm_mode = False

# -----------------------------------
# 8) Setup Phase (Mode/Strength/Time/Color)
# -----------------------------------

def map_range(x, in_min, in_max, out_min, out_max):
    return int((x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)


def wait_for_mode_request():
    global game_state
    print("Waiting for Pi...")
    board.opening_markings()
    chessSquaresLit = 0
    while True:
        chessSquaresLit = board.loading_status(chessSquaresLit)
        time.sleep_ms(1000)
        msg = read_from_pi()
        if not msg:
            continue
        if msg.startswith("heyArduinoChooseMode"):
            while chessSquaresLit < 64:
                chessSquaresLit = board.loading_status(chessSquaresLit)
                time.sleep_ms(15)
            cp.fill(WHITE, 0, 5)
            game_state = GAME_SETUP
            return


def select_game_mode():
    buttons.reset()
    print("Select mode: 1=PC  2=Online  3=Local")
    while True:
        btn = buttons.detect_press()
        if btn == 1:
            send_to_pi("btn_mode_pc")
            # Blink ack 0,0
            board.set_square(0,0,GREEN); board.write(); time.sleep_ms(120)
            board.set_square(0,0,BLACK); board.write(); time.sleep_ms(120)
            board.set_square(0,0,GREEN); board.write(); time.sleep_ms(120)
            board.set_square(0,0,BLACK); board.write()
            return
        if btn == 2:
            send_to_pi("btn_mode_online"); return
        if btn == 3:
            send_to_pi("btn_mode_local"); return
        time.sleep_ms(5)


def _select_singlepress(label, default_value, out_min, out_max):
    buttons.reset()
    print("Select %s: press 1..8 (maps to %d..%d)" % (label, out_min, out_max))
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
    return _select_singlepress("strength", default_value, 1, 20)

def select_time_singlepress(default_value):
    return _select_singlepress("time", default_value, 3000, 12000)

def select_color_choice():
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
    """Drain-burst setup: handle defaults + one actionable prompt, then return."""
    global in_setup, game_state, default_strength, default_move_time
    in_setup = True
    try:
        while True:
            msg = read_from_pi()
            if not msg:
                time.sleep_ms(10)
                continue
            # Drain burst in same call to avoid missing prompt after defaults
            while msg:
                if msg.startswith("heyArduinodefault_strength_"):
                    try:
                        default_strength = int(msg.split("_")[-1])
                        print("Default strength from Pi:", default_strength)
                    except Exception:
                        pass
                    msg = read_from_pi(); continue
                if msg.startswith("heyArduinodefault_time_"):
                    try:
                        default_move_time = int(msg.split("_")[-1])
                        print("Default time from Pi:", default_move_time)
                    except Exception:
                        pass
                    msg = read_from_pi(); continue
                if msg.startswith("heyArduinoEngineStrength"):
                    sel = select_strength_singlepress(default_strength)
                    send_to_pi(str(sel))
                    return
                if msg.startswith("heyArduinoTimeControl"):
                    sel = select_time_singlepress(default_move_time)
                    send_to_pi(str(sel))
                    return
                if msg.startswith("heyArduinoPlayerColor"):
                    select_color_choice(); return
                if msg.startswith("heyArduinoSetupComplete"):
                    game_state = GAME_RUNNING; return
                # unknown -> keep draining
                msg = read_from_pi()
    finally:
        enable_hint_irq()

# ------------------------------------------------------
# 9) Runtime: Engine moves, human moves, errors, promos
# ------------------------------------------------------

def handle_promotion_choice():
    print("[PROMO] Choose: 1=Q  2=R  3=B  4=N")
    buttons.reset()
    while True:
        irq = process_hint_irq()
        if irq == "new":
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


def collect_and_send_move():
    global in_input
    in_input = True
    try:
        while True:
            print("[TURN] Your turn — Button 8 = Hint  |  Button 7 = OK (A1)")
            cp.coord(True); cp.hint(False); cp.ok(False); buttons.reset()
            print("Enter move FROM")
            move_from = enter_from_square()
            if move_from is None:
                return
            print("Enter move TO")
            move_to = enter_to_square(move_from)
            if move_to is None:
                return
            move = move_from + move_to
            board.light_up_move(move, 'Y')
            buttons.reset()
            result = confirm_move_or_reenter(move)
            if result is None:
                return
            if result == 'redo':
                cp.coord(True)
                continue
            cp.coord(False)
            send_to_pi(move)
            print("[Sent move]", move)
            return
    finally:
        in_input = False


def main_loop():
    global hint_hold_mode, hint_irq_flag, showing_hint, hint_waiting
    print("Entering main loop")
    while True:
        irq = process_hint_irq()
        if irq == "new":
            showing_hint = False
            hint_hold_mode = False
            hint_irq_flag = False
            disable_hint_irq()
            cp.hint(False); cp.coord(False)
            board.show_markings()
            continue
        msg = read_from_pi()
        if not msg:
            time.sleep_ms(10)
            continue
        if msg.startswith("heyArduinoResetBoard"):
            hard_reset_board(); continue
        if msg.startswith("heyArduinoChooseMode"):
            showing_hint = False; hint_hold_mode = False; hint_irq_flag = False; hint_waiting = False
            disable_hint_irq(); buttons.reset()
            cp.hint(False); board.show_markings(); cp.fill(WHITE, 0, 5)
            global game_state
            game_state = GAME_SETUP
            select_game_mode()
            while game_state == GAME_SETUP:
                wait_for_setup()
            continue
        if msg.startswith("heyArduinoGameStart"):
            continue
        if msg.startswith("heyArduinom"):
            mv = msg[11:].strip()
            print("Pi move:", mv)
            board.light_up_move(mv, 'N')
            cp.hint(True, WHITE)
            time.sleep_ms(250)
            board.show_markings()
            continue
        if msg.startswith("heyArduinopromotion_choice_needed"):
            handle_promotion_choice(); continue
        if msg.startswith("heyArduinohint_") and not msg.startswith("heypityping_"):
            best = msg[len("heyArduinohint_"):].strip()
            showing_hint = True
            board.light_up_move(best, 'H')
            send_typing_preview("hint", "Hint: " + best + " — enter move to continue")
            cp.hint(True, BLUE)
            continue
        if msg.startswith("heyArduinoerror"):
            print("[ERROR from Pi]:", msg)
            board.error_flash()
            hint_hold_mode = False; hint_irq_flag = False; showing_hint = False
            board.show_markings(); cp.coord(True)
            collect_and_send_move(); continue
        if msg.startswith("heyArduinoturn_"):
            collect_and_send_move(); continue
        # else ignore unknown

# -----------------------------
# 10) Program Entry
# -----------------------------

def run():
    global game_state
    print("Pico Chess Controller Starting (single-file) picoChess.py")
    cp.fill(BLACK); board.clear(BLACK); board.opening_markings(); buttons.reset()
    disable_hint_irq()
    wait_for_mode_request()
    select_game_mode()
    while game_state == GAME_SETUP:
        wait_for_setup()
    while True:
        main_loop()

# Start program
run()
