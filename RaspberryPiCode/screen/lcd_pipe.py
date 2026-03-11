# -*- coding: utf-8 -*-
"""
LCD named-pipe constants shared between display.py and display_server.py.

display_server.py listens on PIPE_PATH for single-line messages in the format:
  L1|L2|L3|L4|size

Where size is one of:
  'auto'    — pick a font size that fits all lines on screen
  integer   — fixed font size in points
  'qr'      — render L1 as a QR code with optional captions in L2..L4
"""

PIPE_PATH: str = "/tmp/lcdpipe"
READY_FLAG_PATH: str = "/tmp/display_server_ready"
