# ============================================================
#  PICO FIRMWARE (2026) - OO Standardized Architecture
#
#  Components (consistent verbs):
#    - cp      : Control panel LEDs + Buttons (cp.off(), cp.profile_*, cp.only_ok(), cp.only_input())
#    - border  : Border coordinate LEDs (border.on(), border.off())
#    - board   : Chessboard LEDs + animations + overlays (board.off(), board.markings(), board.overlay_show())
#    - screen  : UART "screen" messaging to the Pi (screen.typing_from(), screen.typing_to(), screen.typing_confirm())
#    - link    : UART link (link.send(), link.read())
#    - app     : Orchestrator (routing + loops)
#
#  Notes:
#    - This keeps your existing UART protocol strings intact: "heypi..." and "heyArduino..."
#    - Behavioral intent is preserved; the refactor focuses on structure, naming, and ownership.
# ============================================================

from __future__ import annotations

from machine import Pin, UART
import time
import neopixel


# ============================================================
# CONFIG
# ============================================================


class CFG:
    # ----------------------------
    # BUTTONS
    # ----------------------------
    class Buttons:
        PINS = [2, 3, 4, 5, 10, 8, 7, 6, 9, 11]  # 1..8 coords, 9=OK, 10=HINT
        OK_INDEX = 8  # 0-based into PINS
        HINT_INDEX = 9  # 0-based into PINS
        SHUTDOWN_INDEX = 7  # 0-based into PINS -> physical button 8 (H/8)

        DEBOUNCE_MS = 150
        LONG_PRESS_MS = 500
        HOLD_DRAW_MS = 2000
        SHUTDOWN_HOLD_MS = 2000

    # ----------------------------
    # LEDS
    # ----------------------------
    class LEDs:
        # Control panel strip (buttons + OK + hint + border coords)
        PANEL_PIN = 16
        PANEL_COUNT = 22

        # Zones within the panel strip
        CP_ZONE = (0, 6)  # 0..5
        BORDER_ZONE = (6, 22)  # 6..21

        # Chessboard strip
        BOARD_PIN = 22
        BOARD_W = 8
        BOARD_H = 8

        # Matrix orientation
        ORIGIN_BOTTOM_RIGHT = True
        ZIGZAG = True

        # Panel roles inside CP zone
        CP_OK_PIX = 4
        CP_HINT_PIX = 5

        # Border mapping inside panel strip
        FILES_LEDS = [6, 7, 8, 9, 10, 11, 12, 13]  # A..H
        RANKS_LEDS = [14, 15, 16, 17, 18, 19, 20, 21]  # 1..8

    # ----------------------------
    # COLORS
    # ----------------------------
    class Colors:
        BLACK = (0, 0, 0)
        WHITE = (255, 255, 255)
        DIMW = (10, 10, 10)
        RED = (255, 0, 0)
        GREEN = (0, 255, 0)
        BLUE = (0, 0, 255)
        CYAN = (0, 255, 255)
        MAGENTA = (255, 0, 255)
        YELLOW = (255, 255, 0)
        ORANGE = (255, 165, 0)

        ENGINE = BLUE
        BORDER_DIM = (40, 40, 40)


# Shorthand
C = CFG.Colors


# ============================================================
# UTILS
# ============================================================


class Utils:
    @staticmethod
    def is_alnum_1(ch: str) -> bool:
        if not ch or len(ch) != 1:
            return False
        o = ord(ch)
        return (48 <= o <= 57) or (65 <= o <= 90) or (97 <= o <= 122)

    @staticmethod
    def map_range(x: int, in_min: int, in_max: int, out_min: int, out_max: int) -> int:
        return int((x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)

    @staticmethod
    def parse_cap_suffix(raw: str) -> tuple[str, bool]:
        raw = (raw or "").strip()
        if raw.endswith("_cap"):
            return raw[:-4], True
        return raw, False


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
        self.persistent_trail_type = None  # 'engine'|'hint'|'wrong'
        self.persistent_trail_move = None
        self.persistent_trail_end_color = None


class UARTLink:
    def __init__(self):
        self.uart = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1), timeout=10)

    def send(self, kind: str, payload: str = "") -> None:
        # Preserve protocol: heypi{kind}{payload}\n
        self.uart.write(f"heypi{kind}{payload}\n".encode())

    def read(self) -> str | None:
        if self.uart.any():
            try:
                return self.uart.readline().decode().strip()
            except Exception:
                return None
        return None


class Screen:
    """UART-driven 'screen' API (Pi LCD messages)."""

    def __init__(self, link: UARTLink, st: State):
        self.link = link
        self.st = st

    def typing_from(self, text: str) -> None:
        if self.st.game_state != Game.RUNNING:
            return
        self.link.uart.write(f"heypityping_from_{text}\n".encode())

    def typing_to(self, move_from: str, partial_to: str) -> None:
        if self.st.game_state != Game.RUNNING:
            return
        self.link.uart.write(f"heypityping_to_{move_from} -> {partial_to}\n".encode())

    def typing_confirm(self, move_uci: str) -> None:
        if self.st.game_state != Game.RUNNING:
            return
        frm, to = move_uci[:2], move_uci[2:4]
        self.link.uart.write(f"heypityping_confirm_{frm} -> {to}\n".encode())


# ============================================================
# CONTROL PANEL + BUTTONS
# ============================================================


class ControlPanel:
    """Owns:
      - panel NeoPixel strip (PANEL_COUNT)
      - button pins + edge detection + gating
      - hint IRQ flag + suppression window
      - OK hold + shutdown hold helpers

    Exposes standardized verbs:
      - off(), apply_if_changed(), border(on/off), clear_header()
      - profile_*() and 'only_*' helpers
      - detect_press_raw(), detect_press_allowed()
    """

    def __init__(self, st: State):
        self.st = st

        # Buttons
        self.pins = [Pin(g, Pin.IN, Pin.PULL_UP) for g in CFG.Buttons.PINS]
        self.BTN_OK = self.pins[CFG.Buttons.OK_INDEX]
        self.BTN_HINT = self.pins[CFG.Buttons.HINT_INDEX]
        self.BTN_SHUT = self.pins[CFG.Buttons.SHUTDOWN_INDEX]

        self._last_btn = [1] * len(self.pins)
        self.allowed = None  # None or set[int] of button numbers 1..10

        # Panel LEDs
        self.panel = neopixel.NeoPixel(
            Pin(CFG.LEDs.PANEL_PIN, Pin.OUT), CFG.LEDs.PANEL_COUNT
        )
        self._panel_last = None

        # IRQ / holds
        self.hint_irq_flag = False
        self.suppress_hints_until_ms = 0

        self._ok_press_ms = None
        self._ok_fired = False

        self._shut_press_ms = None
        self._shut_fired = False

        self.enable_hint_irq()

    # ----------------------------
    # LEDs: write coalescing
    # ----------------------------
    def _snapshot(self):
        return [tuple(self.panel[i]) for i in range(CFG.LEDs.PANEL_COUNT)]

    def apply_if_changed(self, force: bool = False):
        cur = self._snapshot()
        if force or (self._panel_last is None) or (cur != self._panel_last):
            self.panel.write()
            self._panel_last = cur

    def off(self, force: bool = False):
        for i in range(CFG.LEDs.PANEL_COUNT):
            self.panel[i] = C.BLACK
        self.apply_if_changed(force=force)

    def clear_header(self):
        a, b = CFG.LEDs.CP_ZONE
        for i in range(a, b):
            self.panel[i] = C.BLACK

    # ----------------------------
    # Border LEDs
    # ----------------------------
    def border(self, on: bool = True, color=C.BORDER_DIM, force: bool = False):
        col = color if on else C.BLACK
        for idx in CFG.LEDs.FILES_LEDS + CFG.LEDs.RANKS_LEDS:
            if 0 <= idx < CFG.LEDs.PANEL_COUNT:
                self.panel[idx] = col
        self.apply_if_changed(force=force)

    # ----------------------------
    # Button edge detection & gating
    # ----------------------------
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
                return i + 1  # 1..10
        return None

    def detect_press_allowed(self):
        while True:
            b = self.detect_press_raw()
            if b is None:
                return None
            if self.allowed is None or b in self.allowed:
                return b
            time.sleep_ms(5)

    @staticmethod
    def is_non_coord_button(btn_num: int) -> bool:
        return btn_num in (9, 10)  # OK, HINT

    # ----------------------------
    # IRQ
    # ----------------------------
    def _hint_irq(self, pin):
        self.hint_irq_flag = True

    def disable_hint_irq(self):
        self.BTN_HINT.irq(handler=None)

    def enable_hint_irq(self):
        self.BTN_HINT.irq(trigger=Pin.IRQ_FALLING, handler=self._hint_irq)

    # ----------------------------
    # OK hold
    # ----------------------------
    def reset_ok_hold(self):
        self._ok_press_ms = None
        self._ok_fired = False

    def ok_long_hold_fired(self, hold_ms: int = CFG.Buttons.LONG_PRESS_MS) -> bool:
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
        self._ok_press_ms = None
        self._ok_fired = False
        return False

    # ----------------------------
    # Shutdown hold
    # ----------------------------
    def shutdown_held(self, hold_ms: int = CFG.Buttons.SHUTDOWN_HOLD_MS) -> bool:
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

    # ----------------------------
    # CP UX helpers (standardized)
    # ----------------------------
    def only_ok(self, on: bool = True):
        col = C.RED if (self.st.game_mode == Mode.ONLINE) else C.GREEN
        self.panel[0] = C.BLACK
        self.panel[1] = C.BLACK
        self.panel[2] = C.BLACK
        self.panel[3] = C.BLACK
        self.panel[CFG.LEDs.CP_OK_PIX] = col if on else C.BLACK
        self.panel[CFG.LEDs.CP_HINT_PIX] = C.BLACK
        self.apply_if_changed()

    def coords_top(self, color):
        self.border(False)
        self.panel[0] = color
        self.panel[1] = color
        self.panel[2] = C.BLACK
        self.panel[3] = C.BLACK
        self.apply_if_changed()

    def only_input(self):
        self.border(True)
        self.panel[0] = C.WHITE
        self.panel[1] = C.WHITE
        self.panel[2] = C.WHITE
        self.panel[3] = C.WHITE
        self.panel[CFG.LEDs.CP_OK_PIX] = C.RED
        self.panel[CFG.LEDs.CP_HINT_PIX] = C.YELLOW
        self.apply_if_changed()

    # Profiles
    def profile_main_menu(self):
        self.border(False)
        self.panel[0] = C.WHITE
        self.panel[1] = C.WHITE
        self.panel[2] = C.BLACK
        self.panel[3] = C.BLACK
        self.panel[CFG.LEDs.CP_OK_PIX] = C.BLACK
        self.panel[CFG.LEDs.CP_HINT_PIX] = C.BLACK
        self.apply_if_changed()
        self.set_allowed([1, 2, 3, 4])

    def profile_vs_strength_time(self):
        self.border(False)
        self.panel[0] = C.WHITE
        self.panel[1] = C.WHITE
        self.panel[2] = C.WHITE
        self.panel[3] = C.WHITE
        self.panel[CFG.LEDs.CP_OK_PIX] = C.RED
        self.panel[CFG.LEDs.CP_HINT_PIX] = C.BLACK
        self.apply_if_changed()
        self.set_allowed([1, 2, 3, 4, 5, 6, 7, 8, 9])

    def profile_vs_color(self):
        self.border(False)
        self.panel[0] = C.WHITE
        self.panel[1] = C.WHITE
        self.panel[2] = C.BLACK
        self.panel[3] = C.BLACK
        self.panel[CFG.LEDs.CP_OK_PIX] = C.RED
        self.panel[CFG.LEDs.CP_HINT_PIX] = C.BLACK
        self.apply_if_changed()
        self.set_allowed([1, 2, 3, 9])

    def profile_puzzle_top(self):
        self.border(False)
        self.panel[0] = C.WHITE
        self.panel[1] = C.WHITE
        self.panel[2] = C.BLACK
        self.panel[3] = C.BLACK
        self.panel[CFG.LEDs.CP_OK_PIX] = C.RED
        self.panel[CFG.LEDs.CP_HINT_PIX] = C.BLACK
        self.apply_if_changed()
        self.set_allowed([1, 2, 3, 9])

    def profile_menu_paged(self):
        self.border(False)
        self.panel[0] = C.WHITE
        self.panel[1] = C.WHITE
        self.panel[2] = C.BLACK
        self.panel[3] = C.BLACK
        self.panel[CFG.LEDs.CP_OK_PIX] = C.RED
        self.panel[CFG.LEDs.CP_HINT_PIX] = C.BLUE
        self.apply_if_changed()
        self.set_allowed([1, 2, 3, 4, 9, 10])

    def profile_only_ok_green(self):
        self.panel[0] = C.BLACK
        self.panel[1] = C.BLACK
        self.panel[2] = C.BLACK
        self.panel[3] = C.BLACK
        self.panel[CFG.LEDs.CP_OK_PIX] = C.GREEN
        self.panel[CFG.LEDs.CP_HINT_PIX] = C.BLACK
        self.apply_if_changed()
        self.set_allowed([9])

    def profile_puzzle_play(self):
        self.border(True)
        self.panel[0] = C.WHITE
        self.panel[1] = C.WHITE
        self.panel[2] = C.WHITE
        self.panel[3] = C.WHITE
        self.panel[CFG.LEDs.CP_OK_PIX] = C.RED
        self.panel[CFG.LEDs.CP_HINT_PIX] = C.YELLOW
        self.apply_if_changed()
        self.set_allowed([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])


class Border:
    def __init__(self, cp: ControlPanel):
        self.cp = cp

    def on(self, color=C.BORDER_DIM, force: bool = False):
        self.cp.border(True, color=color, force=force)

    def off(self, force: bool = False):
        self.cp.border(False, force=force)


# ============================================================
# CHESSBOARD
# ============================================================


class ChessBoard:
    def __init__(self):
        self.w, self.h = CFG.LEDs.BOARD_W, CFG.LEDs.BOARD_H
        self.origin_bottom_right = CFG.LEDs.ORIGIN_BOTTOM_RIGHT
        self.zigzag = CFG.LEDs.ZIGZAG
        self.np = neopixel.NeoPixel(Pin(CFG.LEDs.BOARD_PIN, Pin.OUT), self.w * self.h)

        # cache base markings (checker pattern)
        self._marking_cache = [C.BLACK] * (self.w * self.h)
        LIGHT = C.WHITE
        DARK = C.BLACK
        for y in range(self.h):
            for x in range(self.w):
                col = DARK if ((x + y) % 2 == 0) else LIGHT
                self._raw_set(x, y, col, into_cache=True)

        # overlay state
        self.overlay_active = False
        self.overlay_type = None
        self.overlay_move = None
        self._last_from_only = None

        self.off()

    # ---------- core ----------
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
        self.clear(C.BLACK)

    def clear(self, color=C.BLACK):
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

    # ---------- markings / scenes ----------
    def markings(self):
        self._last_from_only = None
        self.overlay_active = False
        self.overlay_type = None
        self.overlay_move = None
        for i in range(self.w * self.h):
            self.np[i] = self._marking_cache[i]
        self.write()

    def opening(self):
        self.clear(C.BLACK)
        for k in range(self.w + self.h - 1):
            for y in range(self.h):
                x = k - y
                if 0 <= x < self.w:
                    self.set_square(x, y, C.GREEN)
            self.write()
            time.sleep_ms(25)
        time.sleep_ms(150)
        self.markings()

    def loading_step(self, count):
        total = self.w * self.h
        if count >= total:
            return count
        idx = count
        y = idx // self.w
        x = (self.w - 1) - (idx % self.w)
        self.set_square(x, y, C.BLUE)
        self.write()
        return count + 1

    def illegal(self, hold_ms=700):
        for i in range(self.w * self.h):
            self.np[i] = C.BLUE
        self.write()
        time.sleep_ms(hold_ms)
        for _ in range(3):
            for i in range(8):
                self.set_square(i, i, C.RED)
                self.set_square(i, 7 - i, C.RED)
            self.write()
            time.sleep_ms(hold_ms)
            for i in range(8):
                self.set_square(i, i, C.BLUE)
                self.set_square(i, 7 - i, C.BLUE)
            self.write()
            time.sleep_ms(hold_ms)
        self.markings()

    def _hline(self, x, y, length, color):
        for dx in range(length):
            self.set_square(x + dx, y, color)

    def _vline(self, x, y, length, color):
        for dy in range(length):
            self.set_square(x, y + dy, color)

    def prompt_time(self):
        self.clear(C.BLACK)
        pts = [(2, 6), (3, 6), (4, 6), (5, 6), (4, 5), (4, 4), (4, 3), (4, 2)]
        for x, y in pts:
            self.set_square(x, y, C.MAGENTA)
        self.write()

    def prompt_strength(self):
        self.clear(C.BLACK)
        pts = [(2, 6), (2, 5), (2, 4), (2, 3), (2, 2), (3, 2), (4, 2), (5, 2)]
        for x, y in pts:
            self.set_square(x, y, C.MAGENTA)
        self.write()

    def scene_gameover(self):
        for i in range(self.w * self.h):
            self.np[i] = C.GREEN
        self.write()
        for y in range(self.h):
            self.set_square(2, y, C.WHITE)
            self.set_square(5, y, C.WHITE)
        for x in range(self.w):
            self.set_square(x, 2, C.WHITE)
            self.set_square(x, 5, C.WHITE)
        self.write()

    def scene_promotion(self):
        for i in range(self.w * self.h):
            self.np[i] = C.MAGENTA
        self.write()
        self._vline(2, 1, 6, C.WHITE)
        self._hline(2, 6, 4, C.WHITE)
        self._hline(2, 4, 4, C.WHITE)
        self._vline(5, 5, 2, C.WHITE)
        self.write()

    # ---------- trails / overlays ----------
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
            path = []
            x, y = fx, fy
            for _ in range(adx + 1):
                path.append((x, y))
                x += sx
                y += sy
            return path

        # Knight "L" path
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
            # dedup
            out = []
            for p in path:
                if not out or out[-1] != p:
                    out.append(p)
            return out

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

    def overlay_show(
        self, role, move_uci, color_override=None, end_color=None, cap=False
    ):
        self.overlay_active = True
        self.overlay_type = role
        self.overlay_move = move_uci
        self.markings()

        col = (
            color_override
            if (color_override is not None)
            else (C.ENGINE if role == "engine" else C.YELLOW)
        )
        endc = end_color if (end_color is not None) else (C.MAGENTA if cap else None)
        self.draw_trail(move_uci, col, end_color=endc)

    def overlay_clear(self):
        self.overlay_active = False
        self.overlay_type = None
        self.overlay_move = None
        self._last_from_only = None
        self.markings()

    def preview_from(self, sq):
        if self._last_from_only == sq and not self.overlay_active:
            return
        self._last_from_only = sq
        self.markings()
        xy = self.algebraic_to_xy(sq)
        if xy:
            self.set_square(xy[0], xy[1], C.GREEN)
            self.write()

    def preview_trail(self, uci, cap=False):
        self._last_from_only = None
        self.markings()
        self.draw_trail(uci, C.GREEN, end_color=(C.MAGENTA if cap else None))

    def blink_square_keep(self, sq, color_on, times=1, on_ms=220, off_ms=140):
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
            self.set_square(x, y, C.BLACK)
            self.write()
            time.sleep_ms(off_ms)
        self.np[idx] = prev
        self.write()


# ============================================================
# INPUT COLLECTOR
# ============================================================


class InputCollector:
    def __init__(
        self,
        st: State,
        cp: ControlPanel,
        board: ChessBoard,
        screen: Screen,
        link: UARTLink,
    ):
        self.st = st
        self.cp = cp
        self.board = board
        self.screen = screen
        self.link = link

    def _probe_capture_with_pi(self, uci, timeout_ms=150) -> bool:
        self.st.preview_cap_flag = False
        self.link.send("capq_", uci)
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            msg = self.link.read()
            if not msg:
                time.sleep_ms(5)
                continue
            if msg.startswith("heyArduinocapr_"):
                val = msg.split("_", 1)[1].strip()
                self.st.preview_cap_flag = val.startswith("1")
                return self.st.preview_cap_flag
        return False

    def enter_from(self, app, seed_btn=None, preset_col=None):
        if self.st.game_state != Game.RUNNING:
            return None

        self.cp.reset_ok_hold()

        if self.cp.shutdown_held():
            app.shutdown()

        # If persistent overlay is up, first press dismisses it.
        if self.st.persistent_trail_active:
            while True:
                if self.cp.shutdown_held():
                    app.shutdown()
                msg = self.link.read()
                if msg:
                    if app.handle_overlay_or_gameover(msg) == "gameover":
                        return None
                b = self.cp.detect_press_raw()
                if not b:
                    time.sleep_ms(5)
                    continue
                app.clear_persistent_trail()
                if 1 <= b <= 8:
                    seed_btn = b
                break
            self.cp.only_input()
            self.cp.reset_edges()

        col = None
        row = None

        if preset_col is not None:
            col = preset_col
            self.screen.typing_from(col)

        while col is None:
            if self.st.game_state != Game.RUNNING:
                return None

            if seed_btn is not None:
                b = seed_btn
                seed_btn = None
            else:
                if self.cp.shutdown_held():
                    app.shutdown()

                irq = app.process_hint_irq()
                if irq == "new":
                    return None

                msg = self.link.read()
                if msg:
                    outcome = app.handle_overlay_or_gameover(msg)
                    if outcome == "gameover":
                        return None
                    if outcome in ("hint", "engine"):
                        self.cp.reset_edges()
                        return None

                b = self.cp.detect_press_raw()
                if not b:
                    time.sleep_ms(5)
                    continue

            if ControlPanel.is_non_coord_button(b):
                continue

            col = chr(ord("a") + b - 1)
            self.screen.typing_from(col)

        while row is None:
            if self.st.game_state != Game.RUNNING:
                return None

            if self.cp.shutdown_held():
                app.shutdown()

            if self.cp.ok_long_hold_fired():
                self.screen.typing_from("")
                self.board.markings()
                # wait release
                while self.cp.BTN_OK.value() == 0:
                    if self.cp.shutdown_held():
                        app.shutdown()
                    irq = app.process_hint_irq()
                    if irq == "new":
                        break
                    time.sleep_ms(10)
                self.cp.reset_ok_hold()
                self.cp.reset_edges()
                return ("back_from", None)

            irq = app.process_hint_irq()
            if irq == "new":
                return None

            msg = self.link.read()
            if msg:
                outcome = app.handle_overlay_or_gameover(msg)
                if outcome == "gameover":
                    return None
                if outcome in ("hint", "engine"):
                    self.cp.reset_edges()
                    return None

            b = self.cp.detect_press_raw()
            if not b:
                time.sleep_ms(5)
                continue

            if ControlPanel.is_non_coord_button(b):
                continue

            row = str(b)
            self.screen.typing_from(col + row)

        frm = col + row
        self.board.preview_from(frm)
        return frm

    def enter_to(self, app, move_from, preset_col=None):
        if self.st.game_state != Game.RUNNING:
            return None

        self.cp.reset_ok_hold()

        if self.cp.shutdown_held():
            app.shutdown()

        if self.st.persistent_trail_active:
            while True:
                if self.cp.shutdown_held():
                    app.shutdown()
                msg = self.link.read()
                if msg:
                    if app.handle_overlay_or_gameover(msg) == "gameover":
                        return None
                b = self.cp.detect_press_raw()
                if not b:
                    time.sleep_ms(5)
                    continue
                app.clear_persistent_trail()
                if 1 <= b <= 8:
                    seed_btn = b
                break
            self.cp.only_input()
            self.cp.reset_edges()

        col = None
        row = None

        if preset_col is not None:
            if (
                isinstance(preset_col, str)
                and len(preset_col) == 1
                and ("a" <= preset_col <= "h")
            ):
                col = preset_col
                self.screen.typing_to(move_from, col)

        while col is None:
            if self.st.game_state != Game.RUNNING:
                return None

            if self.cp.shutdown_held():
                app.shutdown()

            if self.cp.ok_long_hold_fired():
                self.screen.typing_from(move_from[0])
                self.board.markings()
                while self.cp.BTN_OK.value() == 0:
                    if self.cp.shutdown_held():
                        app.shutdown()
                    irq = app.process_hint_irq()
                    if irq == "new":
                        break
                    time.sleep_ms(10)
                self.cp.reset_ok_hold()
                self.cp.reset_edges()
                return ("back_to_from_rank", move_from[0])

            irq = app.process_hint_irq()
            if irq == "new":
                return None

            msg = self.link.read()
            if msg:
                outcome = app.handle_overlay_or_gameover(msg)
                if outcome == "gameover":
                    return None
                if outcome in ("hint", "engine"):
                    self.cp.reset_edges()
                    return None

            b = self.cp.detect_press_raw()
            if not b:
                time.sleep_ms(5)
                continue
            if ControlPanel.is_non_coord_button(b):
                continue

            col = chr(ord("a") + b - 1)
            self.screen.typing_to(move_from, col)

        while row is None:
            if self.st.game_state != Game.RUNNING:
                return None

            if self.cp.shutdown_held():
                app.shutdown()

            if self.cp.ok_long_hold_fired():
                self.screen.typing_to(move_from, "")
                self.board.preview_from(move_from)
                while self.cp.BTN_OK.value() == 0:
                    if self.cp.shutdown_held():
                        app.shutdown()
                    irq = app.process_hint_irq()
                    if irq == "new":
                        break
                    time.sleep_ms(10)
                self.cp.reset_ok_hold()
                self.cp.reset_edges()
                return ("back_to_to_file", move_from)

            irq = app.process_hint_irq()
            if irq == "new":
                return None

            msg = self.link.read()
            if msg:
                outcome = app.handle_overlay_or_gameover(msg)
                if outcome == "gameover":
                    return None
                if outcome in ("hint", "engine"):
                    self.cp.reset_edges()
                    return None

            b = self.cp.detect_press_raw()
            if not b:
                time.sleep_ms(5)
                continue
            if ControlPanel.is_non_coord_button(b):
                continue

            row = str(b)
            self.screen.typing_to(move_from, col + row)

        to = col + row
        uci = move_from + to
        cap_prev = self._probe_capture_with_pi(uci)
        self.board.preview_trail(uci, cap=cap_prev)
        return to

    def confirm(self, app, move_uci):
        if self.st.game_state != Game.RUNNING:
            return None

        self.cp.only_ok(True)

        # wait OK release if held
        while self.cp.BTN_OK.value() == 0:
            if self.cp.shutdown_held():
                app.shutdown()
            irq = app.process_hint_irq()
            if irq == "new":
                self.cp.only_ok(False)
                return None
            time.sleep_ms(10)

        self.cp.reset_edges()
        self.screen.typing_confirm(move_uci)

        while True:
            if self.st.game_state != Game.RUNNING:
                self.cp.only_ok(False)
                return None

            if self.cp.shutdown_held():
                app.shutdown()

            irq = app.process_hint_irq()
            if irq == "new":
                self.cp.only_ok(False)
                return None

            msg = self.link.read()
            if msg:
                outcome = app.handle_overlay_or_gameover(msg)
                if outcome == "gameover":
                    self.cp.only_ok(False)
                    return None
                if outcome in ("hint", "engine"):
                    self.cp.reset_edges()
                    return None

            # OK pressed: short confirm, long backspace
            if self.cp.BTN_OK.value() == 0:
                t0 = time.ticks_ms()
                fired = False
                while self.cp.BTN_OK.value() == 0:
                    if self.cp.shutdown_held():
                        app.shutdown()
                    irq = app.process_hint_irq()
                    if irq == "new":
                        self.cp.only_ok(False)
                        return None
                    if (not fired) and time.ticks_diff(
                        time.ticks_ms(), t0
                    ) >= CFG.Buttons.LONG_PRESS_MS:
                        fired = True
                        partial = move_uci[:-1]
                        frm = partial[:2]
                        if len(partial) == 3:
                            self.screen.typing_to(frm, partial[2])
                        else:
                            self.screen.typing_to(frm, "")
                        self.board.preview_from(frm)
                    time.sleep_ms(10)

                held_ms = time.ticks_diff(time.ticks_ms(), t0)
                self.cp.reset_ok_hold()

                if fired:
                    self.cp.only_ok(False)
                    # wait release already happened; reset edges
                    self.cp.reset_edges()
                    return ("backspace_confirm", move_uci[:-1])

                if held_ms < CFG.Buttons.LONG_PRESS_MS:
                    self.cp.only_ok(False)
                    return "ok"

                self.cp.reset_edges()
                continue

            b = self.cp.detect_press_raw()
            if not b:
                time.sleep_ms(5)
                continue

            self.cp.only_ok(False)
            return ("redo", b)

    def collect_and_send(self, app):
        self.st.in_input = True
        try:
            seed = None
            preset_from_col = None

            while True:
                if self.cp.shutdown_held():
                    app.shutdown()

                self.cp.only_input()
                self.cp.reset_edges()

                move_from = self.enter_from(
                    app, seed_btn=seed, preset_col=preset_from_col
                )
                preset_from_col = None

                if isinstance(move_from, tuple) and move_from[0] == "back_from":
                    seed = None
                    continue

                if move_from is None:
                    if self.st.persistent_trail_active:
                        seed = None
                        continue
                    return

                seed = None

                move_to = self.enter_to(app, move_from)

                if isinstance(move_to, tuple):
                    tag = move_to[0]
                    if tag == "back_to_from_rank":
                        preset_from_col = move_to[1]
                        continue
                    if tag == "back_to_to_file":
                        self.cp.only_input()
                        self.cp.reset_edges()
                        move_to2 = self.enter_to(app, move_from)
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
                    if self.st.persistent_trail_active:
                        seed = None
                        continue
                    return

                move = move_from + move_to

                res = self.confirm(app, move)
                if res is None:
                    if self.st.persistent_trail_active:
                        seed = None
                        continue
                    return

                while isinstance(res, tuple) and res[0] == "backspace_confirm":
                    partial = res[1]

                    if len(partial) == 3:
                        frm = partial[:2]
                        to_file = partial[2]
                        self.cp.only_input()
                        self.cp.reset_edges()
                        self.cp.reset_ok_hold()

                        move_to3 = self.enter_to(app, frm, preset_col=to_file)
                        if isinstance(move_to3, tuple):
                            if move_to3[0] == "back_to_from_rank":
                                preset_from_col = move_to3[1]
                                res = ("restart_from", None)
                                break
                            if move_to3[0] == "back_to_to_file":
                                res = ("backspace_confirm", frm)
                                continue
                        if move_to3 is None:
                            res = ("restart_from", None)
                            break

                        move = frm + move_to3
                        res = self.confirm(app, move)
                        if res is None:
                            res = ("restart_from", None)
                            break
                        continue

                    if len(partial) == 2:
                        frm = partial
                        self.cp.only_input()
                        self.cp.reset_edges()
                        self.cp.reset_ok_hold()

                        move_to4 = self.enter_to(app, frm, preset_col=None)
                        if isinstance(move_to4, tuple):
                            if move_to4[0] == "back_to_from_rank":
                                preset_from_col = move_to4[1]
                                res = ("restart_from", None)
                                break
                            if move_to4[0] == "back_to_to_file":
                                res = ("backspace_confirm", frm)
                                continue
                        if move_to4 is None:
                            res = ("restart_from", None)
                            break

                        move = frm + move_to4
                        res = self.confirm(app, move)
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
                    self.link.send(move)  # preserve behavior: send move as kind
                    self.st.preview_cap_flag = False
                    self.board.markings()
                    return

                if isinstance(res, tuple) and res[0] == "redo":
                    cancel_btn = res[1]
                    seed = cancel_btn if (1 <= cancel_btn <= 8) else None
                    self.cp.only_input()
                    continue
        finally:
            self.st.in_input = False


# ============================================================
# APP (Orchestrator + Router)
# ============================================================


class App:
    def __init__(self):
        self.st = State()
        self.link = UARTLink()
        self.screen = Screen(self.link, self.st)

        self.cp = ControlPanel(self.st)
        self.border = Border(self.cp)
        self.board = ChessBoard()

        self.input = InputCollector(
            self.st, self.cp, self.board, self.screen, self.link
        )

        # Routing table (first match wins)
        self.routes = [
            ("heyArduinook_back_enable", self._on_ok_back_enable),
            ("heyArduinook_back_disable", self._on_ok_back_disable),
            ("heyArduinohint_disable", self._on_hint_disable),
            ("heyArduinohint_enable", self._on_hint_enable),
            ("heyArduinocheck_", self._on_check),
            ("heyArduinoGameOver", self._on_gameover),
            ("heyArduinoResetBoard", self._on_reset_board),
            ("heyArduinoChooseMode", self._on_choose_mode),
            ("heyArduinoChoosePuzzle", self._on_choose_puzzle),
            ("heyArduinoMenuPaged", self._on_menu_paged),
            ("heyArduinoGameStart", self._on_game_start),
            ("heyArduinom", self._on_engine_move),
            ("heyArduinopromotion_choice_needed", self._on_promotion_choice_needed),
            ("heyArduinohint_", self._on_hint_move),
            ("heyArduinopuzzle_wrong_", self._on_puzzle_wrong),
            ("heyArduinoerror", self._on_error),
            ("heyArduinoturn_", self._on_turn),
        ]

    # ----------------------------
    # Power / shutdown
    # ----------------------------
    def shutdown(self):
        self.link.send("xshutdown")
        for _ in range(2):
            self.cp.only_ok(True)
            self.board.clear(C.CYAN)
            time.sleep_ms(180)
            self.cp.only_ok(False)
            self.board.clear(C.BLACK)
            time.sleep_ms(180)

        self.cp.off(force=True)
        self.board.off()
        self.cp.disable_hint_irq()
        while True:
            time.sleep_ms(1000)

    # ----------------------------
    # Persistent overlays
    # ----------------------------
    def clear_persistent_trail(self):
        was_hint = self.st.persistent_trail_type == "hint"

        self.st.persistent_trail_active = False
        self.st.persistent_trail_type = None
        self.st.persistent_trail_move = None
        self.st.persistent_trail_end_color = None

        self.board.overlay_clear()

        if (
            was_hint
            and self.st.game_state == Game.RUNNING
            and (not self.st.engine_ack_pending)
        ):
            self.cp.only_input()

    def show_persistent_trail(self, move_uci, color, trail_type, end_color=None):
        self.st.persistent_trail_active = True
        self.st.persistent_trail_type = trail_type
        self.st.persistent_trail_move = move_uci
        self.st.persistent_trail_end_color = end_color

        cap = (end_color == C.MAGENTA) if (end_color is not None) else False
        role = "engine" if trail_type == "engine" else "hint"

        if trail_type == "hint":
            self.cp.only_ok(True)

        self.board.overlay_show(
            role, move_uci, color_override=color, end_color=end_color, cap=cap
        )

    # ----------------------------
    # Hint processing (incl new-game combo)
    # ----------------------------
    def process_hint_irq(self):
        if not self.st.hint_enabled:
            return None

        if not self.cp.hint_irq_flag:
            return None
        self.cp.hint_irq_flag = False

        if self.cp.shutdown_held():
            self.shutdown()

        now = time.ticks_ms()
        if time.ticks_diff(self.cp.suppress_hints_until_ms, now) > 0:
            return None

        # OK+HINT => new game
        if self.cp.BTN_OK.value() == 0 and self.cp.BTN_HINT.value() == 0:
            self.st.game_state = Game.SETUP
            self.link.send("n")

            self.st.suspend_until_new_game = True
            self.st.engine_ack_pending = False
            self.st.pending_gameover_result = None
            self.st.buffered_turn_msg = None

            self.cp.coords_top(C.WHITE)
            v = 0
            self.board.off()
            while v < (self.board.w * self.board.h):
                v = self.board.loading_step(v)
                time.sleep_ms(25)
            time.sleep_ms(350)
            self.board.markings()
            self.cp.suppress_hints_until_ms = time.ticks_add(now, 800)
            return "new"

        if self.st.game_state != Game.RUNNING:
            return None

        # hint hold => draw (online)
        if self.cp.BTN_HINT.value() == 0:
            t0 = time.ticks_ms()
            while self.cp.BTN_HINT.value() == 0:
                if time.ticks_diff(time.ticks_ms(), t0) >= CFG.Buttons.HOLD_DRAW_MS:
                    self.link.send("btn_draw")
                    return "draw"
                time.sleep_ms(10)

        self.link.send("btn_hint")
        return "hint"

    # ----------------------------
    # Overlay/gameover inlining (for input loops)
    # ----------------------------
    def handle_overlay_or_gameover(self, msg: str):
        if not msg:
            return None

        if msg.startswith("heyArduinoGameOver"):
            res = msg.split(":", 1)[1].strip() if ":" in msg else ""
            self.game_over_wait_ok_and_ack(res)
            return "gameover"

        if msg.startswith("heyArduinohint_"):
            raw = msg[len("heyArduinohint_") :].strip()
            mv, cap = Utils.parse_cap_suffix(raw)
            self.show_persistent_trail(
                mv, C.YELLOW, "hint", end_color=(C.MAGENTA if cap else None)
            )
            return "hint"

        if msg.startswith("heyArduinom"):
            raw = msg[11:].strip()
            mv, cap = Utils.parse_cap_suffix(raw)
            self.show_persistent_trail(
                mv, C.ENGINE, "engine", end_color=(C.MAGENTA if cap else None)
            )
            return "engine"

        return None

    # ----------------------------
    # Hard reset
    # ----------------------------
    def hard_reset_board(self):
        self.st.in_input = False
        self.st.in_setup = False
        self.st.persistent_trail_active = False
        self.st.persistent_trail_type = None
        self.st.persistent_trail_move = None
        self.cp.disable_hint_irq()
        self.cp.reset_edges()
        self.cp.off()
        self.board.markings()

    # ----------------------------
    # Game over ack
    # ----------------------------
    def game_over_wait_ok_and_ack(self, result_str: str):
        self.cp.disable_hint_irq()
        try:
            self.cp.reset_edges()
            self.cp.only_ok(True)
            self.board.scene_gameover()

            if self.cp.shutdown_held():
                self.shutdown()

            while self.cp.BTN_OK.value() == 0:
                time.sleep_ms(10)
            time.sleep_ms(200)
            self.cp.reset_edges()

            blink = False
            last = time.ticks_ms()
            while True:
                now = time.ticks_ms()
                if time.ticks_diff(now, last) > 400:
                    blink = not blink
                    a, b = CFG.LEDs.CP_ZONE
                    for i in range(a, b):
                        self.cp.panel[i] = C.BLACK
                    self.cp.panel[CFG.LEDs.CP_OK_PIX] = C.GREEN if blink else C.BLACK
                    self.cp.apply_if_changed()
                    last = now

                if self.cp.shutdown_held():
                    self.shutdown()

                b = self.cp.detect_press_raw()
                if b == (CFG.Buttons.OK_INDEX + 1):
                    self.cp.only_ok(False)
                    self.link.send("n")
                    break
                time.sleep_ms(20)

            self.board.markings()
        finally:
            self.cp.enable_hint_irq()

    # ----------------------------
    # Setup / selection flows
    # ----------------------------
    def wait_for_mode_request(self):
        self.board.opening()
        lit = 0
        while True:
            if self.cp.shutdown_held():
                self.shutdown()
            lit = self.board.loading_step(lit)
            time.sleep_ms(2000)
            msg = self.link.read()
            if not msg:
                continue
            if msg.startswith("heyArduinoChooseMode"):
                while lit < (self.board.w * self.board.h):
                    if self.cp.shutdown_held():
                        self.shutdown()
                    lit = self.board.loading_step(lit)
                    time.sleep_ms(15)
                self.board.markings()
                self.cp.coords_top(C.WHITE)
                self.st.game_state = Game.SETUP
                return

    def select_game_mode(self):
        self.cp.profile_main_menu()
        self.cp.reset_edges()
        while True:
            if self.cp.shutdown_held():
                self.shutdown()
            b = self.cp.detect_press_allowed()
            if not b:
                time.sleep_ms(5)
                continue
            if b == 1:
                self.st.game_mode = Mode.PC
                self.link.send("btn_mode_pc")
                return
            if b == 2:
                self.st.game_mode = Mode.ONLINE
                self.link.send("btn_mode_online")
                return
            if b == 3:
                self.st.game_mode = Mode.LOCAL
                self.link.send("btn_mode_local")
                return
            if b == 4:
                self.st.game_mode = Mode.PUZZLE
                self.link.send("btn_mode_puzzles")
                return

    def _setup_back_cleanup(self):
        self.st.in_setup = False
        self.st.game_state = Game.IDLE
        self.st.suspend_until_new_game = False
        try:
            self.board.markings()
        except Exception:
            pass
        try:
            self.cp.reset_edges()
        except Exception:
            pass

    def select_puzzle_variant(self):
        self.cp.reset_edges()
        while True:
            if self.cp.shutdown_held():
                self.shutdown()
            b = self.cp.detect_press_allowed()
            if not b:
                time.sleep_ms(5)
                continue
            if b == (CFG.Buttons.OK_INDEX + 1):
                self.link.send("btn_ok")
                self._setup_back_cleanup()
                return
            if 1 <= b <= 3:
                self.link.send(str(b))
                return

    def select_paged_menu_1to4(self):
        self.cp.profile_menu_paged()
        self.cp.reset_edges()
        while True:
            if self.cp.shutdown_held():
                self.shutdown()
            b = self.cp.detect_press_allowed()
            if not b:
                time.sleep_ms(5)
                continue
            if b == (CFG.Buttons.OK_INDEX + 1):
                self.link.send("btn_ok")
                self._setup_back_cleanup()
                return
            if b == (CFG.Buttons.HINT_INDEX + 1):
                self.link.send("btn_hint")
                continue
            if 1 <= b <= 4:
                self.link.send(str(b))
                return

    def select_singlepress(self, out_min, out_max):
        self.cp.reset_edges()
        while True:
            if self.cp.shutdown_held():
                self.shutdown()
            b = self.cp.detect_press_allowed()
            if b == (CFG.Buttons.OK_INDEX + 1):
                self.link.send("btn_ok")
                self._setup_back_cleanup()
                return None
            if b and 1 <= b <= 8:
                return Utils.map_range(b, 1, 8, out_min, out_max)
            time.sleep_ms(5)

    def select_color_choice(self):
        self.cp.profile_vs_color()
        self.cp.reset_edges()
        while True:
            if self.cp.shutdown_held():
                self.shutdown()
            b = self.cp.detect_press_allowed()
            if b == (CFG.Buttons.OK_INDEX + 1):
                self.link.send("btn_ok")
                self._setup_back_cleanup()
                return
            if b == 1:
                self.link.send("s1")
                return
            if b == 2:
                self.link.send("s2")
                return
            if b == 3:
                self.link.send("s3")
                return

    def wait_for_setup(self):
        self.st.in_setup = True
        try:
            while True:
                if self.cp.shutdown_held():
                    self.shutdown()

                b = self.cp.detect_press_raw()
                if b == (CFG.Buttons.OK_INDEX + 1):
                    self.link.send("btn_ok")
                    self._setup_back_cleanup()
                    return

                msg = self.link.read()
                if not msg:
                    time.sleep_ms(10)
                    continue

                if msg.startswith("heyArduinodefault_strength_"):
                    try:
                        self.st.default_strength = int(msg.split("_")[-1])
                    except Exception:
                        pass
                    continue

                if msg.startswith("heyArduinodefault_time_"):
                    try:
                        self.st.default_move_time = int(msg.split("_")[-1])
                    except Exception:
                        pass
                    continue

                if msg.startswith("heyArduinoEngineStrength"):
                    self.cp.profile_vs_strength_time()
                    self.board.prompt_strength()
                    v = self.select_singlepress(1, 20)
                    if v is None:
                        return
                    self.link.send(str(v))
                    time.sleep_ms(120)
                    return

                if msg.startswith("heyArduinoTimeControl"):
                    self.cp.profile_vs_strength_time()
                    self.board.prompt_time()
                    v = self.select_singlepress(1000, 8000)
                    if v is None:
                        return
                    self.link.send(str(v))
                    time.sleep_ms(120)
                    return

                if msg.startswith("heyArduinoPlayerColor"):
                    self.board.markings()
                    self.cp.coords_top(C.WHITE)
                    self.select_color_choice()
                    return

                if msg.startswith("heyArduinoSetupComplete"):
                    self.st.game_state = Game.RUNNING
                    self.st.in_setup = False
                    self.st.suspend_until_new_game = False
                    return
        finally:
            self.cp.enable_hint_irq()

    # ----------------------------
    # Promotion
    # ----------------------------
    def handle_promotion_choice(self):
        self.board.scene_promotion()
        self.cp.coords_top(C.MAGENTA)

        self.cp.reset_edges()
        try:
            while True:
                if self.cp.shutdown_held():
                    self.shutdown()
                irq = self.process_hint_irq()
                if irq == "new":
                    return
                b = self.cp.detect_press_raw()
                if not b:
                    time.sleep_ms(5)
                    continue
                if b == 1:
                    self.link.send("btn_q")
                    break
                if b == 2:
                    self.link.send("btn_r")
                    break
                if b == 3:
                    self.link.send("btn_b")
                    break
                if b == 4:
                    self.link.send("btn_n")
                    break
        finally:
            self.cp.clear_header()
            self.cp.apply_if_changed(force=True)
            self.board.markings()

    # ----------------------------
    # Puzzle setup (Pi-driven)
    # ----------------------------
    def handle_puzzle_setup_cmd(self, msg: str) -> bool:
        if not msg:
            return False

        if msg.startswith("heyArduinopuzzle_setup_begin"):
            self.cp.profile_only_ok_green()
            self.st.puzzle_setup_active = True
            self.cp.disable_hint_irq()
            self.cp.reset_edges()
            self.border.on(force=True)
            self.cp.only_ok(True)
            self.board.markings()
            return True

        if msg.startswith("heyArduinopuzzle_setup_done"):
            self.st.puzzle_setup_active = False
            self.st.game_state = Game.RUNNING
            self.st.in_setup = False
            self.st.suspend_until_new_game = False
            self.cp.profile_puzzle_play()
            self.board.markings()
            self.cp.enable_hint_irq()
            return True

        if not self.st.puzzle_setup_active:
            return False

        if msg.startswith("heyArduinosetup_clear"):
            self.board.markings()
            return True

        if msg.startswith("heyArduinosetup_place_"):
            tail = msg[len("heyArduinosetup_place_") :].strip()
            parts = tail.split("_")
            sq = parts[0].strip() if parts else ""
            side = parts[1].strip().lower() if len(parts) > 1 else "w"
            color = C.GREEN if side.startswith("w") else C.ENGINE
            self.board.markings()
            xy = self.board.algebraic_to_xy(sq)
            if xy:
                x, y = xy
                for _ in range(2):
                    self.board.set_square(x, y, color)
                    self.board.write()
                    time.sleep_ms(200)
                    self.board.set_square(x, y, C.BLACK)
                    self.board.write()
                    time.sleep_ms(200)
                self.board.set_square(x, y, color)
                self.board.write()
            return True

        if msg.startswith("heyArduinosetup_remove_"):
            sq = msg.split("_")[-1].strip()
            self.board.markings()
            xy = self.board.algebraic_to_xy(sq)
            if xy:
                x, y = xy
                for _ in range(3):
                    self.board.set_square(x, y, C.RED)
                    self.board.write()
                    time.sleep_ms(200)
                    self.board.set_square(x, y, C.BLACK)
                    self.board.write()
                    time.sleep_ms(200)
                self.board.set_square(x, y, C.RED)
                self.board.write()
            return True

        if msg.startswith("heyArduinosetup_move_"):
            tail = msg[len("heyArduinosetup_move_") :].strip()
            parts = tail.split("_")
            uci = parts[0].strip() if parts else ""
            side = parts[1].strip().lower() if len(parts) > 1 else "w"
            color = C.GREEN if side.startswith("w") else C.ENGINE
            self.board.overlay_show(
                "setup", uci, color_override=color, end_color=None, cap=False
            )
            return True

        return False

    # ----------------------------
    # Router helpers
    # ----------------------------
    def dispatch(self, msg: str) -> bool:
        # puzzle setup has priority
        try:
            if self.handle_puzzle_setup_cmd(msg):
                return True
        except Exception:
            pass
        for prefix, fn in self.routes:
            if msg.startswith(prefix):
                fn(msg)
                return True
        return False

    # ----------------------------
    # Route handlers
    # ----------------------------
    def _on_ok_back_enable(self, msg):
        self.st.ok_back_enabled = True
        self.cp.only_ok(True)

    def _on_ok_back_disable(self, msg):
        self.st.ok_back_enabled = False
        self.cp.only_ok(False)

    def _on_hint_disable(self, msg):
        self.st.hint_enabled = False

    def _on_hint_enable(self, msg):
        self.st.hint_enabled = True

    def _on_check(self, msg):
        sq = msg.split("_", 1)[1].strip() if "_" in msg else ""
        if sq:
            try:
                self.board.blink_square_keep(sq, C.BLUE, times=1, on_ms=220, off_ms=140)
            except Exception:
                pass

    def _on_gameover(self, msg):
        res = msg.split(":", 1)[1].strip() if ":" in msg else ""
        self.game_over_wait_ok_and_ack(res)

    def _on_reset_board(self, msg):
        self.hard_reset_board()

    def _on_choose_mode(self, msg):
        self.cp.disable_hint_irq()
        self.cp.reset_edges()
        self.board.markings()
        self.cp.coords_top(C.WHITE)
        self.st.game_state = Game.SETUP
        self.select_game_mode()
        while self.st.game_state == Game.SETUP:
            self.wait_for_setup()

    def _on_choose_puzzle(self, msg):
        self.cp.profile_puzzle_top()
        self.cp.disable_hint_irq()
        self.cp.reset_edges()
        self.board.markings()
        self.select_puzzle_variant()
        self.board.markings()
        self.cp.enable_hint_irq()

    def _on_menu_paged(self, msg):
        self.cp.profile_menu_paged()
        self.cp.disable_hint_irq()
        self.cp.reset_edges()
        self.board.markings()
        self.select_paged_menu_1to4()
        self.board.markings()
        self.cp.enable_hint_irq()

    def _on_game_start(self, msg):
        self.board.markings()

    def _on_engine_move(self, msg):
        raw = msg[11:].strip()
        mv, cap = Utils.parse_cap_suffix(raw)
        self.show_persistent_trail(
            mv, C.ENGINE, "engine", end_color=(C.MAGENTA if cap else None)
        )
        self.cp.only_ok(True)
        self.st.engine_ack_pending = True
        self.st.pending_gameover_result = None
        self.st.buffered_turn_msg = None

    def _on_promotion_choice_needed(self, msg):
        self.handle_promotion_choice()

    def _on_hint_move(self, msg):
        raw = msg[len("heyArduinohint_") :].strip()
        mv, cap = Utils.parse_cap_suffix(raw)
        self.cp.only_ok(True)
        self.show_persistent_trail(
            mv, C.YELLOW, "hint", end_color=(C.MAGENTA if cap else None)
        )
        self.cp.reset_edges()

    def _on_puzzle_wrong(self, msg):
        raw = msg[len("heyArduinopuzzle_wrong_") :].strip()
        mv = "".join(ch for ch in raw if Utils.is_alnum_1(ch))
        if len(mv) >= 4:
            mv = mv[:4]
            self.show_persistent_trail(mv, C.RED, "wrong", end_color=None)
            self.cp.only_ok(True)

            self.cp.reset_edges()
            while True:
                if self.cp.shutdown_held():
                    self.shutdown()
                irq = self.process_hint_irq()
                if irq == "new":
                    self.link.send("n")
                    break
                b = self.cp.detect_press_raw()
                if b == (CFG.Buttons.OK_INDEX + 1):
                    self.link.send("btn_ok")
                    break
                time.sleep_ms(10)

            self.cp.only_ok(False)
            self.clear_persistent_trail()
            self.board.markings()

    def _on_error(self, msg):
        self.board.illegal(hold_ms=700)
        self.cp.only_ok(False)

    def _on_turn(self, msg):
        turn_str = msg.split("_", 1)[1].strip().lower()
        if "w" in turn_str:
            self.st.current_turn = "W"
        elif "b" in turn_str:
            self.st.current_turn = "B"

        t_start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t_start) < 80:
            nxt = self.link.read()
            if not nxt:
                time.sleep_ms(5)
                continue
            if nxt.startswith("heyArduinoGameOver"):
                self._on_gameover(nxt)
                return

        self.cp.only_input()
        self.input.collect_and_send(self)

    # ----------------------------
    # Main loop
    # ----------------------------
    def main_loop(self):
        while True:
            if self.cp.shutdown_held():
                self.shutdown()

            # OK-as-back when idle (Pi-controlled)
            if (
                self.st.ok_back_enabled
                and (not self.st.puzzle_setup_active)
                and (not self.st.engine_ack_pending)
            ):
                b0 = self.cp.detect_press_raw()
                if b0 == (CFG.Buttons.OK_INDEX + 1):
                    self.link.send("btn_ok")
                    self.st.ok_back_enabled = False
                    self._setup_back_cleanup()
                    time.sleep_ms(50)
                    continue

            # Puzzle setup mode (Pi-driven guidance)
            if self.st.puzzle_setup_active:
                msg_setup = self.link.read()
                if msg_setup:
                    self.handle_puzzle_setup_cmd(msg_setup)

                # allow OK+HINT cancel
                if self.cp.BTN_OK.value() == 0 and self.cp.BTN_HINT.value() == 0:
                    self.link.send("n")
                    self.st.puzzle_setup_active = False
                    self.cp.only_ok(False)
                    self.cp.enable_hint_irq()
                    self.cp.reset_edges()
                    self.board.opening()
                    time.sleep_ms(50)
                    continue

                b = self.cp.detect_press_raw()
                if b == (CFG.Buttons.OK_INDEX + 1):
                    self.link.send("btn_ok")
                time.sleep_ms(10)
                continue

            irq = self.process_hint_irq()
            if irq == "new":
                self.cp.disable_hint_irq()
                self.cp.off()
                self.board.opening()
                self.st.engine_ack_pending = False
                self.st.pending_gameover_result = None
                self.st.buffered_turn_msg = None
                continue

            # Engine ack pending
            if self.st.engine_ack_pending:
                nxt = self.link.read()

                if nxt and nxt.startswith("heyArduinoGameOver"):
                    self.st.pending_gameover_result = (
                        nxt.split(":", 1)[1].strip() if ":" in nxt else ""
                    )
                    while self.cp.BTN_OK.value() == 0:
                        time.sleep_ms(10)
                    time.sleep_ms(180)
                    self.cp.reset_edges()
                    while True:
                        b = self.cp.detect_press_raw()
                        if b == (CFG.Buttons.OK_INDEX + 1):
                            self.cp.only_ok(False)
                            break
                        time.sleep_ms(15)

                    self.st.engine_ack_pending = False
                    self.game_over_wait_ok_and_ack(
                        self.st.pending_gameover_result or ""
                    )
                    self.st.pending_gameover_result = None
                    self.st.buffered_turn_msg = None
                    continue

                if nxt and nxt.startswith("heyArduinoturn_"):
                    self.st.buffered_turn_msg = nxt

                b = self.cp.detect_press_raw()
                if b == (CFG.Buttons.OK_INDEX + 1):
                    self.link.send("btn_ok")
                    self.st.engine_ack_pending = False
                    self.cp.only_ok(False)
                    self.clear_persistent_trail()

                    if self.st.buffered_turn_msg:
                        turn_str = (
                            self.st.buffered_turn_msg.split("_", 1)[1].strip().lower()
                        )
                        if "w" in turn_str:
                            self.st.current_turn = "W"
                        elif "b" in turn_str:
                            self.st.current_turn = "B"
                        self.st.buffered_turn_msg = None

                    self.cp.only_input()
                    self.input.collect_and_send(self)
                    continue

                time.sleep_ms(10)
                continue

            msg = self.link.read()
            if msg and self.handle_puzzle_setup_cmd(msg):
                continue

            if not msg:
                time.sleep_ms(10)
                continue

            if self.st.suspend_until_new_game or self.st.game_state != Game.RUNNING:
                if not (
                    msg.startswith("heyArduinoChooseMode")
                    or msg.startswith("heyArduinoResetBoard")
                ):
                    continue

            self.dispatch(msg)

    # ----------------------------
    # Entry
    # ----------------------------
    def run(self):
        self.cp.off(force=True)
        self.board.off()
        self.cp.reset_edges()

        self.cp.disable_hint_irq()
        self.wait_for_mode_request()
        self.board.markings()
        self.select_game_mode()

        while self.st.game_state == Game.SETUP:
            self.wait_for_setup()

        while True:
            self.main_loop()


# ============================================================
# START
# ============================================================

App().run()
