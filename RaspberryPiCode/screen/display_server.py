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
def get_font(size: int):
    if size not in FONTS:
        FONTS[size] = ImageFont.truetype(FONT_PATH, size)
    return FONTS[size]

# ------------------------------------------------------
# AUTO FONT SCALING
# ------------------------------------------------------
def find_best_font_size(lines, min_size=14, max_size=28, vpad=4, spacing=6):
    """
    Choose the largest font size that fits both width and height with given padding & spacing.
    Returns: (size, spacing)
    """
    # Try from biggest → smallest
    for size in range(max_size, min_size - 1, -1):
        font = get_font(size)
        draw = ImageDraw.Draw(BLACK_BG)

        total_h = 0
        max_w = 0

        for ln in lines:
            if not ln:
                h = size  # blank line spacing approximated to size
                w = 0
            else:
                bbox = draw.textbbox((0, 0), ln, font=font)
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
            total_h += h + spacing
            max_w = max(max_w, w)

        total_h -= spacing  # remove extra spacing after last line

        if total_h <= (H - 2 * vpad) and max_w <= (W - 2 * vpad):
            return size, spacing

    return min_size, spacing  # fallback

# ------------------------------------------------------
# Draw centered text with explicit size/spacing
# ------------------------------------------------------
def draw_centered_text_with_size(lines, size: int, spacing: int = 6, vpad: int = 0):
    """
    Draw 'lines' using font 'size' and 'spacing', centered on screen.
    """
    img = BLACK_BG.copy()
    draw = ImageDraw.Draw(img)
    font = get_font(size)

    # Measure
    heights = []
    total_h = 0
    for ln in lines:
        if not ln:
            h = size  # blank line spacing approximated to font size
        else:
            bbox = draw.textbbox((0, 0), ln, font=font)
            h = bbox[3] - bbox[1]
        heights.append(h)
        total_h += h + spacing
    total_h -= spacing

    # Vertical center
    y = max(0, (H - total_h) // 2)

    # Draw each line centered horizontally
    for ln, h in zip(lines, heights):
        if ln:
            bbox = draw.textbbox((0, 0), ln, font=font)
            w = bbox[2] - bbox[0]
            draw.text(((W - w) // 2, y), ln, font=font, fill="WHITE")
        y += h + spacing

    disp.ShowImage(img)

def draw_centered_text_auto(lines, min_size=14, max_size=28, vpad=4, spacing=6):
    """
    Autosize to fit, then render centered.
    """
    size, sp = find_best_font_size(lines, min_size=min_size, max_size=max_size, vpad=vpad, spacing=spacing)
    draw_centered_text_with_size(lines, size=size, spacing=sp, vpad=vpad)

# ------------------------------------------------------
# Splash screen
# ------------------------------------------------------
def draw_splash():
    img = BLACK_BG.copy()
    draw = ImageDraw.Draw(img)
    # pick a size that looks good on 1.14"
    size = 28
    font = get_font(size)

    txt = "SMARTCHESS"
    bbox = draw.textbbox((0, 0), txt, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    draw.text(((W - w) // 2, (H - h) // 2 - 10),
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

    # Skip exact duplicate frames
    if line == last_msg:
        continue
    last_msg = line

    # Parse message: "L1|L2|L3|L4|size"
    parts = line.strip().split("|")
    if not parts:
        continue

    raw_size = parts[-1].strip() if parts[-1] else "auto"
    # Support up to 4 lines; ignore extras gracefully
    lines = [p for p in parts[:-1]]

    # Normalize trailing empty lines (optional)
    # while lines and lines[-1] == "":
    #     lines.pop()

    # Decide between fixed size or auto
    try:
        if raw_size.lower() == "auto":
            draw_centered_text_auto(lines)
        else:
            size = int(raw_size)
            draw_centered_text_with_size(lines, size=size, spacing=6)
    except Exception:
        # Fallback to safe auto on any parse/draw error
        draw_centered_text_auto(lines)
