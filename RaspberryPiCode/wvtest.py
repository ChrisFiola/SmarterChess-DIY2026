#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys, getopt
import time
from PIL import Image, ImageDraw, ImageFont
import logging
import spidev as SPI
from lib import LCD_1inch14  # Waveshare’s official driver

logging.basicConfig(level=logging.ERROR)

# ----------------------------------------
# Parse arguments from smartchess.py
# ----------------------------------------
argv = sys.argv[1:]

text1 = ""
text2 = ""
text3 = ""
text4 = ""
textSize = 24

opts, args = getopt.getopt(
    argv,
    "ha:b:c:d:s:",
    ["firstLine=", "secondLine=", "thirdLine=", "fourthLine=", "textSize="],
)

for opt, arg in opts:
    if opt == "-h":
        print("printToOLED.py -a <line1> -b <line2> -c <line3> -d <line4> -s <size>")
        sys.exit()
    elif opt in ("-a", "--firstLine"):
        text1 = arg
    elif opt in ("-b", "--secondLine"):
        text2 = arg
    elif opt in ("-c", "--thirdLine"):
        text3 = arg
    elif opt in ("-d", "--fourthLine"):
        text4 = arg
    elif opt in ("-s", "--textSize"):
        textSize = int(arg)

# ----------------------------------------
# Initialize Waveshare 1.14" display
# ----------------------------------------
try:
    disp = LCD_1inch14.LCD_1inch14()
    disp.Init()
    disp.bl_DutyCycle(80)  # nice bright backlight
    disp.clear()

    W = disp.width
    H = disp.height

    # ----------------------------------------
    # Build image for this display
    # ----------------------------------------
    img = Image.new("RGB", (W, H), "BLACK")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("./Font/Font00.ttf", textSize)
    except:
        font = ImageFont.load_default()

    lines = [text1, text2, text3, text4]
    y = 5

    for line in lines:
        if not line:
            y += textSize + 4
            continue

        # Pillow 10-compatible text measurement
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]

        draw.text(((W - w) // 2, y), line, font=font, fill="WHITE")
        y += h + 6

    disp.ShowImage(img)

except Exception as e:
    print("Display error:", e)
