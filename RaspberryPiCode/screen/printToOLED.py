#!/usr/bin/env python3
"""Small CLI helper to send text to the SmartChess LCD.

Example:
  ./printToOLED.py -a "Line 1" -b "Line 2" -c "Line 3" -d "Line 4" -s auto
  ./printToOLED.py -a "https://lichess.org/..." -b "Scan to join" -s qr
"""
import sys
import getopt

from lcd_pipe import LCDPipeClient

text1 = text2 = text3 = text4 = ""
size = "auto"

opts, _args = getopt.getopt(sys.argv[1:], "ha:b:c:d:s:")
for opt, arg in opts:
    if opt == "-h":
        print(__doc__.strip())
        sys.exit(0)
    if opt == "-a":
        text1 = arg
    elif opt == "-b":
        text2 = arg
    elif opt == "-c":
        text3 = arg
    elif opt == "-d":
        text4 = arg
    elif opt == "-s":
        size = arg

client = LCDPipeClient()
if str(size).lower() == "qr":
    client.qr(text1, captions=[text2, text3, text4])
else:
    client.send(text1, text2, text3, text4, size=str(size))
