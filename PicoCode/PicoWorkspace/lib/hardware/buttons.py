
from machine import Pin
import time
from util.constants import DEBOUNCE_MS

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
        """Return button number (1..8) on falling edge; None otherwise."""
        for idx, p in enumerate(self.pins):
            cur = p.value()   # 0=pressed, 1=released
            prev = self._last[idx]
            self._last[idx] = cur
            if prev == 1 and cur == 0:
                time.sleep_ms(DEBOUNCE_MS)
                return idx + 1
        return None

    @staticmethod
    def is_non_coord_button(btn):
        # Button 7 (A1 OK) and Button 8 (Hint) are not coordinates in 8-button layout
        return btn in (7, 8)
