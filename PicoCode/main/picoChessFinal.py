# ============================================================
#  PICO FIRMWARE — FINAL VERSION (2026)
#  Supports:
#   - 6‑button coordinate entry (a–f, 1–6)
#   - Live typing preview (heypityping_from_, heypityping_to_, heypityping_confirm_)
#   - Arrow‑format confirmation
#   - DIY Machines reset behavior
#   - Local mode & Random color support (protocol)
#   - New‑Game interrupt (A1+Hint) identical to Arduino v27
# ============================================================

from machine import Pin, UART
import time
import neopixel

# ============================================================
# CONFIGURATION
# ============================================================

# Buttons (active‑low)
BUTTON_PINS = [2, 3, 4, 5, 6, 7, 8, 9]   # 1–6=coords, 7=A1(OK), 8=Hint IRQ
DEBOUNCE_MS = 100

# Special roles
OK_BUTTON_INDEX   = 6   # GP8 (Button 7 / A1)
HINT_BUTTON_INDEX = 7   # GP9 (Button 8)

# NeoPixels
CONTROL_PANEL_LED_PIN   = 12
CONTROL_PANEL_LED_COUNT = 22
CHESSBOARD_LED_PIN = 28
BOARD_W, BOARD_H   = 8, 8

# Matrix orientation (match Arduino NeoMatrix: BOTTOM+RIGHT+ROWS+ZIGZAG)
MATRIX_ORIGIN_BOTTOM_RIGHT = True
MATRIX_ZIGZAG = True

# Colors
BLACK=(0,0,0); WHITE=(255,255,255); DIMW=(10,10,10)
RED=(255,0,0); GREEN=(0,255,0); BLUE=(0,0,255)
CYAN=(0,255,255); MAGENTA=(255,0,255); YELLOW=(255,255,0); ORANGE=(255,130,0)

# Control panel pixel roles
CP_COORD_START = 0
CP_OK_PIX      = 4
CP_HINT_PIX    = 5

# ============================================================
# GAME STATE
# ============================================================
confirm_mode = False
in_setup     = False
in_input     = False
hint_irq_flag = False
hint_hold_mode = False
hint_waiting = False
showing_hint = False
suppress_hints_until_ms = 0   # debounce window after New Game

# Game states
GAME_IDLE    = 0
GAME_SETUP   = 1
GAME_RUNNING = 2
game_state   = GAME_IDLE

# Defaults
default_strength   = 5      # Pi maps 1..8 => 1..20
default_move_time  = 2000   # Pi maps 1..8 => 3000..12000

# ============================================================
# UART
# ============================================================

uart = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1), timeout=10)

def send_to_pi(kind, payload=""):
    """Send a message to the Pi as: heypi<kind><payload>\n"""
    uart.write(f"heypi{kind}{payload}\n".encode())


def read_from_pi():
    if uart.any():
        try:
            return uart.readline().decode().strip()
        except:
            return None
    return None


def send_typing_preview(label, text):
    """Typing preview for FROM/TO/CONFIRM only (Option 1)."""
    # heypityping_from_<text>
    # heypityping_to_<text>
    # heypityping_confirm_<uci or arrow move>
    
    if game_state != GAME_RUNNING:
        # Suppress all typing echoes when not actually in gameplay.
        return

    uart.write(f"heypityping_{label}_{text}\n".encode())

# ============================================================
# LED WRAPPERS
# ============================================================

class ControlPanel:
    def __init__(self, pin, count):
        self.np = neopixel.NeoPixel(Pin(pin, Pin.OUT), count)
        self.count = count

    def set(self, i, c):
        if 0 <= i < self.count:
            self.np[i] = c
            self.np.write()

    def fill(self, c, start=0, count=None):
        if count is None:
            count = self.count - start
        end = min(self.count, start + count)
        for i in range(start, end):
            self.np[i] = c
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
        self.np = neopixel.NeoPixel(Pin(pin, Pin.OUT), w*h)

    def clear(self, color=BLACK):
        for i in range(self.w*self.h):
            self.np[i] = color
        self.np.write()

    def _xy_to_index(self, x, y):
        row = y
        if self.origin_bottom_right:
            if self.zigzag:
                col_index = (self.w - 1 - x) if (row % 2 == 0) else x
            else:
                col_index = (self.w - 1 - x)
            return row*self.w + col_index
        else:
            row_top = (self.h - 1) - y
            if self.zigzag:
                col_index = x if (row_top % 2 == 0) else (self.w - 1 - x)
            else:
                col_index = x
            return row_top*self.w + col_index

    def set_square(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.np[self._xy_to_index(x, y)] = color

    def write(self):
        self.np.write()

    def algebraic_to_xy(self, sq):
        if not sq or len(sq) < 2:
            return None
        f, r = sq[0].lower(), sq[1]
        if not ('a' <= f <= 'h'): return None
        if not ('1' <= r <= '8'): return None
        return (ord(f)-97, int(r)-1)

    def show_markings(self):
        for y in range(self.h):
            for x in range(self.w):
                col = (80,80,80) if ((x+y) % 2 == 0) else (160,160,160)
                self.set_square(x, y, col)
        self.write()

    def opening_markings(self):
        self.clear(BLACK)
        for k in range(self.w + self.h - 1):
            for y in range(self.h):
                x = k - y
                if 0 <= x < self.w:
                    self.set_square(x, y, GREEN)
            self.write(); time.sleep_ms(25)
        time.sleep_ms(150); self.show_markings()

    def loading_status(self, count):
        total = self.w*self.h
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
                self.set_square(i, 7-i, RED)
                self.set_square(i, i, RED)
            self.write(); time.sleep_ms(450)
            self.show_markings(); time.sleep_ms(450)

    def light_up_move(self, m, mode='Y'):
        if not m or len(m) < 4: return
        frm, to = m[:2], m[2:4]
        if mode == 'Y': c_from, c_to = YELLOW, WHITE
        elif mode == 'N': c_from, c_to = ORANGE, GREEN
        else: c_from, c_to = CYAN, BLUE
        fxy = self.algebraic_to_xy(frm)
        txy = self.algebraic_to_xy(to)
        if fxy: self.set_square(fxy[0], fxy[1], c_from)
        if txy: self.set_square(txy[0], txy[1], c_to)
        self.write()

# ============================================================
# BUTTONS
# ============================================================

class ButtonManager:
    def __init__(self, pins):
        self.pins = [Pin(g, Pin.IN, Pin.PULL_UP) for g in pins]
        self._last = [1]*len(self.pins)

    def btn(self, index):
        return self.pins[index]

    def reset(self):
        for i,p in enumerate(self.pins):
            self._last[i] = p.value()

    def detect_press(self):
        for i,p in enumerate(self.pins):
            cur = p.value(); prev = self._last[i]
            self._last[i] = cur
            if prev == 1 and cur == 0:
                time.sleep_ms(DEBOUNCE_MS)
                return i+1
        return None

    @staticmethod
    def is_non_coord_button(b):
        return b in (7,8)

# Instantiate
cp = ControlPanel(CONTROL_PANEL_LED_PIN, CONTROL_PANEL_LED_COUNT)
board = Chessboard(CHESSBOARD_LED_PIN, BOARD_W, BOARD_H,
                   origin_bottom_right=MATRIX_ORIGIN_BOTTOM_RIGHT,
                   zigzag=MATRIX_ZIGZAG)
buttons = ButtonManager(BUTTON_PINS)

BTN_OK   = buttons.btn(OK_BUTTON_INDEX)
BTN_HINT = buttons.btn(HINT_BUTTON_INDEX)

# ============================================================
# IRQ — Hint/New Game
# ============================================================

def disable_hint_irq(): BTN_HINT.irq(handler=None)

def enable_hint_irq():  BTN_HINT.irq(trigger=Pin.IRQ_FALLING, handler=hint_irq)

def hint_irq(pin):
    global hint_irq_flag
    hint_irq_flag = True

BTN_HINT.irq(trigger=Pin.IRQ_FALLING, handler=hint_irq)

# ============================================================
# HELPERS
# ============================================================

def map_range(x, in_min, in_max, out_min, out_max):
    return int((x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)


def hard_reset_board():
    global in_input, in_setup, confirm_mode
    global hint_irq_flag, hint_hold_mode, showing_hint
    in_input=False; in_setup=False; confirm_mode=False
    hint_irq_flag=False; hint_hold_mode=False; showing_hint=False
    disable_hint_irq(); buttons.reset()
    cp.fill(BLACK); board.clear(BLACK); board.show_markings()

# ============================================================
# HINT / NEW GAME (IRQ flag consumer)
# ============================================================

def process_hint_irq():
    global hint_irq_flag, suppress_hints_until_ms, game_state
    if not hint_irq_flag:
        return None
    hint_irq_flag = False

    now = time.ticks_ms()
    if time.ticks_diff(suppress_hints_until_ms, now) > 0:
        return None

    # New Game if A1 held during hint
    if BTN_OK.value() == 0:
        game_state = GAME_SETUP

        send_to_pi("n")
        cp.hint(False); cp.fill(WHITE, 0, 5)
        v = 0
        while v < 64:
            v = board.loading_status(v)
            time.sleep_ms(25)
        time.sleep_ms(1000)
        board.show_markings()
        suppress_hints_until_ms = time.ticks_add(now, 800)
        return "new"

    # Hint during setup is ignored (Arduino-like)
    if game_state != GAME_RUNNING:
        return None

    cp.hint(True, BLUE); time.sleep_ms(100); cp.hint(True, WHITE)
    send_to_pi("btn_hint")
    return "hint"

# ============================================================
# LIVE PREVIEWS (FROM / TO / CONFIRM)
# ============================================================

def _send_from_preview(text):
    send_typing_preview("from", text)

def _send_to_preview(move_from, partial_to):
    send_typing_preview("to", f"{move_from} → {partial_to}")

def _send_confirm_preview(move):
    frm, to = move[:2], move[2:4]
    send_typing_preview("confirm", f"{frm} → {to}")

# ============================================================
# MOVE ENTRY (6‑button layout)
# ============================================================


def enter_from_square(seed_btn=None):
    
    if game_state != GAME_RUNNING:
        return None

    col=None; row=None
    cp.coord(True); cp.ok(False); cp.hint(False)
    buttons.reset()

    # Column (a..f)
    while col is None:
        
        if game_state != GAME_RUNNING:
            return None

        # If we have a seed button from a cancel, prefer it once
        if seed_btn is not None:
            b = seed_btn
            seed_btn = None  # consume seed
        else:
            irq = process_hint_irq()
            if irq == "new": return None
            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5)
                continue

        if ButtonManager.is_non_coord_button(b):
            continue
        col = chr(ord('a') + b - 1)
        _send_from_preview(col)

    # Row (1..6)
    while row is None:
        
        if game_state != GAME_RUNNING:
            return None

        irq = process_hint_irq()
        if irq == "new": return None
        b = buttons.detect_press()
        if not b:
            time.sleep_ms(5)
            continue
        if ButtonManager.is_non_coord_button(b):
            continue
        row = str(b)
        _send_from_preview(col + row)

    return col + row



def enter_to_square(move_from):
    
    if game_state != GAME_RUNNING:
        return None

    col=None; row=None
    cp.coord(True); cp.ok(False)
    buttons.reset()

    # Column
    while col is None:
        
        if game_state != GAME_RUNNING:
            return None

        irq = process_hint_irq()
        if irq == "new": return None
        b = buttons.detect_press()
        if not b: time.sleep_ms(5); continue
        if ButtonManager.is_non_coord_button(b): continue
        col = chr(ord('a') + b - 1)
        _send_to_preview(move_from, col)

    # Row
    while row is None:
        
        if game_state != GAME_RUNNING:
            return None

        irq = process_hint_irq()
        if irq == "new": return None
        b = buttons.detect_press()
        if not b: time.sleep_ms(5); continue
        if ButtonManager.is_non_coord_button(b): continue
        row = str(b)
        _send_to_preview(move_from, col + row)

    return col + row



def confirm_move(move):
    
    if game_state != GAME_RUNNING:
        return None

    global confirm_mode
    confirm_mode = True
    cp.coord(False); cp.ok(True)
    buttons.reset()

    _send_confirm_preview(move)

    try:
        while True:
            
            if game_state != GAME_RUNNING:
                return None

            irq = process_hint_irq()
            if irq == "new": return None

            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5)
                continue

            if b == 7:  # OK
                cp.ok(False)
                return "ok"
            else:
                # Return which button cancelled, so caller can reuse it
                cp.ok(False)
                board.show_markings()
                return ("redo", b)
    finally:
        confirm_mode = False




def collect_and_send_move():
    global in_input
    in_input = True
    try:
        seed = None  # NEW: coordinate seed for FROM

        while True:
            cp.coord(True); cp.hint(False); cp.ok(False)
            buttons.reset()

            move_from = enter_from_square(seed_btn=seed)
            if move_from is None: return
            seed = None  # consumed once

            move_to = enter_to_square(move_from)
            if move_to is None: return

            move = move_from + move_to
            board.light_up_move(move, 'Y')

            res = confirm_move(move)
            if res is None:
                return
            if res == 'ok':
                send_to_pi(move)
                return

            # res is ('redo', btn)
            if isinstance(res, tuple) and res[0] == 'redo':
                cancel_btn = res[1]
                # If the cancel was a coord button, use it to seed FROM
                seed = cancel_btn if (1 <= cancel_btn <= 6) else None
                cp.coord(True)
                continue
    finally:
        in_input = False


# ============================================================
# SETUP / MODE SELECTION
# ============================================================

def wait_for_mode_request():
    global game_state
    board.opening_markings()
    lit = 0
    while True:
        lit = board.loading_status(lit)
        time.sleep_ms(1000)
        msg = read_from_pi()
        if not msg: continue
        if msg.startswith("heyArduinoChooseMode"):
            while lit < 64:
                lit = board.loading_status(lit)
                time.sleep_ms(15)
            cp.fill(WHITE, 0, 5)
            game_state = GAME_SETUP
            return

def select_game_mode():
    buttons.reset()
    while True:
        b = buttons.detect_press()
        if b == 1: send_to_pi("btn_mode_pc");    return
        if b == 2: send_to_pi("btn_mode_online");return
        if b == 3: send_to_pi("btn_mode_local"); return
        time.sleep_ms(5)

def select_singlepress(label, default_value, out_min, out_max):
    buttons.reset()
    while True:
        b = buttons.detect_press()
        if b and 1 <= b <= 8:
            return map_range(b, 1, 8, out_min, out_max)
        time.sleep_ms(5)

def select_strength_singlepress(default_value):
    return select_singlepress("strength", default_value, 1, 20)

def select_time_singlepress(default_value):
    return select_singlepress("time", default_value, 3000, 12000)

def select_color_choice():
    buttons.reset()
    while True:
        b = buttons.detect_press()
        if b == 1: send_to_pi("s1"); return   # White/First
        if b == 2: send_to_pi("s2"); return   # Black/Second
        if b == 3: send_to_pi("s3"); return   # Random
        time.sleep_ms(5)

def wait_for_setup():
    global in_setup, game_state, default_strength, default_move_time
    in_setup = True
    try:
        while True:
            msg = read_from_pi()
            if not msg:
                time.sleep_ms(10); continue

            if msg.startswith("heyArduinodefault_strength_"):
                try: default_strength = int(msg.split("_")[-1])
                except: pass
                continue

            if msg.startswith("heyArduinodefault_time_"):
                try: default_move_time = int(msg.split("_")[-1])
                except: pass
                continue

            if msg.startswith("heyArduinoEngineStrength"):
                v = select_strength_singlepress(default_strength)
                send_to_pi(str(v)); return

            if msg.startswith("heyArduinoTimeControl"):
                v = select_time_singlepress(default_move_time)
                send_to_pi(str(v)); return

            if msg.startswith("heyArduinoPlayerColor"):
                select_color_choice(); return

            if msg.startswith("heyArduinoSetupComplete"):
                game_state = GAME_RUNNING
                in_setup = False
                return
    finally:
        enable_hint_irq()

# ============================================================
# PROMOTION CHOICE
# ============================================================

def handle_promotion_choice():
    buttons.reset()
    while True:
        irq = process_hint_irq()
        if irq == "new": return
        b = buttons.detect_press()
        if not b:
            time.sleep_ms(5); continue
        if b == 1: send_to_pi("btn_q"); return
        if b == 2: send_to_pi("btn_r"); return
        if b == 3: send_to_pi("btn_b"); return
        if b == 4: send_to_pi("btn_n"); return

# ============================================================
# MAIN LOOP
# ============================================================

def main_loop():
    global showing_hint, hint_hold_mode, hint_irq_flag
    while True:
        irq = process_hint_irq()
        if irq == "new":
            showing_hint=False; hint_hold_mode=False; hint_irq_flag=False
            disable_hint_irq(); cp.hint(False); cp.coord(False)
            board.show_markings()
            continue

        msg = read_from_pi()
        if not msg:
            time.sleep_ms(10); continue

        if msg.startswith("heyArduinoResetBoard"):
            hard_reset_board(); continue

        if msg.startswith("heyArduinoChooseMode"):
            showing_hint=False; hint_hold_mode=False; hint_irq_flag=False
            disable_hint_irq(); buttons.reset()
            cp.hint(False); board.show_markings(); cp.fill(WHITE,0,5)
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
            board.light_up_move(mv,'N')
            cp.hint(True,WHITE)
            time.sleep_ms(250)
            board.show_markings()
            continue

        if msg.startswith("heyArduinopromotion_choice_needed"):
            handle_promotion_choice(); continue

        if msg.startswith("heyArduinohint_"):
            best = msg[len("heyArduinohint_"):].strip()
            showing_hint = True
            board.light_up_move(best,'H')
            cp.hint(True,BLUE)
            continue

        if msg.startswith("heyArduinoerror"):
            board.error_flash()
            hint_hold_mode=False; hint_irq_flag=False; showing_hint=False
            board.show_markings(); cp.coord(True)
            collect_and_send_move(); continue

        if msg.startswith("heyArduinoturn_"):
            collect_and_send_move(); continue

# ============================================================
# ENTRY
# ============================================================

def run():
    global game_state
    print("Pico Chess Controller Starting (single-file) picoChessFinal.py")
    cp.fill(BLACK); board.clear(BLACK)
    buttons.reset()

    disable_hint_irq()
    wait_for_mode_request()
    select_game_mode()

    while game_state == GAME_SETUP:
        wait_for_setup()

    while True:
        main_loop()

# Start
run()
