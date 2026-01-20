#!/usr/bin/env python3
import sys, getopt
import board
import digitalio
from PIL import Image, ImageDraw, ImageFont
from adafruit_rgb_display.st7789 import ST7789

# --------------------------
# Parse CLI arguments
# --------------------------
argv = sys.argv[1:]

textLine1 = ""
textLine2 = ""
textLine3 = ""
textLine4 = ""
textSize = 24
portrait = False  # default landscape (240x135)

opts, args = getopt.getopt(
    argv, "ha:b:c:d:s:", ["firstLine=", "secondLine=", "thirdLine=", "fourthLine=", "textSize=", "portrait"]
)

for opt, arg in opts:
    if opt == "-h":
        print("Usage: printToOLED.py -a <line1> -b <line2> -c <line3> -d <line4> -s <size> [--portrait]")
        sys.exit(0)
    elif opt in ("-a", "--firstLine"):
        textLine1 = arg
    elif opt in ("-b", "--secondLine"):
        textLine2 = arg
    elif opt in ("-c", "--thirdLine"):
        textLine3 = arg
    elif opt in ("-d", "--fourthLine"):
        textLine4 = arg
    elif opt in ("-s", "--textSize"):
        textSize = int(arg)
    elif opt == "--portrait":
        portrait = True

# --------------------------
# ST7789 SPI Setup (1.14" 240x135)
# --------------------------
spi = board.SPI()  # SCLK=GPIO11, MOSI=GPIO10

cs_pin = digitalio.DigitalInOut(board.CE0)     # GPIO8
dc_pin = digitalio.DigitalInOut(board.D25)     # GPIO25
reset_pin = digitalio.DigitalInOut(board.D27)  # GPIO27

# Adafruit 1.14" ST7789: 240x135 active area with controller offsets
X_OFFSET = 53
Y_OFFSET = 40

if not portrait:
    # Landscape: 240x135
    WIDTH, HEIGHT = 240, 135
    ROTATION = 270  # choose 0/90/180/270 depending on connector side preference
else:
    # Portrait: 135x240
    WIDTH, HEIGHT = 135, 240
    ROTATION = 0

display = ST7789(
    spi,
    cs=cs_pin,
    dc=dc_pin,
    rst=reset_pin,
    baudrate=62_500_000,
    width=WIDTH,
    height=HEIGHT,
    rotation=ROTATION,
    x_offset=X_OFFSET,
    y_offset=Y_OFFSET
)

# --------------------------
# Build PIL Image
# --------------------------
image = Image.new("RGB", (WIDTH, HEIGHT), "black")
draw = ImageDraw.Draw(image)

# Robust font loading
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", textSize)
except Exception:
    font = ImageFont.load_default()

lines = [textLine1, textLine2, textLine3, textLine4]
y = 5

for line in lines:
    if not line:
        y += textSize + 6
        continue
    # Centered text
    w, h = draw.textsize(line, font=font)
    draw.text(((WIDTH - w) // 2, y), line, font=font, fill="white")
    y += h + 8

