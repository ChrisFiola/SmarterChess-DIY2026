
from machine import Pin
import neopixel
from util.constants import (
    CP_COORD_START, CP_OK_PIX, CP_HINT_PIX,
    WHITE, BLACK
)

class ControlPanel:
    def __init__(self, pin, count):
        self.np = neopixel.NeoPixel(Pin(pin, Pin.OUT), count)
        self.count = count

    def set(self, idx, color):
        if 0 <= idx < self.count:
            self.np[idx] = color
            self.np.write()

    def fill(self, color, start=0, count=None):
        if count is None:
            count = self.count - start
        end = min(self.count, start + count)
        for i in range(start, end):
            self.np[i] = color
        self.np.write()

    def coord(self, on=True):
        self.fill(WHITE if on else BLACK, CP_COORD_START, 4)

    def ok(self, on=True):
        self.set(CP_OK_PIX, WHITE if on else BLACK)

    def hint(self, on=True, color=WHITE):
        self.set(CP_HINT_PIX, color if on else BLACK)
