"""
ILI9341 2.8" TFT (240x320) + XPT2046 touch controller for Raspberry Pi Pico W.
SPI1 bus — pins per WIRING_DIAGRAM.md.

Renders display_server-format messages: "L1|L2|...|size_token"
Supported size tokens: auto, header, header:badge, menu, menu:page,
                       annotation, annotation:page, setup, qr, clock, online,
                       menuheader, menuheader:badge, and integer font-size.

Layout (portrait 240x320):
  ┌──────────────────────────┐  y=0
  │     HEADER (48px)        │  dark background, white text
  ├──────────────────────────┤  y=48
  │                          │
  │   CONTENT  (172px)       │  menu items / status text / annotations
  │                          │
  ├──────────────────────────┤  y=220
  │   ACTION ZONE (50px)     │  TAP TO CONFIRM  / active info
  ├──────────────────────────┤  y=270
  │  [◀ Left]  │  [Right ▶] │  NAV BAR (50px)
  └──────────────────────────┘  y=320
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


BLACK     = 0x0000
WHITE     = 0xFFFF
GRAY      = _rgb(128, 128, 128)
DARK_GRAY = _rgb(30, 30, 30)
DIM_WHITE = _rgb(180, 180, 180)
ACCENT    = _rgb(0, 160, 200)     # cyan-blue header accent
ACCENT_DIM= _rgb(0, 60, 80)      # dimmed accent for nav background
CONFIRM_BG= _rgb(0, 50, 30)      # dark green confirm zone background
CONFIRM_FG= _rgb(0, 220, 100)    # bright green confirm text
ITEM_SEP  = _rgb(45, 45, 45)     # item separator line

# ── ILI9341 init sequence ─────────────────────────────────────────────────────
_INIT = (
    (0xEF, b"\x03\x80\x02"),
    (0xCF, b"\x00\xc1\x30"),
    (0xED, b"\x64\x03\x12\x81"),
    (0xE8, b"\x85\x00\x78"),
    (0xCB, b"\x39\x2c\x00\x34\x02"),
    (0xF7, b"\x20"),
    (0xEA, b"\x00\x00"),
    (0xC0, b"\x23"),              # Power control
    (0xC1, b"\x10"),              # Power control
    (0xC5, b"\x3e\x28"),          # VCM control
    (0xC7, b"\x86"),              # VCM control 2
    (0x36, b"\xe8"),              # MADCTL: portrait, BGR
    (0x3A, b"\x55"),              # 16-bit colour
    (0xB1, b"\x00\x18"),          # Frame rate
    (0xB6, b"\x08\x82\x27"),      # Display function control
    (0xF2, b"\x00"),              # Gamma disable
    (0x26, b"\x01"),              # Gamma curve 1
    (0xE0, b"\x0f\x31\x2b\x0c\x0e\x08\x4e\xf1\x37\x07\x10\x03\x0e\x09\x00"),
    (0xE1, b"\x00\x0e\x14\x03\x11\x07\x31\xc1\x48\x08\x0f\x0c\x31\x36\x0f"),
)


# ═══════════════════════════════════════════════════════════════════════════════
# Low-level ILI9341 driver
# ═══════════════════════════════════════════════════════════════════════════════
class ILI9341:

    def __init__(self):
        self._dc  = Pin(_DC,  Pin.OUT, value=1)
        self._rst = Pin(_RST, Pin.OUT, value=1)
        self._cs  = Pin(_CS,  Pin.OUT, value=1)
        self._bl  = Pin(_BL,  Pin.OUT, value=0)
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
        self._cmd(0x11)     # Sleep out
        time.sleep_ms(120)
        self._cmd(0x29)     # Display on

    def _set_window(self, x0, y0, x1, y1):
        self._write(0x2A, bytes([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
        self._write(0x2B, bytes([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
        self._cmd(0x2C)

    # ── Drawing primitives ────────────────────────────────────────────────────

    def fill(self, color):
        self._set_window(0, 0, W - 1, H - 1)
        hi, lo = color >> 8, color & 0xFF
        chunk = bytes([hi, lo] * W)
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

    def vline(self, x, y, length, color):
        self.fill_rect(x, y, 1, length, color)

    def char(self, x, y, ch, fg, bg, scale=1):
        """Render one 8×8 character scaled by *scale*."""
        mono = bytearray(8)
        fb = framebuf.FrameBuffer(mono, 8, 8, framebuf.MONO_HLSB)
        fb.fill(0)
        fb.text(ch, 0, 0, 1)

        cw   = 8 * scale
        ch_h = 8 * scale
        buf  = bytearray(cw * ch_h * 2)

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
                        buf[idx]     = hi
                        buf[idx + 1] = lo

        self._set_window(x, y, x + cw - 1, y + ch_h - 1)
        self._dc.value(1)
        self._cs.value(0)
        self._spi.write(buf)
        self._cs.value(1)

    def text(self, x, y, s, fg, bg, scale=1, bg_fill=True):
        """Draw string *s* at (x, y).  bg_fill=False skips background pixels."""
        cw = 8 * scale
        for c in s:
            if x + cw > W:
                break
            self.char(x, y, c, fg, bg, scale)
            x += cw

    def text_centered(self, y, s, fg, bg, scale=1):
        x = max(0, (W - len(s) * 8 * scale) // 2)
        self.text(x, y, s, fg, bg, scale)

    def text_right(self, y, s, fg, bg, scale=1):
        x = max(0, W - len(s) * 8 * scale - 4)
        self.text(x, y, s, fg, bg, scale)

    def rect(self, x, y, w, h, color):
        """Draw an unfilled rectangle."""
        self.hline(x, y, w, color)
        self.hline(x, y + h - 1, w, color)
        self.vline(x, y, h, color)
        self.vline(x + w - 1, y, h, color)


# ═══════════════════════════════════════════════════════════════════════════════
# XPT2046 touch controller
# ═══════════════════════════════════════════════════════════════════════════════
class XPT2046:
    """
    Touch controller on shared SPI1.
    Calibration defaults suit a typical 2.8" ILI9341 module.
    Adjust X_MIN/MAX, Y_MIN/MAX after running the calibration helper.
    """
    X_MIN, X_MAX = 200, 3800
    Y_MIN, Y_MAX = 200, 3800
    SAMPLE_JITTER = 120

    def __init__(self, spi, cs_pin=_T_CS, irq_pin=_T_IRQ):
        self._spi = spi
        self._cs  = Pin(cs_pin,  Pin.OUT, value=1)
        self._irq = Pin(irq_pin, Pin.IN,  Pin.PULL_UP)

    def _read_chan(self, cmd):
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
        """Return (x, y) in screen pixels, or None."""
        x1 = self._read_chan(0xD0)
        y1 = self._read_chan(0x90)
        x2 = self._read_chan(0xD0)
        y2 = self._read_chan(0x90)
        if abs(x1 - x2) > self.SAMPLE_JITTER or abs(y1 - y2) > self.SAMPLE_JITTER:
            return None
        x_raw = (x1 + x2) >> 1
        y_raw = (y1 + y2) >> 1
        if x_raw < 100 or y_raw < 100:
            return None
        x = int((x_raw - self.X_MIN) * W / (self.X_MAX - self.X_MIN))
        y = int((y_raw - self.Y_MIN) * H / (self.Y_MAX - self.Y_MIN))
        return max(0, min(W - 1, x)), max(0, min(H - 1, y))

    def get_zone(self, zones):
        pt = self.read()
        if pt is None:
            return None
        px, py = pt
        for name, (x0, y0, x1, y1) in zones.items():
            if x0 <= px <= x1 and y0 <= py <= y1:
                return name
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Text helpers
# ═══════════════════════════════════════════════════════════════════════════════

# Map Unicode chess/arrow symbols to ASCII so the 8x8 font can render them.
_CHESS_MAP = {
    "\u2654": "K", "\u2655": "Q", "\u2656": "R", "\u2657": "B",
    "\u2658": "N", "\u2659": "P",
    "\u265a": "k", "\u265b": "q", "\u265c": "r", "\u265d": "b",
    "\u265e": "n", "\u265f": "p",
    "\u2192": "->", "\u2190": "<-", "\u2194": "<->",
    "\u00d7": "x",  # multiplication sign → x (captures)
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
}


def _clean(text):
    """Replace non-ASCII chess/arrow symbols; strip other non-printable chars."""
    if not text:
        return ""
    out = []
    for ch in text:
        if 32 <= ord(ch) <= 126:
            out.append(ch)
        else:
            out.append(_CHESS_MAP.get(ch, ""))
    return "".join(out)


def _truncate(text, n):
    text = _clean(text)
    return text if len(text) <= n else text[:n - 1] + "~"


def _word_wrap(text, max_chars):
    """Wrap *text* (after _clean) at word boundaries."""
    text = _clean(text).strip()
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]
    words = text.split()
    lines, cur = [], ""
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
    return lines or [text[:max_chars]]


def _is_footer_hint(line):
    if not line:
        return False
    low = _clean(line).strip().lower()
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
    """Split footer on 2+ spaces → up to 3 parts."""
    text = _clean(text or "").strip()
    if not text:
        return []
    parts, buf, run = [], [], 0
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
    return parts[:3] or ([text] if text else [])


def _parse_nav_from_footer(foot_parts):
    """Return (left_label, right_label) for the nav bar from footer parts."""
    left = right = ""
    for fp in foot_parts:
        fl = fp.strip().lower()
        if fl.startswith("ok") or fl.startswith("back") or "ok=" in fl:
            left = fp.split("=", 1)[-1].strip() if "=" in fp else fp
        elif fl.startswith("hint") or fl.startswith("next") or "hint=" in fl:
            right = fp.split("=", 1)[-1].strip() if "=" in fp else fp
    return left, right


# ═══════════════════════════════════════════════════════════════════════════════
# High-level display controller
# ═══════════════════════════════════════════════════════════════════════════════
class TFTDisplay:
    """
    Receives pipe-format messages from the Pi (via UART with DISP: prefix stripped)
    and renders them on the ILI9341 with full touch zone tracking.

    Zone names returned by touch_action():
      "item_1" … "item_5" — menu item rows (tapped directly)
      "btn_ok"            — OK / Back / confirm (nav-left or action zone)
      "btn_hint"          — Hint / Next (nav-right)
      "game_confirm"      — Tap-to-confirm zone during move entry
      "game_hint"         — Hint zone during gameplay
      "game_menu"         — Menu/exit zone during gameplay
      "page_prev"         — Previous page (annotation)
      "page_next"         — Next page (annotation)
    """

    # ── Zone y-coordinates ────────────────────────────────────────────────────
    HDR_TOP  = 0
    HDR_BOT  = 48    # header bar
    BODY_TOP = 48
    BODY_BOT = 220   # content area
    ACT_TOP  = 220
    ACT_BOT  = 270   # action / confirm zone
    NAV_TOP  = 270
    NAV_BOT  = H     # nav bar

    # Annotation layout (no header bar)
    ANN_TITLE_H  = 28
    ANN_BODY_TOP = 28
    ANN_BODY_BOT = 290
    ANN_NAV_TOP  = 290

    def __init__(self):
        self._lcd  = ILI9341()
        self._xpt  = XPT2046(self._lcd.spi)
        self._clock_lines  = None
        self._last_payload = None
        self._touch_zones  = {}
        self._last_touch_ms = 0
        self._TOUCH_DEBOUNCE_MS = 350
        self.show_splash()

    # ── Splash / boot ─────────────────────────────────────────────────────────

    def show_splash(self):
        lcd = self._lcd
        lcd.fill(BLACK)
        # Header accent bar
        lcd.fill_rect(0, 0, W, self.HDR_BOT, ACCENT_DIM)
        lcd.text_centered(14, "SMARTCHESS", WHITE, ACCENT_DIM, scale=3)
        lcd.text_centered(self.HDR_BOT + 40, "Starting...", GRAY, BLACK, scale=2)

    # ── Main render entry point ───────────────────────────────────────────────

    def render(self, payload):
        """Parse and render a pipe-format display message."""
        payload = (payload or "").strip()
        if not payload or payload == self._last_payload:
            return
        self._last_payload = payload
        self._touch_zones  = {}

        parts    = payload.split("|")
        size_tok = parts[-1].strip().lower() if parts else "auto"
        lines    = parts[:-1]

        # Clock overlay — doesn't redraw screen
        if size_tok == "clock":
            if lines and lines[0] == "__clock__":
                self._clock_lines = lines[1:3]
            elif lines and lines[0] == "__clock_clear__":
                self._clock_lines = None
            return

        if size_tok == "qr":
            self._render_qr(lines)
            return

        if size_tok.startswith("annotation"):
            page = size_tok.split(":", 1)[1] if ":" in size_tok else ""
            self._render_annotation(lines, page)
            return

        if size_tok.startswith("menu") and not size_tok.startswith("menuheader"):
            page = size_tok.split(":", 1)[1] if ":" in size_tok else ""
            self._render_menu(lines, page)
            return

        if size_tok == "online":
            self._render_online(lines)
            return

        try:
            _sz = int(size_tok)
            self._render_fixed(lines, _sz)
            return
        except (ValueError, TypeError):
            pass

        # header / menuheader / setup / auto
        badge = size_tok.split(":", 1)[1] if ":" in size_tok else ""
        if self._clock_lines and size_tok in ("auto", "header"):
            self._render_online(list(self._clock_lines) + lines)
            return

        is_menu_header = size_tok.startswith("menuheader")
        self._render_header_panel(lines, badge, is_menu_header=is_menu_header)

    # ═══════════════════════════════════════════════════════════════════════
    # Layout renderers
    # ═══════════════════════════════════════════════════════════════════════

    def _render_menu(self, lines, page=""):
        """
        Full-screen menu: items fill y=0..NAV_TOP, nav bar at bottom.
        Each item is a large tappable row — touch zones registered as "item_N".
        """
        lcd = self._lcd
        lcd.fill(BLACK)

        has_footer = len(lines) > 1 and _is_footer_hint(lines[-1])
        footer_raw = lines[-1] if has_footer else ""
        items = [_clean(ln) for ln in (lines[:-1] if has_footer else lines) if ln]

        foot_parts = _split_footer(footer_raw) if has_footer else []
        nav_left, nav_right = _parse_nav_from_footer(foot_parts)

        # Page indicator (top-right, small, inside first item)
        if page:
            lcd.text_right(6, _truncate(page, 6), GRAY, BLACK, scale=1)

        n = max(len(items), 1)
        item_zone_h = self.NAV_TOP  # 270px divided among items
        row_h = item_zone_h // n

        for i, item in enumerate(items[:5]):
            y0 = i * row_h
            y1 = y0 + row_h

            # Separator line (not before first item)
            if i > 0:
                lcd.hline(0, y0, W, ITEM_SEP)

            # Left accent stripe
            lcd.fill_rect(0, y0 + 2, 4, row_h - 4, ACCENT)

            # Item text: scale=2 (16px), vertically centered in row
            text_y = y0 + (row_h - 16) // 2
            t = _truncate(item, 14)
            lcd.text_centered(text_y, t, WHITE, BLACK, scale=2)

            # Touch zone — full row width
            self._touch_zones["item_%d" % (i + 1)] = (0, y0 + 1, W - 1, y1 - 1)

        self._draw_nav_bar(nav_left, nav_right)

    def _render_header_panel(self, lines, badge="", is_menu_header=False):
        """
        Standard panel used for game status, confirmations, setup screens.
        Header bar at top, content in middle, action zone + nav bar at bottom.
        """
        lcd = self._lcd
        lcd.fill(BLACK)

        if not lines:
            return

        header    = _clean(lines[0]).strip() if lines else ""
        has_footer = len(lines) > 1 and _is_footer_hint(lines[-1])
        footer_raw = lines[-1] if has_footer else ""
        foot_parts = _split_footer(footer_raw) if has_footer else []
        body_lines = lines[1:-1] if has_footer and len(lines) > 2 else lines[1:]
        body_lines = [_clean(ln) for ln in body_lines if ln is not None]

        # ── Header bar ────────────────────────────────────────────────────────
        lcd.fill_rect(0, self.HDR_TOP, W, self.HDR_BOT, ACCENT_DIM)
        hdr_scale = 3 if len(header) <= 9 else 2
        hdr_h = 8 * hdr_scale
        hdr_y = max(4, (self.HDR_BOT - hdr_h) // 2)
        lcd.text_centered(hdr_y, _truncate(header, 13 if hdr_scale == 2 else 10),
                          WHITE, ACCENT_DIM, hdr_scale)
        if badge:
            lcd.text_right(hdr_y + (hdr_h - 8) // 2,
                           _truncate(badge, 5), GRAY, ACCENT_DIM, scale=1)

        # ── Content (body) ────────────────────────────────────────────────────
        body_top    = self.BODY_TOP + 8
        body_bottom = self.BODY_BOT - 4

        if is_menu_header:
            # Items as tappable rows
            self._render_items_in_zone(body_lines, body_top, body_bottom)
        else:
            # Plain body text, auto-scaled
            self._render_body_text(body_lines, body_top, body_bottom)

        # ── Action zone ───────────────────────────────────────────────────────
        is_gameplay = self._is_gameplay(header, body_lines, footer_raw)
        self._render_action_zone(foot_parts, is_gameplay, footer_raw)

        # ── Nav bar ───────────────────────────────────────────────────────────
        if is_gameplay:
            self._draw_nav_bar("< Menu", "Hint >")
            self._touch_zones["game_menu"] = (0, self.NAV_TOP, W // 2 - 1, H - 1)
            self._touch_zones["game_hint"] = (W // 2, self.NAV_TOP, W - 1, H - 1)
        else:
            nav_left, nav_right = _parse_nav_from_footer(foot_parts)
            if nav_left or nav_right or is_menu_header:
                if not nav_left and is_menu_header:
                    nav_left = "< Back"
                self._draw_nav_bar(nav_left, nav_right)
                if nav_left:
                    self._touch_zones["btn_ok"] = (0, self.NAV_TOP, W // 2 - 1, H - 1)
                if nav_right:
                    self._touch_zones["btn_hint"] = (W // 2, self.NAV_TOP, W - 1, H - 1)

    def _render_annotation(self, lines, page=""):
        """
        Study annotation layout: maximises text area.
          y=0..28    : chapter title (small, accent bar)
          y=28..290  : body text (scale=1, max ~24 lines of 28 chars)
          y=290..320 : nav bar  [< Prev/Back]  [Next/Skip >]
        """
        lcd = self._lcd
        lcd.fill(BLACK)

        if not lines:
            return

        has_footer = len(lines) > 1 and _is_footer_hint(lines[-1])
        footer_raw = lines[-1] if has_footer else ""
        foot_parts = _split_footer(footer_raw) if has_footer else []
        content = lines[:-1] if has_footer else list(lines)
        content = [_clean(ln) for ln in content if ln is not None]

        # Title line (first content line, or empty)
        title = content[0].strip() if content else ""
        body  = content[1:] if len(content) > 1 else []

        # Title bar
        lcd.fill_rect(0, 0, W, self.ANN_TITLE_H, ACCENT_DIM)
        if title:
            lcd.text_centered(7, _truncate(title, 26), WHITE, ACCENT_DIM, scale=1)
        if page:
            lcd.text_right(7, _truncate(page, 6), GRAY, ACCENT_DIM, scale=1)

        # Body text: word-wrap all body lines, render at scale=1
        # 28 chars × 8px = 224px wide (fits with 8px margin each side)
        wrapped = []
        for ln in body:
            if not ln.strip():
                wrapped.append("")          # preserve blank lines as spacers
            else:
                wrapped.extend(_word_wrap(ln, 27))

        avail_h  = self.ANN_BODY_BOT - self.ANN_BODY_TOP
        line_h   = 11   # 8px char + 3px gap
        max_vis  = avail_h // line_h   # ~23 lines visible at once

        y = self.ANN_BODY_TOP + 4
        for ln in wrapped[:max_vis]:
            if not ln:
                y += line_h // 2   # half-height blank
                continue
            lcd.text(8, y, _truncate(ln, 27), DIM_WHITE, BLACK, scale=1)
            y += line_h

        # Nav bar
        nav_left, nav_right = _parse_nav_from_footer(foot_parts)
        if not nav_left:
            nav_left = "< Back"
        if not nav_right and len(wrapped) > max_vis:
            nav_right = "More >"
        self._draw_nav_bar(nav_left, nav_right, y0=self.ANN_NAV_TOP)
        if nav_left:
            self._touch_zones["page_prev"] = (0, self.ANN_NAV_TOP, W // 2 - 1, H - 1)
            self._touch_zones["btn_ok"]    = (0, self.ANN_NAV_TOP, W // 2 - 1, H - 1)
        if nav_right:
            self._touch_zones["page_next"] = (W // 2, self.ANN_NAV_TOP, W - 1, H - 1)
            self._touch_zones["btn_hint"]  = (W // 2, self.ANN_NAV_TOP, W - 1, H - 1)

    def _render_online(self, lines):
        """Online clock + body content."""
        lcd = self._lcd
        lcd.fill(BLACK)

        clocks = lines[:2] if len(lines) >= 2 else (list(lines) + [""])[:2]
        body   = [_clean(ln) for ln in lines[2:] if ln] if len(lines) > 2 else []

        # Header bar carries clock info
        lcd.fill_rect(0, self.HDR_TOP, W, self.HDR_BOT, ACCENT_DIM)
        for i, cl in enumerate(clocks):
            if not cl:
                continue
            parts = cl.strip().split(" ", 1)
            label = parts[0] if len(parts) == 2 else ""
            tval  = parts[1] if len(parts) == 2 else cl
            yt = 6 if i == 0 else 26
            if i == 0:
                if label:
                    lcd.text(6, yt + 2, _truncate(label, 4), GRAY, ACCENT_DIM, 1)
                    lcd.text(6 + len(_truncate(label, 4)) * 8 + 4, yt,
                             _truncate(tval, 5), WHITE, ACCENT_DIM, 2)
                else:
                    lcd.text(6, yt, _truncate(tval, 6), WHITE, ACCENT_DIM, 2)
            else:
                tval_t  = _truncate(tval, 5)
                label_t = _truncate(label, 4)
                tw = len(tval_t) * 16 + (len(label_t) * 8 + 4 if label_t else 0)
                rx = W - tw - 6
                if label_t:
                    lcd.text(rx, yt + 2, label_t, GRAY, ACCENT_DIM, 1)
                    lcd.text(rx + len(label_t) * 8 + 4, yt, tval_t, WHITE, ACCENT_DIM, 2)
                else:
                    lcd.text(W - len(tval_t) * 16 - 6, yt, tval_t, WHITE, ACCENT_DIM, 2)

        self._render_body_text(body, self.BODY_TOP + 8, self.BODY_BOT - 4)
        # Gameplay nav
        self._draw_nav_bar("< Menu", "Hint >")
        self._touch_zones["game_menu"] = (0, self.NAV_TOP, W // 2 - 1, H - 1)
        self._touch_zones["game_hint"] = (W // 2, self.NAV_TOP, W - 1, H - 1)

    def _render_qr(self, lines):
        lcd = self._lcd
        lcd.fill(BLACK)
        lcd.fill_rect(0, 0, W, self.HDR_BOT, ACCENT_DIM)
        lcd.text_centered(16, "Scan QR Code", WHITE, ACCENT_DIM, scale=2)
        data     = _clean(lines[0]).strip() if lines else ""
        captions = lines[1:] if len(lines) > 1 else []
        y = self.BODY_TOP + 8
        for chunk in _word_wrap(data, 28)[:10]:
            lcd.text(8, y, chunk, DIM_WHITE, BLACK, scale=1)
            y += 11
        y += 4
        for cap in captions[:3]:
            if cap:
                lcd.text(8, y, _truncate(_clean(cap), 28), GRAY, BLACK, scale=1)
                y += 11

    def _render_fixed(self, lines, size_hint):
        scale = 3 if size_hint >= 26 else (2 if size_hint >= 16 else 1)
        lcd = self._lcd
        lcd.fill(BLACK)
        ch   = 8 * scale
        gap  = 4
        n    = len(lines)
        total_h = n * (ch + gap) - gap
        y = max(4, (H - total_h) // 2)
        for ln in lines:
            lcd.text_centered(y, _truncate(ln or "", 30 // scale), WHITE, BLACK, scale)
            y += ch + gap

    # ═══════════════════════════════════════════════════════════════════════
    # Sub-renderers
    # ═══════════════════════════════════════════════════════════════════════

    def _render_items_in_zone(self, items, top, bottom):
        """Render a list of items as tappable rows within [top, bottom]."""
        lcd = self._lcd
        items = [i for i in items if i]
        n = max(len(items), 1)
        zone_h = bottom - top
        row_h  = zone_h // n

        for i, item in enumerate(items[:5]):
            y0 = top + i * row_h
            text_y = y0 + (row_h - 16) // 2
            if i > 0:
                lcd.hline(0, y0, W, ITEM_SEP)
            lcd.fill_rect(0, y0 + 2, 4, row_h - 4, ACCENT)
            lcd.text_centered(text_y, _truncate(item, 14), WHITE, BLACK, scale=2)
            self._touch_zones["item_%d" % (i + 1)] = (0, y0 + 1, W - 1, y0 + row_h - 1)

    def _render_body_text(self, body_lines, top, bottom):
        """Render wrapped body text centered in the given vertical zone."""
        lcd = self._lcd
        if not body_lines:
            return

        # Collect all wrapped lines
        wrapped = []
        for ln in body_lines:
            if ln:
                wrapped.extend(_word_wrap(ln, 15))
        if not wrapped:
            return

        avail_h = bottom - top
        # Pick scale: prefer scale=2 (16px), fall back to scale=1 for many lines
        scale = 2
        ch    = 8 * scale
        gap   = 5
        line_h = ch + gap
        if len(wrapped) * line_h > avail_h:
            scale  = 1
            ch     = 8
            gap    = 3
            line_h = ch + gap

        total_h = len(wrapped) * line_h - gap
        y = top + max(0, (avail_h - total_h) // 2)

        for ln in wrapped:
            if y + ch > bottom:
                break
            lcd.text_centered(y, _truncate(ln, 30 // scale), WHITE, BLACK, scale)
            y += line_h

    def _render_action_zone(self, foot_parts, is_gameplay, footer_raw=""):
        """Draw the action zone (y=220..270) with contextual content."""
        lcd = self._lcd
        y_top = self.ACT_TOP

        if is_gameplay:
            # Active confirm zone: bright green background, prominent text
            lcd.fill_rect(0, y_top, W, self.ACT_BOT - y_top, CONFIRM_BG)
            lcd.hline(0, y_top, W, CONFIRM_FG)
            lcd.text_centered(y_top + 8,  "TAP TO CONFIRM", CONFIRM_FG, CONFIRM_BG, scale=1)
            lcd.text_centered(y_top + 24, "YOUR MOVE", CONFIRM_FG, CONFIRM_BG, scale=2)
            self._touch_zones["game_confirm"] = (
                40, y_top, W - 41, self.ACT_BOT - 1
            )
        elif foot_parts:
            # Show first footer part as the action label (e.g. "OK=Confirm")
            label = foot_parts[0].split("=", 1)[-1].strip() if "=" in foot_parts[0] else foot_parts[0]
            label = _truncate(label, 20)
            lcd.hline(0, y_top, W, GRAY)
            lcd.text_centered(y_top + 8, label, DIM_WHITE, BLACK, scale=2)
            self._touch_zones["btn_ok"] = (20, y_top, W - 21, self.ACT_BOT - 1)
        else:
            # Empty zone separator
            lcd.hline(0, y_top, W, DARK_GRAY)

    # ═══════════════════════════════════════════════════════════════════════
    # Nav bar
    # ═══════════════════════════════════════════════════════════════════════

    def _draw_nav_bar(self, left="", right="", y0=None):
        """Draw the bottom nav bar with left and right labels."""
        if y0 is None:
            y0 = self.NAV_TOP
        lcd = self._lcd
        bar_h = H - y0

        lcd.fill_rect(0, y0, W, bar_h, DARK_GRAY)
        lcd.hline(0, y0, W, GRAY)

        mid_x = W // 2
        if left and right:
            lcd.vline(mid_x, y0 + 1, bar_h - 2, GRAY)

        text_y = y0 + (bar_h - 8) // 2   # vertically center scale=1 text (8px)

        if left:
            t = _truncate("< " + left, 12)
            lcd.text(8, text_y, t, DIM_WHITE, DARK_GRAY, scale=1)

        if right:
            t = _truncate(right + " >", 12)
            lcd.text(W - len(t) * 8 - 8, text_y, t, DIM_WHITE, DARK_GRAY, scale=1)

    # ═══════════════════════════════════════════════════════════════════════
    # Gameplay helpers
    # ═══════════════════════════════════════════════════════════════════════

    def _is_gameplay(self, header, body_lines, footer_raw):
        """Detect whether the current panel is a game-status / move-entry screen."""
        lh = (header or "").lower()
        lf = (footer_raw or "").lower()
        lb = " ".join((ln or "").lower() for ln in body_lines if ln)
        return (
            lh.startswith("you are ")
            or lh == "play move"
            or "play move" in lb
            or "ok=confirm" in lf
            or "hold ok" in lf
            or "hint=see" in lf
        )

    # ═══════════════════════════════════════════════════════════════════════
    # Touch interface
    # ═══════════════════════════════════════════════════════════════════════

    @property
    def touch(self):
        return self._xpt

    def touch_action(self):
        """
        Poll touch and map to a zone name.
        Applies debounce: returns None if last touch was < DEBOUNCE_MS ago.
        Returns zone name string or None.
        """
        if not self._touch_zones:
            return None
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_touch_ms) < self._TOUCH_DEBOUNCE_MS:
            return None
        zone = self._xpt.get_zone(self._touch_zones)
        if zone:
            self._last_touch_ms = now
        return zone
