
#!/usr/bin/env python3
import os, sys, time
from PIL import Image, ImageDraw, ImageFont

# Waveshare ST7789 driver
sys.path.append("/home/king/LCD_Module_RPI_code/RaspberryPi/python")
from lib.LCD_1inch14 import LCD_1inch14

PIPE = "/tmp/lcdpipe"
READY_FLAG = "/tmp/display_server_ready"

# Remove stale ready flag
if os.path.exists(READY_FLAG):
    os.remove(READY_FLAG)

# Init display
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
    if size not in FONTS:
        FONTS[size] = ImageFont.truetype(FONT_PATH, size)
    return FONTS[size]

# -------------------------------------------------------
# Perfect centering for EXACTLY the style you liked
# -------------------------------------------------------
def draw_centered_text(lines, size):
    img = BLACK_BG.copy()
    draw = ImageDraw.Draw(img)
    font = get_font(size)

    # Compute total height
    heights = []
    total_h = 0

    for ln in lines:
        if not ln:
            h = size   # blank line spacing
        else:
            bbox = draw.textbbox((0,0), ln, font=font)
            h = bbox[3] - bbox[1]
        heights.append(h)
        total_h += h + 6

    total_h -= 6  # remove last extra spacing

    # Vertical center
    y = (H - total_h) // 2

    # Draw each line (centered horizontally)
    for ln, h in zip(lines, heights):
        if ln:
            bbox = draw.textbbox((0,0), ln, font=font)
            w = bbox[2] - bbox[0]
            draw.text(((W - w)//2, y), ln, font=font, fill="WHITE")
        y += h + 6

    disp.ShowImage(img)

# -------------------------------------------------------
# Splash screen (fixes white startup)
# -------------------------------------------------------
def draw_splash():
    img = BLACK_BG.copy()
    draw = ImageDraw.Draw(img)

    title = "SMARTCHESS"
    font = get_font(26)

    bbox = draw.textbbox((0,0), title, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    draw.text(((W - w)//2, (H - h)//2 - 10), title, font=font, fill="WHITE")

    disp.ShowImage(img)

# Show splash immediately (no white screen)
draw_splash()

# Signal ready
with open(READY_FLAG, "w") as f:
    f.write("ready\n")

# -------------------------------------------------------
# Main loop
# -------------------------------------------------------
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
