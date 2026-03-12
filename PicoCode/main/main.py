from machine import Pin, UART, reset
import time
import neopixel
import ubinascii
import os as _uos


class Config:
    class UART:
        BAUD = 115200
        TX_PIN = 0
        RX_PIN = 1
        TIMEOUT_MS = 10

    class Buttons:
        PINS = [2, 3, 4, 5, 10, 8, 7, 6, 9, 11]  # 1..8 coords, 9=OK, 10=HINT
        OK_INDEX = 8
        HINT_INDEX = 9
        SHUTDOWN_INDEX = 7  # button "8"/H

        DEBOUNCE_MS = 200
        OK_LONG_PRESS_MS = 500
        HINT_HOLD_DRAW_MS = 2000
        SHUTDOWN_HOLD_MS = 2000

    class LEDs:
        PANEL_PIN = 16
        PANEL_COUNT = 22

        CP_ZONE_START = 0
        CP_ZONE_END = 6

        CP_OK_PIX = 4
        CP_HINT_PIX = 5

        FILES = [6, 7, 8, 9, 10, 11, 12, 13]
        RANKS = [14, 15, 16, 17, 18, 19, 20, 21]
        BORDER_COLOR = (40, 40, 40)

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
        CONFIRM_DELAY_MS = 0

        CONFIRM_OK_GRACE_MS = 250

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


# ── Boot-time brightness (persisted to flash, applied once at startup) ────────


def _load_brightness():
    try:
        with open("/brightness.txt", "r") as _f:
            return max(1, min(8, int(_f.read().strip())))
    except Exception:
        return 5


def _save_and_reset_brightness(val):
    """Persist brightness to flash then reboot so scaling takes effect."""
    val = max(1, min(8, int(val)))
    try:
        with open("/brightness.txt", "w") as _f:
            _f.write(str(val))
    except Exception:
        pass
    try:
        ack = "brightness_set_" + str(val)
        for _ in range(3):
            link.send(ack)
            time.sleep_ms(120)
    except Exception:
        pass
    reset()


def _scale(color, brightness):
    r, g, b = color
    f = brightness / 8.0
    return (int(r * f), int(g * f), int(b * f))


_brightness = _load_brightness()

# Scale BORDER_COLOR in-place so the ControlPanel.border() default parameter
# (evaluated at class-parse time, which is after this block) picks up the
# already-dimmed value.
Config.LEDs.BORDER_COLOR = _scale(Config.LEDs.BORDER_COLOR, _brightness)

# ── Color aliases (scaled once at boot) ───────────────────────────────────────
_C = Config.Colors
BLACK = _C.BLACK  # black is always (0,0,0), no scaling needed
WHITE = _scale(_C.WHITE, _brightness)
RED = _scale(_C.RED, _brightness)
GREEN = _scale(_C.GREEN, _brightness)
BLUE = _scale(_C.BLUE, _brightness)
CYAN = _scale(_C.CYAN, _brightness)
MAGENTA = _scale(_C.MAGENTA, _brightness)
YELLOW = _scale(_C.YELLOW, _brightness)
ENGINE_COLOR = _scale(_C.ENGINE, _brightness)


class Game:
    IDLE = 0
    SETUP = 1
    RUNNING = 2


# Button index → UCI promotion piece token for the promotion picker.
_PROMO = {1: "btn_q", 2: "btn_r", 3: "btn_b", 4: "btn_n"}


class State:
    def __init__(self):
        self.game_state = Game.IDLE
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


class UARTLink:
    def __init__(self):
        self.uart = UART(
            0,
            baudrate=Config.UART.BAUD,
            tx=Pin(Config.UART.TX_PIN),
            rx=Pin(Config.UART.RX_PIN),
            timeout=Config.UART.TIMEOUT_MS,
        )

    def send(self, kind, payload=""):
        self.uart.write(("heypi" + str(kind) + str(payload) + "\n").encode())

    def read(self):
        if self.uart.any():
            try:
                return self.uart.readline().decode().strip()
            except Exception:
                return None
        return None

    def write_raw(self, s):
        self.uart.write((s + "\n").encode())


class Screen:
    def __init__(self, link, st_):
        self.link = link
        self.st = st_

    def _ok(self):
        return self.st.game_state == Game.RUNNING

    def typing_from(self, text):
        if self._ok():
            self.link.write_raw("heypityping_from_" + text)

    def typing_to(self, move_from, partial_to):
        if self._ok():
            self.link.write_raw("heypityping_to_" + move_from + " -> " + partial_to)

    def typing_confirm(self, move_uci):
        if self._ok():
            frm, to = move_uci[:2], move_uci[2:4]
            self.link.write_raw("heypityping_confirm_" + frm + " -> " + to)

    def clear(self, kind):
        if kind == "confirm":
            time.sleep_ms(Config.Timing.CONFIRM_DELAY_MS)
        elif kind == "to":
            self.link.write_raw("heypityping_to_")
        elif kind == "from":
            self.link.write_raw("heypityping_from_")

    def wait_for_lcd_ack(
        self, expected_ack="heyArduinolcd_ack_confirm", timeout_ms=300
    ):
        print("[PICO ACK] waiting for:", expected_ack, "timeout_ms=", timeout_ms)
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        ok_seen = False

        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if cp.shutdown_held():
                _shutdown_pico()
            if cp.BTN_OK.value() == 0:
                ok_seen = True

            msg = self.link.read()
            if not msg:
                time.sleep_ms(Config.Timing.FAST_POLL_MS)
                continue

            print("[PICO ACK] got:", msg)

            if msg == expected_ack:
                print("[PICO ACK] matched, ok_seen =", ok_seen)
                return True, ok_seen

            if msg.startswith("heyArduinoGameOver"):
                print("[PICO ACK] interrupted by gameover")
                _handle_gameover(msg)
                return False, ok_seen

            if _handle_puzzle_setup_message(msg):
                continue

            if msg.startswith("heyArduinohint_") or msg.startswith("heyArduinom"):
                _handle_overlay_or_gameover(msg)
                continue

        print("[PICO ACK] timeout waiting for:", expected_ack, "ok_seen =", ok_seen)
        return False, ok_seen


link = UARTLink()
screen = Screen(link, st)


class Profiles:
    def __init__(self, cp):
        self.cp = cp

    def _apply(
        self,
        border_on,
        top,
        bottom,
        ok,
        hint,
        allowed,
        ok_color=GREEN,
        hint_color=YELLOW,
    ):
        self.cp.border(border_on)
        self.cp._set_cp_buttons(
            top=top,
            bottom=bottom,
            ok=ok,
            hint=hint,
            ok_color=ok_color,
            hint_color=hint_color,
        )
        self.cp.apply()
        self.cp.set_allowed(allowed)

    def vs_strength_time(self):
        self._apply(
            False, True, True, True, False, [1, 2, 3, 4, 5, 6, 7, 8, 9], ok_color=RED
        )

    def brightness(self):
        self._apply(
            False, True, True, True, False, [1, 2, 3, 4, 5, 6, 7, 8, 9], ok_color=RED
        )

    def vs_color(self):
        self._apply(False, True, False, True, False, [1, 2, 3, 9], ok_color=RED)

    def menu_paged(self):
        self._apply(
            False,
            True,
            False,
            True,
            True,
            [1, 2, 3, 4, 9, 10],
            ok_color=RED,
            hint_color=BLUE,
        )

    def puzzle_play(self):
        self._apply(
            True,
            True,
            True,
            True,
            True,
            [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            ok_color=RED,
            hint_color=YELLOW,
        )


class ControlPanel:
    def __init__(self, st_):
        self.st = st_
        self._panel = neopixel.NeoPixel(
            Pin(Config.LEDs.PANEL_PIN, Pin.OUT), Config.LEDs.PANEL_COUNT
        )
        self._panel_last = None

        self.pins = [Pin(g, Pin.IN, Pin.PULL_UP) for g in Config.Buttons.PINS]
        self.BTN_OK = self.pins[Config.Buttons.OK_INDEX]
        self.BTN_HINT = self.pins[Config.Buttons.HINT_INDEX]
        self._BTN_SHUT = self.pins[Config.Buttons.SHUTDOWN_INDEX]

        self._last_btn = [1] * len(self.pins)
        self._allowed = None

        self.hint_irq_flag = False
        self.suppress_hints_until_ms = 0

        self._ok_press_ms = None
        self._ok_fired = False
        self._shut_press_ms = None
        self._shut_fired = False

        self._confirm_ok_armed = False
        self._confirm_ok_latched = False
        self._confirm_ok_ms = None

        self.profile = Profiles(self)
        self.enable_hint_irq()
        self.BTN_OK.irq(trigger=Pin.IRQ_FALLING, handler=self._ok_irq)

    # ── confirm-OK capture ────────────────────────────────────────────────────

    def arm_confirm_ok(self):
        self._confirm_ok_armed = True
        self._confirm_ok_latched = False
        self._confirm_ok_ms = None

    def disarm_confirm_ok(self):
        self._confirm_ok_armed = False
        self._confirm_ok_latched = False
        self._confirm_ok_ms = None

    def consume_confirm_ok(self, window_ms=300):
        if self._confirm_ok_latched:
            self._confirm_ok_latched = False
            self._confirm_ok_ms = None
            return True
        if (
            self._confirm_ok_ms is not None
            and time.ticks_diff(time.ticks_ms(), self._confirm_ok_ms) <= window_ms
        ):
            self._confirm_ok_ms = None
            self._confirm_ok_latched = False
            return True
        return False

    def _ok_irq(self, pin):
        if self._confirm_ok_armed:
            self._confirm_ok_latched = True
            self._confirm_ok_ms = time.ticks_ms()

    # ── LED panel ─────────────────────────────────────────────────────────────

    def _snapshot(self):
        return [tuple(self._panel[i]) for i in range(Config.LEDs.PANEL_COUNT)]

    def apply(self, force=False):
        cur = self._snapshot()
        if force or self._panel_last is None or cur != self._panel_last:
            self._panel.write()
            self._panel_last = cur

    def off(self, force=False):
        for i in range(Config.LEDs.PANEL_COUNT):
            self._panel[i] = BLACK
        self.apply(force=force)

    def clear_header(self):
        for i in range(Config.LEDs.CP_ZONE_START, Config.LEDs.CP_ZONE_END):
            self._panel[i] = BLACK

    def border(self, on=True, color=Config.LEDs.BORDER_COLOR, force=False):
        col = color if on else BLACK
        for idx in Config.LEDs.FILES + Config.LEDs.RANKS:
            self._panel[idx] = col
        self.apply(force=force)

    def _set_cp_buttons(self, top, bottom, ok, hint, ok_color=GREEN, hint_color=YELLOW):
        self._panel[0] = WHITE if top else BLACK
        self._panel[1] = WHITE if top else BLACK
        self._panel[2] = WHITE if bottom else BLACK
        self._panel[3] = WHITE if bottom else BLACK
        self._panel[Config.LEDs.CP_OK_PIX] = ok_color if ok else BLACK
        self._panel[Config.LEDs.CP_HINT_PIX] = hint_color if hint else BLACK

    def only_ok(self, on=True):
        self._set_cp_buttons(False, False, ok=on, hint=False, ok_color=GREEN)
        self.apply()

    def only_input(self):
        self.border(True)
        self._set_cp_buttons(
            True, True, ok=True, hint=True, ok_color=RED, hint_color=YELLOW
        )
        self.apply()

    def show_coords_top(self, color=WHITE):
        self.border(False)
        self._panel[0] = color
        self._panel[1] = color
        self._panel[2] = BLACK
        self._panel[3] = BLACK
        self.apply()

    # ── button helpers ────────────────────────────────────────────────────────

    def reset_edges(self):
        for i, p in enumerate(self.pins):
            self._last_btn[i] = p.value()

    def set_allowed(self, btns):
        self._allowed = None if btns is None else set(int(x) for x in btns)
        self.reset_edges()

    def detect_press_raw(self):
        for i, p in enumerate(self.pins):
            cur = p.value()
            prev = self._last_btn[i]
            self._last_btn[i] = cur
            if prev == 1 and cur == 0:
                time.sleep_ms(Config.Buttons.DEBOUNCE_MS)
                return i + 1
        return None

    def detect_press_allowed(self):
        while True:
            b = self.detect_press_raw()
            if b is None:
                return None
            if self._allowed is None or b in self._allowed:
                return b
            time.sleep_ms(Config.Timing.FAST_POLL_MS)

    @staticmethod
    def is_non_coord_button(b):
        return b in (9, 10)

    # ── hint IRQ ──────────────────────────────────────────────────────────────

    def _hint_irq(self, pin):
        self.hint_irq_flag = True

    def disable_hint_irq(self):
        self.BTN_HINT.irq(handler=None)

    def enable_hint_irq(self):
        self.BTN_HINT.irq(trigger=Pin.IRQ_FALLING, handler=self._hint_irq)

    # ── OK long-hold ──────────────────────────────────────────────────────────

    def reset_ok_hold(self):
        self._ok_press_ms = None
        self._ok_fired = False

    def ok_long_hold_fired(self, hold_ms=Config.Buttons.OK_LONG_PRESS_MS):
        if self.BTN_OK.value() == 0:
            if self._ok_press_ms is None:
                self._ok_press_ms = time.ticks_ms()
                self._ok_fired = False
            if (
                not self._ok_fired
                and time.ticks_diff(time.ticks_ms(), self._ok_press_ms) >= hold_ms
            ):
                self._ok_fired = True
                return True
            return False
        self.reset_ok_hold()
        return False

    # ── shutdown hold ─────────────────────────────────────────────────────────

    def wait_for_ok_release(self):
        while self.BTN_OK.value() == 0:
            time.sleep_ms(Config.Timing.POLL_MS)

    def set_ok_blink(self, on):
        self.clear_header()
        self._panel[Config.LEDs.CP_OK_PIX] = GREEN if on else BLACK
        self.apply()

    def shutdown_held(self, hold_ms=Config.Buttons.SHUTDOWN_HOLD_MS):
        if self._BTN_SHUT.value() == 0:
            if self._shut_press_ms is None:
                self._shut_press_ms = time.ticks_ms()
                self._shut_fired = False
            if (
                not self._shut_fired
                and time.ticks_diff(time.ticks_ms(), self._shut_press_ms) >= hold_ms
            ):
                self._shut_fired = True
                return True
            return False
        self._shut_press_ms = None
        self._shut_fired = False
        return False


cp = ControlPanel(st)


class ChessBoard:
    def __init__(self):
        self.w, self.h = Config.LEDs.W, Config.LEDs.H
        self.origin_bottom_right = Config.LEDs.ORIGIN_BOTTOM_RIGHT
        self.np = neopixel.NeoPixel(
            Pin(Config.LEDs.CHESS_PIN, Pin.OUT), self.w * self.h
        )

        self._marking_cache = [BLACK] * (self.w * self.h)
        for y in range(self.h):
            for x in range(self.w):
                col = BLACK if ((x + y) % 2 == 0) else WHITE
                self._raw_set(x, y, col, into_cache=True)
        self.off()

        self.overlay_active = False
        self.overlay_type = None
        self.overlay_move = None
        self._last_from_only = None

    def _xy_to_index(self, x, y):
        row = y
        if self.origin_bottom_right:
            col_index = (self.w - 1 - x) if (row % 2 == 0) else x
            return row * self.w + col_index
        row_top = (self.h - 1) - y
        col_index = x if (row_top % 2 == 0) else (self.w - 1 - x)
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
        if not ("a" <= f <= "h") or not ("1" <= r <= "8"):
            return None
        return (ord(f) - 97, int(r) - 1)

    def show_markings(self):
        for i in range(self.w * self.h):
            self.np[i] = self._marking_cache[i]
        self.write()

    def opening(self):
        self.clear(BLACK)
        for k in range(self.w + self.h - 1):
            for y in range(self.h):
                x = k - y
                if 0 <= x < self.w:
                    self.set_square(x, y, GREEN)
            self.write()
            time.sleep_ms(Config.Timing.LOADING_STEP_MS)
        time.sleep_ms(Config.Timing.LOADING_POST_MS)
        self.show_markings()

    def loading_step(self, count):
        total = self.w * self.h
        if count >= total:
            return count
        y = count // self.w
        x = (self.w - 1) - (count % self.w)
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
        for x, y in [(2, 6), (3, 6), (4, 6), (5, 6), (4, 5), (4, 4), (4, 3), (4, 2)]:
            self.set_square(x, y, MAGENTA)
        self.write()

    def prompt_strength(self):
        self.clear(BLACK)
        for x, y in [(2, 6), (2, 5), (2, 4), (2, 3), (2, 2), (3, 2), (4, 2), (5, 2)]:
            self.set_square(x, y, MAGENTA)
        self.write()

    def prompt_brightness(self):
        self.clear(BLACK)
        for i, x in enumerate(range(8)):
            color = CYAN if i < 4 else YELLOW
            self.set_square(x, i, color)
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
        dx, dy = tx - fx, ty - fy
        adx, ady = abs(dx), abs(dy)

        if fx == tx and fy != ty:
            sy = self._sgn(dy)
            return [(fx, y) for y in range(fy, ty + sy, sy)]
        if fy == ty and fx != tx:
            sx = self._sgn(dx)
            return [(x, fy) for x in range(fx, tx + sx, sx)]
        if adx == ady and adx != 0:
            sx, sy = self._sgn(dx), self._sgn(dy)
            return [(fx + i * sx, fy + i * sy) for i in range(adx + 1)]
        if (adx, ady) in ((1, 2), (2, 1)):
            sx, sy = self._sgn(dx), self._sgn(dy)
            path = [(fx, fy)]
            if ady == 2:
                path += [(fx, fy + sy), (fx, fy + 2 * sy), (fx + sx, fy + 2 * sy)]
            else:
                path += [(fx + sx, fy), (fx + 2 * sx, fy), (fx + 2 * sx, fy + sy)]
            if path[-1] != (tx, ty):
                path.append((tx, ty))
            # deduplicate consecutive
            ded = []
            for p in path:
                if not ded or ded[-1] != p:
                    ded.append(p)
            return ded
        return [(fx, fy), (tx, ty)]

    def draw_trail(self, uci, color, end_color=None):
        if not uci or len(uci) < 4:
            return
        path = self._path_squares(uci[:2], uci[2:4])
        for i, (x, y) in enumerate(path):
            c = end_color if (end_color and i == len(path) - 1) else color
            self.set_square(x, y, c)
        self.write()

    def blink_square_keep(
        self,
        sq,
        color_on,
        times=4,
        on_ms=Config.Timing.BLINK_ON_MS,
        off_ms=Config.Timing.BLINK_OFF_MS,
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
            if color_override is not None
            else (ENGINE_COLOR if role == "engine" else YELLOW)
        )
        endc = end_color if end_color is not None else (MAGENTA if cap else None)
        self.draw_trail(uci, col, end_color=endc)

    def overlay_clear(self):
        self.overlay_active = False
        self.overlay_type = None
        self.overlay_move = None
        self._last_from_only = None
        self.show_markings()

    def puzzle_blink(self, sq, color, times, on_ms=200, off_ms=200):
        xy = self.algebraic_to_xy(sq)
        if not xy:
            return
        x, y = xy
        for _ in range(times):
            self.set_square(x, y, color)
            self.write()
            time.sleep_ms(on_ms)
            self.set_square(x, y, BLACK)
            self.write()
            time.sleep_ms(off_ms)
        self.set_square(x, y, color)
        self.write()


board = ChessBoard()


# ── Utility helpers ───────────────────────────────────────────────────────────


def _is_alphanumeric(ch):
    if not ch or len(ch) != 1:
        return False
    o = ord(ch)
    return (48 <= o <= 57) or (65 <= o <= 90) or (97 <= o <= 122)


def _map_range(x, in_min, in_max, out_min, out_max):
    return int((x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)


# ── Overlay / trail ───────────────────────────────────────────────────────────


def _show_overlay(payload, color, trail_type):
    """Parse payload, record persistent trail state, and render on the board.

    Replaces the old parse_overlay_payload → show_overlay_from_payload →
    show_persistent_trail chain.
    """
    cap = payload.endswith("_cap")
    uci = payload[:-4] if cap else payload
    end_color = MAGENTA if cap else None

    st.persistent_trail_active = True
    st.persistent_trail_type = trail_type
    st.persistent_trail_move = uci
    st.persistent_trail_end_color = end_color

    if trail_type == "hint":
        cp.only_ok(True)

    role = "engine" if trail_type == "engine" else trail_type
    board.overlay_show(role, uci, cap=cap, color_override=color, end_color=end_color)


def _clear_persistent_trail():
    was_hint = st.persistent_trail_type == "hint"
    st.persistent_trail_active = False
    st.persistent_trail_type = None
    st.persistent_trail_move = None
    st.persistent_trail_end_color = None
    board.overlay_clear()
    if was_hint and st.game_state == Game.RUNNING and not st.engine_ack_pending:
        cp.only_input()


# ── Poll helpers ──────────────────────────────────────────────────────────────


def _tick_input_loop():
    """Single iteration of the standard input-loop guard.

    Returns one of:
      ('new_game',)   – hint+OK combo triggered a new game
      ('gameover',)   – game-over message received
      ('overlay',)    – hint/engine overlay received (input loop should restart)
      ('btn', n)      – a button edge was detected
      None            – nothing happened this tick
    """
    if cp.shutdown_held():
        _shutdown_pico()
    irq = _handle_hint_irq()
    if irq == "new":
        return ("new_game",)
    msg = link.read()
    if msg:
        outcome = _handle_overlay_or_gameover(msg)
        if outcome == "gameover":
            return ("gameover",)
        if outcome in ("hint", "engine"):
            cp.reset_edges()
            return ("overlay",)
    b = cp.detect_press_raw()
    if b is not None:
        return ("btn", b)
    time.sleep_ms(Config.Timing.FAST_POLL_MS)
    return None


def _wait_for_trail_clear():
    """Block until any button is pressed while a persistent trail is displayed,
    then clear it.

    Returns:
      None         – no trail was active (caller should proceed normally)
      'gameover'   – game ended while waiting
      int (1-8)    – coord button that dismissed the trail (usable as seed)
      0            – non-coord button dismissed the trail
    """
    if not st.persistent_trail_active:
        return None
    while True:
        if cp.shutdown_held():
            _shutdown_pico()
        msg = link.read()
        if msg and _handle_overlay_or_gameover(msg) == "gameover":
            return "gameover"
        b = cp.detect_press_raw()
        if b is not None:
            _clear_persistent_trail()
            cp.only_input()
            cp.reset_edges()
            return b if 1 <= b <= 8 else 0
        time.sleep_ms(Config.Timing.FAST_POLL_MS)


# ── Capture probe ─────────────────────────────────────────────────────────────


def _check_if_move_captures(uci, timeout_ms=150):
    st.preview_cap_flag = False
    link.send("capq_", uci)
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        msg = link.read()
        if not msg:
            time.sleep_ms(Config.Timing.FAST_POLL_MS)
            continue
        if msg.startswith("heyArduinocapr_"):
            val = msg.split("_", 1)[1].strip()
            st.preview_cap_flag = val.startswith("1")
            return st.preview_cap_flag
    return False


# ── Hint / new-game IRQ ───────────────────────────────────────────────────────


def _handle_hint_irq():
    if not st.hint_enabled or not cp.hint_irq_flag:
        return None
    cp.hint_irq_flag = False

    if cp.shutdown_held():
        _shutdown_pico()

    now = time.ticks_ms()
    if time.ticks_diff(cp.suppress_hints_until_ms, now) > 0:
        return None

    # Both buttons held → new game
    if cp.BTN_OK.value() == 0 and cp.BTN_HINT.value() == 0:
        st.game_state = Game.SETUP
        st.suspend_until_new_game = True
        st.engine_ack_pending = False
        st.pending_gameover_result = None
        st.buffered_turn_msg = None
        link.send("n")

        cp.show_coords_top(WHITE)
        board.off()
        v = 0
        while v < (board.w * board.h):
            v = board.loading_step(v)
            time.sleep_ms(Config.Timing.LOADING_STEP_MS)
        time.sleep_ms(Config.Timing.LOADING_POST_MS)
        board.markings()
        cp.suppress_hints_until_ms = time.ticks_add(
            now, Config.Timing.NEW_GAME_SUPPRESS_MS
        )
        return "new"

    if st.game_state != Game.RUNNING:
        return None

    if cp.BTN_HINT.value() == 0:
        t0 = time.ticks_ms()
        while cp.BTN_HINT.value() == 0:
            if time.ticks_diff(time.ticks_ms(), t0) >= Config.Buttons.HINT_HOLD_DRAW_MS:
                link.send("btn_draw")
                return "draw"
            time.sleep_ms(Config.Timing.POLL_MS)

    link.send("btn_hint")
    return "hint"


# ── Pi message overlay/gameover handler ───────────────────────────────────────


def _handle_overlay_or_gameover(msg):
    if not msg:
        return None
    if msg.startswith("heyArduinoGameOver"):
        res = msg.split(":", 1)[1].strip() if ":" in msg else ""
        _show_game_over_and_ack(res)
        return "gameover"
    if msg.startswith("heyArduinocheck_"):
        sq = msg.split("_", 1)[1].strip() if "_" in msg else ""
        if sq:
            board.blink_square_keep(sq, RED)
        return "check"
    if msg.startswith("heyArduinohint_"):
        _show_overlay(msg[len("heyArduinohint_") :], YELLOW, "hint")
        return "hint"
    if msg.startswith("heyArduinom"):
        _show_overlay(msg[len("heyArduinom") :], ENGINE_COLOR, "engine")
        return "engine"
    return None


# ── Move input ────────────────────────────────────────────────────────────────


def _select_from_square(seed_btn=None, preset_col=None):
    if st.game_state != Game.RUNNING:
        return None
    cp.reset_ok_hold()
    if cp.shutdown_held():
        _shutdown_pico()

    seed = _wait_for_trail_clear()
    if seed == "gameover":
        return None
    if seed:
        seed_btn = seed

    col = None
    if preset_col is not None:
        col = preset_col
        screen.typing_from(col)

    # ── collect file (column) ────────────────────────────────────────────────
    while col is None:
        if st.game_state != Game.RUNNING:
            return None
        if seed_btn is not None:
            b, seed_btn = seed_btn, None
        else:
            ev = _tick_input_loop()
            if ev is None:
                continue
            if ev[0] != "btn":
                return None  # new_game / gameover / overlay
            b = ev[1]
        if not cp.is_non_coord_button(b):
            col = chr(ord("a") + b - 1)
            screen.typing_from(col)

    # ── collect rank (row) ───────────────────────────────────────────────────
    while True:
        if st.game_state != Game.RUNNING:
            return None
        if cp.shutdown_held():
            _shutdown_pico()
        if cp.ok_long_hold_fired():
            screen.typing_from("")
            board.markings()
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(Config.Timing.POLL_MS)
            cp.reset_ok_hold()
            cp.reset_edges()
            return ("back_from", None)
        ev = _tick_input_loop()
        if ev is None:
            continue
        if ev[0] != "btn":
            return None
        b = ev[1]
        if not cp.is_non_coord_button(b):
            frm = col + str(b)
            screen.typing_from(frm)
            board.preview_from(frm)
            return frm


def _select_to_square(move_from, preset_col=None):
    if st.game_state != Game.RUNNING:
        return None
    cp.reset_ok_hold()
    if cp.shutdown_held():
        _shutdown_pico()

    seed = _wait_for_trail_clear()
    if seed == "gameover":
        return None
    if seed and preset_col is None:
        preset_col = chr(ord("a") + seed - 1)

    col = None
    if preset_col and len(preset_col) == 1 and "a" <= preset_col <= "h":
        col = preset_col
        screen.typing_to(move_from, col)

    # ── collect file (column) ────────────────────────────────────────────────
    while col is None:
        if st.game_state != Game.RUNNING:
            return None
        if cp.shutdown_held():
            _shutdown_pico()
        if cp.ok_long_hold_fired():
            screen.typing_from(move_from[0])
            board.markings()
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(Config.Timing.POLL_MS)
            cp.reset_ok_hold()
            cp.reset_edges()
            return ("back_to_from_rank", move_from[0])
        ev = _tick_input_loop()
        if ev is None:
            continue
        if ev[0] != "btn":
            return None
        b = ev[1]
        if not cp.is_non_coord_button(b):
            col = chr(ord("a") + b - 1)
            screen.typing_to(move_from, col)

    # ── collect rank (row) ───────────────────────────────────────────────────
    while True:
        if st.game_state != Game.RUNNING:
            return None
        if cp.shutdown_held():
            _shutdown_pico()
        if cp.ok_long_hold_fired():
            screen.typing_to(move_from, "")
            board.preview_from(move_from)
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(Config.Timing.POLL_MS)
            cp.reset_ok_hold()
            cp.reset_edges()
            return ("back_to_to_file", move_from)
        ev = _tick_input_loop()
        if ev is None:
            continue
        if ev[0] != "btn":
            return None
        b = ev[1]
        if not cp.is_non_coord_button(b):
            to = col + str(b)
            uci = move_from + to
            board.preview_trail(uci, cap=_check_if_move_captures(uci))
            screen.typing_to(move_from, to)
            return to


def _confirm_move(move):
    if st.game_state != Game.RUNNING:
        return None

    cp.only_ok(True)

    # Wait until OK is physically released before arming
    while cp.BTN_OK.value() == 0:
        if cp.shutdown_held():
            _shutdown_pico()
        if _handle_hint_irq() == "new":
            cp.only_ok(False)
            return None
        time.sleep_ms(Config.Timing.POLL_MS)

    cp.reset_edges()
    cp.arm_confirm_ok()

    print("[PICO CONFIRM] send typing_confirm:", move)
    screen.typing_confirm(move)
    acked, ok_seen_during_ack = screen.wait_for_lcd_ack(
        "heyArduinolcd_ack_confirm", timeout_ms=300
    )
    print("[PICO CONFIRM] acked =", acked, "ok_seen_during_ack =", ok_seen_during_ack)

    if not acked:
        cp.disarm_confirm_ok()
        cp.only_ok(False)
        return None

    # Small wait to let any in-flight OK IRQ (fired during ACK exchange) settle
    # before checking the latch. Without this, the IRQ can fire in the ~5ms gap
    # between wait_for_lcd_ack() returning and consume_confirm_ok() being called,
    # causing the latch to be missed on the first check.
    time.sleep_ms(30)

    if ok_seen_during_ack or cp.consume_confirm_ok(window_ms=300):
        print("[PICO CONFIRM] consuming armed confirm OK")
        cp.disarm_confirm_ok()
        cp.only_ok(False)
        screen.clear("confirm")
        cp.wait_for_ok_release()
        cp.reset_edges()
        return "ok"

    while True:
        if st.game_state != Game.RUNNING:
            cp.disarm_confirm_ok()
            cp.only_ok(False)
            return None

        if cp.shutdown_held():
            _shutdown_pico()

        if _handle_hint_irq() == "new":
            cp.disarm_confirm_ok()
            cp.only_ok(False)
            return None

        msg = link.read()
        if msg:
            outcome = _handle_overlay_or_gameover(msg)
            if outcome == "gameover":
                cp.disarm_confirm_ok()
                cp.only_ok(False)
                return None
            if outcome in ("hint", "engine"):
                cp.reset_edges()
                cp.disarm_confirm_ok()
                return None

        if cp.consume_confirm_ok(window_ms=300) or cp.BTN_OK.value() == 0:
            t0 = time.ticks_ms()
            fired = False

            while cp.BTN_OK.value() == 0:
                if cp.shutdown_held():
                    _shutdown_pico()
                if _handle_hint_irq() == "new":
                    cp.disarm_confirm_ok()
                    cp.only_ok(False)
                    return None
                if (
                    not fired
                    and time.ticks_diff(time.ticks_ms(), t0)
                    >= Config.Buttons.OK_LONG_PRESS_MS
                ):
                    fired = True
                    partial = move[:-1]
                    frm = partial[:2]
                    screen.typing_to(frm, partial[2] if len(partial) == 3 else "")
                    board.preview_from(frm)
                time.sleep_ms(Config.Timing.POLL_MS)

            held_ms = time.ticks_diff(time.ticks_ms(), t0)
            cp.reset_ok_hold()
            cp.disarm_confirm_ok()

            if fired:
                cp.only_ok(False)
                cp.reset_edges()
                screen.clear("confirm")
                return ("backspace_confirm", move[:-1])

            if held_ms < Config.Buttons.OK_LONG_PRESS_MS:
                cp.only_ok(False)
                screen.clear("confirm")
                return "ok"

            cp.reset_edges()
            continue

        b = cp.detect_press_raw()
        if b == (Config.Buttons.OK_INDEX + 1):
            # OK caught by edge-detection instead of the IRQ/value path.
            # This happens when BTN_OK.value() reads 1 (HIGH) during a
            # bounce in the condition above, then detect_press_raw reads
            # it 0 (LOW) a few µs later — a falling edge is detected and
            # the button is misrouted as "redo". Treat it as a confirm.
            cp.disarm_confirm_ok()
            cp.only_ok(False)
            screen.clear("confirm")
            cp.wait_for_ok_release()
            cp.reset_edges()
            return "ok"
        if b:
            cp.disarm_confirm_ok()
            cp.only_ok(False)
            screen.clear("confirm")
            return ("redo", b)

        time.sleep_ms(Config.Timing.FAST_POLL_MS)


def _retry_to_square(frm, preset_to_col=None):
    """Re-enter the to-square with optional column preset, handling the full
    back-chain.  Returns ('restart_from', col_or_None) or a valid to-square
    string, or None on abort.
    """
    while True:
        cp.only_input()
        cp.reset_edges()
        cp.reset_ok_hold()
        move_to = _select_to_square(frm, preset_col=preset_to_col)
        preset_to_col = None

        if isinstance(move_to, tuple):
            tag = move_to[0]
            if tag == "back_to_from_rank":
                return ("restart_from", move_to[1])
            if tag == "back_to_to_file":
                # stay in the while-loop: re-enter to-square without preset
                continue
        return move_to  # str or None


def _collect_and_submit_move():
    st.in_input = True
    try:
        seed = None
        preset_from_col = None

        while True:
            if cp.shutdown_held():
                _shutdown_pico()

            cp.only_input()
            cp.reset_edges()

            move_from = _select_from_square(seed_btn=seed, preset_col=preset_from_col)
            seed = None
            preset_from_col = None

            if isinstance(move_from, tuple) and move_from[0] == "back_from":
                continue
            if move_from is None:
                if st.persistent_trail_active:
                    continue
                return

            # ── enter TO square ──────────────────────────────────────────────
            move_to = _select_to_square(move_from)

            if isinstance(move_to, tuple):
                tag = move_to[0]
                if tag == "back_to_from_rank":
                    preset_from_col = move_to[1]
                    continue
                if tag == "back_to_to_file":
                    result = _retry_to_square(move_from)
                    if isinstance(result, tuple) and result[0] == "restart_from":
                        preset_from_col = result[1]
                        continue
                    if result is None or isinstance(result, tuple):
                        continue
                    move_to = result

            if move_to is None:
                if st.persistent_trail_active:
                    continue
                return

            # ── confirm ──────────────────────────────────────────────────────
            move = move_from + move_to
            res = _confirm_move(move)

            if res is None:
                if st.persistent_trail_active:
                    continue
                return

            if res == "ok":
                time.sleep_ms(200)
                link.send(move)
                st.preview_cap_flag = False
                board.markings()
                return

            if isinstance(res, tuple) and res[0] == "redo":
                cancel_btn = res[1]
                seed = cancel_btn if 1 <= cancel_btn <= 8 else None
                cp.only_input()
                continue

            # ── backspace from confirm ───────────────────────────────────────
            while isinstance(res, tuple) and res[0] == "backspace_confirm":
                partial = res[1]
                n = len(partial)

                if n >= 3:  # have from(2) + partial-to-file(1)
                    frm = partial[:2]
                    to_file = partial[2] if n == 3 else None
                    result = _retry_to_square(frm, preset_to_col=to_file)
                    if result is None or (
                        isinstance(result, tuple) and result[0] == "restart_from"
                    ):
                        preset_from_col = result[1] if result else None
                        res = ("restart_from", None)
                        break
                    move = frm + result
                    res = _confirm_move(move)
                    if res is None:
                        res = ("restart_from", None)
                        break
                    continue

                if n == 2:  # have from only
                    result = _retry_to_square(partial)
                    if result is None or (
                        isinstance(result, tuple) and result[0] == "restart_from"
                    ):
                        preset_from_col = result[1] if result else None
                        res = ("restart_from", None)
                        break
                    move = partial + result
                    res = _confirm_move(move)
                    if res is None:
                        res = ("restart_from", None)
                        break
                    continue

                # n <= 1: only partial from-file, restart from scratch
                preset_from_col = partial[0] if n == 1 else None
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

    finally:
        st.in_input = False


# ── Game-over ─────────────────────────────────────────────────────────────────


def _show_game_over_and_ack(result_str):
    cp.disable_hint_irq()
    try:
        cp.reset_edges()
        cp.only_ok(True)
        board.scene_gameover()

        if cp.shutdown_held():
            _shutdown_pico()

        while cp.BTN_OK.value() == 0:
            time.sleep_ms(Config.Timing.POLL_MS)
        time.sleep_ms(200)
        cp.reset_edges()

        blink = False
        last = time.ticks_ms()
        while True:
            now = time.ticks_ms()
            if time.ticks_diff(now, last) > Config.Timing.GAMEOVER_BLINK_MS:
                blink = not blink
                cp.set_ok_blink(blink)
                last = now

            if cp.shutdown_held():
                _shutdown_pico()

            b = cp.detect_press_raw()
            if b == (Config.Buttons.OK_INDEX + 1):
                cp.only_ok(False)
                link.send("n")
                break
            time.sleep_ms(Config.Timing.SLOW_POLL_MS)

        board.markings()
    finally:
        cp.enable_hint_irq()


# ── Startup / mode selection ──────────────────────────────────────────────────


def _run_startup_sequence():
    board.opening()
    lit = 0
    while True:
        if cp.shutdown_held():
            _shutdown_pico()
        lit = board.loading_step(lit)
        time.sleep_ms(Config.Timing.LOADING_TICK_MS)
        msg = link.read()
        if not msg:
            continue
        if msg.startswith("heyArduinoChooseMode") or msg.startswith(
            "heyArduinoMenuPaged"
        ):
            while lit < (board.w * board.h):
                if cp.shutdown_held():
                    _shutdown_pico()
                lit = board.loading_step(lit)
                time.sleep_ms(Config.Timing.LOADING_FILL_MS)
            _enter_setup_mode()
            if msg.startswith("heyArduinoMenuPaged"):
                _handle_menu_paged(msg)
            return


def _enter_setup_mode():
    cp.disable_hint_irq()
    cp.reset_edges()
    board.markings()
    cp.show_coords_top(WHITE)
    st.game_state = Game.SETUP
    st.suspend_until_new_game = False


def _reset_to_idle():
    st.in_setup = False
    st.game_state = Game.IDLE
    st.suspend_until_new_game = False
    try:
        board.markings()
    except Exception:
        pass
    cp.reset_edges()


def _select_mapped_value(out_min, out_max, *, cancel_to_idle=False):
    cp.reset_edges()
    while True:
        if cp.shutdown_held():
            _shutdown_pico()
        b = cp.detect_press_allowed()
        if b == (Config.Buttons.OK_INDEX + 1):
            link.send("btn_ok")
            if cancel_to_idle:
                _reset_to_idle()
            return None
        if b and 1 <= b <= 8:
            return _map_range(b, 1, 8, out_min, out_max)
        time.sleep_ms(Config.Timing.FAST_POLL_MS)


def _run_game_setup_loop():
    st.in_setup = True
    try:
        while True:
            if cp.shutdown_held():
                _shutdown_pico()

            msg = link.read()
            if not msg:
                time.sleep_ms(Config.Timing.POLL_MS)
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
                v = _select_mapped_value(1, 20)
                if v is None:
                    return
                link.send(str(v))
                time.sleep_ms(Config.Timing.SETUP_TRANSITION_MS)
                return

            if msg.startswith("heyArduinoTimeControl"):
                cp.profile.vs_strength_time()
                board.prompt_time()
                v = _select_mapped_value(1000, 8000)
                if v is None:
                    return
                link.send(str(v))
                time.sleep_ms(Config.Timing.SETUP_TRANSITION_MS)
                return

            if msg.startswith("heyArduinoPlayerColor"):
                board.markings()
                cp.show_coords_top(WHITE)
                cp.profile.vs_color()
                cp.reset_edges()
                while True:
                    if cp.shutdown_held():
                        _shutdown_pico()
                    b2 = cp.detect_press_allowed()
                    if b2 == (Config.Buttons.OK_INDEX + 1):
                        link.send("btn_ok")
                        return
                    if b2 in (1, 2, 3):
                        link.send("s" + str(b2))
                        return
                    time.sleep_ms(Config.Timing.FAST_POLL_MS)

            if msg.startswith("heyArduinoSetupComplete"):
                st.game_state = Game.RUNNING
                st.in_setup = False
                st.suspend_until_new_game = False
                return

            if msg.startswith("heyArduinoMenuPaged"):
                _enter_setup_mode()
                _handle_menu_paged(msg)
                continue

            if msg.startswith("heyArduinoGetBrightness"):
                link.send("brightness_" + str(_brightness))
                continue

            if msg.startswith("heyArduinoSetBrightness_"):
                try:
                    _save_and_reset_brightness(int(msg.split("_")[-1]))
                except Exception:
                    pass
                continue

            if msg.startswith("heyArduinoBrightnessControl"):
                cp.profile.brightness()
                board.prompt_brightness()
                link.send("brightness_" + str(_brightness))
                v = _select_mapped_value(1, 8)
                if v is None:
                    return
                link.send(str(v))
                time.sleep_ms(Config.Timing.SETUP_TRANSITION_MS)
                return

            if msg.startswith("heyArduinoUpdateMode"):
                _handle_update_mode(msg)
                return

            if msg.startswith("heyArduinoChooseMode"):
                _enter_setup_mode()
                continue
    finally:
        cp.enable_hint_irq()


# ── Promotion ─────────────────────────────────────────────────────────────────


def _handle_promotion_choice():
    board.scene_promotion()
    cp.show_coords_top(MAGENTA)
    cp.reset_edges()
    try:
        while True:
            if cp.shutdown_held():
                _shutdown_pico()
            if _handle_hint_irq() == "new":
                return
            b = cp.detect_press_raw()
            if b in _PROMO:
                link.send(_PROMO[b])
                break
            if b:
                pass  # ignore other buttons
            else:
                time.sleep_ms(Config.Timing.FAST_POLL_MS)
    finally:
        cp.clear_header()
        cp.apply(force=True)
        board.markings()


# ── Puzzle setup ──────────────────────────────────────────────────────────────


def _handle_puzzle_setup_message(msg):
    if not msg:
        return False

    if msg.startswith("heyArduinopuzzle_setup_begin"):
        st.puzzle_setup_active = True
        cp.disable_hint_irq()
        cp.reset_edges()
        cp.border(True, force=True)
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
        board.puzzle_blink(sq, color, times=2)
        return True

    if msg.startswith("heyArduinosetup_remove_"):
        sq = msg.split("_")[-1].strip()
        board.markings()
        board.puzzle_blink(sq, RED, times=3)
        return True

    if msg.startswith("heyArduinosetup_move_"):
        tail = msg[len("heyArduinosetup_move_") :].strip()
        parts = tail.split("_")
        uci = parts[0].strip() if parts else ""
        side = parts[1].strip().lower() if len(parts) > 1 else "w"
        color = GREEN if side.startswith("w") else ENGINE_COLOR
        board.overlay_show("setup", uci, cap=False, color_override=color)
        return True

    return False


# ── Shutdown ──────────────────────────────────────────────────────────────────


def _shutdown_pico():
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
        time.sleep_ms(Config.Timing.SHUTDOWN_IDLE_MS)


# ── Message route handlers ────────────────────────────────────────────────────


def _handle_gameover(msg):
    res = msg.split(":", 1)[1].strip() if ":" in msg else ""
    _show_game_over_and_ack(res)


def _handle_reset_board(_msg):
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
    _enter_setup_mode()
    while st.game_state == Game.SETUP:
        _run_game_setup_loop()


def _handle_menu_paged(_msg):
    cp.profile.menu_paged()
    cp.disable_hint_irq()
    cp.reset_edges()
    board.markings()
    link.send("menu_ready")
    while True:
        if cp.shutdown_held():
            _shutdown_pico()
        b = cp.detect_press_allowed()
        if not b:
            time.sleep_ms(Config.Timing.FAST_POLL_MS)
            continue
        if b == (Config.Buttons.OK_INDEX + 1):
            link.send("btn_ok")
            break
        if b == (Config.Buttons.HINT_INDEX + 1):
            link.send("btn_hint")
            continue
        if 1 <= b <= 4:
            link.send(str(b))
            break
    board.markings()
    cp.enable_hint_irq()


def _handle_engine_move(msg):
    _show_overlay(msg[len("heyArduinom") :], ENGINE_COLOR, "engine")
    cp.only_ok(True)
    st.engine_ack_pending = True
    st.pending_gameover_result = None
    st.buffered_turn_msg = None


def _handle_hint_move(msg):
    _show_overlay(msg[len("heyArduinohint_") :], YELLOW, "hint")
    cp.only_ok(True)
    cp.reset_edges()


def _handle_puzzle_wrong(msg):
    raw = msg[len("heyArduinopuzzle_wrong_") :].strip()
    mv = "".join(ch for ch in raw if _is_alphanumeric(ch))[:4]
    if len(mv) < 4:
        return
    _show_overlay(mv, RED, "wrong")
    cp.only_ok(True)
    cp.reset_edges()
    while True:
        if cp.shutdown_held():
            _shutdown_pico()
        if _handle_hint_irq() == "new":
            link.send("n")
            break
        b = cp.detect_press_raw()
        if b == (Config.Buttons.OK_INDEX + 1):
            link.send("btn_ok")
            break
        time.sleep_ms(Config.Timing.POLL_MS)
    cp.only_ok(False)
    _clear_persistent_trail()
    board.markings()


def _handle_turn(msg):
    turn_str = msg.split("_", 1)[1].strip().lower()
    st.current_turn = "W" if "w" in turn_str else "B"

    t_start = time.ticks_ms()
    while (
        time.ticks_diff(time.ticks_ms(), t_start)
        < Config.Timing.TURN_GAMEOVER_WINDOW_MS
    ):
        nxt = link.read()
        if not nxt:
            time.sleep_ms(Config.Timing.FAST_POLL_MS)
            continue
        if nxt.startswith("heyArduinoGameOver"):
            _handle_gameover(nxt)
            return

    cp.only_input()
    _collect_and_submit_move()


# ── Dispatch table ────────────────────────────────────────────────────────────


def _set_ok_back_enabled(enabled):
    st.ok_back_enabled = enabled
    cp.only_ok(enabled)


def _handle_set_brightness(msg):
    try:
        _save_and_reset_brightness(int(msg.split("_")[-1]))
    except Exception:
        pass


def _handle_update_mode(_msg):
    """Receive a new main.py from the Pi in base64 chunks and flash it."""
    _TEMP = "/main_new.py"
    board.off()
    cp.off(force=True)
    cp.disable_hint_irq()
    link.send("UpdateReady")
    try:
        with open(_TEMP, "wb") as _f:
            while True:
                time.sleep_ms(20)
                msg = link.read()
                if msg is None:
                    continue
                if msg.startswith("heyArduinoUpdateChunk_"):
                    _f.write(ubinascii.a2b_base64(msg[len("heyArduinoUpdateChunk_") :]))
                elif msg.startswith("heyArduinoUpdateDone"):
                    break
                elif msg.startswith("heyArduinoUpdateAbort"):
                    try:
                        _uos.remove(_TEMP)
                    except Exception:
                        pass
                    return
        _uos.remove("/main.py")
        _uos.rename(_TEMP, "/main.py")
        link.send("UpdateComplete")
        time.sleep_ms(300)
        reset()
    except Exception:
        try:
            _uos.remove(_TEMP)
        except Exception:
            pass
        link.send("UpdateError")


ROUTES = [
    ("heyArduinook_back_enable", lambda _: (_set_ok_back_enabled(True))),
    ("heyArduinook_back_disable", lambda _: (_set_ok_back_enabled(False))),
    ("heyArduinohint_disable", lambda _: setattr(st, "hint_enabled", False)),
    ("heyArduinohint_enable", lambda _: setattr(st, "hint_enabled", True)),
    (
        "heyArduinocheck_",
        lambda m: board.blink_square_keep(
            m.split("_", 1)[1].strip() if "_" in m else "", RED
        ),
    ),
    ("heyArduinoSetBrightness_", _handle_set_brightness),
    ("heyArduinoUpdateMode", _handle_update_mode),
    ("heyArduinoGameOver", _handle_gameover),
    ("heyArduinoResetBoard", _handle_reset_board),
    ("heyArduinoChooseMode", _handle_choose_mode),
    ("heyArduinoMenuPaged", _handle_menu_paged),
    ("heyArduinom", _handle_engine_move),
    ("heyArduinopromotion_choice_needed", lambda _: _handle_promotion_choice()),
    ("heyArduinohint_", _handle_hint_move),
    ("heyArduinopuzzle_wrong_", _handle_puzzle_wrong),
    ("heyArduinoerror", lambda _: (board.illegal_flash(), cp.only_ok(False))),
    ("heyArduinoturn_", _handle_turn),
]


def _route_incoming_message(msg):
    try:
        if _handle_puzzle_setup_message(msg):
            return True
    except Exception:
        pass
    for prefix, fn in ROUTES:
        if msg.startswith(prefix):
            fn(msg)
            return True
    return False


# ── Main loop ─────────────────────────────────────────────────────────────────


def _main_loop():
    while True:
        if cp.shutdown_held():
            _shutdown_pico()

        if (
            st.ok_back_enabled
            and not st.puzzle_setup_active
            and not st.engine_ack_pending
        ):
            b0 = cp.detect_press_raw()
            if b0 == (Config.Buttons.OK_INDEX + 1):
                link.send("btn_ok")
                st.ok_back_enabled = False
                _reset_to_idle()
                time.sleep_ms(50)
                continue

        if st.puzzle_setup_active:
            msg_setup = link.read()
            if msg_setup:
                _handle_puzzle_setup_message(msg_setup)
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
            if b == (Config.Buttons.OK_INDEX + 1):
                link.send("btn_ok")
            time.sleep_ms(Config.Timing.POLL_MS)
            continue

        if _handle_hint_irq() == "new":
            cp.disable_hint_irq()
            cp.off()
            board.opening()
            st.engine_ack_pending = False
            st.pending_gameover_result = None
            st.buffered_turn_msg = None
            continue

        if st.engine_ack_pending:
            nxt = link.read()

            if nxt and nxt.startswith("heyArduinoGameOver"):
                st.pending_gameover_result = (
                    nxt.split(":", 1)[1].strip() if ":" in nxt else ""
                )
                while cp.BTN_OK.value() == 0:
                    time.sleep_ms(Config.Timing.POLL_MS)
                time.sleep_ms(Config.Timing.ENGINE_ACK_POST_MS)
                cp.reset_edges()
                while True:
                    b = cp.detect_press_raw()
                    if b == (Config.Buttons.OK_INDEX + 1):
                        cp.only_ok(False)
                        break
                    time.sleep_ms(15)
                st.engine_ack_pending = False
                _show_game_over_and_ack(st.pending_gameover_result)
                st.pending_gameover_result = None
                st.buffered_turn_msg = None
                continue

            if nxt and nxt.startswith("heyArduinoturn_"):
                st.buffered_turn_msg = nxt

            b = cp.detect_press_raw()
            if b == (Config.Buttons.OK_INDEX + 1):
                link.send("btn_ok")
                st.engine_ack_pending = False
                cp.only_ok(False)
                _clear_persistent_trail()

                if st.buffered_turn_msg:
                    turn_str = st.buffered_turn_msg.split("_", 1)[1].strip().lower()
                    st.current_turn = "W" if "w" in turn_str else "B"
                    st.buffered_turn_msg = None

                cp.only_input()
                _collect_and_submit_move()
                continue

            time.sleep_ms(Config.Timing.POLL_MS)
            continue

        msg = link.read()
        if msg and _handle_puzzle_setup_message(msg):
            continue

        if not msg:
            time.sleep_ms(Config.Timing.POLL_MS)
            continue

        if st.suspend_until_new_game or st.game_state != Game.RUNNING:
            if not (
                msg.startswith("heyArduinoChooseMode")
                or msg.startswith("heyArduinoResetBoard")
                or msg.startswith("heyArduinoUpdateMode")
            ):
                continue

        _route_incoming_message(msg)


def run():
    cp.off(force=True)
    board.off()
    cp.reset_edges()

    cp.disable_hint_irq()
    _run_startup_sequence()

    while st.game_state == Game.SETUP:
        _run_game_setup_loop()

    while True:
        _main_loop()


run()
