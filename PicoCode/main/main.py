# ============================================================
#  PICO FIRMWARE (2026) - Control Panel LEDs per requested UX
#  + Centralized Chessboard UI (ChessboardUI)
#  + DIY illegal animation + coordinate bars lit (6..21 DIM)
#  + "New Game" guard to ignore stale messages and never send a move
#  + Capture blink on destination square (keeps your full trail/logic)
#  + Hold H/8 to shutdown Pico (signals Pi then powers LEDs off)
#
#  PATCHED:
#   - Fix OK not working in puzzle setup:
#       * remove duplicate puzzle_setup_active blocks in main_loop
#       * use ONE handler: handle_puzzle_setup_cmd(...)
#       * sync ok_last_val when puzzle_setup begins
#       * forward OK press reliably during puzzle setup
# ============================================================

from machine import Pin, UART
import time
import neopixel

# ============================================================
# =============== CONFIG & CONSTANTS =========================
# ============================================================

# Buttons (active-low) wiring stays unchanged
BUTTON_PINS = [2, 3, 4, 5, 10, 8, 7, 6, 9, 11]  # 1–8=coords, 9=A1(OK), 11=Hint IRQ
DEBOUNCE_MS = 300


# OK long-hold threshold for backspace during move entry
LONG_PRESS_MS = 500
# Special role indexes (0-based into BUTTON_PINS)
OK_BUTTON_INDEX = 8  # Button 9
HINT_BUTTON_INDEX = 9  # Button 10

# --- NEW: Shutdown via holding H/8 (button #8) ---
SHUTDOWN_BTN_INDEX = 7  # 0-based index into BUTTON_PINS -> button "8" (H/8)
SHUTDOWN_HOLD_MS = 2000

HINT_HOLD_DRAW_MS = 2000

# NeoPixels
CONTROL_PANEL_LED_PIN = 16
CONTROL_PANEL_LED_COUNT = 22
CHESSBOARD_LED_PIN = 22
BOARD_W, BOARD_H = 8, 8

# Matrix orientation (DIY Machines: bottom-right origin + rows + zigzag)
MATRIX_ORIGIN_BOTTOM_RIGHT = True
MATRIX_ZIGZAG = True

# Colors (keep standard palette)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
DIMW = (10, 10, 10)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
CYAN = (0, 255, 255)
MAGENTA = (255, 0, 255)
YELLOW = (255, 255, 0)
ORANGE = (255, 130, 0)

ENGINE_COLOR = BLUE  # Deep blue for computer moves

# Control panel pixel roles
CP_COORD_START = 0
CP_OK_PIX = 4
CP_HINT_PIX = 5

# Choice-lane base: where we "draw" buttons 1..N (N in [3,4,8]) on CP LEDs
CP_CHOICE_BASE = 6  # Button k -> LED index CP_CHOICE_BASE + (k-1)

# ===== Extended coordinate LED mapping (A–H files + 1–8 ranks) =====
# Chain of 22 LEDs:
#   0..3  = small coord ready block
#   4     = OK (GREEN per your UX)
#   5     = HINT (YELLOW per your UX)
#   6..13 = Files A..H
#   14..21= Ranks 1..8
CP_FILES_LEDS = [6, 7, 8, 9, 10, 11, 12, 13]  # A..H
CP_RANKS_LEDS = [14, 15, 16, 17, 18, 19, 20, 21]  # 1..8

# ============================================================
# =============== STATE & MODES ===============================
# ============================================================

# Game states
GAME_IDLE = 0
GAME_SETUP = 1
GAME_RUNNING = 2
game_state = GAME_IDLE

# Modes / turn tracking
MODE_PC = "pc"  # vs computer
MODE_ONLINE = "online"  # vs remote (placeholder)
MODE_LOCAL = "local"  # local 2P
MODE_PUZZLE = "puzzle"  # daily puzzle
game_mode = MODE_PC
current_turn = "W"  # 'W' or 'B'

# Defaults (Pi remaps values; we keep these for initial prompts)
default_strength = 5  # Pi maps 1..8 => 1..20
default_move_time = 2000  # Pi maps 1..8 => 3000..12000

# Simple input guards
in_setup = False
in_input = False

# --- Engine move acknowledgement state ---
engine_ack_pending = False
pending_gameover_result = None
buffered_turn_msg = None

# Was a capture detected for the last previewed move?
preview_cap_flag = False

# --- Guard to ignore stale messages after "New Game" is requested ---
suspend_until_new_game = False

# --- Puzzle setup mode (Pi-driven LED guidance) ---
puzzle_setup_active = False

# ============================================================
# =============== PERSISTENT OVERLAYS ========================
# ============================================================

persistent_trail_active = False
persistent_trail_type = None  # 'hint' or 'engine'
persistent_trail_move = None  # e.g., 'e2e4'

# ============================================================
# =============== UART (Pico <-> Pi) =========================
# ============================================================

uart = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1), timeout=10)


def send_to_pi(kind, payload=""):
    uart.write(f"heypi{kind}{payload}\n".encode())


def read_from_pi():
    if uart.any():
        try:
            return uart.readline().decode().strip()  # type: ignore
        except:
            return None
    return None


def send_typing_preview(label, text):
    if game_state != GAME_RUNNING:
        return
    uart.write(f"heypityping_{label}_{text}\n".encode())


def _handle_pi_overlay_or_gameover(msg):
    if not msg:
        return None

    if msg.startswith("heyArduinoGameOver"):
        res = msg.split(":", 1)[1].strip() if ":" in msg else ""
        game_over_wait_ok_and_ack(res)
        return "gameover"

    if msg.startswith("heyArduinohint_"):
        raw = msg[len("heyArduinohint_") :].strip()
        cap = raw.endswith("_cap")
        best = raw[:-4] if cap else raw
        show_persistent_trail(
            best, YELLOW, "hint", end_color=(MAGENTA if cap else None)
        )
        return "hint"

    if msg.startswith("heyArduinom"):
        raw = msg[11:].strip()
        cap = raw.endswith("_cap")
        mv = raw[:-4] if cap else raw
        show_persistent_trail(
            mv, ENGINE_COLOR, "engine", end_color=(MAGENTA if cap else None)
        )
        return "engine"

    return None


# ============================================================
# =============== LED PANELS (CONTROL + BOARD) ===============
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

    def _set_no_write(self, i, c):
        if 0 <= i < self.count:
            self.np[i] = c

    def _write(self):
        self.np.write()

    def coord(self, COLOR, on=True):
        self.fill(COLOR if on else BLACK, CP_COORD_START, 4)

    def coordTop(self, COLOR, on=True):
        self.fill(COLOR if on else BLACK, CP_COORD_START, 2)

    def coordDown(self, COLOR, on=True):
        self.fill(COLOR if on else BLACK, CP_COORD_START + 2, 2)

    def choice(self, COLOR, on=True):
        self.fill(COLOR if on else BLACK, CP_COORD_START, 4)

    def ok(self, on=True):
        self.set(CP_OK_PIX, GREEN if on else BLACK)

    def hint(self, on=True, color=YELLOW):
        self.set(CP_HINT_PIX, (color if on else BLACK))

    def bars_set_dim(self, dim_color, on=True):
        col = dim_color if on else BLACK
        for idx in CP_FILES_LEDS + CP_RANKS_LEDS:
            if 0 <= idx < self.count:
                self._set_no_write(idx, col)
        self._write()

    def clear_small_panel(self):
        for i in range(0, 6):
            if i < self.count:
                self._set_no_write(i, BLACK)
        self._write()


class Chessboard:
    def __init__(self, pin, w, h, origin_bottom_right=True, zigzag=True):
        self.w, self.h = w, h
        self.origin_bottom_right = origin_bottom_right
        self.zigzag = zigzag
        self.np = neopixel.NeoPixel(Pin(pin, Pin.OUT), w * h)

        self._marking_cache = [BLACK] * (w * h)
        LIGHT = (100, 100, 100)
        DARK = (3, 3, 3)
        for y in range(self.h):
            for x in range(self.w):
                col = DARK if ((x + y) % 2 == 0) else LIGHT
                self._raw_set(x, y, col, into_cache=True)
        self.clear(BLACK)

    def _xy_to_index(self, x, y):
        row = y
        if self.origin_bottom_right:
            if self.zigzag:
                col_index = (self.w - 1 - x) if (row % 2 == 0) else x
            else:
                col_index = self.w - 1 - x
            return row * self.w + col_index
        else:
            row_top = (self.h - 1) - y
        if self.zigzag:
            col_index = x if (row_top % 2 == 0) else (self.w - 1 - x)
        else:
            col_index = x
        return row_top * self.w + col_index

    def _raw_set(self, x, y, color, into_cache=False):
        idx = self._xy_to_index(x, y)
        self.np[idx] = color
        if into_cache:
            self._marking_cache[idx] = color

    def clear(self, color=BLACK):
        for i in range(self.w * self.h):
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
        if not ("a" <= f <= "h"):
            return None
        if not ("1" <= r <= "8"):
            return None
        return (ord(f) - 97, int(r) - 1)

    @staticmethod
    def _sgn(v):
        return 0 if v == 0 else (1 if v > 0 else -1)

    def _path_squares(self, frm, to):
        f = self.algebraic_to_xy(frm)
        t = self.algebraic_to_xy(to)
        if not f or not t:
            return []

        fx, fy = f
        tx, ty = t
        dx = tx - fx
        dy = ty - fy
        adx, ady = abs(dx), abs(dy)
        path = []

        if fx == tx and fy != ty:
            sy = self._sgn(dy)
            for y in range(fy, ty + sy, sy):
                path.append((fx, y))
            return path

        if fy == ty and fx != tx:
            sx = self._sgn(dx)
            for x in range(fx, tx + sx, sx):
                path.append((x, fy))
            return path

        if adx == ady and adx != 0:
            sx = self._sgn(dx)
            sy = self._sgn(dy)
            x, y = fx, fy
            for _ in range(adx + 1):
                path.append((x, y))
                x += sx
                y += sy
            return path

        # Knight: longer leg first (L path)
        if (adx, ady) in ((1, 2), (2, 1)):
            sx = self._sgn(dx)
            sy = self._sgn(dy)
            path.append((fx, fy))
            if ady == 2:
                path.append((fx, fy + 1 * sy))
                path.append((fx, fy + 2 * sy))
                path.append((fx + 1 * sx, fy + 2 * sy))
            else:
                path.append((fx + 1 * sx, fy))
                path.append((fx + 2 * sx, fy))
                path.append((fx + 2 * sx, fy + 1 * sy))
            if path[-1] != (tx, ty):
                path.append((tx, ty))
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
            if end_color and i == len(path) - 1:
                self.set_square(x, y, end_color)
            else:
                self.set_square(x, y, color)
        self.write()

    # ---------- DIY Illegal + Capture Blink Helpers ----------

    def show_markings(self):
        for i in range(self.w * self.h):
            self.np[i] = self._marking_cache[i]
        self.np.write()

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

    def illegal_flash(self, hold_ms=700):
        # DIY Machines-style illegal animation
        for i in range(self.w * self.h):
            self.np[i] = BLUE
        self.np.write()
        time.sleep_ms(hold_ms)

        for _ in range(3):
            for i in range(8):
                self.set_square(i, i, RED)
                self.set_square(i, 7 - i, RED)
            self.write()
            time.sleep_ms(hold_ms)

            for i in range(8):
                self.set_square(i, i, BLUE)
                self.set_square(i, 7 - i, BLUE)
            self.write()
            time.sleep_ms(hold_ms)

        self.show_markings()

    # --- CAPTURE BLINK HELPERS (blink destination only; keep your trail/colors) ---

    def _blink_square_xy(
        self, x, y, color_on, times=3, on_ms=200, off_ms=200, final_color=None
    ):
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        for _ in range(times):
            self.set_square(x, y, color_on)
            self.write()
            time.sleep_ms(on_ms)
            self.set_square(x, y, BLACK)
            self.write()
            time.sleep_ms(off_ms)
        self.set_square(x, y, (final_color if final_color is not None else color_on))
        self.write()

    def blink_dest_algebraic(
        self, to_sq, color_on, times=3, on_ms=200, off_ms=200, final_color=None
    ):
        xy = self.algebraic_to_xy(to_sq)
        if not xy:
            return
        x, y = xy
        self._blink_square_xy(
            x,
            y,
            color_on,
            times=times,
            on_ms=on_ms,
            off_ms=off_ms,
            final_color=final_color,
        )

    # ---------- Prompts / Scenes ----------

    def draw_hline(self, x, y, length, color):
        for dx in range(length):
            self.set_square(x + dx, y, color)

    def draw_vline(self, x, y, length, color):
        for dy in range(length):
            self.set_square(x, y + dy, color)

    def show_time_prompt(self):
        self.clear(BLACK)
        T = [(2, 6), (3, 6), (4, 6), (5, 6), (4, 5), (4, 4), (4, 3), (4, 2)]
        for x, y in T:
            self.set_square(x, y, MAGENTA)
        self.write()

    def show_strength_prompt(self):
        self.clear(BLACK)
        L = [(2, 6), (2, 5), (2, 4), (2, 3), (2, 2), (3, 2), (4, 2), (5, 2)]
        for x, y in L:
            self.set_square(x, y, MAGENTA)
        self.write()

    def show_checkmate_scene_hash(self):
        for i in range(self.w * self.h):
            self.np[i] = GREEN
        self.np.write()
        for y in range(self.h):
            self.set_square(2, y, WHITE)
            self.set_square(5, y, WHITE)
        for x in range(self.w):
            self.set_square(x, 2, WHITE)
            self.set_square(x, 5, WHITE)
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
        self.overlay_type = None
        self.overlay_move = None

    def off(self):
        self.board.clear(BLACK)

    def markings(self):
        self.board.show_markings()

    def opening(self):
        self.board.opening_markings()

    def loading_step(self, count):
        return self.board.loading_status(count)

    def illegal(self):
        self.board.illegal_flash(hold_ms=700)

    def prompt_time(self):
        self.board.show_time_prompt()

    def prompt_strength(self):
        self.board.show_strength_prompt()

    def game_over_scene(self):
        self.board.show_checkmate_scene_hash()

    def promotion_scene(self):
        self.board.show_promotion_scene_p()

    def preview_from(self, sq):
        self.markings()
        xy = self.board.algebraic_to_xy(sq)
        if xy:
            self.board.set_square(xy[0], xy[1], GREEN)
            self.board.write()

    def preview_trail(self, uci, cap=False):
        self.markings()
        endc = MAGENTA if cap else None
        self.board.draw_trail(uci, GREEN, end_color=endc)
        if cap:
            to_sq = uci[2:4]
            self.board.blink_dest_algebraic(
                to_sq,
                color_on=(endc if endc else RED),
                times=3,
                on_ms=200,
                off_ms=200,
                final_color=endc,
            )

    def redraw_final_trail(self, uci, cap=False):
        self.off()
        endc = MAGENTA if cap else None
        self.board.draw_trail(uci, GREEN, end_color=endc)
        if cap:
            to_sq = uci[2:4]
            self.board.blink_dest_algebraic(
                to_sq,
                color_on=(endc if endc else RED),
                times=3,
                on_ms=200,
                off_ms=200,
                final_color=endc,
            )

    def overlay_show(
        self, role, move_uci, cap=False, color_override=None, end_color=None
    ):
        self.overlay_active = True
        self.overlay_type = role
        self.overlay_move = move_uci
        self.off()
        col = (
            color_override
            if color_override is not None
            else (ENGINE_COLOR if role == "engine" else YELLOW)
        )
        endc = end_color if end_color is not None else (MAGENTA if cap else None)
        self.board.draw_trail(move_uci, col, end_color=endc)
        if cap:
            to_sq = move_uci[2:4]
            self.board.blink_dest_algebraic(
                to_sq,
                color_on=(endc if endc else RED),
                times=3,
                on_ms=200,
                off_ms=200,
                final_color=endc,
            )

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
        self._last = [1] * len(self.pins)

    def btn(self, index):
        return self.pins[index]

    def reset(self):
        for i, p in enumerate(self.pins):
            self._last[i] = p.value()

    def detect_press(self):
        for i, p in enumerate(self.pins):
            cur = p.value()
            prev = self._last[i]
            self._last[i] = cur
            if prev == 1 and cur == 0:
                time.sleep_ms(DEBOUNCE_MS)
                return i + 1
        return None

    @staticmethod
    def is_non_coord_button(b):
        return b in (9, 10)


# Instantiate hardware interfaces
cp = ControlPanel(CONTROL_PANEL_LED_PIN, CONTROL_PANEL_LED_COUNT)
board = Chessboard(
    CHESSBOARD_LED_PIN,
    BOARD_W,
    BOARD_H,
    origin_bottom_right=MATRIX_ORIGIN_BOTTOM_RIGHT,
    zigzag=MATRIX_ZIGZAG,
)
ui_board = ChessboardUI(board)
buttons = ButtonManager(BUTTON_PINS)

BTN_OK = buttons.btn(OK_BUTTON_INDEX)
BTN_HINT = buttons.btn(HINT_BUTTON_INDEX)
BTN_SHUT = buttons.btn(SHUTDOWN_BTN_INDEX)  # <- H/8 button pin object

ok_last_val = 1  # edge detector for OK in puzzle setup

# --- OK long-hold backspace (single-shot, fires while still held) ---
_ok_press_ms = None
_ok_fired = False


def reset_ok_hold_state():
    global _ok_press_ms, _ok_fired
    _ok_press_ms = None
    _ok_fired = False


def ok_long_hold_fired(hold_ms=LONG_PRESS_MS):
    """Return True once when OK has been held for hold_ms.
    Non-blocking: call frequently inside move-entry loops.
    Resets on release. Single-shot (no repeat while held).
    """
    global _ok_press_ms, _ok_fired
    if BTN_OK.value() == 0:  # pressed (active-low)
        if _ok_press_ms is None:
            _ok_press_ms = time.ticks_ms()
            _ok_fired = False
        if (not _ok_fired) and time.ticks_diff(
            time.ticks_ms(), _ok_press_ms
        ) >= hold_ms:
            _ok_fired = True
            return True
        return False
    _ok_press_ms = None
    _ok_fired = False
    return False


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
# =============== CP LED HELPERS (ONLY/CHOICES) ==============
# ============================================================


def cp_all_off():
    cp.fill(BLACK)


def cp_bars_dim_on():
    cp.bars_set_dim(DIMW, on=True)


def cp_only_ok(on=True):
    cp.clear_small_panel()
    cp.ok(on)


def cp_only_hint_and_coords_for_input():
    cp.clear_small_panel()
    cp.coord(WHITE)
    cp.hint(True, YELLOW)
    # OK is RED during entry (hold OK to delete last character)
    cp.set(CP_OK_PIX, RED)
    cp_bars_dim_on()


def cp_show_coords_top(COLOR):
    cp.clear_small_panel()
    cp.coordTop(COLOR, True)


# ============================================================
# =============== HELPERS & RESET ============================
# ============================================================


def map_range(x, in_min, in_max, out_min, out_max):
    return int((x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)


def hard_reset_board():
    global in_input, in_setup, persistent_trail_active, persistent_trail_type, persistent_trail_move
    in_input = False
    in_setup = False
    persistent_trail_active = False
    persistent_trail_type = None
    persistent_trail_move = None
    disable_hint_irq()
    buttons.reset()
    cp_all_off()
    ui_board.off()
    ui_board.markings()


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
            preview_cap_flag = val.startswith("1")
            return preview_cap_flag
    return False


# --- NEW: Shutdown (hold H/8) helpers ---

# Non-blocking hold-tracking state for the shutdown button.
_shutdown_press_ms = None
_shutdown_fired = False


def is_shutdown_held(hold_ms=SHUTDOWN_HOLD_MS):
    """Non-blocking hold detector for the H/8 shutdown button.

    The previous implementation blocked while the button was held, which
    starved the main loop and caused short H/8 presses to be missed during
    move entry (you'd have to press multiple times).

    This version records the press timestamp and returns True once when the
    hold threshold is reached. It resets on release.
    """
    global _shutdown_press_ms, _shutdown_fired

    if BTN_SHUT.value() == 0:  # pressed (active-low)
        if _shutdown_press_ms is None:
            _shutdown_press_ms = time.ticks_ms()
            _shutdown_fired = False

        if (not _shutdown_fired) and time.ticks_diff(
            time.ticks_ms(), _shutdown_press_ms
        ) >= hold_ms:
            _shutdown_fired = True
            return True
        return False

    # released
    _shutdown_press_ms = None
    _shutdown_fired = False
    return False


def shutdown_pico():
    send_to_pi("xshutdown")

    for _ in range(2):
        cp_only_ok(True)
        board.clear(CYAN)
        time.sleep_ms(180)
        cp_only_ok(False)
        board.clear(BLACK)
        time.sleep_ms(180)

    cp_all_off()
    board.clear(BLACK)
    disable_hint_irq()

    while True:
        time.sleep_ms(1000)


# ============================================================
# =============== PERSISTENT TRAILS (HINT/ENGINE) ============
# ============================================================


def clear_persistent_trail():
    global persistent_trail_active, persistent_trail_type, persistent_trail_move
    was_hint = persistent_trail_type == "hint"
    persistent_trail_active = False
    persistent_trail_type = None
    persistent_trail_move = None
    ui_board.overlay_clear()
    if was_hint and game_state == GAME_RUNNING and not engine_ack_pending:
        cp_only_hint_and_coords_for_input()


def show_persistent_trail(move_uci, color, trail_type, end_color=None):
    global persistent_trail_active, persistent_trail_type, persistent_trail_move
    persistent_trail_active = True
    persistent_trail_type = trail_type
    persistent_trail_move = move_uci
    cap = (end_color == MAGENTA) if end_color is not None else False
    role = "engine" if trail_type == "engine" else "hint"
    if trail_type == "hint":
        # Hint received => only OK lit (green) until user dismisses overlay
        cp_only_ok(True)
    ui_board.overlay_show(
        role, move_uci, cap=cap, color_override=color, end_color=end_color
    )


def cancel_user_input_and_restart():
    buttons.reset()


# ============================================================
# =============== HINT / NEW GAME PROCESSOR ==================
# ============================================================


def process_hint_irq():
    global hint_irq_flag, suppress_hints_until_ms, game_state, suspend_until_new_game
    global engine_ack_pending, pending_gameover_result, buffered_turn_msg

    if not hint_irq_flag:
        return None
    hint_irq_flag = False

    if is_shutdown_held():
        shutdown_pico()

    now = time.ticks_ms()
    if time.ticks_diff(suppress_hints_until_ms, now) > 0:
        return None

    if BTN_OK.value() == 0:
        game_state = GAME_SETUP
        send_to_pi("n")

        suspend_until_new_game = True
        engine_ack_pending = False
        pending_gameover_result = None
        buffered_turn_msg = None

        cp_show_coords_top(WHITE)
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

    # Detect hold-vs-tap on Hint:
    # - Tap => normal hint request ("btn_hint")
    # - Hold (>= HINT_HOLD_DRAW_MS) => draw offer token ("btn_draw") for online mode
    if BTN_HINT.value() == 0:
        t0 = time.ticks_ms()
        while BTN_HINT.value() == 0:
            if time.ticks_diff(time.ticks_ms(), t0) >= HINT_HOLD_DRAW_MS:
                send_to_pi("btn_draw")
                return "draw"
            time.sleep_ms(10)

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


def enter_from_square(seed_btn=None, preset_col=None):
    if game_state != GAME_RUNNING:
        return None

    reset_ok_hold_state()

    if is_shutdown_held():
        shutdown_pico()

    if persistent_trail_active:
        while True:
            if is_shutdown_held():
                shutdown_pico()
            msg = read_from_pi()
            if msg:
                outcome = _handle_pi_overlay_or_gameover(msg)
                if outcome == "gameover":
                    return None
            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5)
                continue
            clear_persistent_trail()
            if 1 <= b <= 8:
                seed_btn = b
            break
        cp_only_hint_and_coords_for_input()
        buttons.reset()

    col = None
    row = None

    # If caller provided a preset file letter (after backspace), keep original LCD behavior:
    # show the single-letter "from" preview and continue by asking for the rank.
    if preset_col is not None:
        col = preset_col
        _send_from_preview(col)

    while col is None:
        if game_state != GAME_RUNNING:
            return None

        if seed_btn is not None:
            b = seed_btn
            seed_btn = None
        else:
            if is_shutdown_held():
                shutdown_pico()

            irq = process_hint_irq()
            if irq == "new":
                return None

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
                time.sleep_ms(5)
                continue

        if ButtonManager.is_non_coord_button(b):
            continue
        col = chr(ord("a") + b - 1)
        _send_from_preview(col)

    while row is None:
        if game_state != GAME_RUNNING:
            return None

        if is_shutdown_held():
            shutdown_pico()

        # Backspace during FROM rank entry: hold OK deletes the file (last char) and returns to file selection
        if ok_long_hold_fired():
            _send_from_preview("")
            ui_board.markings()
            return ("back_from", None)

        irq = process_hint_irq()
        if irq == "new":
            return None

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
            time.sleep_ms(5)
            continue
        if ButtonManager.is_non_coord_button(b):
            continue
        row = str(b)
        _send_from_preview(col + row)

    frm = col + row
    ui_board.preview_from(frm)
    return frm


def enter_to_square(move_from, preset_col=None):
    if game_state != GAME_RUNNING:
        return None

    reset_ok_hold_state()

    if is_shutdown_held():
        shutdown_pico()

    if persistent_trail_active:
        while True:
            if is_shutdown_held():
                shutdown_pico()
            msg = read_from_pi()
            if msg:
                outcome = _handle_pi_overlay_or_gameover(msg)
                if outcome == "gameover":
                    return None
            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5)
                continue
            clear_persistent_trail()
            if 1 <= b <= 8:
                seed_btn = b
            break

        cp_only_hint_and_coords_for_input()
        buttons.reset()

    col = None
    row = None

    if preset_col is not None:
        # Only accept a real file letter preset (a..h). Ignore "" or invalid.
        if (
            isinstance(preset_col, str)
            and len(preset_col) == 1
            and ("a" <= preset_col <= "h")
        ):
            col = preset_col
            _send_to_preview(move_from, col)
    while col is None:
        if game_state != GAME_RUNNING:
            return None

        if is_shutdown_held():
            shutdown_pico()

        # Backspace before TO file chosen: delete last FROM char (rank) and go back to FROM rank entry
        if ok_long_hold_fired():
            # We are deleting FROM rank (e2 -> e). Show remaining FROM buffer on LCD.
            _send_from_preview(move_from[0])
            ui_board.markings()
            return ("back_to_from_rank", move_from[0])

        irq = process_hint_irq()
        if irq == "new":
            return None

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
            time.sleep_ms(5)
            continue
        if ButtonManager.is_non_coord_button(b):
            continue
        col = chr(ord("a") + b - 1)
        _send_to_preview(move_from, col)

    while row is None:
        if game_state != GAME_RUNNING:
            return None

        if is_shutdown_held():
            shutdown_pico()

        # Backspace during TO rank entry: delete the TO file and restart TO file selection
        if ok_long_hold_fired():
            _send_to_preview(move_from, "")
            ui_board.preview_from(move_from)
            return ("back_to_to_file", move_from)

        irq = process_hint_irq()
        if irq == "new":
            return None

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
            time.sleep_ms(5)
            continue
        if ButtonManager.is_non_coord_button(b):
            continue
        row = str(b)
        _send_to_preview(move_from, col + row)

    to = col + row
    uci = move_from + to
    cap_prev = probe_capture_with_pi(uci)
    ui_board.preview_trail(uci, cap=cap_prev)
    return to


def confirm_move(move):
    if game_state != GAME_RUNNING:
        return None

    cp_only_ok(True)
    buttons.reset()
    _send_confirm_preview(move)

    while True:
        if game_state != GAME_RUNNING:
            cp_only_ok(False)
            return None

        if is_shutdown_held():
            shutdown_pico()

        irq = process_hint_irq()
        if irq == "new":
            cp_only_ok(False)
            return None

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
            time.sleep_ms(5)
            continue

        if b == (OK_BUTTON_INDEX + 1):
            # OK at confirm stage:
            # - Short press (release < LONG_PRESS_MS): CONFIRM
            # - Long hold (>= LONG_PRESS_MS): DELETE last character immediately (single-shot) and return to TO-rank entry
            t0 = time.ticks_ms()
            fired = False

            while BTN_OK.value() == 0:
                if is_shutdown_held():
                    shutdown_pico()

                irq = process_hint_irq()
                if irq == "new":
                    cp_only_ok(False)
                    return None

                # Fire delete once at threshold (while still held)
                if (not fired) and time.ticks_diff(
                    time.ticks_ms(), t0
                ) >= LONG_PRESS_MS:
                    fired = True
                    # move is full UCI (len 4). Delete last char => len 3 (keep TO file)
                    partial = move[:-1]  # could be len 3 or len 2
                    frm = partial[:2]

                    if len(partial) == 3:
                        # e2e : show "e2 → e"
                        _send_to_preview(frm, partial[2])
                    else:
                        # e2 : show "e2 →" (TO empty)
                        _send_to_preview(frm, "")
                    ui_board.preview_from(frm)
                    cp_only_hint_and_coords_for_input()
                    # Wait for release so we don't immediately treat it as a new press
                    # (but do not block other critical actions)
                time.sleep_ms(10)

            held_ms = time.ticks_diff(time.ticks_ms(), t0)
            reset_ok_hold_state()

            if fired:
                cp_only_ok(False)
                reset_ok_hold_state()
                return ("backspace_confirm", move[:-1])

            # Short press confirms on release
            if held_ms < LONG_PRESS_MS:
                cp_only_ok(False)
                return "ok"

            # Long hold but somehow didn't fire (edge): ignore
            buttons.reset()
            continue

        else:
            cp_only_ok(False)
            ui_board.markings()
            return ("redo", b)


def collect_and_send_move():
    global in_input, preview_cap_flag
    in_input = True
    try:
        seed = None
        preset_from_col = None

        while True:
            if is_shutdown_held():
                shutdown_pico()

            cp_only_hint_and_coords_for_input()
            buttons.reset()

            move_from = enter_from_square(seed_btn=seed, preset_col=preset_from_col)
            preset_from_col = None

            if isinstance(move_from, tuple) and move_from[0] == "back_from":
                seed = None
                continue

            if move_from is None:
                if persistent_trail_active:
                    seed = None
                    continue
                return
            seed = None

            move_to = enter_to_square(move_from)

            if isinstance(move_to, tuple):
                tag = move_to[0]
                if tag == "back_to_from_rank":
                    preset_from_col = move_to[1]
                    continue
                if tag == "back_to_to_file":
                    # redo TO entry (FROM already selected)
                    cp_only_hint_and_coords_for_input()
                    buttons.reset()
                    move_to2 = enter_to_square(move_from)
                    if (
                        isinstance(move_to2, tuple)
                        and move_to2[0] == "back_to_from_rank"
                    ):
                        preset_from_col = move_to2[1]
                        continue
                    if move_to2 is None or isinstance(move_to2, tuple):
                        continue
                    move_to = move_to2

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

            # Keep handling confirm-backspaces until user actually confirms or cancels
            while isinstance(res, tuple) and res[0] == "backspace_confirm":
                partial = res[1]  # can be len 3,2,1,0

                ui_board.markings()

                if len(partial) == 3:
                    # e2e -> keep FROM=e2, preset TO file='e', re-enter TO rank
                    frm = partial[:2]
                    to_file = partial[2]

                    cp_only_hint_and_coords_for_input()
                    buttons.reset()
                    reset_ok_hold_state()

                    move_to = enter_to_square(frm, preset_col=to_file)
                    if isinstance(move_to, tuple):
                        if move_to[0] == "back_to_from_rank":
                            preset_from_col = move_to[1]
                            res = ("restart_from", None)
                            break
                        if move_to[0] == "back_to_to_file":
                            # user backspaced TO file again, just retry loop
                            res = ("backspace_confirm", frm)  # treat like len==2 next
                            continue
                    if move_to is None:
                        res = ("restart_from", None)
                        break

                    move = frm + move_to
                    res = confirm_move(move)
                    if res is None:
                        res = ("restart_from", None)
                        break
                    continue

                if len(partial) == 2:
                    # e2 -> keep FROM=e2, re-enter TO file selection
                    frm = partial

                    cp_only_hint_and_coords_for_input()
                    buttons.reset()
                    reset_ok_hold_state()

                    move_to = enter_to_square(frm, preset_col=None)
                    if isinstance(move_to, tuple):
                        if move_to[0] == "back_to_from_rank":
                            preset_from_col = move_to[1]
                            res = ("restart_from", None)
                            break
                        if move_to[0] == "back_to_to_file":
                            # retry TO file select
                            res = ("backspace_confirm", frm)
                            continue
                    if move_to is None:
                        res = ("restart_from", None)
                        break

                    move = frm + move_to
                    res = confirm_move(move)
                    if res is None:
                        res = ("restart_from", None)
                        break
                    continue

                if len(partial) == 1:
                    # e -> go back to FROM rank entry with preset file='e'
                    preset_from_col = partial[0]
                    seed = None
                    res = ("restart_from", None)
                    break

                # "" -> restart completely
                preset_from_col = None
                seed = None
                res = ("restart_from", None)
                break

            # If we broke out to restart FROM entry, do it without losing preset state
            if isinstance(res, tuple) and res[0] == "restart_from":
                continue

            if res == "ok":
                ui_board.redraw_final_trail(move, cap=preview_cap_flag)
                time.sleep_ms(200)
                send_to_pi(move)
                preview_cap_flag = False
                ui_board.markings()
                return

            if isinstance(res, tuple) and res[0] == "redo":
                cancel_btn = res[1]
                seed = cancel_btn if (1 <= cancel_btn <= 8) else None
                cp_only_hint_and_coords_for_input()
                continue
    finally:
        in_input = False


def game_over_wait_ok_and_ack(result_str):
    disable_hint_irq()
    try:
        buttons.reset()
        cp_only_ok(True)
        ui_board.game_over_scene()

        if is_shutdown_held():
            shutdown_pico()

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
                cp.clear_small_panel()
                cp.ok(blink)
                last = now

            if is_shutdown_held():
                shutdown_pico()

            b = buttons.detect_press()
            if b == (OK_BUTTON_INDEX + 1):
                cp_only_ok(False)
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
    ui_board.opening()
    lit = 0
    while True:
        if is_shutdown_held():
            shutdown_pico()
        lit = ui_board.loading_step(lit)
        time.sleep_ms(2000)
        msg = read_from_pi()
        if not msg:
            continue
        if msg.startswith("heyArduinoChooseMode"):
            while lit < (board.w * board.h):
                if is_shutdown_held():
                    shutdown_pico()
                lit = ui_board.loading_step(lit)
                time.sleep_ms(15)
            ui_board.markings()
            cp_show_coords_top(WHITE)
            global game_state
            game_state = GAME_SETUP
            return


def select_game_mode():
    buttons.reset()
    global game_mode
    while True:
        if is_shutdown_held():
            shutdown_pico()
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
        if b == 4:
            game_mode = MODE_PUZZLE
            send_to_pi("btn_mode_puzzle")
            return
        time.sleep_ms(5)


def select_singlepress(default_value, out_min, out_max):
    buttons.reset()
    while True:
        if is_shutdown_held():
            shutdown_pico()
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
        if is_shutdown_held():
            shutdown_pico()
        b = buttons.detect_press()
        if b == 1:
            send_to_pi("s1")
            return
        if b == 2:
            send_to_pi("s2")
            return
        if b == 3:
            send_to_pi("s3")
            return
        time.sleep_ms(5)


def wait_for_setup():
    global in_setup, game_state, default_strength, default_move_time, suspend_until_new_game
    in_setup = True
    try:
        while True:
            if is_shutdown_held():
                shutdown_pico()
            msg = read_from_pi()
            if not msg:
                time.sleep_ms(10)
                continue

            if msg.startswith("heyArduinodefault_strength_"):
                try:
                    default_strength = int(msg.split("_")[-1])
                except:
                    pass
                continue

            if msg.startswith("heyArduinodefault_time_"):
                try:
                    default_move_time = int(msg.split("_")[-1])
                except:
                    pass
                continue

            if msg.startswith("heyArduinoEngineStrength"):
                cp.coord(MAGENTA)
                ui_board.prompt_strength()
                v = select_strength_singlepress(default_strength)
                send_to_pi(str(v))
                time.sleep_ms(120)
                ui_board.markings()
                return

            if msg.startswith("heyArduinoTimeControl"):
                cp.coord(MAGENTA)
                ui_board.prompt_time()
                v = select_time_singlepress(default_move_time)
                send_to_pi(str(v))
                time.sleep_ms(120)
                ui_board.markings()
                return

            if msg.startswith("heyArduinoPlayerColor"):
                cp_show_coords_top(WHITE)
                select_color_choice()
                ui_board.markings()
                return

            if msg.startswith("heyArduinoSetupComplete"):
                game_state = GAME_RUNNING
                in_setup = False
                suspend_until_new_game = False
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
    cp_show_coords_top(MAGENTA)

    buttons.reset()
    try:
        while True:
            if is_shutdown_held():
                shutdown_pico()
            irq = process_hint_irq()
            if irq == "new":
                return
            b = buttons.detect_press()
            if not b:
                time.sleep_ms(5)
                continue
            if b == 1:
                send_to_pi("btn_q")
                break
            if b == 2:
                send_to_pi("btn_r")
                break
            if b == 3:
                send_to_pi("btn_b")
                break
            if b == 4:
                send_to_pi("btn_n")
                break
    finally:
        cp.clear_small_panel()
        ui_board.markings()


# ============================================================
# =============== PUZZLE SETUP COMMANDS ======================
# ============================================================


def handle_puzzle_setup_cmd(msg):
    """Pi-driven puzzle setup LED guidance.

    Messages (from Pi) are prefixed with 'heyArduino...'
    """
    global puzzle_setup_active, ok_last_val

    if not msg:
        return False

    if msg.startswith("heyArduinopuzzle_setup_begin"):
        puzzle_setup_active = True
        disable_hint_irq()
        buttons.reset()
        ok_last_val = BTN_OK.value()
        cp_only_ok(True)
        ui_board.markings()
        return True

    if msg.startswith("heyArduinopuzzle_setup_done"):
        puzzle_setup_active = False
        ui_board.markings()
        enable_hint_irq()
        return True

    if not puzzle_setup_active:
        return False

    if msg.startswith("heyArduinosetup_clear"):
        ui_board.markings()
        return True

    if msg.startswith("heyArduinosetup_remove_"):
        sq = msg.split("_")[-1].strip()
        ui_board.markings()
        xy = board.algebraic_to_xy(sq)
        if xy:
            x, y = xy
            for _ in range(3):
                board.set_square(x, y, RED)
                board.write()
                time.sleep_ms(200)
                board.set_square(x, y, BLACK)
                board.write()
                time.sleep_ms(200)

            # leave it RED when done blinking
            board.set_square(x, y, RED)
            board.write()
        return True

    if msg.startswith("heyArduinosetup_move_"):
        tail = msg[len("heyArduinosetup_move_") :].strip()
        parts = tail.split("_")
        uci = parts[0].strip() if parts else ""
        side = parts[1].strip().lower() if len(parts) > 1 else "w"
        color = GREEN if side.startswith("w") else ENGINE_COLOR
        ui_board.off()
        board.draw_trail(uci, color, end_color=None)
        return True

    return False


# ============================================================
# =============== MAIN LOOP ==================================
# ============================================================


def main_loop():
    global current_turn, engine_ack_pending, pending_gameover_result, buffered_turn_msg, suspend_until_new_game, game_state, ok_last_val, puzzle_setup_active

    while True:
        if is_shutdown_held():
            shutdown_pico()

        # During puzzle setup:
        #  - Pi sends setup_move/setup_remove messages => handled here
        #  - Pico forwards OK press => "heypibtn_ok"
        if puzzle_setup_active:
            msg_setup = read_from_pi()
            if msg_setup:
                handle_puzzle_setup_cmd(msg_setup)

            # Allow OK + Hint to cancel puzzle setup and return to mode select
            if BTN_OK.value() == 0 and BTN_HINT.value() == 0:
                send_to_pi("n")
                puzzle_setup_active = False
                cp_only_ok(False)
                enable_hint_irq()
                buttons.reset()
                ui_board.opening()
                time.sleep_ms(50)
                continue

            b = buttons.detect_press()
            if b == (OK_BUTTON_INDEX + 1):
                send_to_pi("btn_ok")
            time.sleep_ms(10)
            continue

        irq = process_hint_irq()
        if irq == "new":
            disable_hint_irq()
            cp_all_off()
            ui_board.opening()
            engine_ack_pending = False
            pending_gameover_result = None
            buffered_turn_msg = None
            continue

        if engine_ack_pending:
            nxt = read_from_pi()

            if nxt and nxt.startswith("heyArduinoGameOver"):
                pending_gameover_result = (
                    nxt.split(":", 1)[1].strip() if ":" in nxt else ""
                )
                while BTN_OK.value() == 0:
                    time.sleep_ms(10)
                time.sleep_ms(180)
                buttons.reset()
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

            if nxt and nxt.startswith("heyArduinoturn_"):
                buffered_turn_msg = nxt

            b = buttons.detect_press()
            if b == (OK_BUTTON_INDEX + 1):
                send_to_pi("btn_ok")

                engine_ack_pending = False
                cp_only_ok(False)
                clear_persistent_trail()
                ui_board.markings()

                if buffered_turn_msg:
                    turn_str = buffered_turn_msg.split("_", 1)[1].strip().lower()
                    if "w" in turn_str:
                        current_turn = "W"
                    elif "b" in turn_str:
                        current_turn = "B"
                    buffered_turn_msg = None

                cp_only_hint_and_coords_for_input()
                collect_and_send_move()
                continue

            time.sleep_ms(10)
            continue

        msg = read_from_pi()
        if msg and handle_puzzle_setup_cmd(msg):
            continue

        if not msg:
            time.sleep_ms(10)
            continue

        if suspend_until_new_game or game_state != GAME_RUNNING:
            if not (
                msg.startswith("heyArduinoChooseMode")
                or msg.startswith("heyArduinoResetBoard")
            ):
                continue

        if msg.startswith("heyArduinoGameOver"):
            res = msg.split(":", 1)[1].strip() if ":" in msg else ""
            game_over_wait_ok_and_ack(res)
            continue

        if msg.startswith("heyArduinoResetBoard"):
            hard_reset_board()
            continue

        if msg.startswith("heyArduinoChooseMode"):
            disable_hint_irq()
            buttons.reset()
            ui_board.markings()
            cp_show_coords_top(WHITE)
            game_state = GAME_SETUP
            select_game_mode()
            while game_state == GAME_SETUP:
                wait_for_setup()
            continue

        if msg.startswith("heyArduinoGameStart"):
            ui_board.markings()
            continue

        if msg.startswith("heyArduinom"):
            raw = msg[11:].strip()
            cap = raw.endswith("_cap")
            mv = raw[:-4] if cap else raw

            show_persistent_trail(
                mv, ENGINE_COLOR, "engine", end_color=(MAGENTA if cap else None)
            )
            cp_only_ok(True)
            engine_ack_pending = True
            pending_gameover_result = None
            buffered_turn_msg = None
            continue

        if msg.startswith("heyArduinopromotion_choice_needed"):
            handle_promotion_choice()
            continue

        if msg.startswith("heyArduinohint_"):
            cp_only_ok(True)
            raw = msg[len("heyArduinohint_") :].strip()
            cap = raw.endswith("_cap")
            best = raw[:-4] if cap else raw
            show_persistent_trail(
                best, YELLOW, "hint", end_color=(MAGENTA if cap else None)
            )
            cancel_user_input_and_restart()
            continue

        if msg.startswith("heyArduinoerror"):
            ui_board.illegal()
            cp_only_hint_and_coords_for_input()
            collect_and_send_move()
            continue

        if msg.startswith("heyArduinoturn_"):
            turn_str = msg.split("_", 1)[1].strip().lower()
            if "w" in turn_str:
                current_turn = "W"
            elif "b" in turn_str:
                current_turn = "B"

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
                cp_only_hint_and_coords_for_input()
                collect_and_send_move()
            continue


# ============================================================
# =============== ENTRY POINT ================================
# ============================================================


def run():
    global game_state
    cp_all_off()
    ui_board.off()
    buttons.reset()

    disable_hint_irq()
    wait_for_mode_request()
    ui_board.markings()
    select_game_mode()

    while game_state == GAME_SETUP:
        wait_for_setup()

    ui_board.markings()
    cp_bars_dim_on()
    enable_hint_irq()

    while True:
        main_loop()


run()

# If A–H or 1–8 appear reversed on your panel, flip either/both:
# CP_FILES_LEDS = list(reversed([6, 7, 8, 9, 10, 11, 12, 13]))
# CP_RANKS_LEDS = list(reversed([14, 15, 16, 17, 18, 19, 20, 21]))
