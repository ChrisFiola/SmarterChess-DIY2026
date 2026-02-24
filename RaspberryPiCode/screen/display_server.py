#!/usr/bin/env python3
import os, time
from PIL import Image, ImageDraw, ImageFont

# Waveshare ST7789 driver
import sys
sys.path.append("/home/king/LCD_Module_RPI_code/RaspberryPi/python")
from lib.LCD_1inch14 import LCD_1inch14

PIPE = "/tmp/lcdpipe"
READY_FLAG = "/tmp/display_server_ready"

if os.path.exists(READY_FLAG):
    os.remove(READY_FLAG)

# Init display
disp = LCD_1inch14(); disp.Init(); disp.bl_DutyCycle(80); disp.clear()
W, H = disp.width, disp.height
FONT_PATH = "/home/king/LCD_Module_RPI_code/RaspberryPi/python/Font/Font00.ttf"
BLACK_BG = Image.new("RGB", (W, H), "BLACK")
FONTS = {}

def get_font(size: int):
    if size not in FONTS:
        FONTS[size] = ImageFont.truetype(FONT_PATH, size)
    return FONTS[size]

def find_best_font_size(lines, min_size=14, max_size=28, vpad=4, spacing=6):
    for size in range(max_size, min_size - 1, -1):
        font = get_font(size)
        draw = ImageDraw.Draw(BLACK_BG)
        total_h = 0; max_w = 0
        for ln in lines:
            if not ln:
                h = size; w = 0
            else:
                bbox = draw.textbbox((0, 0), ln, font=font)
                w = bbox[2] - bbox[0]; h = bbox[3] - bbox[1]
            total_h += h + spacing; max_w = max(max_w, w)
        total_h -= spacing
        if total_h <= (H - 2*vpad) and max_w <= (W - 2*vpad):
            return size, spacing
    return min_size, spacing

def draw_centered_text_with_size(lines, size: int, spacing: int = 6, vpad: int = 0):
    img = BLACK_BG.copy(); draw = ImageDraw.Draw(img); font = get_font(size)
    heights = []; total_h = 0
    for ln in lines:
        if not ln: h = size
        else:
            bbox = draw.textbbox((0,0), ln, font=font)
            h = bbox[3]-bbox[1]
        heights.append(h); total_h += h + spacing
    total_h -= spacing
    y = max(0, (H - total_h)//2)
    for ln, h in zip(lines, heights):
        if ln:
            bbox = draw.textbbox((0,0), ln, font=font)
            w = bbox[2]-bbox[0]
            draw.text(((W - w)//2, y), ln, font=font, fill="WHITE")
        y += h + spacing
    disp.ShowImage(img)


def draw_centered_text_auto(lines, min_size=14, max_size=28, vpad=4, spacing=6):
    size, sp = find_best_font_size(lines, min_size=min_size, max_size=max_size, vpad=vpad, spacing=spacing)
    draw_centered_text_with_size(lines, size=size, spacing=sp, vpad=vpad)

# Splash
img = BLACK_BG.copy(); draw = ImageDraw.Draw(img); size = 28; font = get_font(size)
text = "SMARTCHESS"; bbox = draw.textbbox((0,0), text, font=font)
ww = bbox[2]-bbox[0]; hh = bbox[3]-bbox[1]
draw.text(((W-ww)//2, (H-hh)//2 - 10), text, font=font, fill="WHITE")
disp.ShowImage(img)

with open(READY_FLAG, "w") as f:
    f.write("ready\n")

pipe = open(PIPE, "r")
last_msg = None

while True:
    line = pipe.readline()
    if not line:
        time.sleep(0.003); continue
    if line == last_msg:
        continue
    last_msg = line
    parts = line.strip().split("|")
    if not parts: continue
    raw_size = parts[-1].strip() if parts[-1] else "auto"
    lines = [p for p in parts[:-1]]
    try:
        if raw_size.lower() == "auto":
            draw_centered_text_auto(lines)
        else:
            size = int(raw_size); draw_centered_text_with_size(lines, size=size, spacing=6)
    except Exception:
        draw_centered_text_auto(lines)
