
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
portrait = False

opts, args = getopt.getopt(
    argv, "ha:b:c:d:s:", ["firstLine=", "secondLine=", "thirdLine=", "fourthLine=", "textSize=", "portrait"]
)

for opt, arg in opts:
    if opt == "-a": textLine1 = arg
    elif opt == "-b": textLine2 = arg
    elif opt == "-c": textLine3 = arg
    elif opt == "-d": textLine4 = arg
    elif opt == "-s": textSize = int(arg)
    elif opt == "--portrait": portrait = True

# --------------------------
# ST7789 SPI Setup (1.14" 240x135)
# --------------------------
spi = board.SPI()

cs_pin = digitalio.DigitalInOut(board.CE0)
dc_pin = digitalio.DigitalInOut(board.D25)
reset_pin = digitalio.DigitalInOut(board.D27)

X_OFFSET = 53
Y_OFFSET = 40

if not portrait:
    WIDTH, HEIGHT = 240, 135
    ROTATION = 270
else:
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

try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", textSize)
except:
    font = ImageFont.load_default()

lines = [textLine1, textLine2, textLine3, textLine4]

y = 5
for line in lines:
    if not line:
        y += textSize + 10
        continue

    # Pillow 10+ compatible text measurement
    bbox = draw.textbbox((0, 0), line, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    draw.text(((WIDTH - w) // 2, y), line, font=font, fill="white")
    y += h + 8

# --------------------------
# Display it
# --------------------------
display.image(image)
