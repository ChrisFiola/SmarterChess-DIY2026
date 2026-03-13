#!/usr/bin/env python3
import os, sys, time, select
from PIL import Image, ImageDraw, ImageFont

# Add own directory to path so qrgen.py is importable when launched as a subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Optional QR rendering (pure python, bundled)
try:
    from qrgen import encode_text as _qr_encode_text
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
FONT_PATH = "/home/king/SmarterChess-DIY2026/RaspberryPiCode/ChessSans.ttf"
# FONT_PATH = "/home/king/LCD_Module_RPI_code/RaspberryPi/python/Font/Font00.ttf"

FRAME = Image.new("RGB", (W, H), "BLACK")
DRAW = ImageDraw.Draw(FRAME)

BLACK_BG = Image.new("RGB", (W, H), "BLACK")
DRAW_MEASURE = ImageDraw.Draw(BLACK_BG)
MEASURE_CACHE = {}  # (size, text) -> (w,h)

# Font cache
FONTS = {}


def _open_fifo_blocking(path: str):
    fd = os.open(path, os.O_RDWR)
    return os.fdopen(fd, "r", buffering=1)


def _get_font(size: int):
    if size not in FONTS:
        FONTS[size] = ImageFont.truetype(FONT_PATH, size)
    return FONTS[size]


# ------------------------------------------------------
# AUTO FONT SCALING
# ------------------------------------------------------
def _find_best_font_size(lines, min_size=14, max_size=28, vpad=4, spacing=6):
    for size in range(max_size, min_size - 1, -1):
        font = _get_font(size)

        total_h = 0
        max_w = 0

        for ln in lines:
            if not ln:
                w = 0
                h = size
            else:
                key = (size, ln)
                wh = MEASURE_CACHE.get(key)
                if wh is None:
                    bbox = DRAW_MEASURE.textbbox((0, 0), ln, font=font)
                    wh = (bbox[2] - bbox[0], bbox[3] - bbox[1])
                    MEASURE_CACHE[key] = wh
                w, h = wh

            total_h += h + spacing
            if w > max_w:
                max_w = w

        total_h -= spacing

        if total_h <= (H - 2 * vpad) and max_w <= (W - 2 * vpad):
            return size, spacing

    return min_size, spacing


# ------------------------------------------------------
# Draw centered text with explicit size/spacing
# ------------------------------------------------------
def _draw_centered_text_with_size(lines, size: int, spacing: int = 6, vpad: int = 12):
    # Clear framebuffer (no new allocations)
    DRAW.rectangle((0, 0, W, H), fill="BLACK")

    font = _get_font(size)

    # Measure using DRAW_MEASURE (doesn't touch framebuffer)
    heights = []
    total_h = 0
    for ln in lines:
        if not ln:
            h = size
        else:
            key = (size, ln)
            wh = MEASURE_CACHE.get(key)
            if wh is None:
                bbox = DRAW_MEASURE.textbbox((0, 0), ln, font=font)
                wh = (bbox[2] - bbox[0], bbox[3] - bbox[1])
                MEASURE_CACHE[key] = wh
            _, h = wh
        heights.append(h)
        total_h += h + spacing
    total_h -= spacing

    y = max(vpad, (H - total_h - 2 * vpad) // 2 + vpad)

    for ln, h in zip(lines, heights):
        if ln:
            key = (size, ln)
            wh = MEASURE_CACHE.get(key)
            if wh is None:
                bbox = DRAW_MEASURE.textbbox((0, 0), ln, font=font)
                wh = (bbox[2] - bbox[0], bbox[3] - bbox[1])
                MEASURE_CACHE[key] = wh
            w, _ = wh

            DRAW.text(((W - w) // 2, y), ln, font=font, fill="WHITE")
        y += h + spacing

    disp.ShowImage(FRAME)


def _draw_centered_text_auto(lines, min_size=14, max_size=28, vpad=12, spacing=6):
    """
    Autosize to fit, then render centered.
    """
    size, sp = _find_best_font_size(
        lines, min_size=min_size, max_size=max_size, vpad=vpad, spacing=spacing
    )
    _draw_centered_text_with_size(lines, size=size, spacing=sp, vpad=vpad)


def _draw_menu(lines):
    """Draw menu lines: center the items vertically, pin the last line as a footer at the bottom.

    Expects lines = [item1, item2, item3, footer].  Footer may be empty string.
    """
    if not lines:
        return

    footer = lines[-1] if lines else ""
    items = lines[:-1] if len(lines) > 1 else list(lines)

    DRAW.rectangle((0, 0, W, H), fill="BLACK")

    # Find largest font size where footer fits the screen width
    footer_h = 0
    FOOTER_SIZE = 14  # fallback
    if footer:
        for fs in range(22, 11, -1):
            fnt = _get_font(fs)
            key = (fs, footer)
            wh = MEASURE_CACHE.get(key)
            if wh is None:
                bbox = DRAW_MEASURE.textbbox((0, 0), footer, font=fnt)
                wh = (bbox[2] - bbox[0], bbox[3] - bbox[1])
                MEASURE_CACHE[key] = wh
            fw, fh = wh
            if fw <= W - 8:
                FOOTER_SIZE = fs
                footer_h = fh
                break

    footer_reserved = (footer_h + 8) if footer else 0
    avail_h = H - footer_reserved

    # Auto-size items to fit in available height (above footer)
    spacing = 6
    vpad = 8
    size = 14  # fallback minimum
    for s in range(28, 13, -1):
        font_test = _get_font(s)
        total = 0
        max_w = 0
        for ln in items:
            if not ln:
                h = s
                w = 0
            else:
                key = (s, ln)
                wh = MEASURE_CACHE.get(key)
                if wh is None:
                    bbox = DRAW_MEASURE.textbbox((0, 0), ln, font=font_test)
                    wh = (bbox[2] - bbox[0], bbox[3] - bbox[1])
                    MEASURE_CACHE[key] = wh
                w, h = wh
            total += h + spacing
            if w > max_w:
                max_w = w
        total -= spacing
        if total <= (avail_h - 2 * vpad) and max_w <= (W - 2 * vpad):
            size = s
            break

    font = _get_font(size)

    heights = []
    total_h = 0
    for ln in items:
        if not ln:
            h = size
        else:
            key = (size, ln)
            wh = MEASURE_CACHE.get(key)
            if wh is None:
                bbox = DRAW_MEASURE.textbbox((0, 0), ln, font=font)
                wh = (bbox[2] - bbox[0], bbox[3] - bbox[1])
                MEASURE_CACHE[key] = wh
            _, h = wh
        heights.append(h)
        total_h += h + spacing
    if heights:
        total_h -= spacing

    # Center items within the available height (above footer)
    y = max(vpad, (avail_h - total_h) // 2)

    for ln, h in zip(items, heights):
        if ln:
            key = (size, ln)
            wh = MEASURE_CACHE.get(key)
            if wh is None:
                bbox = DRAW_MEASURE.textbbox((0, 0), ln, font=font)
                wh = (bbox[2] - bbox[0], bbox[3] - bbox[1])
                MEASURE_CACHE[key] = wh
            w, _ = wh
            DRAW.text(((W - w) // 2, y), ln, font=font, fill="WHITE")
        y += h + spacing

    # Pin footer to the very bottom
    if footer:
        font_footer = _get_font(FOOTER_SIZE)
        key = (FOOTER_SIZE, footer)
        wh = MEASURE_CACHE.get(key)
        if wh is None:
            bbox = DRAW_MEASURE.textbbox((0, 0), footer, font=font_footer)
            wh = (bbox[2] - bbox[0], bbox[3] - bbox[1])
            MEASURE_CACHE[key] = wh
        fw, fh = wh
        DRAW.text(((W - fw) // 2, H - fh - 4), footer, font=font_footer, fill="WHITE")

    disp.ShowImage(FRAME)


# ------------------------------------------------------
# QR rendering
# ------------------------------------------------------


def _draw_qr(data: str, caption_lines):
    if not data:
        _draw_centered_text_auto(["QR", "(empty)"])
        return

    if _qr_encode_text is None:
        _draw_centered_text_auto(["QR unsupported", data[:18]])
        return

    try:
        qr = _qr_encode_text(data, ecl="M")
        qsz = qr.size

        # Reserve caption height
        caption_h = 0
        if caption_lines:
            font_cap = _get_font(14)
            for ln in caption_lines[:3]:
                if not ln:
                    continue
                bbox = DRAW_MEASURE.textbbox((0, 0), ln, font=font_cap)
                caption_h += (bbox[3] - bbox[1]) + 4
            caption_h = min(caption_h + 6, 52)

        pad = 6
        avail_w = W - 2 * pad
        avail_h = H - 2 * pad - caption_h
        scale = max(1, min(avail_w // qsz, avail_h // qsz))

        # Clear framebuffer
        DRAW.rectangle((0, 0, W, H), fill="BLACK")

        qr_px = qsz * scale
        ox = (W - qr_px) // 2
        oy = max(pad, (avail_h - qr_px) // 2 + pad)

        # White background for QR
        DRAW.rectangle([ox - 2, oy - 2, ox + qr_px + 1, oy + qr_px + 1], fill="WHITE")

        for yy in range(qsz):
            y0 = oy + yy * scale
            for xx in range(qsz):
                if qr.get_module(xx, yy):
                    x0 = ox + xx * scale
                    DRAW.rectangle(
                        [x0, y0, x0 + scale - 1, y0 + scale - 1],
                        fill="BLACK",
                    )

        # Caption
        if caption_lines:
            font_cap = _get_font(14)
            ycur = min(H - caption_h + 4, oy + qr_px + 6)
            for ln in caption_lines[:3]:
                if not ln:
                    continue
                bbox = DRAW_MEASURE.textbbox((0, 0), ln, font=font_cap)
                tw = bbox[2] - bbox[0]
                DRAW.text(((W - tw) // 2, ycur), ln, font=font_cap, fill="WHITE")
                ycur += (bbox[3] - bbox[1]) + 4

        disp.ShowImage(FRAME)
    except Exception as _qr_exc:
        import traceback
        print(f"[QR ERROR] {type(_qr_exc).__name__}: {_qr_exc}", flush=True)
        traceback.print_exc()
        _draw_centered_text_auto(["QR error", data[:18]])


# ------------------------------------------------------
# Splash screen
# ------------------------------------------------------
def _draw_splash():
    DRAW.rectangle((0, 0, W, H), fill="BLACK")

    size = 28
    font = _get_font(size)
    txt = "SMARTCHESS"

    bbox = DRAW_MEASURE.textbbox((0, 0), txt, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    DRAW.text(((W - w) // 2, (H - h) // 2 - 10), txt, font=font, fill="WHITE")
    disp.ShowImage(FRAME)


# Draw splash on start
_draw_splash()

# Signal ready to Pi
with open(READY_FLAG, "w") as f:
    f.write("ready\n")

# ------------------------------------------------------
# Main loop
# ------------------------------------------------------
pipe = _open_fifo_blocking(PIPE)
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
                pipe = _open_fifo_blocking(PIPE)
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
            _draw_qr(qr_data, captions)
        elif raw_size.lower() == "menu":
            _draw_menu(lines)
        elif raw_size.lower() == "auto":
            _draw_centered_text_auto(lines)
        else:
            size = int(raw_size)
            _draw_centered_text_with_size(lines, size=size, spacing=6)
    except Exception:
        _draw_centered_text_auto(lines)
