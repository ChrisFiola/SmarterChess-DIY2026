# ============================================================
#  PICO FIRMWARE (2026)
# ============================================================

from machine import Pin, UART
import time
import neopixel

# ============================================================
# =============== CONFIG & CONSTANTS ==========================
# ============================================================

# Buttons (active‑low) wiring stays unchanged
BUTTON_PINS = [2, 3, 4, 6, 7, 8, 9, 10, 12, 13]   # 1–8=coords, 9=A1(OK), 10=Hint IRQ
DEBOUNCE_MS = 300

# Special role indexes (0-based into BUTTON_PINS)
OK_BUTTON_INDEX   = 8   # Button 9
HINT_BUTTON_INDEX = 9   # Button 10

# NeoPixels
CONTROL_PANEL_LED_PIN   = 14
CONTROL_PANEL_LED_COUNT = 22
CHESSBOARD_LED_PIN      = 22
BOARD_W, BOARD_H        = 8, 8

# Matrix orientation (DIY Machines: bottom-right origin + rows + zigzag)
MATRIX_ORIGIN_BOTTOM_RIGHT = True
MATRIX_ZIGZAG = True

# Colors (keep standard palette)
BLACK=(0,0,0); WHITE=(255,255,255); DIMW=(10,10,10)
RED=(255,0,0); GREEN=(0,255,0); BLUE=(0,0,255)
CYAN=(0,255,255); MAGENTA=(255,0,255); YELLOW=(255,255,0); ORANGE=(255,130,0)

ENGINE_COLOR = BLUE  # Deep blue for computer moves

# Control panel pixel roles
CP_COORD_START = 0
CP_OK_PIX      = 4
CP_HINT_PIX    = 5

# ============================================================
# =============== STATE & MODES ===============================
# ============================================================

# Game states
GAME_IDLE    = 0
GAME_SETUP   = 1
GAME_RUNNING = 2
game_state   = GAME_IDLE

# Modes / turn tracking
MODE_PC     = "pc"      # vs computer
MODE_ONLINE = "online"  # vs remote (placeholder)
MODE_LOCAL  = "local"   # local 2P
game_mode   = MODE_PC
current_turn = 'W'      # 'W' or 'B'

# Defaults (Pi remaps values; we keep these for initial prompts)
default_strength   = 5      # Pi maps 1..8 => 1..20
default_move_time  = 2000   # Pi maps 1..8 => 3000..12000

# Simple input guards
in_setup = False
in_input = False

# ============================================================
# =============== PERSISTENT OVERLAYS ========================
# ============================================================
# We keep a persistent overlay (hint/engine) until the user presses
# any coordinate or OK. Latest overlay wins.

persistent_trail_active = False
persistent_trail_type   = None    # 'hint' or 'engine'
persistent_trail_move   = None    # UCI string (e.g., 'e2e4')

# ============================================================
# =============== UART (Pico <-> Pi) =========================
# ============================================================

uart = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1), timeout=10)

def send_to_pi(kind, payload=""):
    """Send: heypi<kind><payload>\\n (protocol preserved)."""
    uart.write(f"heypi{kind}{payload}\n".encode())

def read_from_pi():
    """Non-blocking read if available; returns lower-level raw line w/o newline or None."""
    if uart.any():
        try:
            return uart.readline().decode().strip() # type: ignore
        except:
            return None
    return None

def send_typing_preview(label, text):
    """Typing preview for FROM/TO/CONFIRM only."""
    if game_state != GAME_RUNNING:
        return
    # heypityping_<label>_<text>
    uart.write(f"heypityping_{label}_{text}\n".encode())

# ============================================================
# =============== LED PANELS (CONTROL + BOARD) ===============
# ============================================================

class ControlPanel:
    """Small NeoPixel strip that mirrors coordinate readiness, OK, and Hint."""
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
    """8x8 chessboard LED matrix with DIY Machines wiring (bottom-right origin, zigzag)."""
    def __init__(self, pin, w, h, origin_bottom_right=True, zigzag=True):
        self.w, self.h = w, h
        self.origin_bottom_right = origin_bottom_right
        self.zigzag = zigzag
        self.np = neopixel.NeoPixel(Pin(pin, Pin.OUT), w*h)

        # Precompute checkerboard pattern buffer (LIGHT/DARK) for quick restore
        self._marking_cache = [BLACK]*(w*h)
        LIGHT = (100,100,120); DARK=(0,0,0)
        for y in range(self.h):
            for x in range(self.w):
                col = DARK if ((x+y) % 2 == 0) else LIGHT
                self._raw_set(x, y, col, into_cache=True)
        # Initialize physical board off; show markings when asked
        self.clear(BLACK)

    # -------- low-level mapping --------
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

    def _raw_set(self, x, y, color, into_cache=False):
        """Set without bounds/write; optionally update marking cache."""
        idx = self._xy_to_index(x, y)
        self.np[idx] = color
        if into_cache:
            self._marking_cache[idx] = color

    # -------- public drawing API --------
    def clear(self, color=BLACK):
        for i in range(self.w*self.h):
            self.np[i] = color
        self.np.write()

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

    @staticmethod
    def _sgn(v):
        return 0 if v == 0 else (1 if v > 0 else -1)

    def _path_squares(self, frm, to):
        """
        FROM..TO inclusive path:
         - Rank/file straight and diagonals inclusive
         - Knight: include unit steps along the longer leg first (visual L)
         - Fallback: from->to
        """
        f = self.algebraic_to_xy(frm)
        t = self.algebraic_to_xy(to)
        if not f or not t:
            return []

        fx, fy = f; tx, ty = t
        dx = tx - fx; dy = ty - fy
        adx, ady = abs(dx), abs(dy)
        path = []

        # File
        if fx == tx and fy != ty:
            sy = self._sgn(dy)
            for y in range(fy, ty + sy, sy):
                path.append((fx, y))
            return path

        # Rank
        if fy == ty and fx != tx:
            sx = self._sgn(dx)
            for x in range(fx, tx + sx, sx):
                path.append((x, fy))
            return path

        # Diagonal
        if adx == ady and adx != 0:
            sx = self._sgn(dx); sy = self._sgn(dy)
            x, y = fx, fy
            for _ in range(adx + 1):
                path.append((x, y))
                x += sx; y += sy
            return path

        # Knight: longer leg first (L path)
        if (adx, ady) in ((1,2), (2,1)):
            sx = self._sgn(dx); sy = self._sgn(dy)
            path.append((fx, fy))
            if ady == 2:
                path.append((fx, fy + 1*sy))
                path.append((fx, fy + 2*sy))
                path.append((fx + 1*sx, fy + 2*sy))
            else:
                path.append((fx + 1*sx, fy))
                path.append((fx + 2*sx, fy))
                path.append((fx + 2*sx, fy + 1*sy))
            if path[-1] != (tx, ty):
                path.append((tx, ty))
            dedup = []
            for p in path:
                if not dedup or dedup[-1] != p:
                    dedup.append(p)
            return dedup

        return [(fx, fy), (tx, ty)]

    def draw_trail(self, move_uci, color, end_color=None):
        """Light FROM..TO along computed path. end_color used for target if provided."""
        if not move_uci or len(move_uci) < 4:
            return
        frm, to = move_uci[:2], move_uci[2:4]
        path = self._path_squares(frm, to)
        for i, (x, y) in enumerate(path):
            if end_color and i == len(path)-1:
                self.set_square(x, y, end_color)
            else:
                self.set_square(x, y, color)
        self.write()

    # ---------- Display patterns ----------
    def show_markings(self):
        # Write cached checkerboard pattern (faster than recompute)
        for i in range(self.w*self.h):
            self.np[i] = self._marking_cache[i]
        self.np.write()

    def opening_markings(self):
        # Small diagonal sweep, then show markings
        self.clear(BLACK)
        for k in range(self.w + self.h - 1):
            for y in range(self.h):
                x = k - y
                if 0 <= x < self.w:
                    self.set_square(x, y, GREEN)
            self.write(); time.sleep_ms(25)
        time.sleep_ms(150); self.show_markings()

    def loading_status(self, count):
        # Blue progressive fill (DIY style)
        total = self.w*self.h
        if count >= total: return count
        idx = count
        y = idx // self.w
        x = (self.w - 1) - (idx % self.w)
        self.set_square(x, y, BLUE)
        self.write()
        return count + 1

    def illegal_flash(self, hold_ms=700):
        # Entire board red (instant)
        self.clear(RED)
        time.sleep_ms(hold_ms)
        self.show_markings()

    # Minimal helpers for setup icons (keep visual parity)
    def draw_hline(self, x, y, length, color):
        for dx in range(length):
            self.set_square(x+dx, y, color)

    def draw_vline(self, x, y, length, color):
        for dy in range(length):
            self.set_square(x, y+dy, color)

    def show_time_prompt(self):
        self.clear(BLACK)
        T = [(2,6),(3,6),(4,6),(5,6), (4,5),(4,4),(4,3),(4,2)]
        for x,y in T: self.set_square(x,y,MAGENTA)
        self.write()

    def show_strength_prompt(self):
        self.clear(BLACK)
        L = [(2,6),(2,5),(2,4),(2,3),(2,2), (3,2),(4,2),(5,2)]
        for x,y in L: self.set_square(x,y,MAGENTA)
        self.write()

    def show_checkmate_scene_hash(self):
        """
        Fill board MAGENTA and overlay a bold white '#' sign.
        On 8x8:
        - vertical bars at x = 2 and x = 5
        - horizontal bars at y = 2 and y = 5
        """
        # Full MAGENTA
        for i in range(self.w * self.h):
            self.np[i] = MAGENTA
        self.np.write()

        # Draw '#' bars in WHITE
        # vertical bars
        for y in range(self.h):
            self.set_square(2, y, WHITE)
            self.set_square(5, y, WHITE)
        # horizontal bars
        for x in range(self.w):
            self.set_square(x, 2, WHITE)
            self.set_square(x, 5, WHITE)
        self.write()


# ============================================================
# =============== BUTTONS & INPUT ============================
# ============================================================

class ButtonManager:
    """Debounced, edge-triggered button manager (active-low)."""
    def __init__(self, pins):
        self.pins = [Pin(g, Pin.IN, Pin.PULL_UP) for g in pins]
        self._last = [1]*len(self.pins)

    def btn(self, index):
        return self.pins[index]

    def reset(self):
        for i,p in enumerate(self.pins):
            self._last[i] = p.value()

    def detect_press(self):
        """Return 1-based index on press, else None."""
        for i,p in enumerate(self.pins):
            cur = p.value(); prev = self._last[i]
            self._last[i] = cur
            if prev == 1 and cur == 0:
                time.sleep_ms(DEBOUNCE_MS)
                return i+1
        return None

    @staticmethod
    def is_non_coord_button(b):
        return b in (9,10)

# Instantiate hardware interfaces
cp = ControlPanel(CONTROL_PANEL_LED_PIN, CONTROL_PANEL_LED_COUNT)
board = Chessboard(CHESSBOARD_LED_PIN, BOARD_W, BOARD_H,
                   origin_bottom_right=MATRIX_ORIGIN_BOTTOM_RIGHT,
                   zigzag=MATRIX_ZIGZAG)
buttons = ButtonManager(BUTTON_PINS)

BTN_OK   = buttons.btn(OK_BUTTON_INDEX)
BTN_HINT = buttons.btn(HINT_BUTTON_INDEX)

# ============================================================
# =============== HINT IRQ (EDGE) ============================
# ============================================================

hint_irq_flag = False
suppress_hints_until_ms = 0   # small debounce window after setup banners

def hint_irq(pin):
    global hint_irq_flag
    hint_irq_flag = True

def disable_hint_irq():
    BTN_HINT.irq(handler=None)

def enable_hint_irq():
    BTN_HINT.irq(trigger=Pin.IRQ_FALLING, handler=hint_irq)

# Start with IRQ armed for general use (we toggle during setup as needed)
BTN_HINT.irq(trigger=Pin.IRQ_FALLING, handler=hint_irq)

# ============================================================
# =============== HELPERS & RESET ============================
# ============================================================

def map_range(x, in_min, in_max, out_min, out_max):
    return int((x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)

def hard_reset_board():
    """Return to base markings, clear overlays, reset inputs."""
    global in_input, in_setup
    global persistent_trail_active, persistent_trail_type, persistent_trail_move
    in_input=False; in_setup=False
    persistent_trail_active=False; persistent_trail_type=None; persistent_trail_move=None
    disable_hint_irq(); buttons.reset()
    cp.fill(BLACK); board.clear(BLACK); board.show_markings()

# ============================================================
# =============== PERSISTENT TRAILS (HINT/ENGINE) ============
# ============================================================

def clear_persistent_trail():
    """Clear any persistent hint/engine overlay and restore markings."""
    global persistent_trail_active, persistent_trail_type, persistent_trail_move
    persistent_trail_active = False
    persistent_trail_type   = None
    persistent_trail_move   = None
    cp.hint(False)
    board.show_markings()

def show_persistent_trail(move_uci, color, trail_type, end_color=None):
    """
    Draw a persistent overlay (latest wins). For engine/hint we allow an end_color
    (e.g., cyan for capture) — this is *not* the pre-OK user preview.
    """
    global persistent_trail_active, persistent_trail_type, persistent_trail_move
    persistent_trail_active = True
    persistent_trail_type   = trail_type   # 'hint' or 'engine'
    persistent_trail_move   = move_uci
    board.clear(BLACK)
    board.draw_trail(move_uci, color, end_color=end_color)
    if trail_type == 'hint':
        cp.hint(True, WHITE)

def cancel_user_input_and_restart():
    """
    Abort current input phase but keep the overlay on screen.
    Used when a newer hint/engine trail arrives mid-entry.
    """
    buttons.reset()
    cp.coord(True); cp.ok(False)    # keep overlay on board; do not reset markings here.

# ============================================================
# =============== HINT / NEW GAME PROCESSOR ==================
# ============================================================

def process_hint_irq():
    """
    Consume IRQ flag; handle 'New Game' if OK held; forward hint request to Pi.
    Returns: "new" if new-game initiated, "hint" if hint requested, or None.
    """
    global hint_irq_flag, suppress_hints_until_ms, game_state
    if not hint_irq_flag:
        return None
    hint_irq_flag = False

    now = time.ticks_ms()
    if time.ticks_diff(suppress_hints_until_ms, now) > 0:
        return None

    # New Game if A1(OK) held during hint (DIY behavior)
    if BTN_OK.value() == 0:
        game_state = GAME_SETUP
        send_to_pi("n")
        cp.hint(False); cp.fill(WHITE, 0, 5)
        v = 0
        board.clear(BLACK)
        while v < (board.w * board.h):
            v = board.loading_status(v)
            time.sleep_ms(25)
        time.sleep_ms(350)
        board.show_markings()
        suppress_hints_until_ms = time.ticks_add(now, 800)
        return "new"

    # During setup ignore hints (Arduino-like)
    if game_state != GAME_RUNNING:
        return None

    # Blink hint pixel and signal Pi
    cp.hint(True, BLUE); time.sleep_ms(100); cp.hint(True, WHITE)
    send_to_pi("btn_hint")
    return "hint"

# ============================================================
# =============== LIVE TYPING PREVIEWS =======================
# ============================================================

def _send_from_preview(text):
    send_typing_preview("from", text)

def _send_to_preview(move_from, partial_to):
    send_typing_preview("to", f"{move_from} → {partial_to}")

def _send_confirm_preview(move):
    frm, to = move[:2], move[2:4]
    send_typing_preview("confirm", f"{frm} → {to}")

# ============================================================
# =============== MOVE ENTRY (NO PRE-OK CHECK) ===============
# ============================================================
# Pipeline:
#   1) enter_from_square: draw FROM preview square (green)
#   2) enter_to_square:   draw green trail FROM->TO (no cyan, no legality check)
#   3) confirm_move:      OK triggers Pi validation; illegal => red flash
#
# Any newly arriving hint/engine overlay cancels current entry and displays the overlay.

def enter_from_square(seed_btn=None):
    """Collect FROM: column then row. If overlay active, clear on first press."""
    if game_state != GAME_RUNNING:
        return None

    # If overlay is active, clear it on first user press (OK or coord)
    if persistent_trail_active:
        while True:
            # Newest Pi events override current overlay
            msg = read_from_pi()
            if msg:
                if msg.startswith("heyArduinohint_"):
                    raw = msg[15:].strip()
                    cap = raw.endswith("_cap")
                    mv  = raw[:-4] if cap else raw
                    show_persistent_trail(mv, YELLOW, 'hint', end_color=(CYAN if cap else None))
                    continue
                if msg.startswith("heyArduinom"):
                    raw = msg[11:].strip()
                    cap = raw.endswith("_cap")
                    mv  = raw[:-4] if cap else raw
                    show_persistent_trail(mv, ENGINE_COLOR, 'engine', end_color=(CYAN if cap else None))
                    continue
            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5); continue
            # Any button clears overlay; coordinate press becomes seed for column
            clear_persistent_trail()
            if 1 <= b <= 8:
                seed_btn = b
            break

        cp.coord(True); cp.ok(False); cp.hint(False)
        buttons.reset()

    # Column
    col=None; row=None
    while col is None:
        if game_state != GAME_RUNNING:
            return None

        if seed_btn is not None:
            b = seed_btn
            seed_btn = None
        else:
            irq = process_hint_irq()
            if irq == "new": return None

            # Interrupts from Pi (overlay wins)
            msg = read_from_pi()
            if msg:
                if msg.startswith("heyArduinohint_"):
                    raw = msg[15:].strip()
                    cap = raw.endswith("_cap"); mv = raw[:-4] if cap else raw
                    show_persistent_trail(mv, YELLOW, 'hint', end_color=(CYAN if cap else None))
                    cancel_user_input_and_restart(); return None
                if msg.startswith("heyArduinom"):
                    raw = msg[11:].strip()
                    cap = raw.endswith("_cap"); mv = raw[:-4] if cap else raw
                    show_persistent_trail(mv, ENGINE_COLOR, 'engine', end_color=(CYAN if cap else None))
                    cancel_user_input_and_restart(); return None

            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5); continue

        if ButtonManager.is_non_coord_button(b):
            continue
        col = chr(ord('a') + b - 1)
        _send_from_preview(col)

    # Row
    while row is None:
        if game_state != GAME_RUNNING:
            return None

        irq = process_hint_irq()
        if irq == "new": return None

        msg = read_from_pi()
        if msg:
            if msg.startswith("heyArduinohint_"):
                raw = msg[15:].strip()
                cap = raw.endswith("_cap"); mv = raw[:-4] if cap else raw
                show_persistent_trail(mv, YELLOW, 'hint', end_color=(CYAN if cap else None))
                cancel_user_input_and_restart(); return None
            if msg.startswith("heyArduinom"):
                raw = msg[11:].strip()
                cap = raw.endswith("_cap"); mv = raw[:-4] if cap else raw
                show_persistent_trail(mv, ENGINE_COLOR, 'engine', end_color=(CYAN if cap else None))
                cancel_user_input_and_restart(); return None

        b = buttons.detect_press()
        if not b:
            time.sleep_ms(5); continue
        if ButtonManager.is_non_coord_button(b):
            continue
        row = str(b)
        _send_from_preview(col + row)

    # FROM preview — green square
    frm = col + row
    fxy = board.algebraic_to_xy(frm)
    board.show_markings()
    if fxy:
        board.set_square(fxy[0], fxy[1], GREEN)
        board.write()
    return frm


def enter_to_square(move_from):
    """Collect TO: column then row; draw green trail (no cyan, no legality)."""
    if game_state != GAME_RUNNING:
        return None

    # If overlay is active, clear it on first user press (OK or coord)
    if persistent_trail_active:
        while True:
            msg = read_from_pi()
            if msg:
                if msg.startswith("heyArduinohint_"):
                    raw = msg[15:].strip()
                    cap = raw.endswith("_cap"); mv = raw[:-4] if cap else raw
                    show_persistent_trail(mv, YELLOW, 'hint', end_color=(CYAN if cap else None))
                    continue
                if msg.startswith("heyArduinom"):
                    raw = msg[11:].strip()
                    cap = raw.endswith("_cap"); mv = raw[:-4] if cap else raw
                    show_persistent_trail(mv, ENGINE_COLOR, 'engine', end_color=(CYAN if cap else None))
                    continue
            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5); continue
            clear_persistent_trail()
            break

    cp.coord(True); cp.ok(False)
    buttons.reset()

    col=None; row=None

    # Column
    while col is None:
        if game_state != GAME_RUNNING:
            return None

        irq = process_hint_irq()
        if irq == "new": return None

        msg = read_from_pi()
        if msg:
            if msg.startswith("heyArduinohint_"):
                raw = msg[15:].strip()
                cap = raw.endswith("_cap"); mv = raw[:-4] if cap else raw
                show_persistent_trail(mv, YELLOW, 'hint', end_color=(CYAN if cap else None))
                cancel_user_input_and_restart(); return None
            if msg.startswith("heyArduinom"):
                raw = msg[11:].strip()
                cap = raw.endswith("_cap"); mv = raw[:-4] if cap else raw
                show_persistent_trail(mv, ENGINE_COLOR, 'engine', end_color=(CYAN if cap else None))
                cancel_user_input_and_restart(); return None

        b = buttons.detect_press()
        if not b:
            time.sleep_ms(5); continue
        if ButtonManager.is_non_coord_button(b):
            continue
        col = chr(ord('a') + b - 1)
        _send_to_preview(move_from, col)

    # Row
    while row is None:
        if game_state != GAME_RUNNING:
            return None

        irq = process_hint_irq()
        if irq == "new": return None

        msg = read_from_pi()
        if msg:
            if msg.startswith("heyArduinohint_"):
                raw = msg[15:].strip()
                cap = raw.endswith("_cap"); mv = raw[:-4] if cap else raw
                show_persistent_trail(mv, YELLOW, 'hint', end_color=(CYAN if cap else None))
                cancel_user_input_and_restart(); return None
            if msg.startswith("heyArduinom"):
                raw = msg[11:].strip()
                cap = raw.endswith("_cap"); mv = raw[:-4] if cap else raw
                show_persistent_trail(mv, ENGINE_COLOR, 'engine', end_color=(CYAN if cap else None))
                cancel_user_input_and_restart(); return None

        b = buttons.detect_press()
        if not b:
            time.sleep_ms(5); continue
        if ButtonManager.is_non_coord_button(b):
            continue
        row = str(b)
        _send_to_preview(move_from, col + row)

    # Draw simple green trail preview (FROM->TO), no cyan/no legality
    to = col + row
    board.show_markings()
    board.draw_trail(move_from + to, GREEN)
    return to


def _color_for_user_confirm():
    """
    Trail color after OK for user move:
      - Local mode: always GREEN (white/black users)
      - PC mode: user's move GREEN
    """
    if game_mode == MODE_LOCAL:
        return GREEN
    return GREEN


def confirm_move(move):
    """
    OK to send, or redo if any other button is pressed.
    During confirm, newer hint/engine overlays cancel confirm and restart input.
    """
    if game_state != GAME_RUNNING:
        return None

    cp.coord(False); cp.ok(True)
    buttons.reset()
    _send_confirm_preview(move)

    while True:
        if game_state != GAME_RUNNING:
            cp.ok(False)
            return None

        irq = process_hint_irq()
        if irq == "new":
            cp.ok(False)
            return None

        # New overlay cancels confirm
        msg = read_from_pi()
        if msg:
            if msg.startswith("heyArduinohint_"):
                raw = msg[15:].strip()
                cap = raw.endswith("_cap"); mv = raw[:-4] if cap else raw
                show_persistent_trail(mv, YELLOW, 'hint', end_color=(CYAN if cap else None))
                cancel_user_input_and_restart()
                cp.ok(False)
                return None
            if msg.startswith("heyArduinom"):
                raw = msg[11:].strip()
                cap = raw.endswith("_cap"); mv = raw[:-4] if cap else raw
                show_persistent_trail(mv, ENGINE_COLOR, 'engine', end_color=(CYAN if cap else None))
                cancel_user_input_and_restart()
                cp.ok(False)
                return None

        b = buttons.detect_press()
        if not b:
            time.sleep_ms(5); continue

        if b == (OK_BUTTON_INDEX+1):  # OK
            cp.ok(False)
            return "ok"
        else:
            # Cancel confirm; allow FROM seed if coord button
            cp.ok(False)
            board.show_markings()
            return ("redo", b)

def collect_and_send_move():
    """Full user move entry cycle. No pre-OK legality. Simple green preview."""
    global in_input
    in_input = True
    try:
        seed = None  # optional seed coord if user cancels with a coord button
        while True:
            cp.coord(True); cp.hint(False); cp.ok(False)
            buttons.reset()

            move_from = enter_from_square(seed_btn=seed)
            if move_from is None:
                if persistent_trail_active:
                    # Interrupted by hint/engine; restart fresh
                    seed = None
                    continue
                return
            seed = None

            move_to = enter_to_square(move_from)
            if move_to is None:
                if persistent_trail_active:
                    seed = None
                    continue
                return

            move = move_from + move_to

            # Keep the green preview trail until OK/redo (already drawn by enter_to_square)
            res = confirm_move(move)
            if res is None:
                if persistent_trail_active:
                    seed = None
                    continue
                return

            if res == 'ok':
                # Show the trail again in the correct color (GREEN for user)
                trail_color = _color_for_user_confirm()
                board.clear(BLACK)  # dark background for clarity
                board.draw_trail(move, trail_color)

                time.sleep_ms(200)
                send_to_pi(move)  # Pi validates after OK; illegal => will send error
                # Return to markings; further feedback handled via main_loop on Pi messages
                board.show_markings()
                return

            # res is ('redo', btn)
            if isinstance(res, tuple) and res[0] == 'redo':
                cancel_btn = res[1]
                seed = cancel_btn if (1 <= cancel_btn <= 8) else None
                cp.coord(True)
                continue
    finally:
        in_input = False

def game_over_wait_ok_and_ack(result_str):
    """
    Show MAGENTA '#' scene and wait until OK is pressed.
    Then send 'n' to Pi (same message you use for New Game)
    so the Pi can return to mode select.
    """
    buttons.reset()
    cp.coord(False); cp.hint(True); cp.ok(True)
    board.show_checkmate_scene_hash()
    time.sleep_ms(500)

    # Optional: blink OK pixel while waiting
    blink = False
    last = time.ticks_ms()

    while True:
        now = time.ticks_ms()
        if time.ticks_diff(now, last) > 400:
            blink = not blink
            cp.ok(blink)
            last = now

        b = buttons.detect_press()
        if b == (OK_BUTTON_INDEX + 1):
            cp.ok(False)
            send_to_pi("n")  # signal the Pi to return to mode select
            break
        time.sleep_ms(20)

    # restore markings after acknowledgment
    board.show_markings()

# ============================================================
# =============== SETUP / MODE SELECTION =====================
# ============================================================

def wait_for_mode_request():
    """Show intro sweep and wait until Pi asks us to choose a mode."""
    board.opening_markings()
    lit = 0
    while True:
        lit = board.loading_status(lit)
        time.sleep_ms(1000)
        msg = read_from_pi()
        if not msg:
            continue
        print(f"{msg}")
        if msg.startswith("heyArduinoChooseMode"):
            # Finish the loading fill quickly
            while lit < (board.w * board.h):
                lit = board.loading_status(lit)
                time.sleep_ms(15)
            cp.fill(WHITE, 0, 5)
            board.show_markings()
            # enter SETUP state
            global game_state
            game_state = GAME_SETUP
            return

def select_game_mode():
    """Button 1=PC, 2=Online, 3=Local. Send preserved UART strings to Pi."""
    buttons.reset()
    global game_mode
    while True:
        b = buttons.detect_press()
        if b == 1:
            game_mode = MODE_PC
            send_to_pi("btn_mode_pc")
            return
        if b == 2:
            game_mode = MODE_ONLINE
            send_to_pi("btn_mode_online")
            return
        if b == 3:
            game_mode = MODE_LOCAL
            send_to_pi("btn_mode_local")
            return
        time.sleep_ms(5)

def select_singlepress(default_value, out_min, out_max):
    """Map 1..8 to supplied range on a single coord press."""
    buttons.reset()
    while True:
        b = buttons.detect_press()
        if b and 1 <= b <= 8:
            return map_range(b, 1, 8, out_min, out_max)
        time.sleep_ms(5)

def select_strength_singlepress(default_value):
    return select_singlepress(default_value, 1, 20)

def select_time_singlepress(default_value):
    return select_singlepress(default_value, 1000, 8000)

def select_color_choice():
    """Color: 1=White(First), 2=Black(Second), 3=Random — forward to Pi."""
    buttons.reset()
    while True:
        b = buttons.detect_press()
        if b == 1: send_to_pi("s1"); return   # White/First
        if b == 2: send_to_pi("s2"); return   # Black/Second
        if b == 3: send_to_pi("s3"); return   # Random
        time.sleep_ms(5)

def wait_for_setup():
    """
    Handle configuration prompts from Pi during setup:
      - default_strength_<n>
      - default_time_<ms>
      - EngineStrength / TimeControl / PlayerColor
      - SetupComplete
    """
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
                board.show_strength_prompt()
                v = select_strength_singlepress(default_strength)
                send_to_pi(str(v))
                time.sleep_ms(120)
                board.show_markings()
                return

            if msg.startswith("heyArduinoTimeControl"):
                board.show_time_prompt()
                v = select_time_singlepress(default_move_time)
                send_to_pi(str(v))
                time.sleep_ms(120)
                board.show_markings()
                return

            if msg.startswith("heyArduinoPlayerColor"):
                select_color_choice()
                board.show_markings()
                return

            if msg.startswith("heyArduinoSetupComplete"):
                game_state = GAME_RUNNING
                in_setup = False
                board.show_markings()
                return
    finally:
        enable_hint_irq()

# ============================================================
# =============== PROMOTION CHOICE ===========================
# ============================================================

def handle_promotion_choice():
    """
    Pi requests promotion choice; we forward btn_<piece> back.
    1=Queen, 2=Rook, 3=Bishop, 4=Knight (as before).
    """
    buttons.reset()
    while True:
        irq = process_hint_irq()
        if irq == "new":
            return
        b = buttons.detect_press()
        if not b:
            time.sleep_ms(5); continue
        if b == 1: send_to_pi("btn_q"); return
        if b == 2: send_to_pi("btn_r"); return
        if b == 3: send_to_pi("btn_b"); return
        if b == 4: send_to_pi("btn_n"); return

# ============================================================
# =============== MAIN LOOP ==================================
# ============================================================

def main_loop():
    """Central message loop: handles hints, engine moves, errors, turns, and setup requests."""
    global current_turn
    while True:
        # Consume any hint/new-game IRQ
        irq = process_hint_irq()
        if irq == "new":
            disable_hint_irq(); cp.hint(False); cp.coord(False)
            board.show_markings()
            continue

        msg = read_from_pi()
        if not msg:
            time.sleep_ms(10); continue
        
        # GameOver from Pi: "heyArduinoGameOver:<result>"
        if msg.startswith("heyArduinoGameOver"):
            res = ""
            if ":" in msg:
                res = msg.split(":", 1)[1].strip()
            game_over_wait_ok_and_ack(res)
            continue

        # Hard reset from Pi
        if msg.startswith("heyArduinoResetBoard"):
            hard_reset_board()
            continue

        # Mode selection (Pi triggers it)
        if msg.startswith("heyArduinoChooseMode"):
            disable_hint_irq(); buttons.reset()
            cp.hint(False); board.show_markings(); cp.fill(WHITE,0,5)
            global game_state
            game_state = GAME_SETUP
            select_game_mode()
            while game_state == GAME_SETUP:
                wait_for_setup()
            continue

        # Game start banner (compat)
        if msg.startswith("heyArduinoGameStart"):
            board.show_markings()
            continue

        # Computer/engine move (with optional _cap)
        if msg.startswith("heyArduinom"):
            raw = msg[11:].strip()
            cap = False
            if raw.endswith("_cap"):
                mv = raw[:-4]
                cap = True
            else:
                mv = raw

            # Draw deep blue trail; cyan end if capture flag provided by Pi
            board.clear(BLACK)
            board.draw_trail(mv, ENGINE_COLOR, end_color=(CYAN if cap else None))
            show_persistent_trail(mv, ENGINE_COLOR, 'engine', end_color=(CYAN if cap else None))

            cancel_user_input_and_restart()
            continue

        # Promotion request from Pi
        if msg.startswith("heyArduinopromotion_choice_needed"):
            handle_promotion_choice()
            continue

        # Hint trail from Pi (with optional _cap)
        if msg.startswith("heyArduinohint_"):
            raw = msg[len("heyArduinohint_"):].strip()
            cap = False
            if raw.endswith("_cap"):
                best = raw[:-4]; cap = True
            else:
                best = raw
            board.clear(BLACK)
            board.draw_trail(best, YELLOW, end_color=(CYAN if cap else None))
            show_persistent_trail(best, YELLOW, 'hint', end_color=(CYAN if cap else None))

            cancel_user_input_and_restart()
            continue

        # Illegal / error from Pi -> full red flash, then prompt same side again
        if msg.startswith("heyArduinoerror"):
            board.illegal_flash(hold_ms=700)
            cp.coord(True)
            collect_and_send_move()
            continue

        # Turn notification: heyArduinoturn_W or _B
        if msg.startswith("heyArduinoturn_"):
            turn_str = msg.split("_", 1)[1].strip().lower()
            if 'w' in turn_str:
                current_turn = 'W'
            elif 'b' in turn_str:
                current_turn = 'B'
            # Prompt for local user move
            collect_and_send_move()
            continue

# ============================================================
# =============== ENTRY POINT ================================
# ============================================================

def run():
    global game_state
    print("Pico Chess Controller Starting (CLEAN REWRITE)")
    cp.fill(BLACK); board.clear(BLACK)
    buttons.reset()

    disable_hint_irq()
    wait_for_mode_request()
    board.show_markings()
    select_game_mode()

    while game_state == GAME_SETUP:
        wait_for_setup()

    board.show_markings()
    enable_hint_irq()

    while True:
        main_loop()

# Start firmware
run()

# Clear board
#board.clear(BLACK)     # uncomment to reset the board
