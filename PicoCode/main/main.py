# -*- coding: utf-8 -*-
"""
SMARTCHESS PICO Firmware — Protocol v2 (JSON/NDJSON)
Deep-refactor build (2026-02)
- Clean UART protocol: JSON line per message {"t":<type>, "d":{...}}
- UX parity: LEDs, overlays, capture blink, illegal animation, hints, shutdown hold
- Ready for Lichess integration on Pi (no protocol impedance)

File kept at: PicoCode/main/main.py (same project structure)
"""
from machine import Pin, UART
import time
import ujson as json  # MicroPython JSON
import neopixel

# -------------------- Pins / HW --------------------
BUTTON_PINS = [2, 3, 4, 5, 10, 8, 7, 6, 9, 11]  # 1..8 coords, 9=OK, 11=Hint
OK_BUTTON_INDEX   = 8  # 0-based index into BUTTON_PINS
HINT_BUTTON_INDEX = 9
SHUTDOWN_BTN_INDEX = 7   # H/8 button (long-press)

CONTROL_PANEL_LED_PIN   = 16
CONTROL_PANEL_LED_COUNT = 22
CHESSBOARD_LED_PIN      = 22

BOARD_W, BOARD_H = 8, 8
DEBOUNCE_MS = 300
SHUTDOWN_HOLD_MS = 2000

# Colors
BLACK=(0,0,0); WHITE=(255,255,255); DIMW=(10,10,10)
RED=(255,0,0); GREEN=(0,255,0); BLUE=(0,0,255)
CYAN=(0,255,255); MAGENTA=(255,0,255); YELLOW=(255,255,0)
ENGINE_COLOR = BLUE

# Control panel indexes
CP_COORD_START = 0
CP_OK_PIX      = 4
CP_HINT_PIX    = 5
CP_FILES_LEDS = [6,7,8,9,10,11,12,13]
CP_RANKS_LEDS = [14,15,16,17,18,19,20,21]

# -------------------- Protocol v2 --------------------
class Proto:
    def __init__(self, uart: UART):
        self.uart = uart

    def send(self, t: str, d: dict=None):
        if d is None:
            d = {}
        payload = {"t": t, "d": d}
        try:
            s = json.dumps(payload)
        except Exception:
            s = '{"t":"log","d":{"level":"error","msg":"encode_fail"}}'
        self.uart.write(s + "\n")

    def recv(self):
        if self.uart.any():
            try:
                raw = self.uart.readline()
                if not raw:
                    return None
                s = raw.decode().strip()
                if not s:
                    return None
                obj = json.loads(s)
                if isinstance(obj, dict) and 't' in obj:
                    return obj
            except Exception:
                return None
        return None

# -------------------- LEDs / UI --------------------
class ControlPanel:
    def __init__(self, pin, count):
        self.np = neopixel.NeoPixel(Pin(pin, Pin.OUT), count)
        self.count = count

    def set(self, i, c):
        if 0 <= i < self.count:
            self.np[i] = c; self.np.write()

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

    def ok(self, on=True): self.set(CP_OK_PIX, GREEN if on else BLACK)
    def hint(self, on=True): self.set(CP_HINT_PIX, YELLOW if on else BLACK)
    def clear_small(self):
        for i in range(0, 6):
            self._set_no_write(i, BLACK)
        self._write()

    def coord_top(self, color=WHITE):
        self.fill(color, CP_COORD_START, 2)

    def coord_down(self, color=WHITE):
        self.fill(color, CP_COORD_START+2, 2)

    def bars_dim(self, on=True):
        col = DIMW if on else BLACK
        for idx in CP_FILES_LEDS + CP_RANKS_LEDS:
            self._set_no_write(idx, col)
        self._write()

class Chessboard:
    def __init__(self, pin, w, h, origin_bottom_right=True, zigzag=True):
        self.w, self.h = w, h
        self.origin_bottom_right = origin_bottom_right
        self.zigzag = zigzag
        self.np = neopixel.NeoPixel(Pin(pin, Pin.OUT), w*h)
        self._marking_cache = [BLACK]*(w*h)
        LIGHT=(100,100,100); DARK=(3,3,3)
        for y in range(self.h):
            for x in range(self.w):
                col = DARK if ((x+y) % 2 == 0) else LIGHT
                self._raw_set(x, y, col, into_cache=True)
        self.clear(BLACK)

    def _xy_to_index(self, x, y):
        row = y
        if self.origin_bottom_right:
            col_index = (self.w - 1 - x) if (row % 2 == 0) else x if self.zigzag else (self.w - 1 - x)
            return row*self.w + col_index
        row_top = (self.h - 1) - y
        col_index = x if (row_top % 2 == 0) else (self.w - 1 - x) if self.zigzag else x
        return row_top*self.w + col_index

    def _raw_set(self, x, y, color, into_cache=False):
        idx = self._xy_to_index(x, y)
        self.np[idx] = color
        if into_cache:
            self._marking_cache[idx] = color

    def clear(self, color=BLACK):
        for i in range(self.w*self.h):
            self.np[i] = color
        self.np.write()

    def set_square(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.np[self._xy_to_index(x, y)] = color

    def write(self): self.np.write()

    def algebraic_to_xy(self, sq):
        if not sq or len(sq) < 2: return None
        f, r = sq[0].lower(), sq[1]
        if not ('a' <= f <= 'h'): return None
        if not ('1' <= r <= '8'): return None
        return (ord(f)-97, int(r)-1)

    @staticmethod
    def _sgn(v):
        return 0 if v == 0 else (1 if v > 0 else -1)

    def _path_squares(self, frm, to):
        f = self.algebraic_to_xy(frm); t = self.algebraic_to_xy(to)
        if not f or not t: return []
        fx, fy = f; tx, ty = t
        dx = tx - fx; dy = ty - fy
        adx, ady = abs(dx), abs(dy)
        path = []
        if fx == tx and fy != ty:
            sy = self._sgn(dy)
            for y in range(fy, ty + sy, sy): path.append((fx, y))
            return path
        if fy == ty and fx != tx:
            sx = self._sgn(dx)
            for x in range(fx, tx + sx, sx): path.append((x, fy))
            return path
        if adx == ady and adx != 0:
            sx = self._sgn(dx); sy = self._sgn(dy)
            x, y = fx, fy
            for _ in range(adx + 1):
                path.append((x, y)); x += sx; y += sy
            return path
        # Knight L path
        if (adx, ady) in ((1,2), (2,1)):
            sx = self._sgn(dx); sy = self._sgn(dy)
            path.append((fx, fy))
            if ady == 2:
                path += [(fx, fy+sy), (fx, fy+2*sy), (fx+sx, fy+2*sy)]
            else:
                path += [(fx+sx, fy), (fx+2*sx, fy), (fx+2*sx, fy+sy)]
            if path[-1] != (tx, ty): path.append((tx, ty))
            # dedup
            ded = []
            for p in path:
                if not ded or ded[-1] != p: ded.append(p)
            return ded
        return [(fx, fy), (tx, ty)]

    def draw_trail(self, uci, color, end_color=None):
        if not uci or len(uci) < 4: return
        frm, to = uci[:2], uci[2:4]
        path = self._path_squares(frm, to)
        for i, (x, y) in enumerate(path):
            if end_color and i == len(path)-1:
                self.set_square(x, y, end_color)
            else:
                self.set_square(x, y, color)
        self.write()

    def blink_dest(self, to_sq, color_on, times=3, on_ms=200, off_ms=200, final_color=None):
        xy = self.algebraic_to_xy(to_sq)
        if not xy: return
        x, y = xy
        for _ in range(times):
            self.set_square(x, y, color_on); self.write(); time.sleep_ms(on_ms)
            self.set_square(x, y, BLACK); self.write(); time.sleep_ms(off_ms)
        self.set_square(x, y, (final_color if final_color is not None else color_on)); self.write()

# Simple button wrapper
class ButtonManager:
    def __init__(self, pins):
        self.pins = [Pin(g, Pin.IN, Pin.PULL_UP) for g in pins]
        self._last = [1]*len(self.pins)

    def reset(self):
        for i,p in enumerate(self.pins): self._last[i] = p.value()

    def detect_press(self):
        for i,p in enumerate(self.pins):
            cur = p.value(); prev = self._last[i]
            self._last[i] = cur
            if prev == 1 and cur == 0:
                time.sleep_ms(DEBOUNCE_MS)
                return i+1
        return None

    def btn(self, index):
        return self.pins[index]

# -------------------- Instantiate HW --------------------
uart = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1), timeout=10)
proto = Proto(uart)
cp = ControlPanel(CONTROL_PANEL_LED_PIN, CONTROL_PANEL_LED_COUNT)
board = Chessboard(CHESSBOARD_LED_PIN, BOARD_W, BOARD_H, origin_bottom_right=True, zigzag=True)
buttons = ButtonManager(BUTTON_PINS)

BTN_OK   = buttons.btn(OK_BUTTON_INDEX)
BTN_HINT = buttons.btn(HINT_BUTTON_INDEX)
BTN_SHUT = buttons.btn(SHUTDOWN_BTN_INDEX)

# -------------------- Helpers --------------------

def is_shutdown_held(ms=SHUTDOWN_HOLD_MS):
    if BTN_SHUT.value() == 0:
        t0 = time.ticks_ms()
        while BTN_SHUT.value() == 0:
            if time.ticks_diff(time.ticks_ms(), t0) >= ms:
                return True
            time.sleep_ms(10)
    return False

# UI helpers

def show_opening():
    board.clear(BLACK)
    for k in range(board.w + board.h - 1):
        for y in range(board.h):
            x = k - y
            if 0 <= x < board.w:
                board.set_square(x, y, GREEN)
        board.write(); time.sleep_ms(25)
    time.sleep_ms(150)

# -------------------- Input FSM (concise) --------------------

def letters_map(idx):
    return chr(ord('a') + (idx-1))

def await_ok_blink():
    cp.clear_small(); cp.ok(True)
    while BTN_OK.value() == 0: time.sleep_ms(10)
    time.sleep_ms(150); buttons.reset()
    # wait for press
    while True:
        b = buttons.detect_press()
        if b == (OK_BUTTON_INDEX+1):
            cp.ok(False); return
        time.sleep_ms(10)

# Capture preview request/response

def probe_capture(uci):
    proto.send('cap_probe', {'uci': uci})
    deadline = time.ticks_add(time.ticks_ms(), 200)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        msg = proto.recv()
        if not msg:
            time.sleep_ms(5); continue
        if msg.get('t') == 'cap_result':
            d = msg.get('d') or {}
            if d.get('uci') == uci:
                return bool(d.get('isCap'))
    return False

# Move entry (from->to->OK)

def enter_move():
    # hint button -> request
    cp.clear_small(); cp.hint(True); cp.coord_top(WHITE); cp.coord_down(WHITE)
    buttons.reset()
    frm_col = frm_row = to_col = to_row = None

    while frm_col is None:
        if is_shutdown_held():
            proto.send('shutdown', {})
            return None
        # hint irq
        if BTN_HINT.value() == 0:
            proto.send('hint_request', {})
            time.sleep_ms(250)
        b = buttons.detect_press()
        if not b: time.sleep_ms(5); continue
        if b == (OK_BUTTON_INDEX+1):
            continue
        if 1 <= b <= 8:
            frm_col = letters_map(b)
            proto.send('typing', {'stage':'from','text':frm_col})

    while frm_row is None:
        b = buttons.detect_press()
        if not b: time.sleep_ms(5); continue
        if 1 <= b <= 8:
            frm_row = str(b)
            proto.send('typing', {'stage':'from','text':frm_col+frm_row})

    frm = frm_col + frm_row
    # Preview FROM on board
    xy = board.algebraic_to_xy(frm)
    if xy:
        board.set_square(xy[0], xy[1], GREEN); board.write()

    while to_col is None:
        if is_shutdown_held():
            proto.send('shutdown', {}); return None
        if BTN_HINT.value() == 0:
            proto.send('hint_request', {}); time.sleep_ms(250)
        b = buttons.detect_press()
        if not b: time.sleep_ms(5); continue
        if 1 <= b <= 8:
            to_col = letters_map(b)
            proto.send('typing', {'stage':'to','from':frm,'text':to_col})

    while to_row is None:
        b = buttons.detect_press()
        if not b: time.sleep_ms(5); continue
        if 1 <= b <= 8:
            to_row = str(b)
            proto.send('typing', {'stage':'to','from':frm,'text':to_col+to_row})

    to = to_col + to_row
    uci = frm + to
    # ask preview capture
    is_cap = probe_capture(uci)

    # Trail + blink
    board.clear(BLACK)
    board.draw_trail(uci, GREEN, end_color=(MAGENTA if is_cap else None))
    if is_cap:
        board.blink_dest(to, MAGENTA, times=3, on_ms=180, off_ms=180, final_color=MAGENTA)

    # confirm
    proto.send('typing', {'stage':'confirm','uci':uci})
    await_ok_blink()
    return uci

# -------------------- Scenes / overlays --------------------

def show_overlay(role, uci, is_cap=False, ack=False):
    board.clear(BLACK)
    col = ENGINE_COLOR if role == 'engine' else YELLOW
    endc = MAGENTA if is_cap else None
    board.draw_trail(uci, col, end_color=endc)
    if is_cap:
        board.blink_dest(uci[2:4], endc or RED, times=3, on_ms=180, off_ms=180, final_color=endc)
    if ack:
        await_ok_blink()
        proto.send('ack', {'what':'engine_move'})


def show_gameover(result):
    # simple hash board
    for i in range(board.w * board.h): board.np[i] = GREEN
    board.np.write()
    for y in range(board.h):
        board.set_square(2, y, WHITE); board.set_square(5, y, WHITE)
    for x in range(board.w):
        board.set_square(x, 2, WHITE); board.set_square(x, 5, WHITE)
    board.write()
    await_ok_blink()
    proto.send('new_game', {})


def select_one_of_three():
    # 1=PC, 2=Online, 3=Local
    buttons.reset()
    while True:
        if is_shutdown_held():
            proto.send('shutdown', {}); return None
        b = buttons.detect_press()
        if not b: time.sleep_ms(5); continue
        if b == 1: return 'stockfish'
        if b == 2: return 'online'
        if b == 3: return 'local'


def select_side():
    # 1=White, 2=Black, 3=Random
    buttons.reset()
    while True:
        b = buttons.detect_press()
        if not b: time.sleep_ms(5); continue
        if b == 1: return 'white'
        if b == 2: return 'black'
        if b == 3: return 'random'

# -------------------- Main loop --------------------

def main():
    buttons.reset(); cp.fill(BLACK); board.clear(BLACK); cp.bars_dim(True)
    # Hello banner to Pi
    proto.send('hello', {'fw':'pico-2026','proto':2})
    show_opening()

    while True:
        if is_shutdown_held():
            proto.send('shutdown', {}); time.sleep_ms(300)
        msg = proto.recv()
        if not msg:
            time.sleep_ms(5); continue
        t = msg.get('t'); d = msg.get('d') or {}

        if t == 'ui':
            if d.get('kind') == 'mode_select':
                cp.coord_top(WHITE)
                choice = select_one_of_three()
                if choice:
                    proto.send('mode_choice', {'mode': choice})
                    board.clear(BLACK)

            elif d.get('kind') == 'setup_strength':
                cp.coord_top(MAGENTA)
                val = None
                buttons.reset()
                while val is None:
                    b = buttons.detect_press()
                    if b and 1 <= b <= 8:
                        # map 1..8 -> 1..20
                        v = int((b-1)*(19/7))+1
                        val = v
                proto.send('value', {'kind':'strength','value':val})
                board.clear(BLACK)

            elif d.get('kind') == 'setup_time':
                cp.coord_down(MAGENTA)
                val = None
                buttons.reset()
                while val is None:
                    b = buttons.detect_press()
                    if b and 1 <= b <= 8:
                        # map 1..8 -> 1000..8000 ms
                        val = 1000 + (b-1)*1000
                proto.send('value', {'kind':'time','value':val})
                board.clear(BLACK)

            elif d.get('kind') == 'setup_color':
                cp.coord_top(WHITE)
                color = select_side()
                proto.send('value', {'kind':'color','value':color})
                board.clear(BLACK)

        elif t == 'turn':
            uci = enter_move()
            if uci:
                proto.send('move_submit', {'uci': uci})

        elif t == 'overlay':
            show_overlay(d.get('role'), d.get('uci'), bool(d.get('isCap')), bool(d.get('ack')))

        elif t == 'error':
            # illegal animation simplified
            for i in range(board.w*board.h): board.np[i] = BLUE
            board.np.write(); time.sleep_ms(500)
            for _ in range(2):
                for i in range(8):
                    board.set_square(i, i, RED); board.set_square(i, 7-i, RED)
                board.write(); time.sleep_ms(300)
                for i in range(8):
                    board.set_square(i, i, BLUE); board.set_square(i, 7-i, BLUE)
                board.write(); time.sleep_ms(300)

        elif t == 'promotion_needed':
            # 1=Q,2=R,3=B,4=N
            cp.coord_top(MAGENTA); buttons.reset()
            piece = None
            while piece is None:
                b = buttons.detect_press()
                if not b: time.sleep_ms(5); continue
                if b == 1: piece = 'q'
                elif b == 2: piece = 'r'
                elif b == 3: piece = 'b'
                elif b == 4: piece = 'n'
            proto.send('promotion_choice', {'piece': piece})
            board.clear(BLACK)

        elif t == 'game_over':
            show_gameover(d.get('result', '1/2-1/2'))

        # ignore other messages

# Boot
main()
