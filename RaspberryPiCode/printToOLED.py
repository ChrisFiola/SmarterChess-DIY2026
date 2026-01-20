#!/usr/bin/env python3
import sys, getopt

PIPE = "/tmp/lcdpipe"

text1 = text2 = text3 = text4 = ""
textSize = 30

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

msg = f"{text1}|{text2}|{text3}|{text4}|{textSize}"

with open(PIPE, "w") as f:
    f.write(msg + "\n")
