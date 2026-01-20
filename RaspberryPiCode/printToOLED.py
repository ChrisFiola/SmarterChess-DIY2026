#!/usr/bin/env python3
import sys, getopt
from PIL import ImageFont, ImageDraw, Image

PIPE = "/tmp/lcdpipe"
FONT_PATH = "/home/king/SmarterChess-DIY2026/Font/Font00.ttf"

text1 = text2 = text3 = text4 = ""
textSize = None  # auto-set if not provided

opts, args = getopt.getopt(sys.argv[1:], "ha:b:c:d:s:")

for opt, arg in opts:
    if opt == "-a":
        text1 = arg
    elif opt == "-b":
        text2 = arg
    elif opt == "-c":
        text3 = arg
    elif opt == "-d":
        text4 = arg
    elif opt == "-s":  
        cleaned = arg.strip()
        if cleaned.isdigit():    # only accept valid positive integers
            textSize = int(cleaned)
        else:
            textSize = None      # let auto-size decide

# -----------------------------
# AUTO-FIT TEXT SIZE
# -----------------------------
DISPLAY_W = 240
DISPLAY_H = 135

def fits(size):
    """Return True if all lines fit inside 240×135 using this size."""
    try:
        font = ImageFont.truetype(FONT_PATH, size)
    except:
        font = ImageFont.load_default()

    total_h = 0
    for line in lines:
        bbox = font.getbbox(line)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w > DISPLAY_W - 5:   # keep small margin
            return False
        total_h += h + 8        # interline spacing

    return total_h <= DISPLAY_H

# Forced size overrides auto-fit
if textSize is not None:
    final_size = textSize
else:
    # Try from big to small
    for size in range(60, 10, -2):
        if fits(size):
            final_size = size
            break
    else:
        final_size = 20  # fallback

# -----------------------------
# SEND TO DISPLAY SERVER
# -----------------------------
msg = f"{text1}|{text2}|{text3}|{text4}|{final_size}"

with open(PIPE, "w") as f:
    f.write(msg + "\n")

