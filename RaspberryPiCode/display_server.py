#!/usr/bin/env python3
import os, sys, time
from PIL import Image, ImageDraw, ImageFont

# Import Waveshare driver
sys.path.append("/home/king/LCD_Module_RPI_code/RaspberryPi/python")
from lib.LCD_1inch14 import LCD_1inch14

PIPE = "/tmp/lcdpipe"
READY_FLAG = "/tmp/display_server_ready"

# Remove stale ready flag if exists
if os.path.exists(READY_FLAG):
    os.remove(READY_FLAG)

# Initialize display
disp = LCD_1inch14()
disp.Init()
disp.bl_DutyCycle(80)
disp.clear()

# Screen constants
W, H = disp.width, disp.height
FONT_PATH = "/home/king/LCD_Module_RPI_code/RaspberryPi/python/Font/Font00.ttf"

# Cache
FONTS = {}
BLACK_BG = Image.new("RGB", (W, H), "BLACK")

def get_font(size):
    """Cached font loader."""
    if size not in FONTS:
        FONTS[size] = ImageFont.truetype(FONT_PATH, size)
    return FONTS[size]

# -------------------------------------------------------------------
# WRAPPING TEXT TO WIDTH
# -------------------------------------------------------------------
def wrap_text(text, font, max_width):
    """
    Break text into multiple lines so each fits within max_width pixels.
    """
    words = text.split(" ")
    lines = []
    cur = ""

    for w in words:
        test = (cur + " " + w).strip()
        if font.getlength(test) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            # Break too-long words by characters
            while font.getlength(w) > max_width:
                for i in range(1, len(w) + 1):
                    if font.getlength(w[:i]) > max_width:
                        lines.append(w[:i - 1])
                        w = w[i - 1:]
                        break
            cur = w

    if cur:
        lines.append(cur)

    return lines

# -------------------------------------------------------------------
# PERFECTLY CENTERED TEXT
# -------------------------------------------------------------------
def draw_centered_text(lines, size):
    """
    Render wrapped + centered text fully centered both vertically
    and horizontally. Redraw entire screen each time.
    """
    img = BLACK_BG.copy()
    draw = ImageDraw.Draw(img)
    font = get_font(size)

    # Expand wrapped text
    wrapped = []
    for ln in lines:
        if ln.strip():
            wrapped.extend(wrap_text(ln, font, W - 12))
        else:
            wrapped.append("")

    # Compute total height
    total_height = 0
    line_heights = []
    for ln in wrapped:
        if not ln:
            h = size
        else:
            bbox = draw.textbbox((0, 0), ln, font=font)
            h = bbox[3] - bbox[1]
        line_heights.append(h)
        total_height += h + 8  # spacing

    total_height -= 8  # remove extra spacing after last line

    # Vertical center
    y = (H - total_height) // 2

    # Draw lines
    for ln, lh in zip(wrapped, line_heights):
        if ln:
            bbox = draw.textbbox((0, 0), ln, font=font)
            tw = bbox[2] - bbox[0]
            draw.text(((W - tw) // 2, y), ln, font=font, fill="WHITE")
        y += lh + 8

    disp.ShowImage(img)

# -------------------------------------------------------------------
# SPLASH SCREEN (shown before first message)
# -------------------------------------------------------------------
def draw_splash():
    img = BLACK_BG.copy()
    draw = ImageDraw.Draw(img)

    title_font = get_font(32)
    title = "SMARTCHESS"
    bbox = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((W - (bbox[2]-bbox[0])) // 2, 20),
              title, font=title_font, fill="WHITE")

    logo = ["♜ ♞ ♝ ♛ ♚ ♝ ♞ ♜"]
    logo_font = get_font(28)
    for i, row in enumerate(logo):
        bbox = draw.textbbox((0, 0), row, font=logo_font)
        draw.text(((W - (bbox[2]-bbox[0])) // 2, 70 + i*32),
                   row, font=logo_font, fill="WHITE")

    disp.ShowImage(img)

# Show splash immediately
draw_splash()

# Signal ready
with open(READY_FLAG, "w") as f:
    f.write("ready\n")

# -------------------------------------------------------------------
# MAIN LOOP
# -------------------------------------------------------------------
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

    draw_centered_text(lines, size)
