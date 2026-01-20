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
preset = "waveshare-a"   # default preset
calibrate = False

# Supported presets with (width, height, rotation, x_off, y_off)
PRESETS = {
    # Landscape 240x135, common on Waveshare 1.14"
    # Try A first; if misaligned, try B
    "waveshare-a": {"w": 240, "h": 135, "rot": 270, "xo": 40, "yo": 53},
    "waveshare-b": {"w": 240, "h": 135, "rot": 90,  "xo": 52, "yo": 40},

    # Portrait variants (135x240). Use if you prefer portrait.
    "waveshare-a-portrait": {"w": 135, "h": 240, "rot": 0,   "xo": 40, "yo": 53},
    "waveshare-b-portrait": {"w": 135, "h": 240, "rot": 180, "xo": 52, "yo": 40},

    # Fallback (no offsets) — useful for clones to sanity check
    "zero-offset-land":     {"w": 240, "h": 135, "rot": 270, "xo": 0,  "yo": 0},
    "zero-offset-port":     {"w": 135, "h": 240, "rot": 0,   "xo": 0,  "yo": 0},
}

def usage():
    print(
        "Usage: printToOLED.py "
        "-a <line1> -b <line2> -c <line3> [-d <line4>] -s <size> "
        "[--preset <name>] [--portrait] [--calibrate]\n\n"
        "Presets:\n" +
        "\n".join(f"  - {k}: {v}" for k, v in PRESETS.items())
    )

try:
    opts, args = getopt.getopt(
        argv,
        "ha:b:c:d:s:",
        ["firstLine=", "secondLine=", "thirdLine=", "fourthLine=", "textSize=", "portrait", "preset=", "calibrate"]
    )
except getopt.GetoptError:
    usage()
    sys.exit(2)

for opt, arg in opts:
    if opt == "-h":
        usage()
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
    elif opt == "--preset":
        preset = arg.strip().lower()
    elif opt == "--calibrate":
        calibrate = True

# If user specified portrait but kept default preset, map to portrait variant
if portrait:
    if preset == "waveshare-a":
        preset = "waveshare-a-portrait"
    elif preset == "waveshare-b":
        preset = "waveshare-b-portrait"

if preset not in PRESETS:
    print(f"[WARN] Unknown preset '{preset}', using 'waveshare-a'")
    preset = "waveshare-a"

cfg = PRESETS[preset]
WIDTH, HEIGHT = cfg["w"], cfg["h"]
ROTATION = cfg["rot"]
X_OFFSET = cfg["xo"]
Y_OFFSET = cfg["yo"]

# --------------------------
# ST7789 SPI Setup
# --------------------------
spi = board.SPI()  # SCLK=GPIO11, MOSI=GPIO10

cs_pin = digitalio.DigitalInOut(board.CE0)     # GPIO8
dc_pin = digitalio.DigitalInOut(board.D25)     # GPIO25
reset_pin = digitalio.DigitalInOut(board.D27)  # GPIO27

# Start conservative; you can raise to 62_500_000 after it's stable
BAUD = 40_000_000

display = ST7789(
    spi,
    cs=cs_pin,
    dc=dc_pin,
    rst=reset_pin,
    baudrate=BAUD,
    width=WIDTH,
    height=HEIGHT,
    rotation=ROTATION,
    x_offset=X_OFFSET,
    y_offset=Y_OFFSET
)

# --------------------------
# Build PIL Image (size must match display)
# --------------------------
from PIL import ImageDraw  # already imported PIL.Image above
from PIL import ImageFont  # already imported
from PIL import Image

image = Image.new("RGB", (display.width, display.height), "black")
draw = ImageDraw.Draw(image)

# Calibration pattern: border + grid + labels
if calibrate:
    # Outer border
    draw.rectangle([(0, 0), (display.width - 1, display.height - 1)], outline=(255, 0, 0), width=2)
    # Inner border
    draw.rectangle([(2, 2), (display.width - 3, display.height - 3)], outline=(0, 255, 0), width=1)
    # Grid lines
    for x in range(0, display.width, 20):
        draw.line([(x, 0), (x, display.height)], fill=(0, 0, 255))
    for y in range(0, display.height, 20):
        draw.line([(0, y), (display.width, y)], fill=(0, 0, 255))
    # Labels
    try:
        smallfont = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except Exception:
        smallfont = ImageFont.load_default()
    label = f"{preset} | rot={ROTATION} | off=({X_OFFSET},{Y_OFFSET}) | {display.width}x{display.height}"
    bbox = draw.textbbox((0, 0), label, font=smallfont)
    lw = bbox[2] - bbox[0]
    draw.text(((display.width - lw) // 2, 5), label, font=smallfont, fill=(255, 255, 0))

else:
    # Text rendering (centered lines)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", textSize)
    except Exception:
        font = ImageFont.load_default()

    lines = [textLine1, textLine2, textLine3, textLine4]
    y = 5
    for line in lines:
        if not line:
            y += textSize + 8
            continue
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        draw.text(((display.width - w) // 2, y), line, font=font, fill="white")
        y += h + 8

# --------------------------
# Display it
# --------------------------
display.image(image)
