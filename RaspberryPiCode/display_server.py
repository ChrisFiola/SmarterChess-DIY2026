#!/usr/bin/env python3
import os, sys, time
from PIL import Image, ImageDraw, ImageFont

sys.path.append("/home/king/LCD_Module_RPI_code/RaspberryPi/python")
from lib.LCD_1inch14 import LCD_1inch14

PIPE = "/tmp/lcdpipe"
READY_FLAG = "/tmp/display_server_ready"

# Remove any stale flag
if os.path.exists(READY_FLAG):
    os.remove(READY_FLAG)

disp = LCD_1inch14()
disp.Init()
disp.bl_DutyCycle(80)
disp.clear()

# Signal READY
with open(READY_FLAG, "w") as f:
    f.write("ready\n")

W, H = disp.width, disp.height
FONT = "/home/king/LCD_Module_RPI_code/RaspberryPi/python/Font/Font00.ttf"

FONTS = {}

BLACK_BG = Image.new("RGB", (W, H), "BLACK")

def get_font(size):
    if size not in FONTS:
        FONTS[size] = ImageFont.truetype(FONT, size)
    return FONTS[size]

def draw_text(lines, size):
    img = BLACK_BG.copy()
    draw = ImageDraw.Draw(img)

    try:
        font = get_font(size)
    except:
        font = ImageFont.load_default()

    y = 5
    for ln in lines:
        if not ln:
            y += size + 4
            continue
        bbox = draw.textbbox((0, 0), ln, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        draw.text(((W - w) // 2, y), ln, font=font, fill="WHITE")
        y += h + 8

    disp.ShowImage(img)


pipe = open(PIPE, "r")
last_msg = None

while True:
    line = pipe.readline()
    if not line:
        continue
    
    if line == last_msg:
        continue  # no need to redraw

    last_msg = line
    
    parts = line.strip().split("|")
    size = int(parts[-1])
    lines = parts[:-1]
    draw_text(lines, size)
