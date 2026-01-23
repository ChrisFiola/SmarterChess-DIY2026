from machine import Pin
import neopixel
import time
from util.constants import (
    BOARD_W, BOARD_H, MATRIX_ORIGIN_BOTTOM_RIGHT, MATRIX_ZIGZAG,
    BLACK, GREEN, BLUE, RED, YELLOW, WHITE, CYAN, ORANGE
)

class Chessboard:
    def __init__(self, pin, w, h, origin_bottom_right=MATRIX_ORIGIN_BOTTOM_RIGHT, zigzag=MATRIX_ZIGZAG):
        self.w, self.h = w, h
        self.origin_bottom_right = origin_bottom_right
        self.zigzag = zigzag
        self.np = neopixel.NeoPixel(Pin(pin, Pin.OUT), w * h)

    def clear(self, color=BLACK):
        for i in range(self.w * self.h):
            self.np[i] = color
        self.np.write()

    def _xy_to_index(self, x, y):
        row = y
        if self.origin_bottom_right:
            if self.zigzag:
                if row % 2 == 0:
                    col_index = (self.w - 1) - x   # even row: right->left
                else:
                    col_index = x                   # odd row: left->right
            else:
                col_index = (self.w - 1) - x
            idx = row * self.w + col_index
        else:
            row_from_top = (self.h - 1) - y
            if self.zigzag:
                if row_from_top % 2 == 0:
                    col_index = x
                else:
                    col_index = (self.w - 1) - x
            else:
                col_index = x
            idx = row_from_top * self.w + col_index
        return idx

    def set_square(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.np[self._xy_to_index(x, y)] = color

    def write(self):
        self.np.write()

    def algebraic_to_xy(self, sq):
        if not sq or len(sq) < 2:
            return None
        f, r = sq[0].lower(), sq[1]
        if not ('a' <= f <= 'h'):
            return None
        if not ('1' <= r <= '8'):
            return None
        x = ord(f) - ord('a')     # a..h -> 0..7
        y = int(r) - 1            # 1..8 -> 0..7
        return (x, y)

    def show_markings(self):
        # simple checkered board
        for y in range(self.h):
            for x in range(self.w):
                color = (80, 80, 80) if ((x + y) % 2 == 0) else (160, 160, 160)
                self.set_square(x, y, color)
        self.write()

    def opening_markings(self):
        # diagonal sweep intro animation
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
        """Light squares progressively (bottom->top, right->left) to 64. Returns updated count."""
        total = self.w * self.h
        if count >= total:
            return count
        idx = count
        y = idx // self.w
        x = (self.w - 1) - (idx % self.w)  # right to left
        self.set_square(x, y, BLUE)
        self.write()
        return count + 1

    def error_flash(self, times=3):
        for _ in range(times):
            self.clear(BLUE)
            for i in range(8):
                self.set_square(i, 7 - i, RED)
                self.set_square(i, i, RED)
            self.write()
            time.sleep_ms(450)
            self.show_markings()
            time.sleep_ms(450)

    def light_up_move(self, m, mode='Y'):
        """
        m: 'e2e4' or 'e7e8q' (we use first 4 chars)
        mode: 'Y' (human), 'N' (engine), 'H' (hint)
        """
        if not m or len(m) < 4:
            return
        frm = m[:2]
        to = m[2:4]

        if mode == 'Y':
            c_from, c_to = YELLOW, WHITE
        elif mode == 'N':
            c_from, c_to = ORANGE, GREEN
        else:  # 'H'
            c_from, c_to = CYAN, BLUE

        xy_f = self.algebraic_to_xy(frm)
        xy_t = self.algebraic_to_xy(to)
        if xy_f:
            self.set_square(xy_f[0], xy_f[1], c_from)
        if xy_t:
            self.set_square(xy_t[0], xy_t[1], c_to)
        self.write()
