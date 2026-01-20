
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys, getopt, os
from PIL import Image, ImageDraw, ImageFont
from lib import LCD_1inch14
import time

# -------------------------------
# Parse args
# -------------------------------
argv = sys.argv[1:]

text1 = text2 = text3 = text4 = ""
textSize = 24

opts, args = getopt.getopt(argv, "ha:b:c:d:s:",
                           ["firstLine=", "secondLine=", "thirdLine=", "fourthLine=", "textSize="])

for opt, arg in opts:
    if opt == "-a": text1 = arg
    elif opt == "-b": text2 = arg
    elif opt == "-c": text3 = arg
    elif opt == "-d": text4 = arg
    elif opt == "-s": textSize = int(arg)


# -------------------------------
# Waveshare display init
# -------------------------------
disp = LCD_1inch14.LCD_1inch14()
disp.Init()
disp.bl_DutyCycle(80)


W, H = disp.width, disp.height

# -------------------------------
# Draw text
# -------------------------------
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
    bbox = draw.textbbox((0, 0), line, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    draw.text(((W - w)//2, y), line, fill="WHITE", font=font)
    y += h + 6

disp.ShowImage(img)
