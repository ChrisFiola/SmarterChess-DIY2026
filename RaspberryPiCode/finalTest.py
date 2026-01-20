import board, digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display.st7789 import ST7789

spi = board.SPI()
cs = digitalio.DigitalInOut(board.CE0)  # Pin 24
dc = digitalio.DigitalInOut(board.D25)  # Pin 22
rst = digitalio.DigitalInOut(board.D27)  # Pin 13

# Waveshare 1.14" parameters:
# - Visible size: 240x135
# - Internal RAM: 240x320
# - Offsets: 53 and 40 (documented + widely validated)
display = ST7789(
    spi,
    cs=cs,
    dc=dc,
    rst=rst,
    baudrate=40000000,
    width=240,
    height=135,
    x_offset=40,  # Waveshare vertical crop offset
    y_offset=53,  # Waveshare horizontal crop offset
    rotation=270,  # orient like official examples
)

# create image exactly matching display
img = Image.new("RGB", (display.width, display.height), "blue")
draw = ImageDraw.Draw(img)

draw.rectangle((10, 10, 100, 100), outline="white", width=3)

display.image(img)
