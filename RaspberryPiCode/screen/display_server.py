#!/usr/bin/env python3
import os, sys, time, select
from PIL import Image, ImageDraw, ImageFont

# Ensure local imports (e.g. qrgen.py) work regardless of CWD
sys.path.insert(0, os.path.dirname(__file__))

# Optional QR rendering (pure python, bundled)
try:
    from qrgen import encode_text as _qr_encode_text  # type: ignore
except Exception:
    _qr_encode_text = None

# Waveshare ST7789 driver
sys.path.append("/home/king/LCD_Module_RPI_code/RaspberryPi/python")
from lib.LCD_1inch14 import LCD_1inch14

PIPE = "/tmp/lcdpipe"
READY_FLAG = "/tmp/display_server_ready"

# Remove stale ready flag
if os.path.exists(READY_FLAG):
    os.remove(READY_FLAG)

# Init display
disp = LCD_1inch14()
disp.Init()
disp.bl_DutyCycle(80)
disp.clear()

# Screen constants
W, H = disp.width, disp.height
FONT_PATH = "/home/king/LCD_Module_RPI_code/RaspberryPi/python/Font/Font00.ttf"
BLACK_BG = Image.new("RGB", (W, H), "BLACK")
DRAW_MEASURE = ImageDraw.Draw(BLACK_BG)
MEASURE_CACHE = {}  # (size, text) -> (w,h)

# Font cache
FONTS = {}


def open_fifo_blocking(path: str):
    fd = os.open(path, os.O_RDWR)
    return os.fdopen(fd, "r", buffering=1)


def get_font(size: int):
    if size not in FONTS:
        FONTS[size] = ImageFont.truetype(FONT_PATH, size)
    return FONTS[size]


# ------------------------------------------------------
# AUTO FONT SCALING
# ------------------------------------------------------
def find_best_font_size(lines, min_size=14, max_size=28, vpad=4, spacing=6):
    for size in range(max_size, min_size - 1, -1):
        font = get_font(size)

        total_h = 0
        max_w = 0

        for ln in lines:
            if not ln:
                h = size
                w = 0
            else:
                key = (size, ln)
                wh = MEASURE_CACHE.get(key)

                if wh is None:
                    bbox = DRAW_MEASURE.textbbox((0, 0), ln, font=font)
                    wh = (bbox[2] - bbox[0], bbox[3] - bbox[1])
                    MEASURE_CACHE[key] = wh

                w, h = wh
                total_h += h + spacing
                max_w = max(max_w, w)

        total_h -= spacing

        if total_h <= (H - 2 * vpad) and max_w <= (W - 2 * vpad):
            return size, spacing

    return min_size, spacing


# ------------------------------------------------------
# Draw centered text with explicit size/spacing
# ------------------------------------------------------
def draw_centered_text_with_size(lines, size: int, spacing: int = 6, vpad: int = 0):
    """
    Draw 'lines' using font 'size' and 'spacing', centered on screen.
    """
    img = BLACK_BG.copy()
    draw = ImageDraw.Draw(img)
    font = get_font(size)

    # Measure
    heights = []
    total_h = 0
    for ln in lines:
        if not ln:
            h = size  # blank line spacing approximated to font size
        else:
            bbox = draw.textbbox((0, 0), ln, font=font)
            h = bbox[3] - bbox[1]
        heights.append(h)
        total_h += h + spacing
    total_h -= spacing

    # Vertical center
    y = max(0, (H - total_h) // 2)

    # Draw each line centered horizontally
    for ln, h in zip(lines, heights):
        if ln:
            bbox = draw.textbbox((0, 0), ln, font=font)
            w = bbox[2] - bbox[0]
            draw.text(((W - w) // 2, y), ln, font=font, fill="WHITE")
        y += h + spacing

    disp.ShowImage(img)


def draw_centered_text_auto(lines, min_size=14, max_size=28, vpad=4, spacing=6):
    """
    Autosize to fit, then render centered.
    """
    size, sp = find_best_font_size(
        lines, min_size=min_size, max_size=max_size, vpad=vpad, spacing=spacing
    )
    draw_centered_text_with_size(lines, size=size, spacing=sp, vpad=vpad)


# ------------------------------------------------------
# QR rendering
# ------------------------------------------------------


def draw_qr(data: str, caption_lines):
    """Draw QR code (data) + optional caption lines."""
    if not data:
        draw_centered_text_auto(["QR", "(empty)"])
        return

    # If QR encoder isn't available for any reason, fall back to text.
    if _qr_encode_text is None:
        draw_centered_text_auto(["QR unsupported", data[:18]])
        return

    try:
        qr = _qr_encode_text(data, ecl="M")
        qsz = qr.size

        # Reserve some space for caption (bottom).
        caption_h = 0
        if caption_lines:
            font = get_font(14)
            draw = ImageDraw.Draw(BLACK_BG)
            for ln in caption_lines[:3]:
                if not ln:
                    continue
                bbox = draw.textbbox((0, 0), ln, font=font)
                caption_h += (bbox[3] - bbox[1]) + 4
            caption_h = min(caption_h + 6, 52)

        pad = 6
        avail_w = W - 2 * pad
        avail_h = H - 2 * pad - caption_h
        scale = max(1, min(avail_w // qsz, avail_h // qsz))

        # Build QR image
        img = BLACK_BG.copy()
        draw = ImageDraw.Draw(img)

        qr_px = qsz * scale
        ox = (W - qr_px) // 2
        oy = max(pad, (avail_h - qr_px) // 2 + pad)

        # White background for QR for better scan
        draw.rectangle([ox - 2, oy - 2, ox + qr_px + 1, oy + qr_px + 1], fill="WHITE")

        for y in range(qsz):
            for x in range(qsz):
                if qr.get_module(x, y):
                    x0 = ox + x * scale
                    y0 = oy + y * scale
                    draw.rectangle(
                        [x0, y0, x0 + scale - 1, y0 + scale - 1], fill="BLACK"
                    )

        # Caption under QR
        if caption_lines:
            font = get_font(14)
            ycur = min(H - caption_h + 4, oy + qr_px + 6)
            for ln in caption_lines[:3]:
                if not ln:
                    continue
                bbox = draw.textbbox((0, 0), ln, font=font)
                tw = bbox[2] - bbox[0]
                draw.text(((W - tw) // 2, ycur), ln, font=font, fill="WHITE")
                ycur += (bbox[3] - bbox[1]) + 4

        disp.ShowImage(img)
    except Exception:
        draw_centered_text_auto(["QR error", data[:18]])


# ------------------------------------------------------
# Splash screen
# ------------------------------------------------------
def draw_splash():
    img = BLACK_BG.copy()
    draw = ImageDraw.Draw(img)
    # pick a size that looks good on 1.14"
    size = 28
    font = get_font(size)

    txt = "SMARTCHESS"
    bbox = draw.textbbox((0, 0), txt, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    draw.text(((W - w) // 2, (H - h) // 2 - 10), txt, font=font, fill="WHITE")

    disp.ShowImage(img)


# Draw splash on start
draw_splash()

# Signal ready to Pi
with open(READY_FLAG, "w") as f:
    f.write("ready\n")

# ------------------------------------------------------
# Main loop
# ------------------------------------------------------
pipe = open_fifo_blocking(PIPE)
FPS_CAP = 10.0
MIN_DT = 1.0 / FPS_CAP

last_draw_t = 0.0
last_drawn = None  # last message actually drawn
pending_msg = None  # newest message waiting to be drawn

while True:
    # Wait for input, but wake periodically so we can draw pending messages
    r, _, _ = select.select([pipe], [], [], 0.05)

    if r:
        # Drain all available lines quickly (coalesce bursts)
        while True:
            line = pipe.readline()

            if line == "":
                # With O_RDWR this usually won't happen, but keep it safe
                try:
                    pipe.close()
                except Exception:
                    pass
                time.sleep(0.1)
                pipe = open_fifo_blocking(PIPE)
                pending_msg = None
                last_drawn = None
                break

            msg = line.strip()
            if msg:
                pending_msg = msg  # keep only the latest

            # If no more data immediately available, stop draining
            r2, _, _ = select.select([pipe], [], [], 0)
            if not r2:
                break

    # Nothing pending → keep waiting
    if pending_msg is None:
        continue

    # FPS cap: if too soon, don't draw yet (but keep pending_msg!)
    now = time.monotonic()
    if now - last_draw_t < MIN_DT:
        continue

    # Draw the newest pending message
    msg = pending_msg
    pending_msg = None

    # Skip redraw if identical to last drawn (saves CPU + SPI)
    if msg == last_drawn:
        last_draw_t = now
        continue

    last_drawn = msg
    last_draw_t = now

    # Parse message: "L1|L2|L3|L4|size"
    parts = msg.split("|")
    if not parts:
        continue

    raw_size = parts[-1].strip() if parts[-1] else "auto"
    lines = [p for p in parts[:-1]]

    try:
        if raw_size.lower() == "qr":
            qr_data = (lines[0] if lines else "").strip()
            captions = [ln.strip() for ln in lines[1:]] if len(lines) > 1 else []
            draw_qr(qr_data, captions)
        elif raw_size.lower() == "auto":
            draw_centered_text_auto(lines)
        else:
            size = int(raw_size)
            draw_centered_text_with_size(lines, size=size, spacing=6)
    except Exception:
        draw_centered_text_auto(lines)
