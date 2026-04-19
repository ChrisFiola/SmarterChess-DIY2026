# -*- coding: utf-8 -*-
"""
ILI9341 2.8" TFT driver for Raspberry Pi (spidev + RPi.GPIO).
Drop-in for Waveshare LCD_1inch14: same Init/ShowImage/bl_DutyCycle/clear API.

Wiring (BCM GPIO):
  MOSI  GPIO 10  pin 19    SCK   GPIO 11  pin 23
  MISO  GPIO  9  pin 21    CS    GPIO  8  pin 24  (SPI0 CE0, auto)
  DC    GPIO 25  pin 22    RST   GPIO 27  pin 13
  BL    GPIO 18  pin 12
"""
import time
import spidev
import RPi.GPIO as GPIO

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

_INIT_SEQ = (
    (0xEF, b"\x03\x80\x02"),
    (0xCF, b"\x00\xc1\x30"),
    (0xED, b"\x64\x03\x12\x81"),
    (0xE8, b"\x85\x00\x78"),
    (0xCB, b"\x39\x2c\x00\x34\x02"),
    (0xF7, b"\x20"),
    (0xEA, b"\x00\x00"),
    (0xC0, b"\x23"),
    (0xC1, b"\x10"),
    (0xC5, b"\x3e\x28"),
    (0xC7, b"\x86"),
    (0x36, b"\xe0"),           # MADCTL: portrait, RGB (bit3=BGR cleared)
    (0x3A, b"\x55"),           # 16-bit colour
    (0xB1, b"\x00\x18"),
    (0xB6, b"\x08\x82\x27"),
    (0xF2, b"\x00"),
    (0x26, b"\x01"),
    (0xE0, b"\x0f\x31\x2b\x0c\x0e\x08\x4e\xf1\x37\x07\x10\x03\x0e\x09\x00"),
    (0xE1, b"\x00\x0e\x14\x03\x11\x07\x31\xc1\x48\x08\x0f\x0c\x31\x36\x0f"),
)

_SPI_CHUNK = 4096


class ILI9341:
    """Minimal ILI9341 driver. Matches Waveshare LCD_1inch14 public interface."""

    width  = 240
    height = 320

    def __init__(self, dc: int = 25, rst: int = 27, bl: int = 18,
                 spi_speed: int = 24_000_000):
        self._dc  = dc
        self._rst = rst
        self._bl  = bl

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(dc,  GPIO.OUT)
        GPIO.setup(rst, GPIO.OUT)
        GPIO.setup(bl,  GPIO.OUT, initial=GPIO.LOW)

        self._spi = spidev.SpiDev()
        self._spi.open(0, 0)
        self._spi.max_speed_hz = spi_speed
        self._spi.mode = 0
        print(f"[ILI9341] numpy_accel={_HAS_NUMPY}", flush=True)

        # writebytes2 accepts bytes-like objects; xfer2 needs a list but always works
        if not hasattr(self._spi, 'writebytes2'):
            self._spi.writebytes2 = lambda d: self._spi.xfer2(list(d))

        self._hard_reset()
        for cmd, data in _INIT_SEQ:
            self._cmd(cmd)
            if data:
                self._write_data(data)
        self._cmd(0x11)   # Sleep out
        time.sleep(0.12)
        self._cmd(0x29)   # Display on

    # ── low-level ─────────────────────────────────────────────────────────────

    def _hard_reset(self) -> None:
        GPIO.output(self._rst, GPIO.LOW);  time.sleep(0.015)
        GPIO.output(self._rst, GPIO.HIGH); time.sleep(0.12)

    def _cmd(self, cmd: int) -> None:
        GPIO.output(self._dc, GPIO.LOW)
        self._spi.writebytes([cmd])

    def _write_data(self, data) -> None:
        GPIO.output(self._dc, GPIO.HIGH)
        # Keep as bytes/bytearray — avoid memoryview (silently fails on some spidev builds)
        if not isinstance(data, (bytes, bytearray)):
            data = bytes(data)
        for off in range(0, len(data), _SPI_CHUNK):
            self._spi.writebytes2(data[off: off + _SPI_CHUNK])

    def _set_window(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self._cmd(0x2A)
        self._write_data(bytes([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
        self._cmd(0x2B)
        self._write_data(bytes([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
        self._cmd(0x2C)

    # ── public API ────────────────────────────────────────────────────────────

    def Init(self) -> None:
        """No-op: hardware is initialised in __init__."""

    def bl_DutyCycle(self, duty: int) -> None:
        GPIO.output(self._bl, GPIO.HIGH if duty > 0 else GPIO.LOW)

    def clear(self) -> None:
        self._set_window(0, 0, self.width - 1, self.height - 1)
        GPIO.output(self._dc, GPIO.HIGH)
        row = bytearray(self.width * 2)   # all zeros = black
        for _ in range(self.height):
            self._spi.writebytes2(row)

    def ShowImage(self, img) -> None:
        """Push a Pillow RGB image (width×height) to the display."""
        self._set_window(0, 0, self.width - 1, self.height - 1)
        GPIO.output(self._dc, GPIO.HIGH)

        if _HAS_NUMPY:
            arr = np.array(img, dtype=np.uint8)
            r = arr[:, :, 0].astype(np.uint16)
            g = arr[:, :, 1].astype(np.uint16)
            b = arr[:, :, 2].astype(np.uint16)
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            # byteswap: convert little-endian uint16 to big-endian SPI bytes
            data = bytearray(rgb565.byteswap().tobytes())
        else:
            # Fast C-backed Pillow conversion path (massively faster than per-pixel Python loop).
            try:
                data = bytearray(img.convert("RGB").tobytes("raw", "RGB;16B"))
            except Exception:
                data = bytearray(self.width * self.height * 2)
                i = 0
                for r, g, b in img.getdata():
                    c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                    data[i] = c >> 8
                    data[i + 1] = c & 0xFF
                    i += 2

        # Send as bytearray slices — most compatible across spidev versions
        for off in range(0, len(data), _SPI_CHUNK):
            self._spi.writebytes2(data[off: off + _SPI_CHUNK])

    def close(self) -> None:
        try:
            self._spi.close()
        except Exception:
            pass
        try:
            GPIO.cleanup([self._dc, self._rst, self._bl])
        except Exception:
            pass
