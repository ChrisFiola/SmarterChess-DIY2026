#!/usr/bin/env python3
import sys, getopt
PIPE = "/tmp/lcdpipe"
text1 = text2 = text3 = text4 = ""; forced_size = None
opts, args = getopt.getopt(sys.argv[1:], "ha:b:c:d:s:")
for opt, arg in opts:
    if opt == "-a": text1 = arg
    elif opt == "-b": text2 = arg
    elif opt == "-c": text3 = arg
    elif opt == "-d": text4 = arg
    elif opt == "-s":
        cleaned = arg.strip()
        if cleaned.isdigit(): forced_size = int(cleaned)
lines = [t for t in [text1, text2, text3, text4] if t]
line_count = len(lines)
if forced_size:
    textSize = forced_size
else:
    if line_count == 1: textSize = 28
    elif line_count == 2: textSize = 26
    elif line_count == 3: textSize = 22
    else: textSize = 20
msg = f"{text1}|{text2}|{text3}|{text4}|{textSize}"
with open(PIPE, "w") as f:
    f.write(msg + "\n")
