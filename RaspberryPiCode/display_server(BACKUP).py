#!/usr/bin/env python3
import os, sys, time
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageColor

# Waveshare driver import
sys.path.append("/home/king/LCD_Module_RPI_code/RaspberryPi/python")
from lib.LCD_1inch14 import LCD_1inch14

PIPE = "/tmp/lcdpipe"
READY_FLAG = "/tmp/display_server_ready"

# Remove stale ready file
if os.path.exists(READY_FLAG):
    os.remove(READY_FLAG)

# Initialize display
disp = LCD_1inch14()
disp.Init()
disp.bl_DutyCycle(80)
disp.clear()

# Signal ready
with open(READY_FLAG, "w") as f:
    f.write("ready\n")

# Screen parameters
W, H = disp.width, disp.height
FONT_PATH = "/home/king/LCD_Module_RPI_code/RaspberryPi/python/Font/Font00.ttf"

FONTS = {}
BLACK_BG = Image.new("RGB", (W, H), "BLACK")

# ---------------------------
#  FONT CACHE
# ---------------------------
def get_font(size: int):
    if size not in FONTS:
        FONTS[size] = ImageFont.truetype(FONT_PATH, size)
    return FONTS[size]

# ---------------------------
#  TEXT WRAPPING
# ---------------------------
def wrap_text(text, font, max_width):
    """
    Wrap text to fit into pixel width.
    Returns a list of wrapped lines.
    """
    words = text.split(" ")
    lines = []
    current = ""

    for word in words:
        test = (current + " " + word).strip()
        w = font.getlength(test)
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            # If a single word is too long, break it char-by-char
            while font.getlength(word) > max_width:
                for i in range(1, len(word)+1):
                    if font.getlength(word[:i]) > max_width:
                        lines.append(word[:i-1])
                        word = word[i-1:]
                        break
            current = word

    if current:
        lines.append(current)

    return lines

# ---------------------------
#  PARTIAL REDRAW ENGINE
# ---------------------------
def draw_text(lines, size):
    """
    Only redraw changed lines, keeping background persistent.
    """
    font = get_font(size)
    img = BLACK_BG.copy()
    draw = ImageDraw.Draw(img)

    # Wrap long lines
    wrapped = []
    for ln in lines:
        if ln.strip():
            wrapped.extend(wrap_text(ln, font, W - 10))
        else:
            wrapped.append("")

    # Position lines vertically
    y = 5
    for ln in wrapped:
        if ln == "":
            y += size + 4
            continue

        bbox = draw.textbbox((0, 0), ln, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(((W - tw) // 2, y), ln, font=font, fill="WHITE")
        y += th + 6

    disp.ShowImage(img)

# ---------------------------
#  SPLASH SCREEN
# ---------------------------
def draw_splash():
    img = BLACK_BG.copy()
    draw = ImageDraw.Draw(img)

    # Title
    title_font = get_font(28)
    msg = "SMARTCHESS"
    bbox = draw.textbbox((0, 0), msg, font=title_font)
    draw.text(((W - (bbox[2]-bbox[0])) // 2, 20),
              msg, font=title_font, fill="WHITE")

    # Simple ASCII-style chess icon
    logo = [
        "  ♜ ♞ ♝ ♛ ♚ ♝ ♞ ♜  ",
        "  ♟ ♟ ♟ ♟ ♟ ♟ ♟ ♟  ",
    ]
    piece_font = get_font(22)

    y = 70
    for row in logo:
        bbox = draw.textbbox((0, 0), row, font=piece_font)
        draw.text(((W - (bbox[2]-bbox[0])) // 2, y),
                  row, font=piece_font, fill="WHITE")
        y += (bbox[3]-bbox[1]) + 2

    disp.ShowImage(img)

# Show splash until first pipe message
draw_splash()

# ---------------------------
#  PIPE LOOP
# ---------------------------
pipe = open(PIPE, "r")
last_msg = None

while True:
    line = pipe.readline()

    if not line:
        time.sleep(0.003)
        continue

    if line == last_msg:
        continue

    last_msg = line

    parts = line.strip().split("|")
    size = int(parts[-1])
    lines = parts[:-1]

    draw_text(lines, size)
