# ============================================================
#  PICO FIRMWARE (2026) - Standardized OO Architecture
#
#  Core objects:
#    - link   : UARTLink      (link.send(), link.read())
#    - screen : Screen        (screen.typing_from/to/confirm + clear helpers)
#    - cp     : ControlPanel  (buttons + panel LEDs + profiles; cp.off(), cp.only_ok(), cp.only_input(), cp.profile.main_menu(), ...)
#    - border : Border        (border.on(), border.off())
#    - board  : ChessBoard    (board.off(), board.markings(), board.preview_from(), board.preview_trail(), board.overlay_show(), ...)
#
#  Notes:
#    - No dataclass / no __future__ (MicroPython friendly)
#    - UART protocol strings preserved (heypi..., heyArduino...)
# ============================================================

from machine import Pin, UART
import time
import neopixel


# ============================================================
# CONFIG (grouped constants)
# ============================================================


class CFG:
    class UART:
        BAUD = 115200
        TX_PIN = 0
        RX_PIN = 1
        TIMEOUT_MS = 10

    class Buttons:
        # Active-low buttons with pull-ups.
        PINS = [2, 3, 4, 5, 10, 8, 7, 6, 9, 11]  # 1..8 coords, 9=OK, 10=HINT
        OK_INDEX = 8
        HINT_INDEX = 9
        SHUTDOWN_INDEX = 7  # button "8"/H

        DEBOUNCE_MS = 150
        OK_LONG_PRESS_MS = 500
        HINT_HOLD_DRAW_MS = 2000
        SHUTDOWN_HOLD_MS = 2000

    class LEDs:
        # Control panel (includes border coords)
        PANEL_PIN = 16
        PANEL_COUNT = 22

        # CP zone: 0..5 (two 2-LED group indicators + OK + HINT)
        CP_ZONE_START = 0
        CP_ZONE_END = 6

        # Special pixels within CP zone
        CP_OK_PIX = 4
        CP_HINT_PIX = 5

        # Border mapping within panel strip
        FILES = [6, 7, 8, 9, 10, 11, 12, 13]  # A..H
        RANKS = [14, 15, 16, 17, 18, 19, 20, 21]  # 1..8
        BORDER_COLOR = (40, 40, 40)

        # Chessboard matrix
        CHESS_PIN = 22
        W = 8
        H = 8
        ORIGIN_BOTTOM_RIGHT = True
        ZIGZAG = True

    class Timing:
        POLL_MS = 10
        FAST_POLL_MS = 5
        GAMEOVER_BLINK_MS = 400
        ENGINE_ACK_POST_MS = 180
        SETUP_TRANSITION_MS = 120
        NEW_GAME_SUPPRESS_MS = 800
        TURN_GAMEOVER_WINDOW_MS = 80

        LOADING_STEP_MS = 25
        LOADING_TICK_MS = 2000
        LOADING_FILL_MS = 15
        LOADING_POST_MS = 350
        BLINK_ON_MS = 220
        BLINK_OFF_MS = 140
        SLOW_POLL_MS = 20
        SHUTDOWN_IDLE_MS = 1000
        CONFIRM_DELAY_MS = 800

    class Colors:
        BLACK = (0, 0, 0)
        WHITE = (255, 255, 255)
        RED = (255, 0, 0)
        GREEN = (0, 255, 0)
        BLUE = (0, 0, 255)
        CYAN = (0, 255, 255)
        MAGENTA = (255, 0, 255)
        YELLOW = (255, 255, 0)

        ENGINE = BLUE


# Fast aliases
BLACK = CFG.Colors.BLACK
WHITE = CFG.Colors.WHITE
RED = CFG.Colors.RED
GREEN = CFG.Colors.GREEN
BLUE = CFG.Colors.BLUE
CYAN = CFG.Colors.CYAN
MAGENTA = CFG.Colors.MAGENTA
YELLOW = CFG.Colors.YELLOW
ENGINE_COLOR = CFG.Colors.ENGINE


# ============================================================
# STATE
# ============================================================


class Game:
    IDLE = 0
    SETUP = 1
    RUNNING = 2


class Mode:
    PC = "pc"
    ONLINE = "online"
    LOCAL = "local"
    PUZZLE = "puzzle"


class State:
    def __init__(self):
        self.game_state = Game.IDLE
        self.game_mode = Mode.PC
        self.current_turn = "W"

        self.default_strength = 5
        self.default_move_time = 2000

        self.in_setup = False
        self.in_input = False

        self.engine_ack_pending = False
        self.pending_gameover_result = None
        self.buffered_turn_msg = None

        self.preview_cap_flag = False

        self.suspend_until_new_game = False
        self.ok_back_enabled = False
        self.hint_enabled = True
        self.puzzle_setup_active = False

        self.persistent_trail_active = False
        self.persistent_trail_type = None
        self.persistent_trail_move = None
        self.persistent_trail_end_color = None


st = State()


# ============================================================
# UART LINK + SCREEN API
# ============================================================


class UARTLink:
    def __init__(self):
        self.uart = UART(
            0,
            baudrate=CFG.UART.BAUD,
            tx=Pin(CFG.UART.TX_PIN),
            rx=Pin(CFG.UART.RX_PIN),
            timeout=CFG.UART.TIMEOUT_MS,
        )

    def send(self, kind, payload=""):
        # Preserve protocol: heypi{kind}{payload}\n
        self.uart.write(("heypi" + str(kind) + str(payload) + "\n").encode())

    def read(self):
        if self.uart.any():
            try:
                return self.uart.readline().decode().strip()
            except Exception:
                return None
        return None

    def write_raw(self, s: str):
        self.uart.write((s + "\n").encode())


class Screen:
    """LCD messaging lives on the Pi. Pico only sends typing previews."""

    def __init__(self, link: UARTLink, st_: State):
        self.link = link
        self.st = st_

    def _ok(self):
        return self.st.game_state == Game.RUNNING

    def typing_from(self, text: str):
        if not self._ok():
            return
        self.link.write_raw("heypityping_from_" + text)

    def typing_to(self, move_from: str, partial_to: str):
        if not self._ok():
            return
        self.link.write_raw("heypityping_to_" + move_from + " -> " + partial_to)

    def typing_confirm(self, move_uci: str):
        if not self._ok():
            return
        frm, to = move_uci[:2], move_uci[2:4]
        self.link.write_raw("heypityping_confirm_" + frm + " -> " + to)

    # Clears (fixes “confirm move” stuck display if OK pressed quickly)
    def clear_confirm(self):
        # self.link.write_raw("heypityping_confirm_")
        time.sleep_ms(CFG.Timing.CONFIRM_DELAY_MS)

    def clear_to(self):
        self.link.write_raw("heypityping_to_")

    def clear_from(self):
        self.link.write_raw("heypityping_from_")


link = UARTLink()
screen = Screen(link, st)


# ============================================================
# CONTROL PANEL (buttons + panel LEDs + profiles)
# ============================================================


class Profiles:
    """Profiles are pure panel/button configuration; no game logic."""

    def __init__(self, cp: "ControlPanel"):
        self.cp = cp

    def main_menu(self):
        self.cp.border(False)
        self.cp._set_cp_buttons(top=True, bottom=False, ok=False, hint=False)
        self.cp.apply()
        self.cp.set_allowed([1, 2, 3, 4])

    def vs_strength_time(self):
        self.cp.border(False)
        self.cp._set_cp_buttons(
            top=True, bottom=True, ok=True, hint=False, ok_color=RED
        )
        self.cp.apply()
        self.cp.set_allowed([1, 2, 3, 4, 5, 6, 7, 8, 9])

    def vs_color(self):
        self.cp.border(False)
        self.cp._set_cp_buttons(
            top=True, bottom=False, ok=True, hint=False, ok_color=RED
        )
        self.cp.apply()
        self.cp.set_allowed([1, 2, 3, 9])

    def puzzle_top(self):
        self.cp.border(False)
        self.cp._set_cp_buttons(
            top=True, bottom=False, ok=True, hint=False, ok_color=RED
        )
        self.cp.apply()
        self.cp.set_allowed([1, 2, 3, 9])

    def menu_paged(self):
        self.cp.border(False)
        self.cp._set_cp_buttons(
            top=True, bottom=False, ok=True, hint=True, ok_color=RED, hint_color=BLUE
        )
        self.cp.apply()
        self.cp.set_allowed([1, 2, 3, 4, 9, 10])

    def puzzle_play(self):
        self.cp.border(True)
        self.cp._set_cp_buttons(
            top=True, bottom=True, ok=True, hint=True, ok_color=RED, hint_color=YELLOW
        )
        self.cp.apply()
        self.cp.set_allowed([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])


class ControlPanel:
    def __init__(self, st_: State):
        self.st = st_
        self.panel = neopixel.NeoPixel(
            Pin(CFG.LEDs.PANEL_PIN, Pin.OUT), CFG.LEDs.PANEL_COUNT
        )
        self._panel_last = None

        # Buttons
        self.pins = [Pin(g, Pin.IN, Pin.PULL_UP) for g in CFG.Buttons.PINS]
        self.BTN_OK = self.pins[CFG.Buttons.OK_INDEX]
        self.BTN_HINT = self.pins[CFG.Buttons.HINT_INDEX]
        self.BTN_SHUT = self.pins[CFG.Buttons.SHUTDOWN_INDEX]

        self._last_btn = [1] * len(self.pins)
        self.allowed = None

        # hint IRQ
        self.hint_irq_flag = False
        self.suppress_hints_until_ms = 0

        # holds
        self._ok_press_ms = None
        self._ok_fired = False
        self._shut_press_ms = None
        self._shut_fired = False

        self.profile = Profiles(self)
        self.enable_hint_irq()

    # ---------------- LEDs helpers ----------------
    def _snapshot(self):
        return [tuple(self.panel[i]) for i in range(CFG.LEDs.PANEL_COUNT)]

    def apply(self, force=False):
        cur = self._snapshot()
        if force or (self._panel_last is None) or (cur != self._panel_last):
            self.panel.write()
            self._panel_last = cur

    def off(self, force=False):
        for i in range(CFG.LEDs.PANEL_COUNT):
            self.panel[i] = BLACK
        self.apply(force=force)

    def clear_header(self):
        for i in range(CFG.LEDs.CP_ZONE_START, CFG.LEDs.CP_ZONE_END):
            self.panel[i] = BLACK

    def border(self, on=True, color=CFG.LEDs.BORDER_COLOR, force=False):
        col = color if on else BLACK
        for idx in CFG.LEDs.FILES + CFG.LEDs.RANKS:
            self.panel[idx] = col
        self.apply(force=force)

    def _set_cp_buttons(
        self,
        top: bool,
        bottom: bool,
        ok: bool,
        hint: bool,
        ok_color=GREEN,
        hint_color=YELLOW,
    ):
        # Buttons 1..4 use LEDs 0..1, buttons 5..8 use 2..3 (lit=WHITE)
        self.panel[0] = WHITE if top else BLACK
        self.panel[1] = WHITE if top else BLACK
        self.panel[2] = WHITE if bottom else BLACK
        self.panel[3] = WHITE if bottom else BLACK
        self.panel[CFG.LEDs.CP_OK_PIX] = ok_color if ok else BLACK
        self.panel[CFG.LEDs.CP_HINT_PIX] = hint_color if hint else BLACK

    # Standardized UI verbs
    def only_ok(self, on=True):
        col = RED if (self.st.game_mode == Mode.ONLINE) else GREEN
        self._set_cp_buttons(False, False, ok=on, hint=False, ok_color=col)
        self.apply()

    def only_input(self):
        self.border(True)
        self._set_cp_buttons(
            True, True, ok=True, hint=True, ok_color=RED, hint_color=YELLOW
        )
        self.apply()

    def show_coords_top(self, color=WHITE):
        self.border(False)
        self.panel[0] = color
        self.panel[1] = color
        self.panel[2] = BLACK
        self.panel[3] = BLACK
        self.apply()

    # ---------------- Buttons / gating ----------------
    def reset_edges(self):
        for i, p in enumerate(self.pins):
            self._last_btn[i] = p.value()

    def set_allowed(self, btns):
        self.allowed = None if btns is None else set(int(x) for x in btns)
        self.reset_edges()

    def detect_press_raw(self):
        for i, p in enumerate(self.pins):
            cur = p.value()
            prev = self._last_btn[i]
            self._last_btn[i] = cur
            if prev == 1 and cur == 0:
                time.sleep_ms(CFG.Buttons.DEBOUNCE_MS)
                return i + 1
        return None

    def detect_press_allowed(self):
        while True:
            b = self.detect_press_raw()
            if b is None:
                return None
            if self.allowed is None or b in self.allowed:
                return b
            time.sleep_ms(CFG.Timing.FAST_POLL_MS)

    @staticmethod
    def is_non_coord_button(b):
        return b in (9, 10)

    # ---------------- IRQ ----------------
    def _hint_irq(self, pin):
        self.hint_irq_flag = True

    def disable_hint_irq(self):
        self.BTN_HINT.irq(handler=None)

    def enable_hint_irq(self):
        self.BTN_HINT.irq(trigger=Pin.IRQ_FALLING, handler=self._hint_irq)

    # ---------------- Holds ----------------
    def reset_ok_hold(self):
        self._ok_press_ms = None
        self._ok_fired = False

    def ok_long_hold_fired(self, hold_ms=CFG.Buttons.OK_LONG_PRESS_MS):
        if self.BTN_OK.value() == 0:
            if self._ok_press_ms is None:
                self._ok_press_ms = time.ticks_ms()
                self._ok_fired = False
            if (not self._ok_fired) and time.ticks_diff(
                time.ticks_ms(), self._ok_press_ms
            ) >= hold_ms:
                self._ok_fired = True
                return True
            return False
        self.reset_ok_hold()
        return False

    def shutdown_held(self, hold_ms=CFG.Buttons.SHUTDOWN_HOLD_MS):
        if self.BTN_SHUT.value() == 0:
            if self._shut_press_ms is None:
                self._shut_press_ms = time.ticks_ms()
                self._shut_fired = False
            if (not self._shut_fired) and time.ticks_diff(
                time.ticks_ms(), self._shut_press_ms
            ) >= hold_ms:
                self._shut_fired = True
                return True
            return False
        self._shut_press_ms = None
        self._shut_fired = False
        return False


class Border:
    def __init__(self, cp: ControlPanel):
        self.cp = cp

    def on(self, force=False):
        self.cp.border(True, force=force)

    def off(self, force=False):
        self.cp.border(False, force=force)


cp = ControlPanel(st)
border = Border(cp)


# ============================================================
# CHESSBOARD
# ============================================================


class ChessBoard:
    def __init__(self):
        self.w, self.h = CFG.LEDs.W, CFG.LEDs.H
        self.origin_bottom_right = CFG.LEDs.ORIGIN_BOTTOM_RIGHT
        self.zigzag = CFG.LEDs.ZIGZAG
        self.np = neopixel.NeoPixel(Pin(CFG.LEDs.CHESS_PIN, Pin.OUT), self.w * self.h)

        # base markings cache (checkerboard)
        self._marking_cache = [BLACK] * (self.w * self.h)
        light = WHITE
        dark = BLACK
        for y in range(self.h):
            for x in range(self.w):
                col = dark if ((x + y) % 2 == 0) else light
                self._raw_set(x, y, col, into_cache=True)
        self.off()

        # overlay tracking
        self.overlay_active = False
        self.overlay_type = None
        self.overlay_move = None
        self._last_from_only = None

    def _xy_to_index(self, x, y):
        row = y
        if self.origin_bottom_right:
            if self.zigzag:
                col_index = (self.w - 1 - x) if (row % 2 == 0) else x
            else:
                col_index = self.w - 1 - x
            return row * self.w + col_index
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

    def write(self):
        self.np.write()

    def off(self):
        for i in range(self.w * self.h):
            self.np[i] = BLACK
        self.write()

    def clear(self, color=BLACK):
        for i in range(self.w * self.h):
            self.np[i] = color
        self.write()

    def set_square(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.np[self._xy_to_index(x, y)] = color

    def algebraic_to_xy(self, sq):
        if not sq or len(sq) < 2:
            return None
        f, r = sq[0].lower(), sq[1]
        if not ("a" <= f <= "h"):
            return None
        if not ("1" <= r <= "8"):
            return None
        return (ord(f) - 97, int(r) - 1)

    def show_markings(self):
        for i in range(self.w * self.h):
            self.np[i] = self._marking_cache[i]
        self.write()

    # ----------- scenes / prompts -----------
    def opening(self):
        self.clear(BLACK)
        for k in range(self.w + self.h - 1):
            for y in range(self.h):
                x = k - y
                if 0 <= x < self.w:
                    self.set_square(x, y, GREEN)
            self.write()
            time.sleep_ms(25)
        time.sleep_ms(CFG.Timing.LOADING_POST_MS)
        self.show_markings()

    def loading_step(self, count):
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
        for i in range(self.w * self.h):
            self.np[i] = BLUE
        self.write()
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

    def _draw_hline(self, x, y, length, color):
        for dx in range(length):
            self.set_square(x + dx, y, color)

    def _draw_vline(self, x, y, length, color):
        for dy in range(length):
            self.set_square(x, y + dy, color)

    def prompt_time(self):
        self.clear(BLACK)
        pts = [(2, 6), (3, 6), (4, 6), (5, 6), (4, 5), (4, 4), (4, 3), (4, 2)]
        for x, y in pts:
            self.set_square(x, y, MAGENTA)
        self.write()

    def prompt_strength(self):
        self.clear(BLACK)
        pts = [(2, 6), (2, 5), (2, 4), (2, 3), (2, 2), (3, 2), (4, 2), (5, 2)]
        for x, y in pts:
            self.set_square(x, y, MAGENTA)
        self.write()

    def scene_gameover(self):
        for i in range(self.w * self.h):
            self.np[i] = GREEN
        self.write()
        for y in range(self.h):
            self.set_square(2, y, WHITE)
            self.set_square(5, y, WHITE)
        for x in range(self.w):
            self.set_square(x, 2, WHITE)
            self.set_square(x, 5, WHITE)
        self.write()

    def scene_promotion(self):
        for i in range(self.w * self.h):
            self.np[i] = MAGENTA
        self.write()
        self._draw_vline(2, 1, 6, WHITE)
        self._draw_hline(2, 6, 4, WHITE)
        self._draw_hline(2, 4, 4, WHITE)
        self._draw_vline(5, 5, 2, WHITE)
        self.write()

    # ----------- trails / overlays -----------
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

        if fx == tx and fy != ty:
            sy = self._sgn(dy)
            return [(fx, y) for y in range(fy, ty + sy, sy)]
        if fy == ty and fx != tx:
            sx = self._sgn(dx)
            return [(x, fy) for x in range(fx, tx + sx, sx)]
        if adx == ady and adx != 0:
            sx = self._sgn(dx)
            sy = self._sgn(dy)
            x, y = fx, fy
            out = []
            for _ in range(adx + 1):
                out.append((x, y))
                x += sx
                y += sy
            return out
        if (adx, ady) in ((1, 2), (2, 1)):
            sx = self._sgn(dx)
            sy = self._sgn(dy)
            path = [(fx, fy)]
            if ady == 2:
                path += [
                    (fx, fy + 1 * sy),
                    (fx, fy + 2 * sy),
                    (fx + 1 * sx, fy + 2 * sy),
                ]
            else:
                path += [
                    (fx + 1 * sx, fy),
                    (fx + 2 * sx, fy),
                    (fx + 2 * sx, fy + 1 * sy),
                ]
            if path[-1] != (tx, ty):
                path.append((tx, ty))
            # dedup neighbors
            ded = []
            for p in path:
                if not ded or ded[-1] != p:
                    ded.append(p)
            return ded
        return [(fx, fy), (tx, ty)]

    def draw_trail(self, uci, color, end_color=None):
        if not uci or len(uci) < 4:
            return
        frm, to = uci[:2], uci[2:4]
        path = self._path_squares(frm, to)
        for i, (x, y) in enumerate(path):
            self.set_square(
                x, y, end_color if (end_color and i == len(path) - 1) else color
            )
        self.write()

    def blink_square_keep(
        self,
        sq,
        color_on,
        times=1,
        on_ms=CFG.Timing.BLINK_ON_MS,
        off_ms=CFG.Timing.BLINK_OFF_MS,
    ):
        xy = self.algebraic_to_xy(sq)
        if not xy:
            return
        x, y = xy
        idx = self._xy_to_index(x, y)
        prev = self.np[idx]
        for _ in range(times):
            self.set_square(x, y, color_on)
            self.write()
            time.sleep_ms(on_ms)
            self.set_square(x, y, BLACK)
            self.write()
            time.sleep_ms(off_ms)
        self.np[idx] = prev
        self.write()

    # Centralized UI verbs
    def markings(self):
        self._last_from_only = None
        self.overlay_active = False
        self.overlay_type = None
        self.overlay_move = None
        self.show_markings()

    def preview_from(self, sq):
        if self._last_from_only == sq and not self.overlay_active:
            return
        self._last_from_only = sq
        self.markings()
        xy = self.algebraic_to_xy(sq)
        if xy:
            self.set_square(xy[0], xy[1], GREEN)
            self.write()

    def preview_trail(self, uci, cap=False):
        self._last_from_only = None
        self.markings()
        self.draw_trail(uci, GREEN, end_color=(MAGENTA if cap else None))

    def overlay_show(self, role, uci, cap=False, color_override=None, end_color=None):
        self.overlay_active = True
        self.overlay_type = role
        self.overlay_move = uci
        self.markings()
        col = (
            color_override
            if (color_override is not None)
            else (ENGINE_COLOR if role == "engine" else YELLOW)
        )
        endc = end_color if (end_color is not None) else (MAGENTA if cap else None)
        self.draw_trail(uci, col, end_color=endc)

    def overlay_clear(self):
        self.overlay_active = False
        self.overlay_type = None
        self.overlay_move = None
        self._last_from_only = None
        self.show_markings()


board = ChessBoard()


# ============================================================
# HELPERS (pure)
# ============================================================


def _is_alnum(ch: str) -> bool:
    if not ch or len(ch) != 1:
        return False
    o = ord(ch)
    return (48 <= o <= 57) or (65 <= o <= 90) or (97 <= o <= 122)


def map_range(x, in_min, in_max, out_min, out_max):
    return int((x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)


# ============================================================
# SHUTDOWN
# ============================================================


def shutdown_pico():
    link.send("xshutdown")
    for _ in range(2):
        cp.only_ok(True)
        board.clear(CYAN)
        time.sleep_ms(180)
        cp.only_ok(False)
        board.clear(BLACK)
        time.sleep_ms(180)
    cp.off(force=True)
    board.clear(BLACK)
    cp.disable_hint_irq()
    while True:
        time.sleep_ms(CFG.Timing.SHUTDOWN_IDLE_MS)


# ============================================================
# PERSISTENT TRAILS
# ============================================================


def clear_persistent_trail():
    was_hint = st.persistent_trail_type == "hint"
    st.persistent_trail_active = False
    st.persistent_trail_type = None
    st.persistent_trail_move = None
    st.persistent_trail_end_color = None
    board.overlay_clear()
    if was_hint and st.game_state == Game.RUNNING and (not st.engine_ack_pending):
        cp.only_input()


def show_persistent_trail(move_uci, color, trail_type, end_color=None):
    st.persistent_trail_active = True
    st.persistent_trail_type = trail_type
    st.persistent_trail_move = move_uci
    st.persistent_trail_end_color = end_color
    cap = (end_color == MAGENTA) if end_color is not None else False
    role = "engine" if trail_type == "engine" else "hint"
    if trail_type == "hint":
        cp.only_ok(True)
    board.overlay_show(
        role, move_uci, cap=cap, color_override=color, end_color=end_color
    )


# ============================================================
# CAPTURE PROBE (Pi-assisted)
# ============================================================


def probe_capture_with_pi(uci, timeout_ms=150):
    st.preview_cap_flag = False
    link.send("capq_", uci)
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        msg = link.read()
        if not msg:
            time.sleep_ms(CFG.Timing.FAST_POLL_MS)
            continue
        if msg.startswith("heyArduinocapr_"):
            val = msg.split("_", 1)[1].strip()
            st.preview_cap_flag = val.startswith("1")
            return st.preview_cap_flag
    return False


# ============================================================
# HINT / NEW GAME PROCESSOR
# ============================================================


def process_hint_irq():
    if not st.hint_enabled:
        return None
    if not cp.hint_irq_flag:
        return None
    cp.hint_irq_flag = False

    if cp.shutdown_held():
        shutdown_pico()

    now = time.ticks_ms()
    if time.ticks_diff(cp.suppress_hints_until_ms, now) > 0:
        return None

    # OK+HINT => new game
    if cp.BTN_OK.value() == 0 and cp.BTN_HINT.value() == 0:
        st.game_state = Game.SETUP
        link.send("n")
        st.suspend_until_new_game = True
        st.engine_ack_pending = False
        st.pending_gameover_result = None
        st.buffered_turn_msg = None

        cp.show_coords_top(WHITE)
        v = 0
        board.off()
        while v < (board.w * board.h):
            v = board.loading_step(v)
            time.sleep_ms(CFG.Timing.LOADING_STEP_MS)
        time.sleep_ms(CFG.Timing.LOADING_POST_MS)
        board.markings()
        cp.suppress_hints_until_ms = time.ticks_add(
            now, CFG.Timing.NEW_GAME_SUPPRESS_MS
        )
        return "new"

    if st.game_state != Game.RUNNING:
        return None

    # Hold hint => draw offer (online)
    if cp.BTN_HINT.value() == 0:
        t0 = time.ticks_ms()
        while cp.BTN_HINT.value() == 0:
            if time.ticks_diff(time.ticks_ms(), t0) >= CFG.Buttons.HINT_HOLD_DRAW_MS:
                link.send("btn_draw")
                return "draw"
            time.sleep_ms(CFG.Timing.POLL_MS)

    link.send("btn_hint")
    return "hint"


# ============================================================
# OVERLAY / GAMEOVER inline handler
# ============================================================


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
# MOVE ENTRY (kept behavior; cleaned structure)
# ============================================================


def enter_from_square(seed_btn=None, preset_col=None):
    if st.game_state != Game.RUNNING:
        return None
    cp.reset_ok_hold()

    if cp.shutdown_held():
        shutdown_pico()

    if st.persistent_trail_active:
        # dismiss overlay first
        while True:
            if cp.shutdown_held():
                shutdown_pico()
            msg = link.read()
            if msg and _handle_pi_overlay_or_gameover(msg) == "gameover":
                return None
            b = cp.detect_press_raw()
            if not b:
                time.sleep_ms(CFG.Timing.FAST_POLL_MS)
                continue
            clear_persistent_trail()
            if 1 <= b <= 8:
                seed_btn = b
            break
        cp.only_input()
        cp.reset_edges()

    col = None
    row = None

    if preset_col is not None:
        col = preset_col
        screen.typing_from(col)

    while col is None:
        if st.game_state != Game.RUNNING:
            return None

        if seed_btn is not None:
            b = seed_btn
            seed_btn = None
        else:
            if cp.shutdown_held():
                shutdown_pico()
            irq = process_hint_irq()
            if irq == "new":
                return None
            msg = link.read()
            if msg:
                outcome = _handle_pi_overlay_or_gameover(msg)
                if outcome == "gameover":
                    return None
                if outcome in ("hint", "engine"):
                    cp.reset_edges()
                    return None
            b = cp.detect_press_raw()
            if not b:
                time.sleep_ms(CFG.Timing.FAST_POLL_MS)
                continue

        if cp.is_non_coord_button(b):
            continue
        col = chr(ord("a") + b - 1)
        screen.typing_from(col)

    while row is None:
        if st.game_state != Game.RUNNING:
            return None
        if cp.shutdown_held():
            shutdown_pico()

        if cp.ok_long_hold_fired():
            screen.typing_from("")
            board.markings()
            # wait release
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(CFG.Timing.POLL_MS)
            cp.reset_ok_hold()
            cp.reset_edges()
            return ("back_from", None)

        irq = process_hint_irq()
        if irq == "new":
            return None

        msg = link.read()
        if msg:
            outcome = _handle_pi_overlay_or_gameover(msg)
            if outcome == "gameover":
                return None
            if outcome in ("hint", "engine"):
                cp.reset_edges()
                return None

        b = cp.detect_press_raw()
        if not b:
            time.sleep_ms(CFG.Timing.FAST_POLL_MS)
            continue
        if cp.is_non_coord_button(b):
            continue
        row = str(b)
        screen.typing_from(col + row)

    frm = col + row
    board.preview_from(frm)
    return frm


def enter_to_square(move_from, preset_col=None):
    if st.game_state != Game.RUNNING:
        return None
    cp.reset_ok_hold()

    if cp.shutdown_held():
        shutdown_pico()

    if st.persistent_trail_active:
        while True:
            if cp.shutdown_held():
                shutdown_pico()
            msg = link.read()
            if msg and _handle_pi_overlay_or_gameover(msg) == "gameover":
                return None
            b = cp.detect_press_raw()
            if not b:
                time.sleep_ms(CFG.Timing.FAST_POLL_MS)
                continue
            clear_persistent_trail()
            if 1 <= b <= 8:
                seed_btn = b
            break
        cp.only_input()
        cp.reset_edges()

    col = None
    row = None

    if (
        preset_col is not None
        and isinstance(preset_col, str)
        and len(preset_col) == 1
        and ("a" <= preset_col <= "h")
    ):
        col = preset_col
        screen.typing_to(move_from, col)

    while col is None:
        if st.game_state != Game.RUNNING:
            return None
        if cp.shutdown_held():
            shutdown_pico()

        if cp.ok_long_hold_fired():
            screen.typing_from(move_from[0])
            board.markings()
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(CFG.Timing.POLL_MS)
            cp.reset_ok_hold()
            cp.reset_edges()
            return ("back_to_from_rank", move_from[0])

        irq = process_hint_irq()
        if irq == "new":
            return None

        msg = link.read()
        if msg:
            outcome = _handle_pi_overlay_or_gameover(msg)
            if outcome == "gameover":
                return None
            if outcome in ("hint", "engine"):
                cp.reset_edges()
                return None

        b = cp.detect_press_raw()
        if not b:
            time.sleep_ms(CFG.Timing.FAST_POLL_MS)
            continue
        if cp.is_non_coord_button(b):
            continue
        col = chr(ord("a") + b - 1)
        screen.typing_to(move_from, col)

    while row is None:
        if st.game_state != Game.RUNNING:
            return None
        if cp.shutdown_held():
            shutdown_pico()

        if cp.ok_long_hold_fired():
            screen.typing_to(move_from, "")
            board.preview_from(move_from)
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(CFG.Timing.POLL_MS)
            cp.reset_ok_hold()
            cp.reset_edges()
            return ("back_to_to_file", move_from)

        irq = process_hint_irq()
        if irq == "new":
            return None

        msg = link.read()
        if msg:
            outcome = _handle_pi_overlay_or_gameover(msg)
            if outcome == "gameover":
                return None
            if outcome in ("hint", "engine"):
                cp.reset_edges()
                return None

        b = cp.detect_press_raw()
        if not b:
            time.sleep_ms(CFG.Timing.FAST_POLL_MS)
            continue
        if cp.is_non_coord_button(b):
            continue
        row = str(b)
        screen.typing_to(move_from, col + row)

    to = col + row
    uci = move_from + to
    cap_prev = probe_capture_with_pi(uci)
    board.preview_trail(uci, cap=cap_prev)
    return to


def confirm_move(move):
    if st.game_state != Game.RUNNING:
        return None

    cp.only_ok(True)

    # If OK is already held, wait release so we don't miss the confirm edge.
    while cp.BTN_OK.value() == 0:
        if cp.shutdown_held():
            shutdown_pico()
        if process_hint_irq() == "new":
            cp.only_ok(False)
            return None
        time.sleep_ms(CFG.Timing.POLL_MS)

    cp.reset_edges()
    screen.typing_confirm(move)

    while True:
        if st.game_state != Game.RUNNING:
            cp.only_ok(False)
            return None

        if cp.shutdown_held():
            shutdown_pico()

        if process_hint_irq() == "new":
            cp.only_ok(False)
            return None

        msg = link.read()
        if msg:
            outcome = _handle_pi_overlay_or_gameover(msg)
            if outcome == "gameover":
                cp.only_ok(False)
                return None
            if outcome in ("hint", "engine"):
                cp.reset_edges()
                return None

        # OK confirm: level-based with long-hold backspace
        if cp.BTN_OK.value() == 0:
            t0 = time.ticks_ms()
            fired = False

            while cp.BTN_OK.value() == 0:
                if cp.shutdown_held():
                    shutdown_pico()
                if process_hint_irq() == "new":
                    cp.only_ok(False)
                    return None
                if (not fired) and time.ticks_diff(
                    time.ticks_ms(), t0
                ) >= CFG.Buttons.OK_LONG_PRESS_MS:
                    fired = True
                    partial = move[:-1]
                    frm = partial[:2]
                    if len(partial) == 3:
                        screen.typing_to(frm, partial[2])
                    else:
                        screen.typing_to(frm, "")
                    board.preview_from(frm)
                time.sleep_ms(CFG.Timing.POLL_MS)

            held_ms = time.ticks_diff(time.ticks_ms(), t0)
            cp.reset_ok_hold()

            if fired:
                cp.only_ok(False)
                # wait release
                while cp.BTN_OK.value() == 0:
                    time.sleep_ms(CFG.Timing.POLL_MS)
                cp.reset_edges()
                screen.clear_confirm()
                return ("backspace_confirm", move[:-1])

            if held_ms < CFG.Buttons.OK_LONG_PRESS_MS:
                cp.only_ok(False)
                # IMPORTANT fix: clear confirm line immediately on confirm to avoid LCD staying stale.
                screen.clear_confirm()
                return "ok"

            cp.reset_edges()
            continue

        # Any other button cancels confirm stage
        b = cp.detect_press_raw()
        if not b:
            time.sleep_ms(CFG.Timing.FAST_POLL_MS)
            continue
        cp.only_ok(False)
        screen.clear_confirm()
        return ("redo", b)


def collect_and_send_move():
    st.in_input = True
    try:
        seed = None
        preset_from_col = None

        while True:
            if cp.shutdown_held():
                shutdown_pico()

            cp.only_input()
            cp.reset_edges()

            move_from = enter_from_square(seed_btn=seed, preset_col=preset_from_col)
            preset_from_col = None

            if isinstance(move_from, tuple) and move_from[0] == "back_from":
                seed = None
                continue
            if move_from is None:
                if st.persistent_trail_active:
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
                    cp.only_input()
                    cp.reset_edges()
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
                if st.persistent_trail_active:
                    seed = None
                    continue
                return

            move = move_from + move_to
            res = confirm_move(move)
            if res is None:
                if st.persistent_trail_active:
                    seed = None
                    continue
                return

            while isinstance(res, tuple) and res[0] == "backspace_confirm":
                partial = res[1]
                if len(partial) == 3:
                    frm = partial[:2]
                    to_file = partial[2]
                    cp.only_input()
                    cp.reset_edges()
                    cp.reset_ok_hold()
                    move_to = enter_to_square(frm, preset_col=to_file)
                    if isinstance(move_to, tuple):
                        if move_to[0] == "back_to_from_rank":
                            preset_from_col = move_to[1]
                            res = ("restart_from", None)
                            break
                        if move_to[0] == "back_to_to_file":
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

                if len(partial) == 2:
                    frm = partial
                    cp.only_input()
                    cp.reset_edges()
                    cp.reset_ok_hold()
                    move_to = enter_to_square(frm)
                    if isinstance(move_to, tuple):
                        if move_to[0] == "back_to_from_rank":
                            preset_from_col = move_to[1]
                            res = ("restart_from", None)
                            break
                        if move_to[0] == "back_to_to_file":
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
                    preset_from_col = partial[0]
                    seed = None
                    res = ("restart_from", None)
                    break

                preset_from_col = None
                seed = None
                res = ("restart_from", None)
                break

            if isinstance(res, tuple) and res[0] == "restart_from":
                continue

            if res == "ok":
                time.sleep_ms(200)
                link.send(move)
                st.preview_cap_flag = False
                board.markings()
                return

            if isinstance(res, tuple) and res[0] == "redo":
                cancel_btn = res[1]
                seed = cancel_btn if (1 <= cancel_btn <= 8) else None
                cp.only_input()
                continue
    finally:
        st.in_input = False


# ============================================================
# GAME OVER ACK
# ============================================================


def game_over_wait_ok_and_ack(result_str):
    cp.disable_hint_irq()
    try:
        cp.reset_edges()
        cp.only_ok(True)
        board.scene_gameover()

        if cp.shutdown_held():
            shutdown_pico()

        while cp.BTN_OK.value() == 0:
            time.sleep_ms(CFG.Timing.POLL_MS)
        time.sleep_ms(200)
        cp.reset_edges()

        blink = False
        last = time.ticks_ms()
        while True:
            now = time.ticks_ms()
            if time.ticks_diff(now, last) > CFG.Timing.GAMEOVER_BLINK_MS:
                blink = not blink
                cp.clear_header()
                cp.panel[CFG.LEDs.CP_OK_PIX] = GREEN if blink else BLACK
                cp.apply()
                last = now

            if cp.shutdown_held():
                shutdown_pico()

            b = cp.detect_press_raw()
            if b == (CFG.Buttons.OK_INDEX + 1):
                cp.only_ok(False)
                link.send("n")
                break
            time.sleep_ms(
                CFG.Timing.SLOW_POLL_MS if hasattr(CFG.Timing, "SLOW_POLL_MS") else 20
            )

        board.markings()
    finally:
        cp.enable_hint_irq()


# ============================================================
# SETUP / MODE SELECTION
# ============================================================


def wait_for_mode_request():
    board.opening()
    lit = 0
    while True:
        if cp.shutdown_held():
            shutdown_pico()
        lit = board.loading_step(lit)
        time.sleep_ms(CFG.Timing.LOADING_TICK_MS)
        msg = link.read()
        if not msg:
            continue
        if msg.startswith("heyArduinoChooseMode"):
            while lit < (board.w * board.h):
                if cp.shutdown_held():
                    shutdown_pico()
                lit = board.loading_step(lit)
                time.sleep_ms(CFG.Timing.LOADING_FILL_MS)
            board.markings()
            cp.show_coords_top(WHITE)
            st.game_state = Game.SETUP
            return


def select_game_mode():
    cp.profile.main_menu()
    cp.reset_edges()
    while True:
        if cp.shutdown_held():
            shutdown_pico()
        b = cp.detect_press_allowed()
        if not b:
            time.sleep_ms(CFG.Timing.FAST_POLL_MS)
            continue
        if b == 1:
            st.game_mode = Mode.PC
            link.send("btn_mode_pc")
            return
        if b == 2:
            st.game_mode = Mode.ONLINE
            link.send("btn_mode_online")
            return
        if b == 3:
            st.game_mode = Mode.LOCAL
            link.send("btn_mode_local")
            return
        if b == 4:
            st.game_mode = Mode.PUZZLE
            link.send("btn_mode_puzzles")
            return


def _setup_back_cleanup():
    st.in_setup = False
    st.game_state = Game.IDLE
    st.suspend_until_new_game = False
    try:
        board.markings()
    except Exception:
        pass
    cp.reset_edges()


def select_singlepress(out_min, out_max):
    cp.reset_edges()
    while True:
        if cp.shutdown_held():
            shutdown_pico()
        b = cp.detect_press_allowed()
        if b == (CFG.Buttons.OK_INDEX + 1):
            link.send("btn_ok")
            _setup_back_cleanup()
            return None
        if b and 1 <= b <= 8:
            return map_range(b, 1, 8, out_min, out_max)
        time.sleep_ms(CFG.Timing.FAST_POLL_MS)


def wait_for_setup():
    st.in_setup = True
    try:
        while True:
            if cp.shutdown_held():
                shutdown_pico()

            b = cp.detect_press_raw()
            if b == (CFG.Buttons.OK_INDEX + 1):
                link.send("btn_ok")
                _setup_back_cleanup()
                return

            msg = link.read()
            if not msg:
                time.sleep_ms(CFG.Timing.POLL_MS)
                continue

            if msg.startswith("heyArduinodefault_strength_"):
                try:
                    st.default_strength = int(msg.split("_")[-1])
                except Exception:
                    pass
                continue
            if msg.startswith("heyArduinodefault_time_"):
                try:
                    st.default_move_time = int(msg.split("_")[-1])
                except Exception:
                    pass
                continue

            if msg.startswith("heyArduinoEngineStrength"):
                cp.profile.vs_strength_time()
                board.prompt_strength()
                v = select_singlepress(1, 20)
                if v is None:
                    return
                link.send(str(v))
                time.sleep_ms(CFG.Timing.SETUP_TRANSITION_MS)
                return

            if msg.startswith("heyArduinoTimeControl"):
                cp.profile.vs_strength_time()
                board.prompt_time()
                v = select_singlepress(1000, 8000)
                if v is None:
                    return
                link.send(str(v))
                time.sleep_ms(CFG.Timing.SETUP_TRANSITION_MS)
                return

            if msg.startswith("heyArduinoPlayerColor"):
                board.markings()
                cp.show_coords_top(WHITE)
                # color choice (1..3, OK back)
                cp.profile.vs_color()
                cp.reset_edges()
                while True:
                    if cp.shutdown_held():
                        shutdown_pico()
                    b2 = cp.detect_press_allowed()
                    if b2 == (CFG.Buttons.OK_INDEX + 1):
                        link.send("btn_ok")
                        _setup_back_cleanup()
                        return
                    if b2 == 1:
                        link.send("s1")
                        return
                    if b2 == 2:
                        link.send("s2")
                        return
                    if b2 == 3:
                        link.send("s3")
                        return
                    time.sleep_ms(CFG.Timing.FAST_POLL_MS)

            if msg.startswith("heyArduinoSetupComplete"):
                st.game_state = Game.RUNNING
                st.in_setup = False
                st.suspend_until_new_game = False
                return
    finally:
        cp.enable_hint_irq()


# ============================================================
# PROMOTION
# ============================================================


def handle_promotion_choice():
    board.scene_promotion()
    cp.show_coords_top(MAGENTA)
    cp.reset_edges()
    try:
        while True:
            if cp.shutdown_held():
                shutdown_pico()
            if process_hint_irq() == "new":
                return
            b = cp.detect_press_raw()
            if not b:
                time.sleep_ms(CFG.Timing.FAST_POLL_MS)
                continue
            if b == 1:
                link.send("btn_q")
                break
            if b == 2:
                link.send("btn_r")
                break
            if b == 3:
                link.send("btn_b")
                break
            if b == 4:
                link.send("btn_n")
                break
    finally:
        cp.clear_header()
        cp.apply(force=True)
        board.markings()


# ============================================================
# PUZZLE SETUP GUIDANCE (Pi-driven)
# ============================================================


def handle_puzzle_setup_cmd(msg):
    if not msg:
        return False

    if msg.startswith("heyArduinopuzzle_setup_begin"):
        st.puzzle_setup_active = True
        cp.disable_hint_irq()
        cp.reset_edges()
        border.on(force=True)
        cp.only_ok(True)
        board.markings()
        return True

    if msg.startswith("heyArduinopuzzle_setup_done"):
        st.puzzle_setup_active = False
        st.game_state = Game.RUNNING
        st.in_setup = False
        st.suspend_until_new_game = False
        cp.profile.puzzle_play()
        board.markings()
        cp.enable_hint_irq()
        return True

    if not st.puzzle_setup_active:
        return False

    if msg.startswith("heyArduinosetup_clear"):
        board.markings()
        return True

    if msg.startswith("heyArduinosetup_place_"):
        tail = msg[len("heyArduinosetup_place_") :].strip()
        parts = tail.split("_")
        sq = parts[0].strip() if parts else ""
        side = parts[1].strip().lower() if len(parts) > 1 else "w"
        color = GREEN if side.startswith("w") else ENGINE_COLOR
        board.markings()
        xy = board.algebraic_to_xy(sq)
        if xy:
            x, y = xy
            for _ in range(2):
                board.set_square(x, y, color)
                board.write()
                time.sleep_ms(200)
                board.set_square(x, y, BLACK)
                board.write()
                time.sleep_ms(200)
            board.set_square(x, y, color)
            board.write()
        return True

    if msg.startswith("heyArduinosetup_remove_"):
        sq = msg.split("_")[-1].strip()
        board.markings()
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
            board.set_square(x, y, RED)
            board.write()
        return True

    if msg.startswith("heyArduinosetup_move_"):
        tail = msg[len("heyArduinosetup_move_") :].strip()
        parts = tail.split("_")
        uci = parts[0].strip() if parts else ""
        side = parts[1].strip().lower() if len(parts) > 1 else "w"
        color = GREEN if side.startswith("w") else ENGINE_COLOR
        board.overlay_show(
            "setup", uci, cap=False, color_override=color, end_color=None
        )
        return True

    return False


# ============================================================
# MESSAGE ROUTER
# ============================================================


def _handle_ok_back_enable(_msg):
    st.ok_back_enabled = True
    cp.only_ok(True)


def _handle_ok_back_disable(_msg):
    st.ok_back_enabled = False
    cp.only_ok(False)


def _handle_hint_disable(_msg):
    st.hint_enabled = False


def _handle_hint_enable(_msg):
    st.hint_enabled = True


def _handle_check(msg):
    sq = msg.split("_", 1)[1].strip() if "_" in msg else ""
    if sq:
        board.blink_square_keep(sq, BLUE, times=1)


def _handle_gameover(msg):
    res = msg.split(":", 1)[1].strip() if ":" in msg else ""
    game_over_wait_ok_and_ack(res)


def _handle_reset_board(_msg):
    # conservative reset
    st.in_input = False
    st.in_setup = False
    st.persistent_trail_active = False
    st.persistent_trail_type = None
    st.persistent_trail_move = None
    cp.disable_hint_irq()
    cp.reset_edges()
    cp.off()
    board.markings()


def _handle_choose_mode(_msg):
    cp.disable_hint_irq()
    cp.reset_edges()
    board.markings()
    cp.show_coords_top(WHITE)
    st.game_state = Game.SETUP
    select_game_mode()
    while st.game_state == Game.SETUP:
        wait_for_setup()


def _handle_menu_paged(_msg):
    cp.profile.menu_paged()
    cp.disable_hint_irq()
    cp.reset_edges()
    board.markings()
    # generic paged menu: 1..4 select, hint next, ok back
    while True:
        if cp.shutdown_held():
            shutdown_pico()
        b = cp.detect_press_allowed()
        if not b:
            time.sleep_ms(CFG.Timing.FAST_POLL_MS)
            continue
        if b == (CFG.Buttons.OK_INDEX + 1):
            link.send("btn_ok")
            _setup_back_cleanup()
            break
        if b == (CFG.Buttons.HINT_INDEX + 1):
            link.send("btn_hint")
            continue
        if 1 <= b <= 4:
            link.send(str(b))
            break
    board.markings()
    cp.enable_hint_irq()


def _handle_engine_move(msg):
    raw = msg[11:].strip()
    cap = raw.endswith("_cap")
    mv = raw[:-4] if cap else raw
    show_persistent_trail(
        mv, ENGINE_COLOR, "engine", end_color=(MAGENTA if cap else None)
    )
    cp.only_ok(True)
    st.engine_ack_pending = True
    st.pending_gameover_result = None
    st.buffered_turn_msg = None


def _handle_promotion_needed(_msg):
    handle_promotion_choice()


def _handle_hint_move(msg):
    raw = msg[len("heyArduinohint_") :].strip()
    cap = raw.endswith("_cap")
    best = raw[:-4] if cap else raw
    cp.only_ok(True)
    show_persistent_trail(best, YELLOW, "hint", end_color=(MAGENTA if cap else None))
    cp.reset_edges()


def _handle_puzzle_wrong(msg):
    raw = msg[len("heyArduinopuzzle_wrong_") :].strip()
    mv = "".join(ch for ch in raw if _is_alnum(ch))
    if len(mv) >= 4:
        mv = mv[:4]
        show_persistent_trail(mv, RED, "wrong", end_color=None)
        cp.only_ok(True)
        cp.reset_edges()
        while True:
            if cp.shutdown_held():
                shutdown_pico()
            if process_hint_irq() == "new":
                link.send("n")
                break
            b = cp.detect_press_raw()
            if b == (CFG.Buttons.OK_INDEX + 1):
                link.send("btn_ok")
                break
            time.sleep_ms(CFG.Timing.POLL_MS)
        cp.only_ok(False)
        clear_persistent_trail()
        board.markings()


def _handle_error(_msg):
    board.illegal_flash()
    cp.only_ok(False)


def _handle_turn(msg):
    turn_str = msg.split("_", 1)[1].strip().lower()
    if "w" in turn_str:
        st.current_turn = "W"
    elif "b" in turn_str:
        st.current_turn = "B"

    # Small window to catch immediate gameover
    t_start = time.ticks_ms()
    while (
        time.ticks_diff(time.ticks_ms(), t_start) < CFG.Timing.TURN_GAMEOVER_WINDOW_MS
    ):
        nxt = link.read()
        if not nxt:
            time.sleep_ms(CFG.Timing.FAST_POLL_MS)
            continue
        if nxt.startswith("heyArduinoGameOver"):
            _handle_gameover(nxt)
            return

    cp.only_input()
    collect_and_send_move()


ROUTES = [
    ("heyArduinook_back_enable", _handle_ok_back_enable),
    ("heyArduinook_back_disable", _handle_ok_back_disable),
    ("heyArduinohint_disable", _handle_hint_disable),
    ("heyArduinohint_enable", _handle_hint_enable),
    ("heyArduinocheck_", _handle_check),
    ("heyArduinoGameOver", _handle_gameover),
    ("heyArduinoResetBoard", _handle_reset_board),
    ("heyArduinoChooseMode", _handle_choose_mode),
    ("heyArduinoMenuPaged", _handle_menu_paged),
    ("heyArduinom", _handle_engine_move),
    ("heyArduinopromotion_choice_needed", _handle_promotion_needed),
    ("heyArduinohint_", _handle_hint_move),
    ("heyArduinopuzzle_wrong_", _handle_puzzle_wrong),
    ("heyArduinoerror", _handle_error),
    ("heyArduinoturn_", _handle_turn),
]


def dispatch_pi_message(msg):
    try:
        if handle_puzzle_setup_cmd(msg):
            return True
    except Exception:
        pass
    for prefix, fn in ROUTES:
        if msg.startswith(prefix):
            fn(msg)
            return True
    return False


# ============================================================
# MAIN LOOP
# ============================================================


def main_loop():
    while True:
        if cp.shutdown_held():
            shutdown_pico()

        # OK-as-back when enabled by Pi (e.g., online waiting)
        if (
            st.ok_back_enabled
            and (not st.puzzle_setup_active)
            and (not st.engine_ack_pending)
        ):
            b0 = cp.detect_press_raw()
            if b0 == (CFG.Buttons.OK_INDEX + 1):
                link.send("btn_ok")
                st.ok_back_enabled = False
                _setup_back_cleanup()
                time.sleep_ms(50)
                continue

        # Puzzle setup active: forward OK presses; accept OK+HINT cancel
        if st.puzzle_setup_active:
            msg_setup = link.read()
            if msg_setup:
                handle_puzzle_setup_cmd(msg_setup)

            if cp.BTN_OK.value() == 0 and cp.BTN_HINT.value() == 0:
                link.send("n")
                st.puzzle_setup_active = False
                cp.only_ok(False)
                cp.enable_hint_irq()
                cp.reset_edges()
                board.opening()
                time.sleep_ms(50)
                continue

            b = cp.detect_press_raw()
            if b == (CFG.Buttons.OK_INDEX + 1):
                link.send("btn_ok")
            time.sleep_ms(CFG.Timing.POLL_MS)
            continue

        # HINT IRQ
        if process_hint_irq() == "new":
            cp.disable_hint_irq()
            cp.off()
            board.opening()
            st.engine_ack_pending = False
            st.pending_gameover_result = None
            st.buffered_turn_msg = None
            continue

        # Engine move ACK stage
        if st.engine_ack_pending:
            nxt = link.read()

            if nxt and nxt.startswith("heyArduinoGameOver"):
                st.pending_gameover_result = (
                    nxt.split(":", 1)[1].strip() if ":" in nxt else ""
                )
                while cp.BTN_OK.value() == 0:
                    time.sleep_ms(CFG.Timing.POLL_MS)
                time.sleep_ms(CFG.Timing.ENGINE_ACK_POST_MS)
                cp.reset_edges()
                while True:
                    b = cp.detect_press_raw()
                    if b == (CFG.Buttons.OK_INDEX + 1):
                        cp.only_ok(False)
                        break
                    time.sleep_ms(15)
                st.engine_ack_pending = False
                game_over_wait_ok_and_ack(st.pending_gameover_result)
                st.pending_gameover_result = None
                st.buffered_turn_msg = None
                continue

            if nxt and nxt.startswith("heyArduinoturn_"):
                st.buffered_turn_msg = nxt

            b = cp.detect_press_raw()
            if b == (CFG.Buttons.OK_INDEX + 1):
                link.send("btn_ok")
                st.engine_ack_pending = False
                cp.only_ok(False)
                clear_persistent_trail()

                if st.buffered_turn_msg:
                    turn_str = st.buffered_turn_msg.split("_", 1)[1].strip().lower()
                    if "w" in turn_str:
                        st.current_turn = "W"
                    elif "b" in turn_str:
                        st.current_turn = "B"
                    st.buffered_turn_msg = None

                cp.only_input()
                collect_and_send_move()
                continue

            time.sleep_ms(CFG.Timing.POLL_MS)
            continue

        msg = link.read()
        if msg and handle_puzzle_setup_cmd(msg):
            continue

        if not msg:
            time.sleep_ms(CFG.Timing.POLL_MS)
            continue

        if st.suspend_until_new_game or st.game_state != Game.RUNNING:
            if not (
                msg.startswith("heyArduinoChooseMode")
                or msg.startswith("heyArduinoResetBoard")
            ):
                continue

        dispatch_pi_message(msg)


# ============================================================
# ENTRY POINT
# ============================================================


def run():
    cp.off(force=True)
    board.off()
    cp.reset_edges()

    cp.disable_hint_irq()
    wait_for_mode_request()
    board.markings()
    select_game_mode()

    while st.game_state == Game.SETUP:
        wait_for_setup()

    while True:
        main_loop()


run()
