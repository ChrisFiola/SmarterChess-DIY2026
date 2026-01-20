import board, digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display.st7789 import ST7789

spi = board.SPI()
cs = digitalio.DigitalInOut(board.CE0)
dc = digitalio.DigitalInOut(board.D25)
rst = digitalio.DigitalInOut(board.D27)

# Waveshare 1.14” ST7789 — most common settings
display = ST7789(
    spi,
    cs=cs,
    dc=dc,
    rst=rst,
    baudrate=40_000_000,
    width=135,  # logical driver width
    height=240,  # logical driver height
    rotation=90,  # landscape
    x_offset=0,
    y_offset=0,
)

print("Display reports size:", display.width, "x", display.height)

# IMPORTANT: Use EXACT reported dimensions
w = display.width
h = display.height

print("Creating image:", w, "x", h)
img = Image.new("RGB", (w, h), "blue")
draw = ImageDraw.Draw(img)

# Draw border to see edges clearly
draw.rectangle((0, 0, w - 1, h - 1), outline="white", width=3)

display.image(img)
