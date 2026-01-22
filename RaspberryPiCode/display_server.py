
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
BLACK_BG = Image.new("RGB", (W, H), "BLACK")

# Font cache
FONTS = {}
def get_font(size):
    if size not in FONTS:
        FONTS[size] = ImageFont.truetype(FONT_PATH, size)
    return FONTS[size]

# ------------------------------------------------------
# AUTO FONT SCALING
# ------------------------------------------------------
def find_best_font_size(lines):
    for size in range(32, 14, -1):  # Try sizes 32 → 15
        font = get_font(size)
        draw = ImageDraw.Draw(BLACK_BG)

        total_h = 0
        max_w = 0

        for ln in lines:
            if not ln:
                h = size
                w = 0
            else:
                bbox = draw.textbbox((0,0), ln, font=font)
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]

            total_h += h + 6
            max_w = max(max_w, w)

        total_h -= 6  # Remove extra spacing

        if total_h <= H - 5 and max_w <= W - 5:
            return size

    return 16  # fallback


# ------------------------------------------------------
# Draw perfectly centered text
# ------------------------------------------------------
def draw_centered_text(lines):
    size = find_best_font_size(lines)
    font = get_font(size)
    img = BLACK_BG.copy()
    draw = ImageDraw.Draw(img)

    heights = []
    total_h = 0

    for ln in lines:
        if not ln:
            h = size
        else:
            bbox = draw.textbbox((0,0), ln, font=font)
            h = bbox[3] - bbox[1]
        heights.append(h)
        total_h += h + 6

    total_h -= 6  
    y = (H - total_h) // 2

    for ln, h in zip(lines, heights):
        if ln:
            bbox = draw.textbbox((0,0), ln, font=font)
            w = bbox[2] - bbox[0]
            draw.text(((W - w)//2, y), ln, font=font, fill="WHITE")
        y += h + 6

    disp.ShowImage(img)


# ------------------------------------------------------
# Splash screen
# ------------------------------------------------------
def draw_splash():
    img = BLACK_BG.copy()
    draw = ImageDraw.Draw(img)
    font = get_font(28)

    txt = "SMARTCHESS"
    bbox = draw.textbbox((0,0), txt, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    draw.text(((W-w)//2, (H-h)//2 - 10),
              txt, font=font, fill="WHITE")

    disp.ShowImage(img)


# Draw splash on start
draw_splash()

# Signal ready to Pi
with open(READY_FLAG, "w") as f:
    f.write("ready\n")


# ------------------------------------------------------
# Main loop
# ------------------------------------------------------
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
    lines = parts[:-1]

    draw_centered_text(lines)
