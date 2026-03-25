#!/usr/bin/env python3
import os
import select
import sys
import time

from PIL import Image, ImageDraw, ImageFont

# Add own directory to path so qrgen.py is importable when launched as a subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lcd_pipe import PIPE_PATH, READY_FLAG_PATH

# Optional QR rendering (pure python, bundled)
try:
    from qrgen import encode_text as _qr_encode_text
except Exception:
    _qr_encode_text = None

# Waveshare ST7789 driver
sys.path.append("/home/king/LCD_Module_RPI_code/RaspberryPi/python")
from lib.LCD_1inch14 import LCD_1inch14

# Remove stale ready flag
if os.path.exists(READY_FLAG_PATH):
    os.remove(READY_FLAG_PATH)

# Init display
disp = LCD_1inch14()
disp.Init()
disp.bl_DutyCycle(80)
disp.clear()

# Screen constants
W, H = disp.width, disp.height
DEFAULT_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
    "/home/king/SmarterChess-DIY2026/RaspberryPiCode/ChessSans.ttf",
    "/home/king/SmarterChess-DIY2026/RaspberryPiCode/WorkSans-Medium.ttf",
    "/home/king/LCD_Module_RPI_code/RaspberryPi/python/Font/Font00.ttf",
]
ANNOTATION_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _resolve_font_path(candidates, label: str) -> str:
    for path in candidates:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(f"No {label} font found in candidates: {candidates}")


FONT_PATHS = {
    "default": _resolve_font_path(DEFAULT_FONT_CANDIDATES, "default"),
    "annotation": _resolve_font_path(
        ANNOTATION_FONT_CANDIDATES + DEFAULT_FONT_CANDIDATES,
        "annotation",
    ),
}
print(f"[LCD] using default font: {FONT_PATHS['default']}", flush=True)
print(f"[LCD] using annotation font: {FONT_PATHS['annotation']}", flush=True)

FRAME = Image.new("RGB", (W, H), "BLACK")
DRAW = ImageDraw.Draw(FRAME)

BLACK_BG = Image.new("RGB", (W, H), "BLACK")
DRAW_MEASURE = ImageDraw.Draw(BLACK_BG)
MEASURE_CACHE = {}  # (size, text) -> (w,h)

# Font cache
FONTS = {}

FOOTER_SIZE = 15


def _is_footer_hint(line: str) -> bool:
    low = (line or "").strip().lower()
    if not low:
        return False
    return (
        low.startswith("press ok")
        or low.startswith("press hint")
        or "ok =" in low
        or "ok=" in low
        or "hint =" in low
        or "hint=" in low
        or "ok+" in low
    )


def _fit_single_line_size(
    text: str,
    *,
    min_size: int,
    max_size: int,
    max_w: int,
    font_key: str = "default",
) -> int:
    txt = (text or "").strip()
    if not txt:
        return min_size
    for size in range(max_size, min_size - 1, -1):
        font = _get_font(size, font_key=font_key)
        if _measure(size, txt, font, font_key=font_key)[0] <= max_w:
            return size
    return min_size


def _word_wrap(text, size, max_w):
    """Wrap *text* at word boundaries so no line exceeds *max_w* pixels."""
    font = _get_font(size)
    text = (text or "").strip()
    if not text:
        return [""]
    if _measure(size, text, font)[0] <= max_w:
        return [text]
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if _measure(size, candidate, font)[0] <= max_w:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines if lines else [text]


def _open_fifo_blocking(path: str):
    fd = os.open(path, os.O_RDWR)
    return os.fdopen(fd, "r", buffering=1)


def _measure(size: int, text: str, font, *, font_key: str = "default") -> tuple:
    """Return (width, height) of text at the given font size, using the cache."""
    key = (font_key, size, text)
    wh = MEASURE_CACHE.get(key)
    if wh is None:
        bbox = DRAW_MEASURE.textbbox((0, 0), text, font=font)
        wh = (bbox[2] - bbox[0], bbox[3] - bbox[1])
        MEASURE_CACHE[key] = wh
    return wh


def _get_font(size: int, *, font_key: str = "default"):
    cache_key = (font_key, size)
    if cache_key not in FONTS:
        FONTS[cache_key] = ImageFont.truetype(FONT_PATHS[font_key], size)
    return FONTS[cache_key]


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
                key = ("default", size, ln)
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
            key = ("default", size, ln)
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
            key = ("default", size, ln)
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
    """Auto-size down until everything fits (no wrapping)."""
    size, spacing = _find_best_font_size(lines, min_size, max_size, vpad, spacing)
    _draw_centered_text_with_size(lines, size=size, spacing=spacing, vpad=vpad)


def _draw_menu(lines, page_info="", *, font_key: str = "default"):
    """Draw menu lines with auto-sizing: items centered, footer pinned to the bottom.

    Expects lines = [item1, item2, item3, footer].  Footer may be empty string.
    ``page_info`` is an optional string like "1/2" rendered top-right.
    """
    if not lines:
        return

    raw_footer = lines[-1] if lines else ""
    raw_items = lines[:-1] if len(lines) > 1 else list(lines)

    DRAW.rectangle((0, 0, W, H), fill="BLACK")

    # Page indicator in top-right header area
    header_reserved = 0
    if page_info:
        pg_size = FOOTER_SIZE
        pg_font = _get_font(pg_size, font_key=font_key)
        pg_w, pg_h = _measure(pg_size, page_info, pg_font, font_key=font_key)
        DRAW.text((W - pg_w - 6, 4), page_info, font=pg_font, fill="GRAY")
        header_reserved = pg_h + 4

    footer = raw_footer or ""
    footer_font = _get_font(FOOTER_SIZE, font_key=font_key)
    footer_h = (
        _measure(FOOTER_SIZE, footer, footer_font, font_key=font_key)[1]
        if footer
        else 0
    )
    # Reserve: footer text + separator gap (5) + bottom pad (4)
    footer_reserved = (footer_h + 14) if footer else 0
    avail_h = H - footer_reserved - header_reserved

    spacing = 6
    vpad = 8
    min_size, max_size = 14, 28

    display_lines = [(ln or "") for ln in raw_items if ln]

    item_size = min_size
    for sz in range(max_size, min_size - 1, -1):
        font = _get_font(sz, font_key=font_key)
        heights = [
            _measure(sz, ln, font, font_key=font_key)[1] for ln in display_lines
        ]
        total_h = sum(heights) + spacing * (len(heights) - 1) if heights else 0
        widths = [
            _measure(sz, ln, font, font_key=font_key)[0] for ln in display_lines
        ]
        if total_h <= avail_h - 2 * vpad and all(w <= W - 16 for w in widths):
            item_size = sz
            break

    item_font = _get_font(item_size, font_key=font_key)
    heights = [
        _measure(item_size, ln, item_font, font_key=font_key)[1]
        for ln in display_lines
    ]
    total_h = sum(heights) + spacing * (len(heights) - 1) if heights else 0

    # Center items in the available area between header and footer
    top = header_reserved
    y = top + max(vpad, (avail_h - total_h) // 2)
    for ln, h in zip(display_lines, heights):
        w = _measure(item_size, ln, item_font, font_key=font_key)[0]
        DRAW.text(((W - w) // 2, y), ln, font=item_font, fill="WHITE")
        y += h + spacing

    # Footer: separator line then text
    if footer:
        fw = _measure(FOOTER_SIZE, footer, footer_font, font_key=font_key)[0]
        footer_y = H - footer_h - 4
        DRAW.line((10, footer_y - 5, W - 10, footer_y - 5), fill="WHITE", width=1)
        DRAW.text(((W - fw) // 2, footer_y), footer, font=footer_font, fill="WHITE")

    disp.ShowImage(FRAME)


def _draw_header_panel(lines):
    """Draw a panel with a header line and optional footer."""
    if not lines:
        return

    header = (lines[0] or "").strip()
    footer = (
        (lines[-1] or "").strip()
        if len(lines) > 2 and _is_footer_hint(lines[-1])
        else ""
    )
    raw_body = lines[1:-1] if footer else lines[1:]
    body_lines = [(ln or "") for ln in raw_body if ln]

    DRAW.rectangle((0, 0, W, H), fill="BLACK")

    header_size = _fit_single_line_size(header, min_size=13, max_size=17, max_w=W - 20)
    header_font = _get_font(header_size)
    header_w, header_h = _measure(header_size, header, header_font)
    header_y = 6
    if header:
        DRAW.text(((W - header_w) // 2, header_y), header, font=header_font, fill="WHITE")
    divider_y = header_y + header_h + 6
    DRAW.line((10, divider_y, W - 10, divider_y), fill="WHITE", width=1)

    footer_size = _fit_single_line_size(footer, min_size=11, max_size=14, max_w=W - 20)
    footer_font = _get_font(footer_size)
    footer_h = _measure(footer_size, footer, footer_font)[1] if footer else 0
    footer_reserved = (footer_h + 14) if footer else 0

    avail_top = divider_y + 8
    avail_h = H - avail_top - footer_reserved - 8
    spacing = 5
    min_size, max_size = 12, 24

    body_size = min_size
    for sz in range(max_size, min_size - 1, -1):
        font = _get_font(sz)
        heights = [_measure(sz, ln, font)[1] for ln in body_lines] if body_lines else []
        total_h = sum(heights) + spacing * (len(heights) - 1) if heights else 0
        widths = [_measure(sz, ln, font)[0] for ln in body_lines] if body_lines else []
        if total_h <= avail_h and all(w <= W - 16 for w in widths):
            body_size = sz
            break

    body_font = _get_font(body_size)
    sized = [(ln, _measure(body_size, ln, body_font)) for ln in body_lines]
    total_h = sum(h for _, (_, h) in sized) + spacing * (len(sized) - 1) if sized else 0
    y = avail_top + max(0, (avail_h - total_h) // 2)
    for ln, (w, h) in sized:
        DRAW.text(((W - w) // 2, y), ln, font=body_font, fill="WHITE")
        y += h + spacing

    if footer:
        fw = _measure(footer_size, footer, footer_font)[0]
        footer_y = H - footer_h - 4
        DRAW.line((10, footer_y - 5, W - 10, footer_y - 5), fill="WHITE", width=1)
        DRAW.text(((W - fw) // 2, footer_y), footer, font=footer_font, fill="WHITE")

    disp.ShowImage(FRAME)


def _draw_online(lines):
    """Draw left/right clocks on the top row plus centered body content below."""
    DRAW.rectangle((0, 0, W, H), fill="BLACK")

    clock_lines = list(lines[:2])
    body_lines = list(lines[2:]) if len(lines) > 2 else []

    left = clock_lines[0] if len(clock_lines) > 0 else ""
    right = clock_lines[1] if len(clock_lines) > 1 else ""

    def _split_clock(ln: str):
        ln = (ln or "").strip()
        if not ln:
            return "", ""
        parts = ln.split(" ", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return "", ln

    left_prefix, left_time = _split_clock(left)
    right_prefix, right_time = _split_clock(right)

    label_size = 10
    label_font = _get_font(label_size)
    left_prefix_w, left_prefix_h = (
        _measure(label_size, left_prefix, label_font) if left_prefix else (0, 0)
    )
    right_prefix_w, right_prefix_h = (
        _measure(label_size, right_prefix, label_font) if right_prefix else (0, 0)
    )

    time_size = 18
    time_font = _get_font(time_size)
    side_gap = 2
    half_w = W // 2
    left_time_space = max(24, half_w - left_prefix_w - 6 - side_gap)
    right_time_space = max(24, half_w - right_prefix_w - 6 - side_gap)
    for size in range(24, 17, -1):
        font = _get_font(size)
        left_time_w, _ = _measure(size, left_time, font) if left_time else (0, 0)
        right_time_w, _ = _measure(size, right_time, font) if right_time else (0, 0)
        if left_time_w <= left_time_space and right_time_w <= right_time_space:
            time_size = size
            time_font = font
            break

    y = 4
    row_h = time_size

    if left:
        if left_prefix:
            DRAW.text((2, y + 2), left_prefix, font=label_font, fill="WHITE")
        time_x = 2 + left_prefix_w + (side_gap if left_prefix else 0)
        _, lh = _measure(time_size, left_time, time_font)
        row_h = max(row_h, lh, left_prefix_h + 2)
        DRAW.text((time_x, y), left_time, font=time_font, fill="WHITE")
    if right:
        rw, rh = _measure(time_size, right_time, time_font)
        prefix_x = W - rw - right_prefix_w - (side_gap if right_prefix else 0) - 2
        row_h = max(row_h, rh, right_prefix_h + 2)
        if right_prefix:
            DRAW.text((prefix_x, y + 2), right_prefix, font=label_font, fill="WHITE")
        DRAW.text((W - rw - 2, y), right_time, font=time_font, fill="WHITE")

    divider_y = y + row_h + 4
    DRAW.line((10, divider_y, W - 10, divider_y), fill="WHITE", width=1)

    avail_top = divider_y + 8
    avail_h = max(24, H - avail_top - 8)
    spacing = 5
    vpad = 4
    min_body, max_body = 12, 22

    display_body = [(ln or "") for ln in (body_lines or [""])]
    display_body = [ln for ln in display_body if ln]

    body_size = min_body
    for sz in range(max_body, min_body - 1, -1):
        font = _get_font(sz)
        heights = (
            [_measure(sz, ln, font)[1] for ln in display_body] if display_body else []
        )
        total_h = sum(heights) + spacing * (len(heights) - 1) if heights else 0
        widths = (
            [_measure(sz, ln, font)[0] for ln in display_body] if display_body else []
        )
        if total_h <= avail_h and all(w <= W - 12 for w in widths):
            body_size = sz
            break

    body_font = _get_font(body_size)
    sized = [(ln, _measure(body_size, ln, body_font)) for ln in display_body]
    total_h = sum(h for _, (_, h) in sized) + spacing * (len(sized) - 1) if sized else 0
    y = avail_top + max(vpad, (avail_h - total_h) // 2)
    for ln, (w, h) in sized:
        DRAW.text(((W - w) // 2, y), ln, font=body_font, fill="WHITE")
        y += h + spacing

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
            font_cap = _get_font(FOOTER_SIZE)
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

        # Caption (uppercased, QR data is NOT uppercased)
        if caption_lines:
            font_cap = _get_font(FOOTER_SIZE)
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
with open(READY_FLAG_PATH, "w") as f:
    f.write("ready\n")

# ------------------------------------------------------
# Main loop
# ------------------------------------------------------
pipe = _open_fifo_blocking(PIPE_PATH)
FPS_CAP = 10.0
MIN_DT = 1.0 / FPS_CAP

last_draw_t = 0.0
last_drawn = None  # last message actually drawn
pending_msg = None  # newest message waiting to be drawn
current_lines = []
current_size = "auto"
clock_lines = None


def _render_current():
    raw_size = (current_size or "auto").strip()
    size_key = raw_size.lower()
    lines = list(current_lines)

    if size_key == "qr":
        qr_data = (lines[0] if lines else "").strip()
        captions = [ln.strip() for ln in lines[1:]] if len(lines) > 1 else []
        _draw_qr(qr_data, captions)
        return

    if size_key.startswith("menu"):
        page_info = size_key.split(":", 1)[1] if ":" in size_key else ""
        _draw_menu(lines, page_info=page_info)
        return

    if size_key.startswith("annotation"):
        page_info = size_key.split(":", 1)[1] if ":" in size_key else ""
        _draw_menu(lines, page_info=page_info, font_key="annotation")
        return

    if size_key in ("setup", "header"):
        _draw_header_panel(lines)
        return

    if size_key == "online":
        _draw_online(lines)
        return

    if size_key == "auto" and clock_lines:
        _draw_online(list(clock_lines) + lines)
        return

    if size_key == "auto":
        _draw_centered_text_auto(lines)
        return

    try:
        size = int(raw_size)
        _draw_centered_text_with_size(lines, size=size, spacing=6)
    except Exception:
        _draw_centered_text_auto(lines)


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
                pipe = _open_fifo_blocking(PIPE_PATH)
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

    if raw_size.lower() == "clock":
        if lines and lines[0] == "__clock__":
            clock_lines = lines[1:3]
        elif lines and lines[0] == "__clock_clear__":
            clock_lines = None
        try:
            _render_current()
        except Exception:
            _draw_centered_text_auto(list(current_lines))
        continue

    current_lines = lines
    current_size = raw_size

    try:
        _render_current()
    except Exception:
        _draw_centered_text_auto(lines)
