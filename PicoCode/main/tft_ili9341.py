"""
ILI9341 2.8" TFT (240x320) + XPT2046 touch controller for Raspberry Pi Pico W.
SPI1 bus — pins per WIRING_DIAGRAM.md.

Renders display_server-format messages: "L1|L2|...|size_token"
Supported size tokens: auto, header, header:badge, menu, menu:page,
                       annotation, annotation:page, setup, qr, clock, online,
                       menuheader, menuheader:badge, and integer font-size.
"""

from machine import Pin, SPI
import framebuf
import time

# ── Pin assignments (WIRING_DIAGRAM.md) ─────────────────────────────────────
_SCK = 14
_MOSI = 15
_MISO = 12
_DC = 17
_RST = 18
_CS = 13
_BL = 19
_T_CS = 20
_T_IRQ = 21

# ── Display dimensions (portrait) ────────────────────────────────────────────
W = 240
H = 320


# ── RGB565 helpers ────────────────────────────────────────────────────────────
def _rgb(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


BLACK = 0x0000
WHITE = 0xFFFF
GRAY = _rgb(128, 128, 128)
DARK_GRAY = _rgb(40, 40, 40)
DIM_WHITE = _rgb(200, 200, 200)

# ── ILI9341 init sequence ─────────────────────────────────────────────────────
_INIT = (
    (0xEF, b"\x03\x80\x02"),
    (0xCF, b"\x00\xc1\x30"),
    (0xED, b"\x64\x03\x12\x81"),
    (0xE8, b"\x85\x00\x78"),
    (0xCB, b"\x39\x2c\x00\x34\x02"),
    (0xF7, b"\x20"),
    (0xEA, b"\x00\x00"),
    (0xC0, b"\x23"),  # Power control
    (0xC1, b"\x10"),  # Power control
    (0xC5, b"\x3e\x28"),  # VCM control
    (0xC7, b"\x86"),  # VCM control 2
    (0x36, b"\xe8"),  # MADCTL: MX + BGR → portrait, standard colours
    (0x3A, b"\x55"),  # 16-bit colour
    (0xB1, b"\x00\x18"),  # Frame rate
    (0xB6, b"\x08\x82\x27"),  # Display function control
    (0xF2, b"\x00"),  # Gamma disable
    (0x26, b"\x01"),  # Gamma curve 1
    (0xE0, b"\x0f\x31\x2b\x0c\x0e\x08\x4e\xf1\x37\x07\x10\x03\x0e\x09\x00"),
    (0xE1, b"\x00\x0e\x14\x03\x11\x07\x31\xc1\x48\x08\x0f\x0c\x31\x36\x0f"),
)


# ═══════════════════════════════════════════════════════════════════════════════
# Low-level ILI9341 driver
# ═══════════════════════════════════════════════════════════════════════════════
class ILI9341:

    def __init__(self):
        self._dc = Pin(_DC, Pin.OUT, value=1)
        self._rst = Pin(_RST, Pin.OUT, value=1)
        self._cs = Pin(_CS, Pin.OUT, value=1)
        self._bl = Pin(_BL, Pin.OUT, value=0)
        self._spi = SPI(
            1,
            baudrate=40_000_000,
            sck=Pin(_SCK),
            mosi=Pin(_MOSI),
            miso=Pin(_MISO),
            polarity=0,
            phase=0,
        )
        self._reset()
        self._init_display()
        self._bl.value(1)

    @property
    def spi(self):
        return self._spi

    def _reset(self):
        self._rst.value(0)
        time.sleep_ms(15)
        self._rst.value(1)
        time.sleep_ms(120)

    def _cmd(self, cmd):
        self._dc.value(0)
        self._cs.value(0)
        self._spi.write(bytes([cmd]))
        self._cs.value(1)

    def _data(self, data):
        self._dc.value(1)
        self._cs.value(0)
        self._spi.write(data)
        self._cs.value(1)

    def _write(self, cmd, data=None):
        self._cmd(cmd)
        if data:
            self._data(data)

    def _init_display(self):
        for cmd, data in _INIT:
            self._write(cmd, data)
        self._cmd(0x11)  # Sleep out
        time.sleep_ms(120)
        self._cmd(0x29)  # Display on

    def _set_window(self, x0, y0, x1, y1):
        self._write(0x2A, bytes([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
        self._write(0x2B, bytes([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
        self._cmd(0x2C)

    # ── Drawing primitives ────────────────────────────────────────────────────

    def fill(self, color):
        """Fill entire screen with one colour."""
        self._set_window(0, 0, W - 1, H - 1)
        hi, lo = color >> 8, color & 0xFF
        chunk = bytes([hi, lo] * W)  # one row
        self._dc.value(1)
        self._cs.value(0)
        for _ in range(H):
            self._spi.write(chunk)
        self._cs.value(1)

    def fill_rect(self, x, y, w, h, color):
        if w <= 0 or h <= 0:
            return
        self._set_window(x, y, x + w - 1, y + h - 1)
        hi, lo = color >> 8, color & 0xFF
        row = bytes([hi, lo] * w)
        self._dc.value(1)
        self._cs.value(0)
        for _ in range(h):
            self._spi.write(row)
        self._cs.value(1)

    def hline(self, x, y, length, color):
        self.fill_rect(x, y, length, 1, color)

    def char(self, x, y, ch, fg, bg, scale=1):
        """Render one 8×8 character scaled by *scale*."""
        mono = bytearray(8)
        fb = framebuf.FrameBuffer(mono, 8, 8, framebuf.MONO_HLSB)
        fb.fill(0)
        fb.text(ch, 0, 0, 1)

        cw = 8 * scale
        ch_h = 8 * scale
        buf = bytearray(cw * ch_h * 2)

        fg_hi, fg_lo = fg >> 8, fg & 0xFF
        bg_hi, bg_lo = bg >> 8, bg & 0xFF

        for row in range(8):
            byte = mono[row]
            for col in range(8):
                bit = (byte >> (7 - col)) & 1
                hi, lo = (fg_hi, fg_lo) if bit else (bg_hi, bg_lo)
                base_y = row * scale
                base_x = col * scale
                for sy in range(scale):
                    for sx in range(scale):
                        idx = ((base_y + sy) * cw + (base_x + sx)) * 2
                        buf[idx] = hi
                        buf[idx + 1] = lo

        self._set_window(x, y, x + cw - 1, y + ch_h - 1)
        self._dc.value(1)
        self._cs.value(0)
        self._spi.write(buf)
        self._cs.value(1)

    def text(self, x, y, s, fg, bg, scale=1):
        """Draw string *s* at (x, y). Each char occupies 8*scale pixels wide."""
        cw = 8 * scale
        for c in s:
            if x + cw > W:
                break
            self.char(x, y, c, fg, bg, scale)
            x += cw

    def text_centered(self, y, s, fg, bg, scale=1):
        """Draw string *s* horizontally centred at row *y*."""
        x = max(0, (W - len(s) * 8 * scale) // 2)
        self.text(x, y, s, fg, bg, scale)

    def text_right(self, y, s, fg, bg, scale=1):
        """Draw string *s* right-aligned."""
        x = max(0, W - len(s) * 8 * scale - 4)
        self.text(x, y, s, fg, bg, scale)


# ═══════════════════════════════════════════════════════════════════════════════
# XPT2046 touch controller
# ═══════════════════════════════════════════════════════════════════════════════
class XPT2046:
    """
    Touch controller on shared SPI1.  CS and speed are managed here;
    the display driver must not be active (CS high) during a read.

    Calibration defaults suit a typical 2.8" ILI9341 module.
    Adjust X_MIN/MAX, Y_MIN/MAX after running a calibration sketch.
    """

    X_MIN, X_MAX = 200, 3800
    Y_MIN, Y_MAX = 200, 3800

    def __init__(self, spi, cs_pin=_T_CS, irq_pin=_T_IRQ):
        self._spi = spi
        self._cs = Pin(cs_pin, Pin.OUT, value=1)
        self._irq = Pin(irq_pin, Pin.IN, Pin.PULL_UP)

    def _read_chan(self, cmd):
        """Read one 12-bit ADC channel via SPI at 2 MHz."""
        self._spi.init(baudrate=2_000_000, polarity=0, phase=0)
        self._cs.value(0)
        self._spi.write(bytes([cmd]))
        buf = bytearray(2)
        self._spi.readinto(buf)
        self._cs.value(1)
        self._spi.init(baudrate=40_000_000, polarity=0, phase=0)
        return (buf[0] << 5) | (buf[1] >> 3)

    def touched(self):
        return self._irq.value() == 0

    def read(self):
        """
        Return (x, y) in screen-pixel coordinates, or None if not touched.
        Averages two reads per axis for stability.
        """
        if not self.touched():
            return None
        x1 = self._read_chan(0xD0)
        y1 = self._read_chan(0x90)
        x2 = self._read_chan(0xD0)
        y2 = self._read_chan(0x90)
        x_raw = (x1 + x2) >> 1
        y_raw = (y1 + y2) >> 1
        if x_raw < 100 or y_raw < 100:
            return None
        x = int((x_raw - self.X_MIN) * W / (self.X_MAX - self.X_MIN))
        y = int((y_raw - self.Y_MIN) * H / (self.Y_MAX - self.Y_MIN))
        x = max(0, min(W - 1, x))
        y = max(0, min(H - 1, y))
        return x, y

    def get_zone(self, zones):
        """
        Map a touch point to a zone name.
        zones = {"name": (x0, y0, x1, y1), ...}
        Returns the first matching name, or None.
        """
        pt = self.read()
        if pt is None:
            return None
        px, py = pt
        for name, (x0, y0, x1, y1) in zones.items():
            if x0 <= px <= x1 and y0 <= py <= y1:
                return name
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Layout helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _is_footer_hint(line):
    if not line:
        return False
    low = line.strip().lower()
    return (
        low.startswith("press ok")
        or low.startswith("press hint")
        or "ok =" in low
        or "ok=" in low
        or "hint =" in low
        or "hint=" in low
        or "ok+" in low
    )


def _split_footer(text):
    """Split footer on runs of 2+ spaces → up to 3 parts."""
    text = (text or "").strip()
    if not text:
        return []
    parts = []
    buf = []
    run = 0
    for c in text:
        if c == " ":
            run += 1
            buf.append(c)
        else:
            if run >= 2 and buf:
                seg = "".join(buf).strip()
                if seg:
                    parts.append(seg)
                buf = [c]
            else:
                buf.append(c)
            run = 0
    seg = "".join(buf).strip()
    if seg:
        parts.append(seg)
    return parts[:3] if parts else ([text] if text else [])


def _word_wrap(text, max_chars):
    """Wrap *text* at word boundaries to fit *max_chars* per line."""
    text = (text or "").strip()
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]
    words = text.split()
    lines = []
    cur = ""
    for w in words:
        if not cur:
            cur = w[:max_chars]
        elif len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w[:max_chars]
    if cur:
        lines.append(cur)
    return lines if lines else [text[:max_chars]]


def _best_scale(lines, max_lines_hint=8):
    """Pick the largest scale where every line fits horizontally."""
    if not lines:
        return 2
    max_len = max(len(ln) for ln in lines) if lines else 0
    if max_len <= 10 and len(lines) <= max_lines_hint:
        return 3
    if max_len <= 15:
        return 2
    return 1


def _truncate(text, max_chars):
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "~"


# ═══════════════════════════════════════════════════════════════════════════════
# High-level display controller
# ═══════════════════════════════════════════════════════════════════════════════
class TFTDisplay:
    """
    Receives pipe-format messages from the Pi (via UART/DISP: prefix stripped)
    and renders them on the ILI9341.

    Message format:  "L1|L2|L3|...|size_token"
    (identical to what display_server reads from the FIFO pipe)
    """

    # Layout constants
    _HDR_Y = 4  # header text top
    _HDR_SCALE = 2  # default header scale
    _DIV1_H = 2  # divider thickness
    _FOOT_H = 18  # footer text height (scale 1 = 8px + padding)
    _FOOT_MARGIN = 4  # gap below divider to footer text
    _FOOT_DIV_Y = H - _FOOT_H - _FOOT_MARGIN - 4  # footer divider y ≈ 290
    _FOOT_Y = H - _FOOT_H - 2  # footer text y ≈ 296

    def __init__(self):
        self._lcd = ILI9341()
        self._xpt = XPT2046(self._lcd.spi)
        self._clock_lines = None
        self._last_payload = None
        self.show_splash()

    # ── Public: show_* helpers ────────────────────────────────────────────────

    def show_splash(self):
        lcd = self._lcd
        lcd.fill(BLACK)
        lcd.text_centered(H // 2 - 16, "SMARTCHESS", WHITE, BLACK, scale=2)
        lcd.text_centered(H // 2 + 8, "Starting...", GRAY, BLACK, scale=1)

    def show_boot(self, msg="Starting..."):
        lcd = self._lcd
        lcd.fill(BLACK)
        lcd.text_centered(H // 2 - 8, msg, WHITE, BLACK, scale=2)

    # ── Public: render a pipe-format payload ─────────────────────────────────

    def render(self, payload):
        """
        Parse a display_server pipe message ("L1|L2|...|size") and render it.
        *payload* must NOT include the leading "DISP:" or trailing newline.
        """
        payload = (payload or "").strip()
        if not payload:
            return
        if payload == self._last_payload:
            return
        self._last_payload = payload

        parts = payload.split("|")
        size_tok = parts[-1].strip().lower() if parts else "auto"
        lines = parts[:-1]

        # ── Clock overlay (does not redraw full screen) ──────────────────────
        if size_tok == "clock":
            if lines and lines[0] == "__clock__":
                self._clock_lines = lines[1:3]
            elif lines and lines[0] == "__clock_clear__":
                self._clock_lines = None
            return  # clock-only update; let next real message redraw

        # ── QR ───────────────────────────────────────────────────────────────
        if size_tok == "qr":
            self._render_qr(lines)
            return

        # ── Annotation (left-aligned body text for studies) ──────────────────
        if size_tok.startswith("annotation"):
            page = size_tok.split(":", 1)[1] if ":" in size_tok else ""
            self._render_menu(lines, page, left_align=True)
            return

        # ── Menu/paged ───────────────────────────────────────────────────────
        if size_tok.startswith("menu") and not size_tok.startswith("menuheader"):
            page = size_tok.split(":", 1)[1] if ":" in size_tok else ""
            self._render_menu(lines, page)
            return

        # ── Online clock view ────────────────────────────────────────────────
        if size_tok == "online":
            self._render_online(lines)
            return

        # ── Numeric fixed size ───────────────────────────────────────────────
        try:
            _sz = int(size_tok)
            self._render_fixed(lines, _sz)
            return
        except (ValueError, TypeError):
            pass

        # ── header / menuheader / setup / auto (default path) ────────────────
        badge = ""
        if ":" in size_tok:
            badge = size_tok.split(":", 1)[1]

        # Inject clock lines if active
        if self._clock_lines and size_tok in ("auto", "header"):
            self._render_online(list(self._clock_lines) + lines)
            return

        self._render_header_panel(lines, badge)

    # ── Internal layout renderers ─────────────────────────────────────────────

    def _header_zone_height(self, header, scale):
        """Height consumed by the header text + small gap."""
        return 8 * scale + 6

    def _render_header_panel(self, lines, badge=""):
        """
        Standard header panel:
          [header]  (+ badge top-right)
          ─────────────────────────────
          [body lines, auto-sized]
          ─────────────────────────────
          [footer text]
        """
        lcd = self._lcd
        lcd.fill(BLACK)

        if not lines:
            return

        header = lines[0].strip() if lines else ""

        # Detect footer
        has_footer = len(lines) > 1 and _is_footer_hint(lines[-1])
        footer_raw = lines[-1] if has_footer else ""
        body_lines = lines[1:-1] if has_footer and len(lines) > 2 else lines[1:]
        body_lines = [ln for ln in body_lines if ln is not None]

        # ── Header ────────────────────────────────────────────────────────────
        hdr_scale = 3 if len(header) <= 9 else 2
        hdr_h = 8 * hdr_scale
        y = self._HDR_Y

        lcd.text_centered(y, header, WHITE, BLACK, hdr_scale)
        if badge:
            b = _truncate(badge, 6)
            lcd.text_right(y + (hdr_h - 8) // 2, b, GRAY, BLACK, scale=1)
        y += hdr_h + 4

        # Divider
        lcd.hline(6, y, W - 12, GRAY)
        y += self._DIV1_H + 4

        body_top = y

        # ── Footer ────────────────────────────────────────────────────────────
        if has_footer:
            foot_parts = _split_footer(footer_raw)
            self._draw_footer(foot_parts)
            avail_h = self._FOOT_DIV_Y - body_top - 4
        else:
            avail_h = H - body_top - 4

        # ── Body ──────────────────────────────────────────────────────────────
        if not body_lines:
            return

        # Wrap + pick scale
        wrapped = []
        for ln in body_lines:
            if ln:
                wrapped.extend(_word_wrap(ln, 15))  # try scale-2 width
        scale = _best_scale(wrapped, max_lines_hint=max(1, avail_h // 20))
        ch = 8 * scale
        line_h = ch + 4
        total_h = len(wrapped) * line_h - 4
        body_y = body_top + max(0, (avail_h - total_h) // 2)

        for ln in wrapped:
            if body_y + ch > (self._FOOT_DIV_Y if has_footer else H) - 2:
                break
            lcd.text_centered(body_y, _truncate(ln, 30 // scale), WHITE, BLACK, scale)
            body_y += line_h

    def _render_menu(self, lines, page="", left_align=False):
        """
        Menu layout:
          [page indicator top-right]
          [item 1]
          [item 2]
          [item 3]
          ─────────────────────────────
          [footer]
        """
        lcd = self._lcd
        lcd.fill(BLACK)

        if not lines:
            return

        has_footer = len(lines) > 1 and _is_footer_hint(lines[-1])
        footer_raw = lines[-1] if has_footer else ""
        items = lines[:-1] if has_footer else list(lines)
        items = [ln for ln in items if ln]

        y = 4
        if page:
            p = _truncate(page, 5)
            lcd.text_right(y, p, GRAY, BLACK, scale=1)
            y += 12

        if has_footer:
            self._draw_footer(_split_footer(footer_raw))
            avail_h = self._FOOT_DIV_Y - y - 8
        else:
            avail_h = H - y - 8

        if not items:
            return

        scale = 2
        ch = 8 * scale
        spacing = 6
        total_h = len(items) * (ch + spacing) - spacing
        item_y = y + max(4, (avail_h - total_h) // 2)

        for item in items:
            if item_y + ch > (self._FOOT_DIV_Y if has_footer else H) - 2:
                break
            t = _truncate(item, 15)
            if left_align:
                lcd.text(8, item_y, t, WHITE, BLACK, scale)
            else:
                lcd.text_centered(item_y, t, WHITE, BLACK, scale)
            item_y += ch + spacing

    def _render_online(self, lines):
        """
        Online clock layout:
          [left_clock   right_clock]
          ─────────────────────────
          [body lines]
        """
        lcd = self._lcd
        lcd.fill(BLACK)

        clock_lines = lines[:2] if len(lines) >= 2 else (lines + [""])[:2]
        body_lines = [ln for ln in lines[2:] if ln] if len(lines) > 2 else []

        y = 4
        for i, cl in enumerate(clock_lines):
            if not cl:
                continue
            parts = cl.strip().split(" ", 1)
            label = parts[0] if len(parts) == 2 else ""
            tval = parts[1] if len(parts) == 2 else cl
            if i == 0:
                # Left-align
                if label:
                    lcd.text(4, y + 2, _truncate(label, 4), GRAY, BLACK, scale=1)
                    lcd.text(
                        4 + len(_truncate(label, 4)) * 8 + 2,
                        y,
                        _truncate(tval, 6),
                        WHITE,
                        BLACK,
                        scale=2,
                    )
                else:
                    lcd.text(4, y, _truncate(tval, 7), WHITE, BLACK, scale=2)
            else:
                # Right-align
                tval_t = _truncate(tval, 6)
                label_t = _truncate(label, 4) if label else ""
                full_w = (len(tval_t) * 16) + (len(label_t) * 8 + 2 if label_t else 0)
                rx = W - full_w - 4
                if label_t:
                    lcd.text(rx, y + 2, label_t, GRAY, BLACK, scale=1)
                    lcd.text(
                        rx + len(label_t) * 8 + 2, y, tval_t, WHITE, BLACK, scale=2
                    )
                else:
                    lcd.text(rx, y, tval_t, WHITE, BLACK, scale=2)

        y += 20
        lcd.hline(6, y, W - 12, GRAY)
        y += 6

        if not body_lines:
            return

        wrapped = []
        for ln in body_lines:
            wrapped.extend(_word_wrap(ln, 15))
        scale = _best_scale(wrapped)
        ch = 8 * scale
        avail_h = H - y - 8
        total_h = len(wrapped) * (ch + 4) - 4
        body_y = y + max(0, (avail_h - total_h) // 2)

        for ln in wrapped:
            lcd.text_centered(body_y, _truncate(ln, 30 // scale), WHITE, BLACK, scale)
            body_y += ch + 4

    def _render_qr(self, lines):
        """Render a QR code placeholder (pure-Python QR not available on Pico)."""
        lcd = self._lcd
        lcd.fill(BLACK)
        data = lines[0].strip() if lines else ""
        captions = lines[1:] if len(lines) > 1 else []
        # Show URL/data as wrapped text since QR encoding requires significant
        # RAM that may not be available on Pico.  Captions shown below.
        lcd.text_centered(8, "Scan QR:", WHITE, BLACK, scale=1)
        y = 24
        for chunk in _word_wrap(data, 26):
            lcd.text_centered(y, chunk, DIM_WHITE, BLACK, scale=1)
            y += 12
        y += 4
        for cap in captions[:3]:
            if cap:
                lcd.text_centered(y, _truncate(cap, 26), GRAY, BLACK, scale=1)
                y += 12

    def _render_fixed(self, lines, size_hint):
        """Render lines at a roughly fixed size (mapped from pt to scale)."""
        scale = 3 if size_hint >= 26 else (2 if size_hint >= 16 else 1)
        lcd = self._lcd
        lcd.fill(BLACK)
        ch = 8 * scale
        spacing = 4
        total_h = len(lines) * (ch + spacing) - spacing
        y = max(4, (H - total_h) // 2)
        for ln in lines:
            lcd.text_centered(y, _truncate(ln or "", 30 // scale), WHITE, BLACK, scale)
            y += ch + spacing

    # ── Footer helper ─────────────────────────────────────────────────────────

    def _draw_footer(self, parts):
        """Draw 1–3 footer parts: centred / left+right / left+centre+right."""
        if not parts:
            return
        lcd = self._lcd
        lcd.hline(6, self._FOOT_DIV_Y, W - 12, GRAY)
        y = self._FOOT_Y
        scale = 1

        def _tx(text):
            return _truncate(text, 28)

        if len(parts) == 1:
            lcd.text_centered(y, _tx(parts[0]), GRAY, BLACK, scale)
        elif len(parts) == 2:
            lcd.text(4, y, _tx(parts[0]), GRAY, BLACK, scale)
            t = _tx(parts[1])
            lcd.text(W - len(t) * 8 - 4, y, t, GRAY, BLACK, scale)
        else:
            lcd.text(4, y, _tx(parts[0]), GRAY, BLACK, scale)
            lcd.text_centered(y, _tx(parts[1]), GRAY, BLACK, scale)
            t = _tx(parts[2])
            lcd.text(W - len(t) * 8 - 4, y, t, GRAY, BLACK, scale)

    # ── Touch input ───────────────────────────────────────────────────────────

    @property
    def touch(self):
        return self._xpt
