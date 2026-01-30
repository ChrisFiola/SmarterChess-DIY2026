# ============================================================
#  PICO FIRMWARE (2026) - Control Panel LEDs per requested UX
# ============================================================

from machine import Pin, UART
import time
import neopixel

# ============================================================
# =============== CONFIG & CONSTANTS ==========================
# ============================================================

# Buttons (active‑low) wiring stays unchanged
BUTTON_PINS = [2, 3, 4, 5, 10, 8, 7, 6, 9, 11]   # 1–8=coords, 9=A1(OK), 11=Hint IRQ
DEBOUNCE_MS = 300

# Special role indexes (0-based into BUTTON_PINS)
OK_BUTTON_INDEX   = 8   # Button 9
HINT_BUTTON_INDEX = 9   # Button 10

# NeoPixels
CONTROL_PANEL_LED_PIN   = 16
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

# Choice-lane base: where we "draw" buttons 1..N (N in [3,4,8]) on CP LEDs
CP_CHOICE_BASE = 6  # Button k -> LED index CP_CHOICE_BASE + (k-1)

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


# --- Engine move acknowledgement state ---
engine_ack_pending = False           # waiting for OK to acknowledge engine move?
pending_gameover_result = None       # "1-0" | "0-1" | "1/2-1/2" if Pi already told us
buffered_turn_msg = None             # store a turn_* that arrives before we ack the engine move


# Was a capture detected for the last previewed move?
preview_cap_flag = False


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
    """Send: heypi<kind><payload>\n (protocol preserved)."""
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


def _handle_pi_overlay_or_gameover(msg):
    """
    Returns one of:
      'gameover'  -> Game over handled (scene shown); caller must abort input
      'hint'      -> Hint shown; caller should cancel and restart input
      'engine'    -> Engine overlay shown; caller should cancel and restart input
      None        -> Irrelevant message; caller can continue
    """
    if not msg:
        return None

    if msg.startswith("heyArduinoGameOver"):
        res = msg.split(":", 1)[1].strip() if ":" in msg else ""
        game_over_wait_ok_and_ack(res)
        return "gameover"

    if msg.startswith("heyArduinohint_"):
        raw = msg[len("heyArduinohint_"):].strip()
        cap = raw.endswith("_cap")
        best = raw[:-4] if cap else raw
        show_persistent_trail(best, YELLOW, 'hint', end_color=(MAGENTA if cap else None))
        return "hint"

    if msg.startswith("heyArduinom"):
        raw = msg[11:].strip()
        cap = raw.endswith("_cap")
        mv  = raw[:-4] if cap else raw
        show_persistent_trail(mv, ENGINE_COLOR, 'engine', end_color=(MAGENTA if cap else None))
        return "engine"

    return None

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
        # OK must be GREEN when on
        self.set(CP_OK_PIX, GREEN if on else BLACK)

    def hint(self, on=True, color=YELLOW):
        # Hint must be YELLOW when on (unless explicitly overridden)
        self.set(CP_HINT_PIX, (color if on else BLACK))


class Chessboard:
    """8x8 chessboard LED matrix with DIY Machines wiring (bottom-right origin, zigzag)."""
    def __init__(self, pin, w, h, origin_bottom_right=True, zigzag=True):
        self.w, self.h = w, h
        self.origin_bottom_right = origin_bottom_right
        self.zigzag = zigzag
        self.np = neopixel.NeoPixel(Pin(pin, Pin.OUT), w*h)

        # Precompute checkerboard pattern buffer (LIGHT/DARK) for quick restore
        self._marking_cache = [BLACK]*(w*h)
        LIGHT = (100,100,100); DARK=(3,3,3)
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
        # Full MAGENTA (kept your original color override to GREEN for visibility)
        for i in range(self.w * self.h):
            self.np[i] = GREEN
        self.np.write()

        # Draw '#' bars in WHITE
        for y in range(self.h):
            self.set_square(2, y, WHITE)
            self.set_square(5, y, WHITE)
        for x in range(self.w):
            self.set_square(x, 2, WHITE)
            self.set_square(x, 5, WHITE)
        self.write()

    def show_promotion_scene_p(self):
        """
        Fill board MAGENTA and overlay a bold white 'P' glyph (8x8 grid).
        """
        for i in range(self.w * self.h):
            self.np[i] = MAGENTA
        self.np.write()

        # Draw 'P' in WHITE (blocky)
        self.draw_vline(2, 1, 6, WHITE)
        self.draw_hline(2, 6, 4, WHITE)
        self.draw_hline(2, 4, 4, WHITE)
        self.draw_vline(5, 5, 2, WHITE)
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
# =============== CP LED HELPERS (ONLY/CHOICES) ==============
# ============================================================

def cp_all_off():
    cp.fill(BLACK)

def cp_only_ok(on=True):
    """Turn everything off, then OK GREEN if on=True."""
    cp_all_off()
    cp.ok(on)

def cp_only_hint_and_coords_for_input():
    """
    For user's move entry:
      - coords ON (0..3, white)
      - hint ON (YELLOW)
      - OK OFF
      - everything else OFF
    """
    cp_all_off()
    cp.coord(True)
    cp.hint(True, YELLOW)
    # OK remains off

def cp_show_choice_range(btn_start, btn_end, color):
    """
    Light only the "buttons" in [btn_start..btn_end] on the CP choice-lane.
    Button k -> LED index CP_CHOICE_BASE + (k-1).
    All other CP LEDs are off.
    """
    cp_all_off()
    for k in range(btn_start, btn_end + 1):
        idx = CP_CHOICE_BASE + (k - 1)
        if 0 <= idx < CONTROL_PANEL_LED_COUNT:
            cp.set(idx, color)

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
    cp_all_off(); board.clear(BLACK); board.show_markings()

    
def wait_ok_fresh(blink_ok=True):
    """
    Wait for a fresh OK press:
     - require release (active-low -> wait for HIGH)
     - small guard
     - then wait for a new press
    """
    if blink_ok:
        cp_only_ok(True)  # show OK (GREEN), others off
    # require release first
    while BTN_OK.value() == 0:
        time.sleep_ms(10)
    time.sleep_ms(180)
    buttons.reset()

    # Wait for press
    while True:
        b = buttons.detect_press()
        if b == (OK_BUTTON_INDEX + 1):
            cp_only_ok(False)
            return
        time.sleep_ms(15)


def probe_capture_with_pi(uci, timeout_ms=150):
    """
    Ask the Pi if <uci> would capture in the *current* board state.
    Returns True/False. Times out quickly to avoid blocking UX.
    """
    global preview_cap_flag
    preview_cap_flag = False
    # send heypicapq_<uci>
    send_to_pi("capq_", uci)

    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        msg = read_from_pi()
        if not msg:
            time.sleep_ms(5)
            continue
        # expect heyArduinocapr_0 or heyArduinocapr_1
        if msg.startswith("heyArduinocapr_"):
            val = msg.split("_", 1)[1].strip()
            preview_cap_flag = (val.startswith("1"))
            return preview_cap_flag
    return False


# ============================================================
# =============== PERSISTENT TRAILS (HINT/ENGINE) ============
# ============================================================

def clear_persistent_trail():
    """Clear any persistent hint/engine overlay and restore markings."""
    global persistent_trail_active, persistent_trail_type, persistent_trail_move
    persistent_trail_active = False
    persistent_trail_type   = None
    persistent_trail_move   = None
    # do not touch CP LEDs here (callers decide)
    board.show_markings()

def show_persistent_trail(move_uci, color, trail_type, end_color=None):
    """
    Draw a persistent overlay (latest wins). For engine/hint we allow an end_color
    (e.g., MAGENTA for capture) — this is *not* the pre-OK user preview.
    """
    global persistent_trail_active, persistent_trail_type, persistent_trail_move
    persistent_trail_active = True
    persistent_trail_type   = trail_type   # 'hint' or 'engine'
    persistent_trail_move   = move_uci
    board.clear(BLACK)
    board.draw_trail(move_uci, color, end_color=end_color)

def cancel_user_input_and_restart():
    """
    Abort current input phase but keep the overlay on screen.
    Used when a newer hint/engine trail arrives mid-entry.
    """
    buttons.reset()
    # CP LEDs are controlled by callers to enforce "only" rules.

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
        # Immediately show "choose mode" availability (1..3 only)
        cp_show_choice_range(1, 3, WHITE)
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

    # Forward hint request to Pi; no special CP state here (hint will arrive via UART)
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

def enter_from_square(seed_btn=None):
    """Collect FROM: column then row. If overlay active, clear on first press."""
    if game_state != GAME_RUNNING:
        return None

    # If overlay is active, clear it on first user press (OK or coord)
    if persistent_trail_active:
        while True:
            msg = read_from_pi()
            if msg:
                outcome = _handle_pi_overlay_or_gameover(msg)
                if outcome == "gameover":
                    return None
            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5); continue
            clear_persistent_trail()
            if 1 <= b <= 8:
                seed_btn = b
            break

        cp_only_hint_and_coords_for_input()
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
                outcome = _handle_pi_overlay_or_gameover(msg)
                if outcome == "gameover":
                    return None
                if outcome in ("hint", "engine"):
                    cancel_user_input_and_restart()
                    return None

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
            outcome = _handle_pi_overlay_or_gameover(msg)
            if outcome == "gameover":
                return None
            if outcome in ("hint", "engine"):
                cancel_user_input_and_restart()
                return None

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
    """Collect TO: column then row; draw green trail (no MAGENTA, no legality)."""
    if game_state != GAME_RUNNING:
        return None

    # If overlay is active, clear it on first user press (OK or coord)
    if persistent_trail_active:
        while True:
            msg = read_from_pi()
            if msg:
                outcome = _handle_pi_overlay_or_gameover(msg)
                if outcome == "gameover":
                    return None
            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5); continue
            clear_persistent_trail()
            if 1 <= b <= 8:
                seed_btn = b
            break

        cp_only_hint_and_coords_for_input()
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
            outcome = _handle_pi_overlay_or_gameover(msg)
            if outcome == "gameover":
                return None
            if outcome in ("hint", "engine"):
                cancel_user_input_and_restart()
                return None

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
            outcome = _handle_pi_overlay_or_gameover(msg)
            if outcome == "gameover":
                return None
            if outcome in ("hint", "engine"):
                cancel_user_input_and_restart()
                return None

        b = buttons.detect_press()
        if not b:
            time.sleep_ms(5); continue
        if ButtonManager.is_non_coord_button(b):
            continue
        row = str(b)
        _send_to_preview(move_from, col + row)

    # Draw simple green trail preview (FROM->TO), no MAGENTA/no legality
    to = col + row
    uci = move_from + to
    board.show_markings()
    board.draw_trail(uci, GREEN)
    
    # Ask the Pi if this would capture; recolor end square to MAGENTA if yes
    if probe_capture_with_pi(uci):
        board.draw_trail(uci, GREEN, end_color=MAGENTA)

    return to


def _color_for_user_confirm():
    return GREEN  # user move preview color after OK


def confirm_move(move):
    """
    OK to send, or redo if any other button is pressed.
    During confirm, newer hint/engine overlays cancel confirm and restart input.
    """
    if game_state != GAME_RUNNING:
        return None

    # Only OK (GREEN) during confirm
    cp_only_ok(True)
    buttons.reset()
    _send_confirm_preview(move)

    while True:
        if game_state != GAME_RUNNING:
            cp_only_ok(False)
            return None

        irq = process_hint_irq()
        if irq == "new":
            cp_only_ok(False)
            return None

        # New overlay cancels confirm
        msg = read_from_pi()
        if msg:
            outcome = _handle_pi_overlay_or_gameover(msg)
            if outcome == "gameover":
                cp_only_ok(False)
                return None
            if outcome in ("hint", "engine"):
                cancel_user_input_and_restart()
                return None

        b = buttons.detect_press()
        if not b:
            time.sleep_ms(5); continue

        if b == (OK_BUTTON_INDEX+1):  # OK
            cp_only_ok(False)
            return "ok"
        else:
            # Cancel confirm; allow FROM seed if coord button
            cp_only_ok(False)
            board.show_markings()
            return ("redo", b)

def collect_and_send_move():
    """Full user move entry cycle. No pre-OK legality. Simple green preview."""
    global in_input, preview_cap_flag
    in_input = True
    try:
        seed = None  # optional seed coord if user cancels with a coord button
        while True:
            # For user's move input: coords + hint(YELLOW) only
            cp_only_hint_and_coords_for_input()
            buttons.reset()

            move_from = enter_from_square(seed_btn=seed)
            if move_from is None:
                if persistent_trail_active:
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
                # After OK is pressed, all CP LEDs off (clean state)
                cp_all_off()

                # Show the trail again in GREEN (MAGENTA end if capture previewed)
                trail_color = _color_for_user_confirm()
                board.clear(BLACK)
                board.draw_trail(move, trail_color, end_color=(MAGENTA if preview_cap_flag else None))

                time.sleep_ms(200)
                send_to_pi(move)  # Pi validates after OK; illegal => will send error
                preview_cap_flag = False
                board.show_markings()
                return

            # res is ('redo', btn)
            if isinstance(res, tuple) and res[0] == 'redo':
                cancel_btn = res[1]
                seed = cancel_btn if (1 <= cancel_btn <= 8) else None
                # Back to input state display
                cp_only_hint_and_coords_for_input()
                continue
    finally:
        in_input = False


def game_over_wait_ok_and_ack(result_str):
    disable_hint_irq()
    try:
        buttons.reset()
        # Game over: ONLY OK blinks (GREEN)
        cp_only_ok(True)
        board.show_checkmate_scene_hash()

        # Wait for OK to be released, then a small guard to avoid bounce
        while BTN_OK.value() == 0:
            time.sleep_ms(10)
        time.sleep_ms(200)
        buttons.reset()

        blink = False
        last = time.ticks_ms()
        while True:
            now = time.ticks_ms()
            if time.ticks_diff(now, last) > 400:
                blink = not blink
                # Keep "only OK" while blinking
                cp_all_off()
                cp.ok(blink)
                last = now

            b = buttons.detect_press()
            if b == (OK_BUTTON_INDEX + 1):
                cp_only_ok(False)
                send_to_pi("n")  # back to mode select on Pi
                break
            time.sleep_ms(20)

        board.show_markings()
    finally:
        enable_hint_irq()


# ============================================================
# =============== SETUP / MODE SELECTION =====================
# ============================================================

def wait_for_mode_request():
    """Show intro sweep and wait until Pi asks us to choose a mode."""
    board.opening_markings()
    lit = 0
    while True:
        lit = board.loading_status(lit)
        time.sleep_ms(2000)             # 2 seconds per LED
        msg = read_from_pi()
        if not msg:
            continue
        print(f"{msg}")
        if msg.startswith("heyArduinoChooseMode"):
            # Finish the loading fill quickly
            while lit < (board.w * board.h):
                lit = board.loading_status(lit)
                time.sleep_ms(15)
            board.show_markings()
            # CP: ONLY buttons 1..3 lit for mode selection
            cp_show_choice_range(1, 3, WHITE)
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
                # CP: show only buttons 1..8 (WHITE)
                cp_show_choice_range(1, 8, WHITE)
                board.show_strength_prompt()
                v = select_strength_singlepress(default_strength)
                send_to_pi(str(v))
                time.sleep_ms(120)
                board.show_markings()
                return

            if msg.startswith("heyArduinoTimeControl"):
                # CP: show only buttons 1..8 (WHITE)
                cp_show_choice_range(1, 8, WHITE)
                board.show_time_prompt()
                v = select_time_singlepress(default_move_time)
                send_to_pi(str(v))
                time.sleep_ms(120)
                board.show_markings()
                return

            if msg.startswith("heyArduinoPlayerColor"):
                # CP: show only buttons 1..3 (WHITE)
                cp_show_choice_range(1, 3, WHITE)
                select_color_choice()
                board.show_markings()
                return

            if msg.startswith("heyArduinoSetupComplete"):
                game_state = GAME_RUNNING
                in_setup = False
                cp_all_off()  # clean CP at end of setup
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
    1=Queen, 2=Rook, 3=Bishop, 4=Knight.
    CP: Only buttons 1..4 lit in MAGENTA; OK/HINT off.
    """
    # Board side: show P scene
    board.show_promotion_scene_p()

    # CP: only 1..4 lit in MAGENTA
    cp_show_choice_range(1, 4, MAGENTA)

    buttons.reset()
    try:
        while True:
            irq = process_hint_irq()
            if irq == "new":
                return
            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5); continue
            if b == 1: send_to_pi("btn_q"); break
            if b == 2: send_to_pi("btn_r"); break
            if b == 3: send_to_pi("btn_b"); break
            if b == 4: send_to_pi("btn_n"); break
    finally:
        # Clear CP and restore markings after the choice
        cp_all_off()
        board.show_markings()


# ============================================================
# =============== MAIN LOOP ==================================
# ============================================================

def main_loop():
    """Central message loop: handles hints, engine moves, errors, turns, and setup requests."""
    global current_turn, engine_ack_pending, pending_gameover_result, buffered_turn_msg
    while True:
        # Consume any hint/new-game IRQ
        irq = process_hint_irq()
        if irq == "new":
            disable_hint_irq()
            cp_all_off()
            board.opening_markings()
            engine_ack_pending = False
            pending_gameover_result = None
            buffered_turn_msg = None
            continue

        
        # ---------- HANDLE ENGINE-ACK PENDING FIRST (even if no new msg) ----------
        if engine_ack_pending:
            # Try to read a message (non-blocking)
            nxt = read_from_pi()

            # Prioritize GameOver if it arrives while waiting for OK
            if nxt and nxt.startswith("heyArduinoGameOver"):
                pending_gameover_result = nxt.split(":", 1)[1].strip() if ":" in nxt else ""
                # Require a fresh OK to acknowledge engine move before showing GameOver
                while BTN_OK.value() == 0:
                    time.sleep_ms(10)
                time.sleep_ms(180)
                buttons.reset()
                # Now wait for a new OK press
                while True:
                    b = buttons.detect_press()
                    if b == (OK_BUTTON_INDEX + 1):
                        cp_only_ok(False)
                        break
                    time.sleep_ms(15)

                engine_ack_pending = False
                game_over_wait_ok_and_ack(pending_gameover_result)
                pending_gameover_result = None
                buffered_turn_msg = None
                continue

            # If turn_* arrives while we are waiting, buffer it
            if nxt and nxt.startswith("heyArduinoturn_"):
                buffered_turn_msg = nxt
                # keep waiting for OK; do not start input yet

            # Check OK press to acknowledge engine move
            b = buttons.detect_press()
            if b == (OK_BUTTON_INDEX + 1):
                engine_ack_pending = False
                cp_only_ok(False)
                
                # CLEAR the engine overlay so the next coordinate press isn't consumed
                clear_persistent_trail()
                board.show_markings()

                # If a turn_* was buffered (normal case when no GameOver), process it now
                if buffered_turn_msg:
                    turn_str = buffered_turn_msg.split("_", 1)[1].strip().lower()
                    if 'w' in turn_str:
                        current_turn = 'W'
                    elif 'b' in turn_str:
                        current_turn = 'B'
                    buffered_turn_msg = None

                # Start collecting the human move
                cp_only_hint_and_coords_for_input()
                collect_and_send_move()
                continue

            # Still waiting; small sleep to avoid a hot loop
            time.sleep_ms(10)
            continue
        # ---------- END ENGINE-ACK PENDING GATE ----------


        msg = read_from_pi()
        print(f"{msg}")
        if not msg:
            time.sleep_ms(10); continue
        
        # --- If we're waiting for engine-move acknowledgement, prioritize that flow --- 
        if engine_ack_pending:
            if msg.startswith("heyArduinoGameOver"):
                res = msg.split(":", 1)[1].strip() if ":" in msg else ""
                pending_gameover_result = res
                # Wait for user's OK to acknowledge engine move
                wait_ok_fresh(blink_ok=True)
                engine_ack_pending = False
                cp_only_ok(False)
                game_over_wait_ok_and_ack(pending_gameover_result)
                pending_gameover_result = None
                buffered_turn_msg = None
                continue

            if msg.startswith("heyArduinoturn_"):
                buffered_turn_msg = msg
                continue

            btn = buttons.detect_press()
            if btn == (OK_BUTTON_INDEX + 1):
                engine_ack_pending = False
                cp_only_ok(False)
                if buffered_turn_msg:
                    turn_str = buffered_turn_msg.split("_", 1)[1].strip().lower()
                    if 'w' in turn_str:
                        current_turn = 'W'
                    elif 'b' in turn_str:
                        current_turn = 'B'
                    buffered_turn_msg = None
                    cp_only_hint_and_coords_for_input()
                    collect_and_send_move()
                    continue
                else:
                    board.show_markings()
                    continue

            time.sleep_ms(10)
            continue

        
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
            board.show_markings()
            cp_show_choice_range(1, 3, WHITE)  # only 1..3 lit
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

            # Draw deep blue trail; MAGENTA end if capture flag provided by Pi
            board.clear(BLACK)
            show_persistent_trail(mv, ENGINE_COLOR, 'engine', end_color=(MAGENTA if cap else None))

            # ONLY OK (GREEN) must light when a move is received
            cp_only_ok(True)

            # Acknowledgement flow (unchanged)
            engine_ack_pending = True
            pending_gameover_result = None
            buffered_turn_msg = None
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

            # Board overlay persists (visual hint)
            board.clear(BLACK)
            show_persistent_trail(best, YELLOW, 'hint', end_color=(MAGENTA if cap else None))

            # When a hint is received, ONLY OK lights (GREEN)
            cp_only_ok(True)

            cancel_user_input_and_restart()
            continue

        # Illegal / error from Pi -> full red flash, then prompt same side again
        if msg.startswith("heyArduinoerror"):
            board.illegal_flash(hold_ms=700)
            # After error: back to move input state (coords + hint yellow only)
            cp_only_hint_and_coords_for_input()
            collect_and_send_move()
            continue

        # Turn notification: heyArduinoturn_W or _B
        if msg.startswith("heyArduinoturn_"):
            turn_str = msg.split("_", 1)[1].strip().lower()
            if 'w' in turn_str:
                current_turn = 'W'
            elif 'b' in turn_str:
                current_turn = 'B'
            # Peek for immediate GameOver
            t_start = time.ticks_ms()
            while time.ticks_diff(time.ticks_ms(), t_start) < 80:
                nxt = read_from_pi()
                if not nxt:
                    time.sleep_ms(5)
                    continue
                if nxt.startswith("heyArduinoGameOver"):
                    res = nxt.split(":", 1)[1].strip() if ":" in nxt else ""
                    game_over_wait_ok_and_ack(res)
                    break
            else:
                # Start collecting the human move; CP state for input:
                cp_only_hint_and_coords_for_input()
                collect_and_send_move()
            continue


# ============================================================
# =============== ENTRY POINT ================================
# ============================================================

def run():
    global game_state
    print("Pico Chess Controller Starting (LED UX Update)")
    cp_all_off(); board.clear(BLACK)
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