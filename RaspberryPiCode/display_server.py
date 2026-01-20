#!/usr/bin/env python3
import os, sys
from PIL import Image, ImageDraw, ImageFont

sys.path.append("/home/king/LCD_Module_RPI_code/RaspberryPi/python")
from lib.LCD_1inch14 import LCD_1inch14

PIPE = "/tmp/lcdpipe"

if not os.path.exists(PIPE):
    os.mkfifo(PIPE)

disp = LCD_1inch14()
disp.Init()
disp.bl_DutyCycle(80)

# clear screen once at startup
disp.clear()

W, H = disp.width, disp.height
FONT = "/home/king/LCD_Module_RPI_code/RaspberryPi/python/Font/Font00.ttf"


def draw_text(lines, size):
    img = Image.new("RGB", (W, H), "BLACK")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(FONT, size)
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


while True:
    with open(PIPE, "r") as pipe:
        for line in pipe:
            parts = line.strip().split("|")
            size = int(parts[-1])
            lines = parts[:-1]
            draw_text(lines, size)
