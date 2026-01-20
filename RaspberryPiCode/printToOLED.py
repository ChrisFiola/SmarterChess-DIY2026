
#!/usr/bin/env python3
import sys, getopt
import board
import displayio
import digitalio
from PIL import Image, ImageDraw, ImageFont
import adafruit_st7789

# ------------------------------
# Parse CLI arguments
# ------------------------------
argv = sys.argv[1:]

textLine1 = ""
textLine2 = ""
textLine3 = ""
textLine4 = ""
textSize = 24

opts, args = getopt.getopt(argv, "ha:b:c:d:s:")

for opt, arg in opts:
    if opt == "-a": textLine1 = arg
    elif opt == "-b": textLine2 = arg
    elif opt == "-c": textLine3 = arg
    elif opt == "-d": textLine4 = arg
    elif opt == "-s": textSize = int(arg)

# ------------------------------
# Display Setup
# ------------------------------
spi = board.SPI()  # SCLK=GPIO11, MOSI=GPIO10

# Your wiring:
# CS  = GPIO8   -> board.CE0
# DC  = GPIO25  -> board.D25
# RST = GPIO27  -> board.D27

tft_cs = digitalio.DigitalInOut(board.CE0)
tft_dc = digitalio.DigitalInOut(board.D25)
tft_rst = digitalio.DigitalInOut(board.D27)

displayio.release_displays()

display_bus = displayio.FourWire(
    spi,
    command=tft_dc,
    chip_select=tft_cs,
    reset=tft_rst,
    baudrate=62_500_000
)

WIDTH = 240
HEIGHT = 240

display = adafruit_st7789.ST7789(
    display_bus,
    width=WIDTH,
    height=HEIGHT,
    rotation=0,
    auto_refresh=False
)

# ------------------------------
# Draw image with PIL
# ------------------------------
image = Image.new("RGB", (WIDTH, HEIGHT), "black")
draw = ImageDraw.Draw(image)

try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", textSize)
except:
    font = ImageFont.load_default()

lines = [textLine1, textLine2, textLine3, textLine4]
y = 10

for line in lines:
    w, h = draw.textsize(line, font=font)
    draw.text(((WIDTH - w) // 2, y), line, font=font, fill="white")
    y += h + 12

# ------------------------------
# Send image to the display
# ------------------------------
display.image(image)
display.refresh()
