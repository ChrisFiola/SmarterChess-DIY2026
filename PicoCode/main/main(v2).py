# ============================================================
#  PICO FIRMWARE (2026)
#  DIY Machines-accurate Control Panel LED behavior
#  - Single 22-LED chain: 0..5 panel, 6..21 coordinate bars (always dim)
#  - Centralized Chessboard UI preserved
# ============================================================

from machine import Pin, UART
import time
import neopixel

# ============================================================
# =============== CONFIG & CONSTANTS =========================
# ============================================================

# Buttons (active-low)
BUTTON_PINS = [2, 3, 4, 5, 10, 8, 7, 6, 9, 11]   # 1–8=coords, 9=A1(OK), 11=Hint IRQ
DEBOUNCE_MS = 300

# Special role indexes (0-based into BUTTON_PINS)
OK_BUTTON_INDEX   = 8   # Button 9
HINT_BUTTON_INDEX = 9   # Button 10 (IRQ source)

# NeoPixels
CONTROL_PANEL_LED_PIN   = 16
CONTROL_PANEL_LED_COUNT = 22
CHESSBOARD_LED_PIN      = 22
BOARD_W, BOARD_H        = 8, 8

# Matrix orientation (DIY Machines)
MATRIX_ORIGIN_BOTTOM_RIGHT = True
MATRIX_ZIGZAG = True

# Colors (match DIY Machines semantics)
BLACK=(0,0,0)
WHITE=(255,255,255)
DIMW=(10,10,10)
RED=(255,0,0)
GREEN=(0,255,0)
BLUE=(0,0,255)
CYAN=(0,255,255)
MAGENTA=(255,0,255)
YELLOW=(255,255,0)
ORANGE=(255,130,0)

ENGINE_COLOR = BLUE  # Deep blue for computer moves on board trail

# ---- 22-LED chain mapping (DIY Machines style) ----
# 0..3  = small "coord ready" block
# 4     = OK (WHITE)
# 5     = Hint LED (WHITE when available, BLUE while showing hint)
# 6..13 = Files A..H (decorative)
# 14..21= Ranks 1..8 (decorative)
CP_COORD_START = 0
CP_OK_PIX      = 4
CP_HINT_PIX    = 5
CP_FILES_LEDS  = [6, 7, 8, 9, 10, 11, 12, 13]     # A..H (flip with reversed(...) if needed)
CP_RANKS_LEDS  = [14, 15, 16, 17, 18, 19, 20, 21] # 1..8 (flip with reversed(...) if needed)

# ============================================================
# =============== STATE & MODES ===============================
# ============================================================

GAME_IDLE    = 0
GAME_SETUP   = 1
GAME_RUNNING = 2
game_state   = GAME_IDLE

MODE_PC     = "pc"      # vs Stockfish (DIY Machines "Stockfish")
MODE_ONLINE = "online"  # vs remote (placeholder)
MODE_LOCAL  = "local"   # local 2P
game_mode   = MODE_PC
current_turn = 'W'      # 'W' or 'B'

default_strength   = 5
default_move_time  = 2000

# Flags
in_setup = False
in_input = False

engine_ack_pending = False
pending_gameover_result = None
buffered_turn_msg = None

preview_cap_flag = False

# Persistent board overlays
persistent_trail_active = False
persistent_trail_type   = None    # 'hint' or 'engine'
persistent_trail_move   = None    # 'e2e4'

# Hint availability (DIY: white when available on CP hint LED)
hint_available = False

# ============================================================
# =============== UART (Pico <-> Pi) =========================
# ============================================================

uart = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1), timeout=10)

def send_to_pi(kind, payload=""):
    uart.write(f"heypi{kind}{payload}\n".encode())

def read_from_pi():
    if uart.any():
        try:
            return uart.readline().decode().strip() # type: ignore
        except:
            return None
    return None

def send_typing_preview(label, text):
    if game_state != GAME_RUNNING:
        return
    uart.write(f"heypityping_{label}_{text}\n".encode())

# ============================================================
# =============== LED PANELS (CONTROL + BOARD) ===============
# ============================================================

class ControlPanel:
    """22-LED single chain: 0..5 panel, 6..21 coordinate bars (decorative)."""
    def __init__(self, pin, count):
        self.np = neopixel.NeoPixel(Pin(pin, Pin.OUT), count)
        self.count = count

    def set(self, i, c):
        if 0 <= i < self.count:
            self.np[i] = c
            self.np.write()

    def set_no_write(self, i, c):
        if 0 <= i < self.count:
            self.np[i] = c

    def write(self):
        self.np.write()

    def fill_range(self, start, length, color):
        end = min(self.count, start + length)
        for i in range(start, end):
            self.np[i] = color
        self.np.write()

    # --- DIY Machines: small 4-dot coords (0..3) ---
    def small_coords(self, on=True):
        self.fill_range(CP_COORD_START, 4, WHITE if on else BLACK)

    # --- DIY Machines: OK is WHITE (not green) ---
    def ok_white(self, on=True):
        self.set(CP_OK_PIX, WHITE if on else BLACK)

    # --- DIY Machines: Hint LED control ---
    def hint_off(self):
        self.set(CP_HINT_PIX, BLACK)

    def hint_white(self):
        self.set(CP_HINT_PIX, WHITE)

    def hint_blue(self):
        self.set(CP_HINT_PIX, BLUE)

    # --- Decorative coordinate bars (kept dim after setup) ---
    def bars_dim(self, on=True):
        # Set 6..21 to DIMW (or BLACK if off)
        col = DIMW if on else BLACK
        for idx in CP_FILES_LEDS + CP_RANKS_LEDS:
            self.set_no_write(idx, col)
        self.np.write()

    # Utils to clear only the small panel (0..5), preserving the 16-bar dim state
    def small_panel_clear(self):
        for i in range(0, 6):  # 0..5 inclusive
            self.set_no_write(i, BLACK)
        self.np.write()

    # Mode-selection "0..4 white" (0..3 coords + OK)
    def mode_select_banner(self):
        for i in range(0, 5):  # indices 0..4
            self.set_no_write(i, WHITE)
        # hint off
        self.set_no_write(CP_HINT_PIX, BLACK)
        self.np.write()


class Chessboard:
    """8x8 chessboard LED matrix with DIY Machines wiring (bottom-right origin, zigzag)."""
    def __init__(self, pin, w, h, origin_bottom_right=True, zigzag=True):
        self.w, self.h = w, h
        self.origin_bottom_right = origin_bottom_right
        self.zigzag = zigzag
        self.np = neopixel.NeoPixel(Pin(pin, Pin.OUT), w*h)

        # Precompute checkerboard pattern
        self._marking_cache = [BLACK]*(w*h)
        LIGHT = (100,100,100); DARK=(3,3,3)
        for y in range(self.h):
            for x in range(self.w):
                col = DARK if ((x+y) % 2 == 0) else LIGHT
                self._raw_set(x, y, col, into_cache=True)
        self.clear(BLACK)

    # -------- mapping --------
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
        idx = self._xy_to_index(x, y)
        self.np[idx] = color
        if into_cache:
            self._marking_cache[idx] = color

    # -------- public drawing --------
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

        # Knight (approximate L path for visual)
        if (adx, ady) in ((1,2), (2,1)):
            sx = self._sgn(dx); sy = self._sgn(dy)
            path.append((fx, fy))
            if ady == 2:
                path += [(fx, fy + 1*sy), (fx, fy + 2*sy), (fx + 1*sx, fy + 2*sy)]
            else:
                path += [(fx + 1*sx, fy), (fx + 2*sx, fy), (fx + 2*sx, fy + 1*sy)]
            if path[-1] != (tx, ty):
                path.append((tx, ty))
            # Dedup any repeat
            dedup = []
            for p in path:
                if not dedup or dedup[-1] != p:
                    dedup.append(p)
            return dedup

        return [(fx, fy), (tx, ty)]

    def draw_trail(self, move_uci, color, end_color=None):
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
        for i in range(self.w*self.h):
            self.np[i] = self._marking_cache[i]
        self.np.write()

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
        if count >= total: return count
        idx = count
        y = idx // self.w
        x = (self.w - 1) - (idx % self.w)
        self.set_square(x, y, BLUE)
        self.write()
        return count + 1

    def illegal_flash(self, hold_ms=700):
        self.clear(RED)
        time.sleep_ms(hold_ms)
        self.show_markings()

    # Prompts
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
        for i in range(self.w * self.h):
            self.np[i] = GREEN
        self.np.write()
        for y in range(self.h):
            self.set_square(2, y, WHITE); self.set_square(5, y, WHITE)
        for x in range(self.w):
            self.set_square(x, 2, WHITE); self.set_square(x, 5, WHITE)
        self.write()

    def show_promotion_scene_p(self):
        for i in range(self.w * self.h):
            self.np[i] = MAGENTA
        self.np.write()
        self.draw_vline(2, 1, 6, WHITE)
        self.draw_hline(2, 6, 4, WHITE)
        self.draw_hline(2, 4, 4, WHITE)
        self.draw_vline(5, 5, 2, WHITE)
        self.write()


# ============================================================
# =============== Chessboard UI (centralized) ================
# ============================================================

class ChessboardUI:
    def __init__(self, board: Chessboard):
        self.board = board
        self.overlay_active = False
        self.overlay_type = None  # 'hint' | 'engine'
        self.overlay_move = None

    def off(self): self.board.clear(BLACK)
    def markings(self): self.board.show_markings()
    def opening(self): self.board.opening_markings()
    def loading_step(self, count): return self.board.loading_status(count)

    def illegal(self): self.board.illegal_flash(hold_ms=700)
    def prompt_time(self): self.board.show_time_prompt()
    def prompt_strength(self): self.board.show_strength_prompt()
    def game_over_scene(self): self.board.show_checkmate_scene_hash()
    def promotion_scene(self): self.board.show_promotion_scene_p()

    def preview_from(self, sq):
        self.markings()
        xy = self.board.algebraic_to_xy(sq)
        if xy:
            self.board.set_square(xy[0], xy[1], GREEN); self.board.write()

    def preview_trail(self, uci, cap=False):
        self.markings()
        self.board.draw_trail(uci, GREEN, end_color=(MAGENTA if cap else None))

    def redraw_final_trail(self, uci, cap=False):
        self.off()
        self.board.draw_trail(uci, GREEN, end_color=(MAGENTA if cap else None))

    def overlay_show(self, role, move_uci, cap=False, color_override=None, end_color=None):
        self.overlay_active = True
        self.overlay_type = role
        self.overlay_move = move_uci
        self.off()
        col = color_override if color_override is not None else (ENGINE_COLOR if role == 'engine' else YELLOW)
        endc = end_color if end_color is not None else (MAGENTA if cap else None)
        self.board.draw_trail(move_uci, col, end_color=endc)

    def overlay_clear(self):
        self.overlay_active = False
        self.overlay_type = None
        self.overlay_move = None
        self.markings()

# ============================================================
# =============== BUTTONS & INPUT ============================
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

# Hardware instances
cp = ControlPanel(CONTROL_PANEL_LED_PIN, CONTROL_PANEL_LED_COUNT)
board = Chessboard(CHESSBOARD_LED_PIN, BOARD_W, BOARD_H,
                   origin_bottom_right=MATRIX_ORIGIN_BOTTOM_RIGHT,
                   zigzag=MATRIX_ZIGZAG)
ui_board = ChessboardUI(board)
buttons = ButtonManager(BUTTON_PINS)

BTN_OK   = buttons.btn(OK_BUTTON_INDEX)
BTN_HINT = buttons.btn(HINT_BUTTON_INDEX)

# ============================================================
# =============== HINT IRQ (EDGE) ============================
# ============================================================

hint_irq_flag = False
suppress_hints_until_ms = 0

def hint_irq(pin):
    global hint_irq_flag
    hint_irq_flag = True

def disable_hint_irq():
    BTN_HINT.irq(handler=None)

def enable_hint_irq():
    BTN_HINT.irq(trigger=Pin.IRQ_FALLING, handler=hint_irq)

BTN_HINT.irq(trigger=Pin.IRQ_FALLING, handler=hint_irq)

# ============================================================
# =============== CP LED HELPERS (DIY EXACT) =================
# ============================================================

def cp_small_input_ready():
    """DIY: show only small coord block (0..3 white). Leave hint as-is; don't touch bars."""
    cp.small_panel_clear()
    cp.small_coords(True)

def cp_only_ok_white():
    """DIY: show only OK (white). Leave dim bars intact."""
    cp.small_panel_clear()
    cp.ok_white(True)

def cp_mode_select_small():
    """DIY: during choose mode show 0..4 white (coords + OK), hint OFF; bars OFF until setup complete."""
    cp.mode_select_banner()

def cp_bars_dim_on():
    """DIY: after setup, keep 6..21 dim white at all times."""
    cp.bars_dim(True)

def cp_bars_dim_off():
    cp.bars_dim(False)

# ============================================================
# =============== HELPERS & RESET ============================
# ============================================================

def map_range(x, in_min, in_max, out_min, out_max):
    return int((x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)

def hard_reset_board():
    """Return to base markings, clear overlays, reset inputs."""
    global in_input, in_setup, persistent_trail_active, persistent_trail_type, persistent_trail_move, hint_available
    in_input=False; in_setup=False
    persistent_trail_active=False; persistent_trail_type=None; persistent_trail_move=None
    hint_available = False
    disable_hint_irq(); buttons.reset()
    # Clear everything on CP (including bars)
    cp.fill_range(0, CONTROL_PANEL_LED_COUNT, BLACK)
    ui_board.off(); ui_board.markings()

def wait_ok_fresh(blink_ok=True):
    if blink_ok:
        cp_only_ok_white()
    while BTN_OK.value() == 0:
        time.sleep_ms(10)
    time.sleep_ms(180)
    buttons.reset()
    while True:
        b = buttons.detect_press()
        if b == (OK_BUTTON_INDEX + 1):
            # Clear only small panel after OK
            cp.small_panel_clear()
            return
        time.sleep_ms(15)

def probe_capture_with_pi(uci, timeout_ms=150):
    global preview_cap_flag
    preview_cap_flag = False
    send_to_pi("capq_", uci)
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        msg = read_from_pi()
        if not msg:
            time.sleep_ms(5)
            continue
        if msg.startswith("heyArduinocapr_"):
            val = msg.split("_", 1)[1].strip()
            preview_cap_flag = (val.startswith("1"))
            return preview_cap_flag
    return False

# ============================================================
# =============== PERSISTENT TRAILS (HINT/ENGINE) ============
# ============================================================

def clear_persistent_trail():
    global persistent_trail_active, persistent_trail_type, persistent_trail_move
    persistent_trail_active = False
    persistent_trail_type = None
    persistent_trail_move = None
    ui_board.overlay_clear()

def show_persistent_trail(move_uci, color, trail_type, end_color=None):
    global persistent_trail_active, persistent_trail_type, persistent_trail_move
    persistent_trail_active = True
    persistent_trail_type   = trail_type
    persistent_trail_move   = move_uci
    cap = (end_color == MAGENTA) if end_color is not None else False
    role = 'engine' if trail_type == 'engine' else 'hint'
    ui_board.overlay_show(role, move_uci, cap=cap, color_override=color, end_color=end_color)

def cancel_user_input_and_restart():
    buttons.reset()

# ============================================================
# =============== HINT / NEW GAME PROCESSOR ==================
# ============================================================

def process_hint_irq():
    """
    DIY behavior:
      - If OK is held while hint pressed: start New Game
      - Else: request hint; while showing overlay set hint LED BLUE, then restore WHITE
    """
    global hint_irq_flag, suppress_hints_until_ms, game_state
    if not hint_irq_flag:
        return None
    hint_irq_flag = False

    now = time.ticks_ms()
    if time.ticks_diff(suppress_hints_until_ms, now) > 0:
        return None

    # New Game if OK held
    if BTN_OK.value() == 0:
        game_state = GAME_SETUP
        send_to_pi("n")
        # DIY: turn off hint, show 0..4 white during loading
        cp.hint_off()
        cp_mode_select_small()
        v = 0
        ui_board.off()
        while v < (board.w * board.h):
            v = ui_board.loading_step(v)
            time.sleep_ms(25)
        time.sleep_ms(350)
        ui_board.markings()
        suppress_hints_until_ms = time.ticks_add(now, 800)
        return "new"

    if game_state != GAME_RUNNING:
        return None

    # Request a hint from Pi
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
    if game_state != GAME_RUNNING:
        return None

    # If overlay is active, clear on first press
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
        cp_small_input_ready()
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

    frm = col + row
    ui_board.preview_from(frm)
    return frm

def enter_to_square(move_from):
    if game_state != GAME_RUNNING:
        return None

    seed_btn = None
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
        cp_small_input_ready()
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

    to = col + row
    uci = move_from + to
    cap_prev = probe_capture_with_pi(uci)
    ui_board.preview_trail(uci, cap=cap_prev)
    return to

def _color_for_user_confirm():
    return GREEN

def confirm_move(move):
    if game_state != GAME_RUNNING:
        return None

    # DIY: show OK white, hint OFF, preserve dim bars
    cp.small_panel_clear()
    cp.ok_white(True)
    cp.hint_off()
    buttons.reset()
    _send_confirm_preview(move)

    while True:
        if game_state != GAME_RUNNING:
            cp.small_panel_clear()
            return None

        irq = process_hint_irq()
        if irq == "new":
            cp.small_panel_clear()
            return None

        msg = read_from_pi()
        if msg:
            outcome = _handle_pi_overlay_or_gameover(msg)
            if outcome == "gameover":
                cp.small_panel_clear()
                return None
            if outcome in ("hint", "engine"):
                cancel_user_input_and_restart()
                return None

        b = buttons.detect_press()
        if not b:
            time.sleep_ms(5); continue

        if b == (OK_BUTTON_INDEX+1):
            cp.small_panel_clear()
            return "ok"
        else:
            # Cancel confirm; allow FROM seed if coord button
            cp.small_panel_clear()
            ui_board.markings()
            return ("redo", b)

def collect_and_send_move():
    global in_input, preview_cap_flag
    in_input = True
    try:
        seed = None
        while True:
            # DIY: show only small 0..3 white; hint LED state left as-is
            cp_small_input_ready()
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

            res = confirm_move(move)
            if res is None:
                if persistent_trail_active:
                    seed = None
                    continue
                return

            if res == 'ok':
                # DIY: clear only the small panel (0..5). Keep dim bars on.
                cp.small_panel_clear()

                ui_board.redraw_final_trail(move, cap=preview_cap_flag)
                time.sleep_ms(200)
                send_to_pi(move)
                preview_cap_flag = False
                ui_board.markings()
                return

            if isinstance(res, tuple) and res[0] == 'redo':
                cancel_btn = res[1]
                seed = cancel_btn if (1 <= cancel_btn <= 8) else None
                cp_small_input_ready()
                continue
    finally:
        in_input = False

# ============================================================
# =============== GAMEOVER & OVERLAYS ========================
# ============================================================

def game_over_wait_ok_and_ack(result_str):
    disable_hint_irq()
    try:
        buttons.reset()
        # DIY didn't have an explicit OK-only ack, but we keep a clean flow:
        cp_only_ok_white()
        ui_board.game_over_scene()

        while BTN_OK.value() == 0:
            time.sleep_ms(10)
        time.sleep_ms(200)
        buttons.reset()

        while True:
            b = buttons.detect_press()
            if b == (OK_BUTTON_INDEX + 1):
                cp.small_panel_clear()
                send_to_pi("n")
                break
            time.sleep_ms(20)

        ui_board.markings()
    finally:
        enable_hint_irq()

# ============================================================
# =============== SETUP / MODE SELECTION =====================
# ============================================================

def wait_for_mode_request():
    # DIY: show opening + loading until "ChooseMode"
    ui_board.opening()
    lit = 0
    while True:
        lit = ui_board.loading_step(lit)
        time.sleep_ms(2000)
        msg = read_from_pi()
        if not msg:
            continue
        if msg.startswith("heyArduinoChooseMode"):
            while lit < (board.w * board.h):
                lit = ui_board.loading_step(lit)
                time.sleep_ms(15)
            ui_board.markings()
            # DIY: 0..4 WHITE; hint OFF; bars OFF (until setup complete)
            cp_mode_select_small()
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
    return select_singlepress(default_value, 1000, 8000)

def select_color_choice():
    buttons.reset()
    while True:
        b = buttons.detect_press()
        if b == 1: send_to_pi("s1"); return   # White
        if b == 2: send_to_pi("s2"); return   # Black
        if b == 3: send_to_pi("s3"); return   # Random
        time.sleep_ms(5)

def wait_for_setup():
    """
    DIY parity:
      - During EngineStrength/TimeControl prompts: board icons, CP small panel can remain as-is
      - After SetupComplete: turn on dim bars (6..21 DIM WHITE)
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
                ui_board.prompt_strength()
                v = select_strength_singlepress(default_strength)
                send_to_pi(str(v))
                time.sleep_ms(120)
                ui_board.markings()
                return

            if msg.startswith("heyArduinoTimeControl"):
                ui_board.prompt_time()
                v = select_time_singlepress(default_move_time)
                send_to_pi(str(v))
                time.sleep_ms(120)
                ui_board.markings()
                return

            if msg.startswith("heyArduinoPlayerColor"):
                # DIY OnlineHuman flow (placeholder)
                cp_mode_select_small()
                select_color_choice()
                ui_board.markings()
                return

            if msg.startswith("heyArduinoSetupComplete"):
                game_state = GAME_RUNNING
                in_setup = False
                # DIY: turn on dim bars 6..21
                cp_bars_dim_on()
                ui_board.markings()
                return
    finally:
        enable_hint_irq()

# ============================================================
# =============== PROMOTION CHOICE ===========================
# ============================================================

def handle_promotion_choice():
    ui_board.promotion_scene()
    # DIY CP doesn't change much; we leave small panel as-is
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
        ui_board.markings()

# ============================================================
# =============== PI MESSAGE HANDLER =========================
# ============================================================

def _handle_pi_overlay_or_gameover(msg):
    if not msg:
        return None

    if msg.startswith("heyArduinoGameOver"):
        res = msg.split(":", 1)[1].strip() if ":" in msg else ""
        game_over_wait_ok_and_ack(res)
        return "gameover"

    if msg.startswith("heyArduinohint_"):
        # DIY: while showing hint, set CP hint BLUE; turn off 0..3
        cp.small_panel_clear()
        cp.hint_blue()
        raw = msg[len("heyArduinohint_"):].strip()
        cap = raw.endswith("_cap")
        best = raw[:-4] if cap else raw
        show_persistent_trail(best, YELLOW, 'hint', end_color=(MAGENTA if cap else None))
        # After showing, restore hint WHITE and 0..3 WHITE
        cp.hint_white()
        cp_small_input_ready()
        return "hint"

    if msg.startswith("heyArduinom"):
        raw = msg[11:].strip()
        cap = raw.endswith("_cap")
        mv  = raw[:-4] if cap else raw
        show_persistent_trail(mv, ENGINE_COLOR, 'engine', end_color=(MAGENTA if cap else None))
        return "engine"

    return None

# ============================================================
# =============== MAIN LOOP ==================================
# ============================================================

def main_loop():
    global current_turn, engine_ack_pending, pending_gameover_result, buffered_turn_msg, hint_available
    while True:
        irq = process_hint_irq()
        if irq == "new":
            disable_hint_irq()
            # DIY: during new game load, CP small shows 0..4 white already in process_hint_irq
            ui_board.opening()
            engine_ack_pending = False
            pending_gameover_result = None
            buffered_turn_msg = None
            hint_available = False
            continue

        msg = read_from_pi()
        if not msg:
            time.sleep_ms(10); continue

        # GameOver
        if msg.startswith("heyArduinoGameOver"):
            res = msg.split(":", 1)[1].strip() if ":" in msg else ""
            game_over_wait_ok_and_ack(res)
            hint_available = False
            continue

        # Hard reset
        if msg.startswith("heyArduinoResetBoard"):
            hard_reset_board()
            continue

        # Mode selection
        if msg.startswith("heyArduinoChooseMode"):
            disable_hint_irq(); buttons.reset()
            ui_board.markings()
            cp_mode_select_small()
            global game_state
            game_state = GAME_SETUP
            select_game_mode()
            while game_state == GAME_SETUP:
                wait_for_setup()
            continue

        # Game start banner (compat)
        if msg.startswith("heyArduinoGameStart"):
            ui_board.markings()
            continue

        # Engine move (optional _cap)
        if msg.startswith("heyArduinom"):
            raw = msg[11:].strip()
            cap = raw.endswith("_cap")
            mv  = raw[:-4] if cap else raw

            # Show engine overlay
            show_persistent_trail(mv, ENGINE_COLOR, 'engine', end_color=(MAGENTA if cap else None))

            # DIY flow: they don't force an OK-only state; they proceed.
            # We'll simply prep for human turn after this message arrives via turn_W/B.
            continue

        # Promotion
        if msg.startswith("heyArduinopromotion_choice_needed"):
            handle_promotion_choice()
            continue

        # Hint trail (handled in overlay helper to set BLUE then WHITE)
        if msg.startswith("heyArduinohint_"):
            _ = _handle_pi_overlay_or_gameover(msg)
            continue

        # Illegal / error from Pi
        if msg.startswith("heyArduinoerror"):
            ui_board.illegal()
            # After error: DIY resumes input; show small 0..3 white and hint white if available
            if game_mode == MODE_PC:
                cp.hint_white(); hint_available = True
            cp_small_input_ready()
            collect_and_send_move()
            continue

        # Turn notification
        if msg.startswith("heyArduinoturn_"):
            turn_str = msg.split("_", 1)[1].strip().lower()
            if 'w' in turn_str:
                current_turn = 'W'
            elif 'b' in turn_str:
                current_turn = 'B'

            # Human's turn: DIY sets hint available (white) if Stockfish
            if game_mode == MODE_PC:
                cp.hint_white(); hint_available = True
            else:
                cp.hint_off(); hint_available = False

            # Start input with small coords lit
            cp_small_input_ready()
            collect_and_send_move()
            continue

# ============================================================
# =============== ENTRY POINT ================================
# ============================================================

def run():
    global game_state, hint_available
    print("Pico Chess Controller (DIY Machines CP behavior)")
    # Clear CP; bars off initially
    cp.fill_range(0, CONTROL_PANEL_LED_COUNT, BLACK)
    ui_board.off()
    buttons.reset()

    disable_hint_irq()
    wait_for_mode_request()
    ui_board.markings()

    # DIY: during mode select show 0..4 white
    cp_mode_select_small()
    select_game_mode()

    while game_state == GAME_SETUP:
        wait_for_setup()

    # After setup: turn on dim bars permanently
    cp_bars_dim_on()
    enable_hint_irq()

    # Start main loop
    hint_available = False
    while True:
        main_loop()

# Start firmware
run()

# If you need to flip A–H or 1–8 direction on your physical bar, reverse the arrays:
# CP_FILES_LEDS = list(reversed([6,7,8,9,10,11,12,13]))
# CP_RANKS_LEDS = list(reversed([14,15,16,17,18,19,20,21]))