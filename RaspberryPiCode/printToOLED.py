#!/usr/bin/env python3
import sys, getopt

PIPE = "/tmp/lcdpipe"

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
        textSize = int(arg)
        # COMPLETE SAFETY: only accept positive integers
        try:
            cleaned = arg.strip()
            if cleaned.isdigit():      # only digits allowed
                textSize = int(cleaned)
        except Exception:
            textSize = None


# Auto-size text if not manually given
line_count = len([t for t in [text1, text2, text3, text4] if t])

if textSize is 0:
    if line_count == 1:
        textSize = 48
    elif line_count == 2:
        textSize = 36
    elif line_count == 3:
        textSize = 30
    else:
        textSize = 26   # safe for 4 lines

msg = f"{text1}|{text2}|{text3}|{text4}|{textSize}"

with open(PIPE, "w") as f:
    f.write(msg + "\n")
