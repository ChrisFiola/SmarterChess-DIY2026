# -*- coding: utf-8 -*-
"""
XPT2046 resistive touch controller driver for Raspberry Pi.
Shares SPI0 bus with ILI9341; uses CE1 (GPIO 7) as chip-select.

Wiring (BCM GPIO):
  T_CLK  GPIO 11  pin 23  (shared SPI0 SCK)
  T_DIN  GPIO 10  pin 19  (shared SPI0 MOSI)
  T_DO   GPIO  9  pin 21  (shared SPI0 MISO)
  T_CS   GPIO  7  pin 26  (SPI0 CE1)
  T_IRQ  GPIO 17  pin 11  (optional interrupt)

Calibration defaults suit a typical 2.8" ILI9341 module with MADCTL=0xe8
(portrait, BGR).  Adjust X_MIN/MAX, Y_MIN/MAX if touch positions are offset
after running a calibration sketch.
"""
import spidev
import RPi.GPIO as GPIO

W = 240
H = 320


class XPT2046:
    X_MIN, X_MAX   = 200, 3800
    Y_MIN, Y_MAX   = 200, 3800
    SAMPLE_JITTER  = 120
    DEBOUNCE_MS    = 120    # minimum ms between reported touches
    SWIPE_MIN_PX   = 36     # minimum vertical travel to treat as swipe
    SWIPE_AXIS_RATIO = 1.3  # vertical must dominate horizontal by this factor

    # Orientation correction matching MADCTL=0xe8 (same as Pico driver)
    SWAP_XY = False
    FLIP_X  = True
    FLIP_Y  = False

    def __init__(self, irq_pin: int = 17):
        self._irq_pin = irq_pin

        self._spi = spidev.SpiDev()
        self._spi.open(0, 1)                # SPI0, CE1
        self._spi.max_speed_hz = 2_000_000
        self._spi.mode = 0

        if irq_pin is not None:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(irq_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        self._last_touch_ms = 0
        self._gesture_active = False
        self._gesture_start = None
        self._gesture_last = None

    # ── low-level ─────────────────────────────────────────────────────────────

    def _read_chan(self, cmd: int) -> int:
        r = self._spi.xfer2([cmd, 0x00, 0x00])
        return (r[1] << 5) | (r[2] >> 3)

    # ── public API ────────────────────────────────────────────────────────────

    def touched(self) -> bool:
        if self._irq_pin is None:
            return True
        return GPIO.input(self._irq_pin) == 0

    def read(self):
        """Return (x, y) in screen pixels, or None if no valid touch."""
        if not self.touched():
            return None

        x1 = self._read_chan(0xD0)
        y1 = self._read_chan(0x90)
        x2 = self._read_chan(0xD0)
        y2 = self._read_chan(0x90)

        if abs(x1 - x2) > self.SAMPLE_JITTER or abs(y1 - y2) > self.SAMPLE_JITTER:
            return None

        x_raw = (x1 + x2) >> 1
        y_raw = (y1 + y2) >> 1

        if x_raw < 100 or y_raw < 100:
            return None

        if self.SWAP_XY:
            x_raw, y_raw = y_raw, x_raw

        x = int((x_raw - self.X_MIN) * W / (self.X_MAX - self.X_MIN))
        y = int((y_raw - self.Y_MIN) * H / (self.Y_MAX - self.Y_MIN))

        if self.FLIP_X:
            x = W - 1 - x
        if self.FLIP_Y:
            y = H - 1 - y

        return max(0, min(W - 1, x)), max(0, min(H - 1, y))

    def get_zone(self, zones: dict):
        """Return the name of the first zone that contains the touch point, or None."""
        pt = self.read()
        if pt is None:
            return None
        px, py = pt
        for name, (x0, y0, x1, y1) in zones.items():
            if x0 <= px <= x1 and y0 <= py <= y1:
                return name
        return None

    def get_zone_debounced(self, zones: dict, now_ms: int):
        """Like get_zone but suppresses repeated touches within DEBOUNCE_MS."""
        # Track swipe lifecycle for annotation screens. Gesture is emitted on
        # touch release to avoid fighting with tap detection.
        swipe_rect = zones.get("swipe_annotation")
        is_touching = self.touched()
        if swipe_rect and is_touching:
            pt = self.read()
            if pt is not None:
                px, py = pt
                x0, y0, x1, y1 = swipe_rect
                in_swipe_rect = x0 <= px <= x1 and y0 <= py <= y1
                if in_swipe_rect:
                    if not self._gesture_active:
                        self._gesture_active = True
                        self._gesture_start = pt
                    self._gesture_last = pt
        elif self._gesture_active and not is_touching:
            start = self._gesture_start
            end = self._gesture_last
            self._gesture_active = False
            self._gesture_start = None
            self._gesture_last = None
            if start and end and now_ms - self._last_touch_ms >= self.DEBOUNCE_MS:
                dx = end[0] - start[0]
                dy = end[1] - start[1]
                adx = abs(dx)
                ady = abs(dy)
                if ady >= self.SWIPE_MIN_PX and ady >= adx * self.SWIPE_AXIS_RATIO:
                    self._last_touch_ms = now_ms
                    # Swipe up = next page, swipe down = previous page.
                    return "page_next" if dy < 0 else "page_prev"

        if now_ms - self._last_touch_ms < self.DEBOUNCE_MS:
            return None
        zone = self.get_zone(zones)
        if zone:
            self._last_touch_ms = now_ms
        return zone

    def close(self) -> None:
        try:
            self._spi.close()
        except Exception:
            pass
