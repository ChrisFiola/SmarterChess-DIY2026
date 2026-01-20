
# ws_test_ws_presetA.py
import board, digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display.st7789 import ST7789

spi = board.SPI()
cs = digitalio.DigitalInOut(board.CE0)   # GPIO8
dc = digitalio.DigitalInOut(board.D25)   # GPIO25
rst = digitalio.DigitalInOut(board.D27)  # GPIO27

display = ST7789(
    spi,
    cs=cs,
    dc=dc,
    rst=rst,
    baudrate=40_000_000,   # keep conservative first
    width=240,
    height=135,
    rotation=270,          # landscape; try 90 if upside down
    x_offset=40,
    y_offset=53
)

w, h = display.width, display.height
img = Image.new("RGB", (w, h), "blue")
d = ImageDraw.Draw(img)
d.rectangle((0, 0, w-1, h-1), outline="white", width=3)
display.image(img)
print("OK: drew", w, "x", h, "with offsets 40,53 and rotation 270")
