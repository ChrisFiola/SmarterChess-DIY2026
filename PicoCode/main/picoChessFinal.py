
# ============================================================
#  PICO FIRMWARE — FINAL VERSION (2026) — TRAIL + PREVIEW PATCH
#  PATCH NOTES (Jan 2026):
#   - Knight trail: include unit steps along the long leg first (e.g., g1→g2→g3→f3)
#   - Computer (black/engine) move: guaranteed vivid red, instant, on dark background
#   - Hint: shows instantly on dark background (no pre-markings delay)
#   - Illegal move: shows solid red instantly
#   - FROM-square preview now GREEN (was WHITE)
#   - Local play: White moves = GREEN, Black moves = RED
#   - Versus computer: user's move = GREEN, engine's move = RED
#   - Clear frames before scenes; revert to checkerboard consistently
# ============================================================

from machine import Pin, UART
import time
import neopixel

# ============================================================
# CONFIGURATION
# ============================================================

# Buttons (active‑low). Keep as per your last wiring.
BUTTON_PINS = [2, 3, 4, 6, 7, 8, 9, 10, 12, 13]   # 1–8=coords, 9=A1(OK), 10=Hint IRQ
DEBOUNCE_MS = 300

# Special roles (indexes into BUTTON_PINS, 0-based)
OK_BUTTON_INDEX   = 8   # Button 9
HINT_BUTTON_INDEX = 9   # Button 10

# NeoPixels
CONTROL_PANEL_LED_PIN   = 14
CONTROL_PANEL_LED_COUNT = 22
CHESSBOARD_LED_PIN = 22
BOARD_W, BOARD_H   = 8, 8

# Matrix orientation (DIY Machines: bottom-right origin + rows + zigzag)
MATRIX_ORIGIN_BOTTOM_RIGHT = True
MATRIX_ZIGZAG = True

# Colors
BLACK=(0,0,0); WHITE=(255,255,255); DIMW=(10,10,10)
RED=(255,0,0); GREEN=(0,255,0); BLUE=(0,0,255)
CYAN=(0,255,255); MAGENTA=(255,0,255); YELLOW=(255,255,0); ORANGE=(255,130,0)
ENGINE_COLOR = BLUE  # Deep blue for computer moves

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

# Modes / turn tracking
MODE_PC     = "pc"      # vs computer
MODE_ONLINE = "online"  # vs remote
MODE_LOCAL  = "local"   # local 2P
game_mode   = MODE_PC
current_turn = 'W'      # 'W' or 'B' (set from heyArduinoturn_*)

# Defaults
default_strength   = 5      # Pi maps 1..8 => 1..20
default_move_time  = 2000   # Pi maps 1..8 => 3000..12000

# ============================================================
# PERSISTENT TRAIL (hint/engine overlay) STATE
# ============================================================
# When active, a yellow (hint) or red (engine) trail stays lit
# until user presses a button (OK or coordinate). Latest wins.
persistent_trail_active = False
persistent_trail_type   = None    # 'hint' or 'engine'
persistent_trail_move   = None    # UCI string (e.g., 'e2e4')
last_preview_capture    = False   # set by legality check after TO


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
    """Typing preview for FROM/TO/CONFIRM only."""
    if game_state != GAME_RUNNING:
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

    # ---------- New: trail helpers ----------

    @staticmethod
    def _sgn(v):
        return 0 if v == 0 else (1 if v > 0 else -1)

    def _path_squares(self, frm, to):
        """
        Return a list of (x,y) covering FROM..TO inclusive:
         - Straight rank/file
         - Diagonal
         - Knight: include UNIT steps along the long leg first (L path)
         - Anything else: [from, to]
        """
        f = self.algebraic_to_xy(frm)
        t = self.algebraic_to_xy(to)
        if not f or not t:
            return []

        fx, fy = f; tx, ty = t
        dx = tx - fx; dy = ty - fy
        adx, ady = abs(dx), abs(dy)

        path = []

        # Straight file
        if fx == tx and fy != ty:
            sy = self._sgn(dy)
            for y in range(fy, ty + sy, sy):
                path.append((fx, y))
            return path

        # Straight rank
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

        # Knight (L): include unit steps along the LONGER leg first
        if (adx, ady) in ((1,2), (2,1)):
            sx = self._sgn(dx); sy = self._sgn(dy)
            path.append((fx, fy))
            if ady == 2:
                # Vertical first: step 1, step 2, then horizontal 1
                path.append((fx, fy + 1*sy))
                path.append((fx, fy + 2*sy))
                path.append((fx + 1*sx, fy + 2*sy))
            else:
                # Horizontal first: step 1, step 2, then vertical 1
                path.append((fx + 1*sx, fy))
                path.append((fx + 2*sx, fy))
                path.append((fx + 2*sx, fy + 1*sy))
            # Ensure last is exactly target (in case of any rounding)
            if path[-1] != (tx, ty):
                path.append((tx, ty))
            # Dedup any accidental dupes (defensive)
            dedup = []
            for p in path:
                if not dedup or dedup[-1] != p:
                    dedup.append(p)
            return dedup

        # King step or odd shape (fallback)
        return [(fx, fy), (tx, ty)]

    def draw_trail(self, move_uci, color, end_color=None):
        """
        Light FROM..TO (inclusive) with a path that covers all crossed squares.
        """
        if not move_uci or len(move_uci) < 4:
            return
        frm, to = move_uci[:2], move_uci[2:4]

        path = self._path_squares(frm, to)
        for i, (x,y) in enumerate(path):
            if end_color and i == len(path)-1:
                self.set_square(x, y, end_color)
            else:
                self.set_square(x, y, color)

        self.write()

    # ---------- Display patterns ----------

    def show_markings(self):
        # Clear first to avoid residuals
        self.clear(BLACK)
        LIGHT = (100,100,120)  # cool-ish white to reduce yellow tint
        DARK  = (0,0,0)        # off
        for y in range(self.h):
            for x in range(self.w):
                col = DARK if ((x+y) % 2 == 0) else LIGHT
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
        # Blue progressive fill (DIY style)
        total = self.w*self.h
        if count >= total:
            return count
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

    def light_up_move(self, m, mode='Y'):
        # Kept for compatibility; use draw_trail for color control
        if not m or len(m) < 4: return
        frm, to = m[:2], m[2:4]
        if mode == 'Y': c = YELLOW
        elif mode == 'N': c = GREEN
        elif mode == 'C': c = RED     # computer move
        else: c = CYAN
        self.draw_trail(m, c)

    # Setup visuals to mimic Arduino setUpGame() prompts
    def draw_hline(self, x, y, length, color):
        for dx in range(length):
            self.set_square(x+dx, y, color)

    def draw_vline(self, x, y, length, color):
        for dy in range(length):
            self.set_square(x, y+dy, color)

    def show_strength_prompt(self):
        # Upright 'T' — explicit pixels tuned for bottom-right origin
        self.clear(BLACK)
        T = [
            (2,6),(3,6),(4,6),(5,6),  # top bar
            (4,5),(4,4),(4,3),(4,2)   # stem downwards
        ]
        for x,y in T: self.set_square(x,y,MAGENTA)
        self.write()


    def show_time_prompt(self):
        # Upright 'L' — explicit pixels tuned for bottom-right origin
        self.clear(BLACK)
        L = [
            (2,6),(2,5),(2,4),(2,3),(2,2),  # vertical
            (3,2),(4,2),(5,2)               # bottom bar
        ]
        for x,y in L: self.set_square(x,y,MAGENTA)
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
        return b in (9,10)

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
# PERSISTENT TRAIL HELPERS
# ============================================================
def clear_persistent_trail():
    """Clear any persistent yellow/red trail and restore board markings."""
    global persistent_trail_active, persistent_trail_type, persistent_trail_move
    persistent_trail_active = False
    persistent_trail_type   = None
    persistent_trail_move   = None
    cp.hint(False)
    board.show_markings()

def show_persistent_trail(move_uci, color, trail_type):
    """Show & remember a persistent overlay (latest wins)."""
    global persistent_trail_active, persistent_trail_type, persistent_trail_move
    persistent_trail_active = True
    persistent_trail_type   = trail_type   # 'hint' or 'engine'
    persistent_trail_move   = move_uci
    board.clear(BLACK)
    board.draw_trail(move_uci, color)
    if trail_type == 'hint':
        cp.hint(True, WHITE)

def cancel_user_input_and_restart():
    """Abort current input/confirm phase but keep the overlay on screen."""
    global confirm_mode, in_input
    confirm_mode = False
    in_input     = False
    buttons.reset()
    cp.coord(True); cp.ok(False)    # Do NOT call board.show_markings() here — we keep the overlay.


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
        board.clear(BLACK)
        while v < (board.w * board.h):
            v = board.loading_status(v)
            time.sleep_ms(25)
        time.sleep_ms(350)
        board.show_markings()
        suppress_hints_until_ms = time.ticks_add(now, 800)
        return "new"

    # Hint during setup is ignored (Arduino-like)
    if game_state != GAME_RUNNING:
        return None

    # Signal hint request to Pi
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
# MOVE ENTRY (8‑button layout) + LED PREVIEWS
# ============================================================

def enter_from_square(seed_btn=None):
    if game_state != GAME_RUNNING:
        return None

    col=None; row=None
    
    # If an overlay is active, clear it on first user press (OK or coord)
    if persistent_trail_active:
        while True:
            # Newest event overrides the current overlay
            msg = read_from_pi()
            if msg:
                if msg.startswith("heyArduinohint_"):
                    show_persistent_trail(msg[15:].strip(), YELLOW, 'hint'); continue
                if msg.startswith("heyArduinom"):
                    show_persistent_trail(msg[11:].strip(), RED, 'engine');   continue
            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5); continue
            # Any button clears; coordinate press becomes the seed column
            clear_persistent_trail()
            if 1 <= b <= 8:
                seed_btn = b
            break

        cp.coord(True); cp.ok(False); cp.hint(False)
        buttons.reset()

    # Column (a..h)
    while col is None:
        if game_state != GAME_RUNNING:
            return None

        if seed_btn is not None:
            b = seed_btn
            seed_btn = None
        else:
            irq = process_hint_irq()
            if irq == "new": return None
            # Interrupts from Pi (latest wins) during column selection
            msg = read_from_pi()
            if msg:
                if msg.startswith("heyArduinohint_"):
                    show_persistent_trail(msg[15:].strip(), YELLOW, 'hint')
                    cancel_user_input_and_restart(); return None
                if msg.startswith("heyArduinom"):
                    show_persistent_trail(msg[11:].strip(), RED, 'engine')
                    cancel_user_input_and_restart(); return None
            b = buttons.detect_press()

            if not b:
                time.sleep_ms(5)
                continue

        if ButtonManager.is_non_coord_button(b):
            continue
        col = chr(ord('a') + b - 1)
        _send_from_preview(col)

    # Row (1..8)
    while row is None:
        if game_state != GAME_RUNNING:
            return None
        
        irq = process_hint_irq()
        if irq == "new": return None
        # Interrupts from Pi (latest wins) during row selection
        msg = read_from_pi()
        if msg:
            if msg.startswith("heyArduinohint_"):
                show_persistent_trail(msg[15:].strip(), YELLOW, 'hint')
                cancel_user_input_and_restart(); return None
            if msg.startswith("heyArduinom"):
                show_persistent_trail(msg[11:].strip(), RED, 'engine')
                cancel_user_input_and_restart(); return None
        b = buttons.detect_press()

        if not b:
            time.sleep_ms(5); continue
        if ButtonManager.is_non_coord_button(b):
            continue
        row = str(b)
        _send_from_preview(col + row)

    # Show FROM square preview on board (GREEN per requirement)
    frm = col + row
    fxy = board.algebraic_to_xy(frm)
    board.show_markings()
    if fxy:
        board.set_square(fxy[0], fxy[1], GREEN)  # FROM preview (GREEN)
        board.write()
    return frm

def enter_to_square(move_from):
    if game_state != GAME_RUNNING:
        return None

    col=None; row=None
    # If an overlay is active, clear it on first user press (OK or coord)
    if persistent_trail_active:
        while True:
            msg = read_from_pi()
            if msg:
                if msg.startswith("heyArduinohint_"):
                    show_persistent_trail(msg[15:].strip(), YELLOW, 'hint'); continue
                if msg.startswith("heyArduinom"):
                    show_persistent_trail(msg[11:].strip(), RED, 'engine');   continue
            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5); continue
            clear_persistent_trail()
            break

    cp.coord(True); cp.ok(False)
    buttons.reset()

    # Column
    while col is None:
        if game_state != GAME_RUNNING:
            return None

        irq = process_hint_irq()
        if irq == "new": return None
        # Interrupts from Pi during TO-column selection
        msg = read_from_pi()
        if msg:
            if msg.startswith("heyArduinohint_"):
                show_persistent_trail(msg[15:].strip(), YELLOW, 'hint')
                cancel_user_input_and_restart(); return None
            if msg.startswith("heyArduinom"):
                show_persistent_trail(msg[11:].strip(), RED, 'engine')
                cancel_user_input_and_restart(); return None
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
        # Interrupts from Pi during TO-row selection
        msg = read_from_pi()
        if msg:
            if msg.startswith("heyArduinohint_"):
                show_persistent_trail(msg[15:].strip(), YELLOW, 'hint')
                cancel_user_input_and_restart(); return None
            if msg.startswith("heyArduinom"):
                show_persistent_trail(msg[11:].strip(), RED, 'engine')
                cancel_user_input_and_restart(); return None
        b = buttons.detect_press()

        if not b: time.sleep_ms(5); continue
        if ButtonManager.is_non_coord_button(b): continue
        row = str(b)
        _send_to_preview(move_from, col + row)


    to = col + row

    # ===== Pre-OK legality & capture check with Pi =====
    move = move_from + to
    send_to_pi("chk_", move)  # -> heypichk_<uci>
    # wait up to ~600ms for response
    t0 = time.ticks_ms(); verdict = None
    while time.ticks_diff(time.ticks_ms(), t0) < 600:
        msg = read_from_pi()
        if not msg:
            time.sleep_ms(5); continue
        if msg.startswith("heyArduinocheck_illegal_"):
            verdict = "illegal"; break
        if msg.startswith("heyArduinocheck_ok_"):
            verdict = msg  # contains _cap or _nocap
            break

    # Default if no reply: treat as legal non-capture (failsafe)
    if verdict is None:
        board.show_markings()
        board.draw_trail(move, GREEN)
        return to

    # Illegal preview BEFORE OK: draw red and restart
    if verdict == "illegal" or verdict.startswith("heyArduinocheck_illegal_"):
        board.show_markings()
        board.draw_trail(move, RED)
        time.sleep_ms(650)
        board.show_markings()
        return None  # restart fresh

    # Legal path: capture vs non-capture
    is_cap = verdict.endswith("_cap")
    global last_preview_capture
    last_preview_capture = is_cap
    board.show_markings()
    if is_cap:
        board.draw_trail(move, GREEN, end_color=CYAN)
    else:
        board.draw_trail(move, GREEN)
    return to


def _color_for_user_confirm():
    # Decide trail color when the local user confirms a move
    # - Local mode: White=GREEN, Black=GREEN
    # - PC/engine mode: user's move always GREEN
    if game_mode == MODE_LOCAL:
        return GREEN if (current_turn == 'W') else GREEN
    return GREEN

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
            
            # If Pi sends hint/engine now, overlay it & cancel confirm
            msg = read_from_pi()
            if msg:
                if msg.startswith("heyArduinohint_"):
                    show_persistent_trail(msg[15:].strip(), YELLOW, 'hint')
                    cancel_user_input_and_restart(); return None
                if msg.startswith("heyArduinom"):
                    show_persistent_trail(msg[11:].strip(), RED, 'engine')
                    cancel_user_input_and_restart(); return None

            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5)
                continue

            if b == (OK_BUTTON_INDEX+1):  # OK
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
        seed = None  # coordinate seed for FROM
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

            # Keep the preview trail until OK/redo (already drawn in enter_to_square)
            res = confirm_move(move)
            if res is None:
                if persistent_trail_active:
                    seed = None
                    continue
                return


            if res == 'ok':
                # Show the trail again in the correct color, then send
                trail_color = _color_for_user_confirm()
                board.clear(BLACK)  # dark background for clarity
                if last_preview_capture and trail_color == GREEN:
                    board.draw_trail(move, trail_color, end_color=CYAN)
                else:
                    board.draw_trail(move, trail_color)

                time.sleep_ms(300)
                send_to_pi(move)
                # Return to markings
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

# ============================================================
# SETUP / MODE SELECTION
# ============================================================

def wait_for_mode_request():
    board.opening_markings()
    lit = 0
    while True:
        lit = board.loading_status(lit)
        time.sleep_ms(1000)
        msg = read_from_pi()
        if not msg: continue
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
    buttons.reset()
    while True:
        b = buttons.detect_press()
        if b and 1 <= b <= 8:
            return map_range(b, 1, 8, out_min, out_max)
        time.sleep_ms(5)

def select_strength_singlepress(default_value):
    return select_singlepress(default_value, 1, 20)

def select_time_singlepress(default_value):
    return select_singlepress(default_value, 3000, 12000)

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
                board.show_strength_prompt()
                v = select_strength_singlepress(default_strength)
                send_to_pi(str(v));
                time.sleep_ms(150)
                board.show_markings()
                return

            if msg.startswith("heyArduinoTimeControl"):
                board.show_time_prompt()
                v = select_time_singlepress(default_move_time)
                send_to_pi(str(v));
                time.sleep_ms(150)
                board.show_markings()
                return

            if msg.startswith("heyArduinoPlayerColor"):
                select_color_choice();
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
    global showing_hint, hint_hold_mode, hint_irq_flag, current_turn
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
            board.show_markings()
            continue


        # Computer/engine move (from Pi): persistent BLUE trail
        if msg.startswith("heyArduinom"):
            raw = msg[11:].strip()
            cap = False
            if raw.endswith("_cap"):
                mv = raw[:-4]
                cap = True
            else:
                mv = raw
            # Draw with engine deep blue and cyan end if capture
            board.clear(BLACK)
            board.draw_trail(mv, ENGINE_COLOR, end_color=(CYAN if cap else None))
            # Remember persistent overlay
            show_persistent_trail(mv, ENGINE_COLOR, 'engine')

            cancel_user_input_and_restart()
            continue


        # Promotion
        if msg.startswith("heyArduinopromotion_choice_needed"):
            handle_promotion_choice(); continue


        # Hint from Pi: persistent YELLOW trail
        if msg.startswith("heyArduinohint_"):

            raw = msg[len("heyArduinohint_"):].strip()
            cap = False
            if raw.endswith("_cap"):
                best = raw[:-4]
                cap  = True
            else:
                best = raw
            board.clear(BLACK)
            board.draw_trail(best, YELLOW, end_color=(CYAN if cap else None))
            show_persistent_trail(best, YELLOW, 'hint')

            cancel_user_input_and_restart()
            continue


        # Illegal move / error from Pi
        if msg.startswith("heyArduinoerror"):
            # Solid red instantly
            board.illegal_flash(hold_ms=700)
            hint_hold_mode=False; hint_irq_flag=False; showing_hint=False
            cp.coord(True)
            collect_and_send_move()
            continue

        # Human to move (Pi tells us whose turn): "heyArduinoturn_W" or "_B"
        if msg.startswith("heyArduinoturn_"):
            turn_str = msg.split("_", 1)[1].strip().lower()
            if 'w' in turn_str:
                current_turn = 'W'
            elif 'b' in turn_str:
                current_turn = 'B'
            # Proceed to collect local move
            collect_and_send_move()
            continue

# ============================================================
# ENTRY
# ============================================================

def run():
    global game_state
    print("Pico Chess Controller Starting (trail+preview patched)")
    cp.fill(BLACK); board.clear(BLACK)
    buttons.reset()

    disable_hint_irq()
    wait_for_mode_request()
    board.show_markings()
    select_game_mode()

    while game_state == GAME_SETUP:
        wait_for_setup()

    board.show_markings()

    while True:
        main_loop()

# Start
run()
