from machine import Pin
import time
import neopixel


Config = None
BLACK = None
WHITE = None
RED = None
GREEN = None
BLUE = None
CYAN = None
MAGENTA = None
YELLOW = None
ENGINE_COLOR = None


def configure(config, colors):
    global Config, BLACK, WHITE, RED, GREEN, BLUE, CYAN, MAGENTA, YELLOW, ENGINE_COLOR
    Config = config
    BLACK = colors["BLACK"]
    WHITE = colors["WHITE"]
    RED = colors["RED"]
    GREEN = colors["GREEN"]
    BLUE = colors["BLUE"]
    CYAN = colors["CYAN"]
    MAGENTA = colors["MAGENTA"]
    YELLOW = colors["YELLOW"]
    ENGINE_COLOR = colors["ENGINE_COLOR"]


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
        ok_color=None,
        hint_color=None,
    ):
        if ok_color is None:
            ok_color = GREEN
        if hint_color is None:
            hint_color = YELLOW
        self.cp.border(border_on, apply_now=False)
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

    def menu_paged(
        self,
        has_next=True,
        has_back=True,
        *,
        allow_select=True,
        border_on=False,
        ok_color=None,
    ):
        allowed = []
        if allow_select:
            allowed.extend([1, 2, 3])
        if has_back:
            allowed.append(9)
        if has_next:
            allowed.append(10)
        if ok_color is None:
            ok_color = RED if has_back else BLACK
        self._apply(
            border_on,
            True,
            False,
            True,
            True,
            allowed,
            ok_color=ok_color,
            hint_color=YELLOW if has_next else BLACK,
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

    def apply(self, force=False):
        self._panel.write()

    def off(self, force=False):
        for i in range(Config.LEDs.PANEL_COUNT):
            self._panel[i] = BLACK
        self.apply(force=force)

    def clear_header(self):
        for i in range(Config.LEDs.CP_ZONE_START, Config.LEDs.CP_ZONE_END):
            self._panel[i] = BLACK

    def border(self, on=True, color=None, force=False, apply_now=True):
        if color is None:
            color = Config.LEDs.BORDER_COLOR
        col = color if on else BLACK
        for idx in Config.LEDs.FILES + Config.LEDs.RANKS:
            self._panel[idx] = col
        if apply_now:
            self.apply(force=force)

    def _set_cp_buttons(self, top, bottom, ok, hint, ok_color=None, hint_color=None):
        if ok_color is None:
            ok_color = GREEN
        if hint_color is None:
            hint_color = YELLOW
        self._panel[0] = WHITE if top else BLACK
        self._panel[1] = WHITE if top else BLACK
        self._panel[2] = WHITE if bottom else BLACK
        self._panel[3] = WHITE if bottom else BLACK
        self._panel[Config.LEDs.CP_OK_PIX] = ok_color if ok else BLACK
        self._panel[Config.LEDs.CP_HINT_PIX] = hint_color if hint else BLACK

    def only_ok(self, on=True, color=None, *, border_on=None, force=False):
        if color is None:
            color = GREEN
        if border_on is not None:
            self.border(border_on, force=force, apply_now=False)
        self._set_cp_buttons(False, False, ok=on, hint=False, ok_color=color)
        self.apply(force=force)

    def set_ok_led(self, on=True, color=None):
        if color is None:
            color = GREEN
        self._panel[Config.LEDs.CP_OK_PIX] = color if on else BLACK
        self.apply()

    def only_input(self):
        self.border(True, apply_now=False)
        self._set_cp_buttons(
            True, True, ok=True, hint=True, ok_color=RED, hint_color=YELLOW
        )
        self.apply()

    def show_coords_top(self, color=None, keep_border=False):
        if color is None:
            color = WHITE
        self.border(keep_border, apply_now=False)
        self._panel[0] = color
        self._panel[1] = color
        self._panel[2] = BLACK
        self._panel[3] = BLACK
        self.apply()

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

    def _hint_irq(self, pin):
        self.hint_irq_flag = True

    def disable_hint_irq(self):
        self.BTN_HINT.irq(handler=None)

    def enable_hint_irq(self):
        self.BTN_HINT.irq(trigger=Pin.IRQ_FALLING, handler=self._hint_irq)

    def reset_ok_hold(self):
        self._ok_press_ms = None
        self._ok_fired = False

    def ok_long_hold_fired(self, hold_ms=None):
        if hold_ms is None:
            hold_ms = Config.Buttons.OK_LONG_PRESS_MS
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

    def wait_for_ok_release(self):
        while self.BTN_OK.value() == 0:
            time.sleep_ms(Config.Timing.POLL_MS)

    def set_ok_blink(self, on):
        self.clear_header()
        self._panel[Config.LEDs.CP_OK_PIX] = GREEN if on else BLACK
        self.apply()

    def shutdown_held(self, hold_ms=None):
        if hold_ms is None:
            hold_ms = Config.Buttons.SHUTDOWN_HOLD_MS
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

    def clear(self, color=None):
        if color is None:
            color = BLACK
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
        on_ms=None,
        off_ms=None,
    ):
        if on_ms is None:
            on_ms = Config.Timing.BLINK_ON_MS
        if off_ms is None:
            off_ms = Config.Timing.BLINK_OFF_MS
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
