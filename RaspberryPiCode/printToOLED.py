"""
This code is based on examples kindly created, documented, and shared by Adafruit:

This is for use on (Linux) computers that are using CPython with
Adafruit Blinka to support CircuitPython libraries. CircuitPython does
not support PIL/pillow (python imaging library)!
"""

import board
import sys, getopt
import digitalio
from PIL import Image, ImageDraw, ImageFont
import adafruit_st7789

#Grab the arguments
argv = sys.argv[1:]

textLine1 = ""
textLine2 = ""
textLine3 = ""
textLine4 = ""
textSize = 24


#work through those arguments
try:
    opts, args = getopt.getopt(
        argv, "ha:b:c:d:s:",
        ["firstLine=", "secondLine=", "thirdLine=", "fourthLine=", "textSize="]
    )
except getopt.GetoptError:
    print("Usage: printToOLED.py -a <line1> -b <line2> -c <line3> -d <line4> -s <size>")
    sys.exit(2)

for opt, arg in opts:
    if opt == "-a": textLine1 = arg
    elif opt == "-b": textLine2 = arg
    elif opt == "-c": textLine3 = arg
    elif opt == "-d": textLine4 = arg
    elif opt == "-s": textSize = int(arg)


# -------------------------------
# ST7789 Setup
# -------------------------------
spi = board.SPI()

tft_cs = digitalio.DigitalInOut(board.CE0)   # GPIO8
tft_dc = digitalio.DigitalInOut(board.D25)   # GPIO25
tft_rst = digitalio.DigitalInOut(board.D27)  # GPIO27

tft_cs.direction = digitalio.Direction.OUTPUT
tft_dc.direction = digitalio.Direction.OUTPUT
tft_rst.direction = digitalio.Direction.OUTPUT

WIDTH = 240
HEIGHT = 240

display = adafruit_st7789.ST7789(
    spi,
    cs=tft_cs,
    dc=tft_dc,
    rst=tft_rst,
    width=WIDTH,
    height=HEIGHT,
    rotation=0,
    baudrate=62_500_000  # stable speed
)


# -------------------------------
# Create RGB image buffer
# -------------------------------
image = Image.new("RGB", (WIDTH, HEIGHT), "black")
draw = ImageDraw.Draw(image)

try:
    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        textSize
    )
except:
    font = ImageFont.load_default()


# -------------------------------
# Draw text centered line by line
# -------------------------------
lines = [textLine1, textLine2, textLine3, textLine4]
y = 10

for line in lines:
    if not line:
        y += textSize + 6
        continue
    w, h = draw.textsize(line, font=font)
    draw.text(((WIDTH - w) // 2, y), line, font=font, fill="white")
    y += h + 12


# -------------------------------
# Push image to the display
# -------------------------------
display.image(image)
