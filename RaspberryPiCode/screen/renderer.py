# -*- coding: utf-8 -*-
"""
PIL rendering engine for SmarterChess display.
Ported from display_server.py — all hardware references removed.

Usage:
    r = Renderer(width=240, height=320, font_dir="/path/to/fonts")
    pil_image = r.render("Line1|Line2|header")
"""
import os
import re

from PIL import Image, ImageDraw, ImageFont

try:
    from screen.qrgen import encode_text as _qr_encode_text
except Exception:
    try:
        from qrgen import encode_text as _qr_encode_text
    except Exception:
        _qr_encode_text = None

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

_DEFAULT_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
    os.path.join(_SCRIPT_DIR, "..", "ChessSans.ttf"),
    os.path.join(_SCRIPT_DIR, "..", "WorkSans-Medium.ttf"),
    "/home/king/SmarterChess-DIY2026/RaspberryPiCode/ChessSans.ttf",
    "/home/king/SmarterChess-DIY2026/RaspberryPiCode/WorkSans-Medium.ttf",
    "/home/king/LCD_Module_RPI_code/RaspberryPi/python/Font/Font00.ttf",
]

_ANNOTATION_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    os.path.join(_SCRIPT_DIR, "..", "WorkSans-Medium.ttf"),
    "/home/king/SmarterChess-DIY2026/RaspberryPiCode/WorkSans-Medium.ttf",
    os.path.join(_SCRIPT_DIR, "..", "ChessSans.ttf"),
    "/home/king/SmarterChess-DIY2026/RaspberryPiCode/ChessSans.ttf",
]


def _resolve_font(candidates, label):
    for p in candidates:
        norm = os.path.normpath(p)
        if os.path.exists(norm):
            return norm
    raise FileNotFoundError(f"No {label} font found. Tried: {candidates}")


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


class Renderer:
    """Stateful PIL renderer.  Call render(pipe_line) to get a PIL Image."""

    FOOTER_SIZE = 15

    def __init__(self, width: int = 240, height: int = 320):
        self.W = width
        self.H = height

        self._font_paths = {
            "default":    _resolve_font(_DEFAULT_FONT_CANDIDATES,    "default"),
            "annotation": _resolve_font(_ANNOTATION_FONT_CANDIDATES + _DEFAULT_FONT_CANDIDATES,
                                        "annotation"),
        }
        print(f"[Renderer] default font:    {self._font_paths['default']}", flush=True)
        print(f"[Renderer] annotation font: {self._font_paths['annotation']}", flush=True)

        self._frame   = Image.new("RGB", (width, height), "BLACK")
        self._draw    = ImageDraw.Draw(self._frame)
        self._measure_img  = Image.new("RGB", (width, height), "BLACK")
        self._measure_draw = ImageDraw.Draw(self._measure_img)

        self._fonts: dict = {}
        self._measure_cache: dict = {}
        self._last_header_body_size = 18

        # Online clock state
        self._clock_lines = None

        # Touch zone tracking — updated by draw methods each frame
        self._rendered_hdr_bot  = 56
        self._rendered_nav_top  = 270
        self._rendered_act_top  = 220
        self._rendered_item_rects: list = []

    # ══════════════════════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════════════════════

    def render(self, pipe_line: str) -> Image.Image:
        """Parse a pipe-format message and return a rendered PIL Image.

        Format: "L1|L2|...|size_token"
        Special: "__clock__|line1|line2|clock"  and  "__clock_clear__|clock"
        """
        parts = pipe_line.strip().split("|")
        if not parts:
            return self._frame.copy()

        raw_size = parts[-1].strip() if parts[-1] else "auto"
        lines    = [p for p in parts[:-1]]

        if raw_size.lower() == "clock":
            if lines and lines[0] == "__clock__":
                self._clock_lines = lines[1:3]
            elif lines and lines[0] == "__clock_clear__":
                self._clock_lines = None
        else:
            self._current_lines = lines
            self._current_size  = raw_size

        self._render_current(self._current_lines, self._current_size)
        return self._frame.copy()

    def render_splash(self) -> Image.Image:
        self._draw_splash()
        return self._frame.copy()

    # ══════════════════════════════════════════════════════════════════════════
    # Touch zone computation (call after render to get current zones)
    # ══════════════════════════════════════════════════════════════════════════

    def current_touch_zones(self) -> dict:
        """Return touch zone dict for the most recently rendered frame."""
        size_key = (getattr(self, "_current_size", "auto") or "auto").lower()
        lines    = getattr(self, "_current_lines", [])
        return self._compute_zones(size_key, lines)

    def _compute_zones(self, size_key: str, lines: list) -> dict:
        W, H = self.W, self.H
        # Use positions tracked during last render; fall back to estimates
        HDR_BOT    = self._rendered_hdr_bot
        NAV_TOP    = self._rendered_nav_top
        ACT_TOP    = self._rendered_act_top
        item_rects = self._rendered_item_rects

        zones = {}
        sk = size_key.lower()

        # Nav bar present for all structured layouts
        if sk.startswith(("header", "menu", "setup", "auto", "annotation")):
            zones["game_menu"] = (0,       NAV_TOP, W // 2 - 1, H - 1)
            zones["game_hint"] = (W // 2,  NAV_TOP, W - 1,      H - 1)

        # Action zone (confirm / OK) for header-style layouts
        if sk in ("header", "auto", "setup") or sk.startswith("header:"):
            zones["game_confirm"] = (0, ACT_TOP, W - 1, NAV_TOP - 1)

        # Item zones for menu / annotation layouts
        if sk.startswith(("menu", "annotation")):
            if item_rects:
                for i, rect in enumerate(item_rects[:4]):
                    zones[f"item_{i + 1}"] = rect
            else:
                # Fallback: divide content area evenly
                body = []
                if lines:
                    rest = lines[1:]
                    if rest and _is_footer_hint(rest[-1]):
                        body = rest[:-1]
                    else:
                        body = rest
                body = [l for l in body if l]
                n = min(len(body), 4)
                if n:
                    content_h = NAV_TOP - HDR_BOT
                    item_h    = content_h // n
                    for i in range(n):
                        y0 = HDR_BOT + i * item_h
                        y1 = y0 + item_h - 1
                        zones[f"item_{i + 1}"] = (0, y0, W - 1, y1)

        return zones

    # ══════════════════════════════════════════════════════════════════════════
    # Internal render dispatch
    # ══════════════════════════════════════════════════════════════════════════

    def _render_current(self, lines, raw_size):
        size_key = (raw_size or "auto").strip().lower()

        if size_key == "qr":
            qr_data  = (lines[0] if lines else "").strip()
            captions = [ln.strip() for ln in lines[1:]] if len(lines) > 1 else []
            self._draw_qr(qr_data, captions)
            return

        if size_key.startswith("menuheader"):
            page_info = raw_size.split(":", 1)[1] if ":" in raw_size else ""
            self._draw_menuheader(lines, page_info=page_info)
            return

        if size_key.startswith("menu"):
            page_info = size_key.split(":", 1)[1] if ":" in size_key else ""
            self._draw_menu(lines, page_info=page_info)
            return

        if size_key.startswith("annotation"):
            page_info = size_key.split(":", 1)[1] if ":" in size_key else ""
            self._draw_menu(lines, page_info=page_info,
                            font_key="annotation", align="left",
                            footer_font_key="default")
            return

        if size_key == "setup":
            self._draw_header_panel(lines)
            return

        if size_key.startswith("header"):
            if self._clock_lines:
                self._draw_online(list(self._clock_lines) + lines)
                return
            badge = raw_size.split(":", 1)[1] if ":" in raw_size else ""
            self._draw_header_panel(lines, badge=badge)
            return

        if size_key == "online":
            self._draw_online(lines)
            return

        if size_key == "auto" and self._clock_lines:
            self._draw_online(list(self._clock_lines) + lines)
            return

        if size_key == "auto":
            self._draw_header_panel(lines)
            return

        try:
            size = int(raw_size)
            self._draw_centered_with_size(lines, size=size, spacing=6)
        except Exception:
            self._draw_centered_auto(lines)

    # ══════════════════════════════════════════════════════════════════════════
    # Font helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _get_font(self, size: int, *, font_key: str = "default"):
        key = (font_key, size)
        if key not in self._fonts:
            self._fonts[key] = ImageFont.truetype(self._font_paths[font_key], size)
        return self._fonts[key]

    def _measure(self, size: int, text: str, font, *, font_key: str = "default"):
        key = (font_key, size, text)
        wh  = self._measure_cache.get(key)
        if wh is None:
            bb = self._measure_draw.textbbox((0, 0), text, font=font)
            wh = (bb[2] - bb[0], bb[3] - bb[1])
            self._measure_cache[key] = wh
        return wh

    def _fit_single_line_size(self, text, *, min_size, max_size, max_w,
                               font_key="default"):
        txt = (text or "").strip()
        if not txt:
            return min_size
        for size in range(max_size, min_size - 1, -1):
            font = self._get_font(size, font_key=font_key)
            if self._measure(size, txt, font, font_key=font_key)[0] <= max_w:
                return size
        return min_size

    def _find_best_font_size(self, lines, min_size=14, max_size=28,
                              vpad=4, spacing=6):
        W, H = self.W, self.H
        for size in range(max_size, min_size - 1, -1):
            font    = self._get_font(size)
            total_h = 0
            max_w   = 0
            for ln in lines:
                if not ln:
                    w, h = 0, size
                else:
                    w, h = self._measure(size, ln, font)
                total_h += h + spacing
                if w > max_w:
                    max_w = w
            total_h -= spacing
            if total_h <= (H - 2 * vpad) and max_w <= (W - 2 * vpad):
                return size, spacing
        return min_size, spacing

    def _pick_header_body_size(self, body_lines, *, avail_h, max_w, spacing,
                                min_size, max_size):
        if not body_lines:
            return min_size

        def _fits(sz):
            font    = self._get_font(sz)
            heights = [self._measure(sz, ln, font)[1] for ln in body_lines]
            widths  = [self._measure(sz, ln, font)[0] for ln in body_lines]
            total_h = sum(heights) + spacing * (len(heights) - 1)
            return total_h <= avail_h and all(w <= max_w for w in widths)

        start = min(max(self._last_header_body_size, min_size), max_size)
        body_size = min_size
        if _fits(start):
            body_size = start
            while body_size < max_size and _fits(body_size + 1):
                body_size += 1
        else:
            for sz in range(start - 1, min_size - 1, -1):
                if _fits(sz):
                    body_size = sz
                    break
        self._last_header_body_size = body_size
        return body_size

    def _word_wrap(self, text, size, max_w):
        font = self._get_font(size)
        text = (text or "").strip()
        if not text:
            return [""]
        if self._measure(size, text, font)[0] <= max_w:
            return [text]
        words = text.split()
        lines, current = [], ""
        for word in words:
            candidate = (current + " " + word).strip()
            if self._measure(size, candidate, font)[0] <= max_w:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines if lines else [text]

    # ══════════════════════════════════════════════════════════════════════════
    # Footer helpers
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _normalize_footer_text(line):
        txt = (line or "").strip()
        if not txt:
            return ""
        txt = re.sub(r"\bOK\s*=\s*",   "OK=",   txt)
        txt = re.sub(r"\bHint\s*=\s*", "Hint=", txt)
        txt = re.sub(r"\bHINT\s*=\s*", "HINT=", txt)
        txt = re.sub(r"\s{2,}", " ", txt)
        return txt

    def _split_footer_parts(self, raw):
        raw = (raw or "").strip()
        if not raw:
            return []
        parts = re.split(r"\s{2,}", raw)
        return [self._normalize_footer_text(p) for p in parts if p.strip()][:3]

    def _draw_footer_aligned(self, parts, font, size, footer_y, *,
                              pad=12, font_key="default"):
        if not parts:
            return
        W = self.W
        if len(parts) == 1:
            w = self._measure(size, parts[0], font, font_key=font_key)[0]
            self._draw.text(((W - w) // 2, footer_y), parts[0],
                            font=font, fill="WHITE")
        elif len(parts) == 2:
            rw = self._measure(size, parts[1], font, font_key=font_key)[0]
            self._draw.text((pad, footer_y), parts[0], font=font, fill="WHITE")
            self._draw.text((W - rw - pad, footer_y), parts[1],
                            font=font, fill="WHITE")
        else:
            cw = self._measure(size, parts[1], font, font_key=font_key)[0]
            rw = self._measure(size, parts[2], font, font_key=font_key)[0]
            self._draw.text((pad, footer_y), parts[0], font=font, fill="WHITE")
            self._draw.text(((W - cw) // 2, footer_y), parts[1],
                            font=font, fill="WHITE")
            self._draw.text((W - rw - pad, footer_y), parts[2],
                            font=font, fill="WHITE")

    # ══════════════════════════════════════════════════════════════════════════
    # Draw functions (ported from display_server.py)
    # ══════════════════════════════════════════════════════════════════════════

    def _draw_centered_with_size(self, lines, size, spacing=6, vpad=12):
        W, H = self.W, self.H
        self._draw.rectangle((0, 0, W, H), fill="BLACK")
        font    = self._get_font(size)
        heights = []
        total_h = 0
        for ln in lines:
            h = self._measure(size, ln, font)[1] if ln else size
            heights.append(h)
            total_h += h + spacing
        total_h -= spacing
        y = max(vpad, (H - total_h - 2 * vpad) // 2 + vpad)
        for ln, h in zip(lines, heights):
            if ln:
                w = self._measure(size, ln, font)[0]
                self._draw.text(((W - w) // 2, y), ln, font=font, fill="WHITE")
            y += h + spacing

    def _draw_centered_auto(self, lines, min_size=14, max_size=28,
                             vpad=12, spacing=6):
        size, spacing = self._find_best_font_size(lines, min_size, max_size,
                                                   vpad, spacing)
        self._draw_centered_with_size(lines, size=size, spacing=spacing, vpad=vpad)

    def _draw_menu(self, lines, page_info="", *, font_key="default",
                   align="center", footer_font_key=None):
        W, H = self.W, self.H
        footer_font_key = footer_font_key or font_key
        if not lines:
            return

        raw_footer = lines[-1] if lines else ""
        raw_items  = lines[:-1] if len(lines) > 1 else list(lines)

        self._draw.rectangle((0, 0, W, H), fill="BLACK")

        header_reserved = 0
        if page_info:
            pg_size = self.FOOTER_SIZE
            pg_font = self._get_font(pg_size, font_key=footer_font_key)
            pg_w, pg_h = self._measure(pg_size, page_info, pg_font,
                                       font_key=footer_font_key)
            self._draw.text((W - pg_w - 6, 4), page_info,
                            font=pg_font, fill="GRAY")
            header_reserved = pg_h + 4

        footer_parts = self._split_footer_parts(raw_footer or "")
        footer_font  = self._get_font(self.FOOTER_SIZE, font_key=footer_font_key)
        footer_h = (
            self._measure(self.FOOTER_SIZE, footer_parts[0], footer_font,
                          font_key=footer_font_key)[1]
            if footer_parts else 0
        )
        footer_reserved = (footer_h + 14) if footer_parts else 0
        avail_h = H - footer_reserved - header_reserved

        min_size, max_size = 14, 28

        display_lines = [(ln or "") for ln in raw_items if ln]
        n = len(display_lines)

        # Equal-height tiles filling the available area
        tile_gap   = 4          # px gap between tiles
        tile_pad_x = 8          # horizontal inset from screen edge
        tile_pad_y = 6          # vertical text padding inside tile
        tile_h     = max(30, (avail_h - tile_gap * (n + 1)) // n) if n else 30
        tile_inner = tile_h - 2 * tile_pad_y

        # Fit font into tile inner height
        item_size = min_size
        for sz in range(max_size, min_size - 1, -1):
            font   = self._get_font(sz, font_key=font_key)
            widths = [self._measure(sz, ln, font, font_key=font_key)[0]
                      for ln in display_lines]
            max_h  = max((self._measure(sz, ln, font, font_key=font_key)[1]
                          for ln in display_lines), default=sz)
            if max_h <= tile_inner and all(w <= W - 2 * tile_pad_x - 16 for w in widths):
                item_size = sz
                break

        item_font = self._get_font(item_size, font_key=font_key)

        self._rendered_item_rects = []
        for i, ln in enumerate(display_lines):
            tile_y0 = header_reserved + tile_gap + i * (tile_h + tile_gap)
            tile_y1 = tile_y0 + tile_h - 1
            # Outline box
            try:
                self._draw.rounded_rectangle(
                    [tile_pad_x, tile_y0, W - 1 - tile_pad_x, tile_y1],
                    radius=6, outline="WHITE", width=1)
            except AttributeError:
                self._draw.rectangle(
                    [tile_pad_x, tile_y0, W - 1 - tile_pad_x, tile_y1],
                    outline="WHITE", width=1)
            # Text centred in tile
            tw, th = self._measure(item_size, ln, item_font, font_key=font_key)
            tx = tile_pad_x + 8 if align == "left" else (W - tw) // 2
            ty = tile_y0 + (tile_h - th) // 2
            self._draw.text((tx, ty), ln, font=item_font, fill="WHITE")
            self._rendered_item_rects.append((0, tile_y0, W - 1, tile_y1))

        self._rendered_hdr_bot = header_reserved

        if footer_parts:
            footer_y = H - footer_h - 4
            self._rendered_nav_top = max(footer_y - 8, 260)
            self._rendered_act_top = self._rendered_nav_top
            self._draw.line((10, footer_y - 5, W - 10, footer_y - 5),
                            fill="WHITE", width=1)
            self._draw_footer_aligned(footer_parts, footer_font,
                                      self.FOOTER_SIZE, footer_y,
                                      font_key=footer_font_key)
        else:
            self._rendered_nav_top = H - 50
            self._rendered_act_top = H - 50

    def _draw_menuheader(self, lines, page_info: str = ""):
        """Header bar + equal-height outlined item tiles + footer."""
        W, H = self.W, self.H
        if not lines:
            return

        header      = (lines[0] or "").strip()
        raw_footer  = lines[-1] if len(lines) > 1 else ""
        raw_items   = lines[1:-1] if len(lines) > 2 else (lines[1:] if len(lines) > 1 else [])
        footer_parts = self._split_footer_parts(raw_footer or "")

        self._draw.rectangle((0, 0, W, H), fill="BLACK")

        # ── Header bar ────────────────────────────────────────────────────────
        header_size = self._fit_single_line_size(header, min_size=13, max_size=17,
                                                  max_w=W - 20)
        header_font = self._get_font(header_size)
        _, header_h = self._measure(header_size, header, header_font)
        header_y    = 6
        if header:
            hw = self._measure(header_size, header, header_font)[0]
            self._draw.text(((W - hw) // 2, header_y), header,
                            font=header_font, fill="WHITE")
        divider_y = header_y + header_h + 6
        self._draw.line((10, divider_y, W - 10, divider_y), fill="WHITE", width=1)
        content_top = divider_y + 6

        # Page indicator (top-right)
        if page_info:
            pg_font = self._get_font(self.FOOTER_SIZE)
            pg_w, _ = self._measure(self.FOOTER_SIZE, page_info, pg_font)
            self._draw.text((W - pg_w - 6, header_y + 1), page_info,
                            font=pg_font, fill="GRAY")

        # ── Footer ────────────────────────────────────────────────────────────
        footer_font = self._get_font(self.FOOTER_SIZE)
        footer_h    = (self._measure(self.FOOTER_SIZE, footer_parts[0], footer_font)[1]
                       if footer_parts else 0)
        footer_reserved = (footer_h + 14) if footer_parts else 0
        content_bot = H - footer_reserved

        if footer_parts:
            footer_y = H - footer_h - 4
            self._draw.line((10, footer_y - 5, W - 10, footer_y - 5),
                            fill="WHITE", width=1)
            self._draw_footer_aligned(footer_parts, footer_font, self.FOOTER_SIZE,
                                      footer_y)

        # ── Item tiles ────────────────────────────────────────────────────────
        display_lines = [(ln or "") for ln in raw_items if ln]
        n = len(display_lines)

        tile_gap   = 5
        tile_pad_x = 8
        avail_h    = content_bot - content_top
        tile_h     = max(28, (avail_h - tile_gap * (n + 1)) // n) if n else 28
        tile_inner = tile_h - 8   # vertical text room inside tile

        min_size, max_size = 14, 22
        item_size = min_size
        for sz in range(max_size, min_size - 1, -1):
            font   = self._get_font(sz)
            max_th = max((self._measure(sz, ln, font)[1] for ln in display_lines),
                         default=sz)
            max_tw = max((self._measure(sz, ln, font)[0] for ln in display_lines),
                         default=0)
            if max_th <= tile_inner and max_tw <= W - 2 * tile_pad_x - 16:
                item_size = sz
                break

        item_font = self._get_font(item_size)
        self._rendered_item_rects = []
        for i, ln in enumerate(display_lines):
            tile_y0 = content_top + tile_gap + i * (tile_h + tile_gap)
            tile_y1 = tile_y0 + tile_h - 1
            try:
                self._draw.rounded_rectangle(
                    [tile_pad_x, tile_y0, W - 1 - tile_pad_x, tile_y1],
                    radius=5, outline="WHITE", width=1)
            except AttributeError:
                self._draw.rectangle(
                    [tile_pad_x, tile_y0, W - 1 - tile_pad_x, tile_y1],
                    outline="WHITE", width=1)
            tw, th = self._measure(item_size, ln, item_font)
            self._draw.text(((W - tw) // 2, tile_y0 + (tile_h - th) // 2),
                            ln, font=item_font, fill="WHITE")
            self._rendered_item_rects.append((0, tile_y0, W - 1, tile_y1))

        # Touch zone tracking
        _ftr_y = H - footer_h - 4 if footer_parts else H
        self._rendered_hdr_bot  = content_top
        self._rendered_nav_top  = max(_ftr_y - 8, divider_y + 40)
        self._rendered_act_top  = self._rendered_nav_top

    def _draw_header_panel(self, lines, badge: str = ""):
        W, H = self.W, self.H
        if not lines:
            return

        header         = (lines[0] or "").strip()
        raw_footer_line = (
            lines[-1] if (len(lines) > 1 and _is_footer_hint(lines[-1])) else ""
        )
        footer_parts = self._split_footer_parts(raw_footer_line)
        footer       = " ".join(footer_parts)
        raw_body     = lines[1:-1] if footer_parts else lines[1:]
        body_lines   = [(ln or "") for ln in raw_body if ln]

        self._draw.rectangle((0, 0, W, H), fill="BLACK")

        badge = (badge or "").strip()
        badge_size = self._fit_single_line_size(badge, min_size=11, max_size=14,
                                                max_w=52)
        badge_font = self._get_font(badge_size)
        badge_w, badge_h = (self._measure(badge_size, badge, badge_font)
                            if badge else (0, 0))

        header_max_w = W - 20 - (badge_w + 10 if badge else 0)
        header_size  = self._fit_single_line_size(header, min_size=13, max_size=17,
                                                   max_w=header_max_w)
        header_font  = self._get_font(header_size)
        header_w, header_h = self._measure(header_size, header, header_font)
        header_y = 6
        if header:
            self._draw.text(((W - header_w) // 2, header_y), header,
                            font=header_font, fill="WHITE")
        if badge:
            self._draw.text((W - badge_w - 8, header_y + 1), badge,
                            font=badge_font, fill="WHITE")
        divider_y = header_y + header_h + 6
        self._draw.line((10, divider_y, W - 10, divider_y), fill="WHITE", width=1)

        footer_size = self._fit_single_line_size(footer or "OK = confirm",
                                                  min_size=11, max_size=14,
                                                  max_w=W - 20)
        footer_font = self._get_font(footer_size)
        footer_h    = self._measure(footer_size, footer or "Ag", footer_font)[1]
        footer_reserved = footer_h + 14

        avail_top = divider_y + 8
        avail_h   = H - avail_top - footer_reserved - 8
        spacing   = 5
        min_size, max_size = 12, 24

        # Track for touch zones (footer_y = H - footer_h - 4, same formula used below)
        _ftr_y = H - footer_h - 4
        self._rendered_hdr_bot      = avail_top
        self._rendered_nav_top      = max(_ftr_y - 8, divider_y + 40)
        self._rendered_act_top      = max(avail_top + 20, self._rendered_nav_top - 60)
        self._rendered_item_rects   = []

        body_size = self._pick_header_body_size(
            body_lines, avail_h=avail_h, max_w=W - 16,
            spacing=spacing, min_size=min_size, max_size=max_size,
        )
        body_font = self._get_font(body_size)
        sized     = [(ln, self._measure(body_size, ln, body_font))
                     for ln in body_lines]
        total_h   = (sum(h for _, (_, h) in sized) + spacing * (len(sized) - 1)
                     if sized else 0)
        y = avail_top + max(0, (avail_h - total_h) // 2)
        for ln, (w, h) in sized:
            self._draw.text(((W - w) // 2, y), ln, font=body_font, fill="WHITE")
            y += h + spacing

        footer_y = H - footer_h - 4
        self._draw.line((10, footer_y - 5, W - 10, footer_y - 5),
                        fill="WHITE", width=1)
        if footer_parts:
            self._draw_footer_aligned(footer_parts, footer_font, footer_size,
                                      footer_y)

    def _draw_online(self, lines):
        W, H = self.W, self.H
        self._draw.rectangle((0, 0, W, H), fill="BLACK")

        clock_lines = list(lines[:2])
        body_lines  = list(lines[2:]) if len(lines) > 2 else []

        left  = clock_lines[0] if len(clock_lines) > 0 else ""
        right = clock_lines[1] if len(clock_lines) > 1 else ""

        def _split_clock(ln):
            ln = (ln or "").strip()
            if not ln:
                return "", ""
            parts = ln.split(" ", 1)
            return (parts[0], parts[1]) if len(parts) == 2 else ("", ln)

        left_prefix,  left_time  = _split_clock(left)
        right_prefix, right_time = _split_clock(right)

        label_size = 10
        label_font = self._get_font(label_size)
        lp_w, lp_h = self._measure(label_size, left_prefix,  label_font) if left_prefix  else (0, 0)
        rp_w, rp_h = self._measure(label_size, right_prefix, label_font) if right_prefix else (0, 0)

        time_size = 18
        time_font = self._get_font(time_size)
        side_gap  = 2
        half_w    = W // 2
        for size in range(24, 17, -1):
            font  = self._get_font(size)
            lt_w  = self._measure(size, left_time,  font)[0] if left_time  else 0
            rt_w  = self._measure(size, right_time, font)[0] if right_time else 0
            if (lt_w <= max(24, half_w - lp_w - 6 - side_gap) and
                    rt_w <= max(24, half_w - rp_w - 6 - side_gap)):
                time_size = size
                time_font = font
                break

        y     = 4
        row_h = time_size
        if left:
            if left_prefix:
                self._draw.text((2, y + 2), left_prefix,
                                font=label_font, fill="WHITE")
            time_x = 2 + lp_w + (side_gap if left_prefix else 0)
            _, lh  = self._measure(time_size, left_time, time_font)
            row_h  = max(row_h, lh, lp_h + 2)
            self._draw.text((time_x, y), left_time, font=time_font, fill="WHITE")
        if right:
            rw, rh = self._measure(time_size, right_time, time_font)
            pfx_x  = W - rw - rp_w - (side_gap if right_prefix else 0) - 2
            row_h  = max(row_h, rh, rp_h + 2)
            if right_prefix:
                self._draw.text((pfx_x, y + 2), right_prefix,
                                font=label_font, fill="WHITE")
            self._draw.text((W - rw - 2, y), right_time,
                            font=time_font, fill="WHITE")

        divider_y = y + row_h + 4
        self._draw.line((10, divider_y, W - 10, divider_y), fill="WHITE", width=1)

        avail_top = divider_y + 8
        avail_h   = max(24, H - avail_top - 8)
        spacing   = 5
        vpad      = 4
        min_body, max_body = 12, 22

        display_body = [ln for ln in (body_lines or [""]) if ln]
        body_size    = min_body
        for sz in range(max_body, min_body - 1, -1):
            font    = self._get_font(sz)
            heights = [self._measure(sz, ln, font)[1] for ln in display_body] if display_body else []
            total_h = sum(heights) + spacing * (len(heights) - 1) if heights else 0
            widths  = [self._measure(sz, ln, font)[0] for ln in display_body] if display_body else []
            if total_h <= avail_h and all(w <= W - 12 for w in widths):
                body_size = sz
                break

        body_font = self._get_font(body_size)
        sized     = [(ln, self._measure(body_size, ln, body_font))
                     for ln in display_body]
        total_h   = (sum(h for _, (_, h) in sized) + spacing * (len(sized) - 1)
                     if sized else 0)
        y = avail_top + max(vpad, (avail_h - total_h) // 2)
        for ln, (w, h) in sized:
            self._draw.text(((W - w) // 2, y), ln, font=body_font, fill="WHITE")
            y += h + spacing

    def _draw_qr(self, data: str, caption_lines):
        W, H = self.W, self.H
        if not data:
            self._draw_centered_auto(["QR", "(empty)"])
            return
        if _qr_encode_text is None:
            self._draw_centered_auto(["QR unsupported", data[:18]])
            return
        try:
            qr  = _qr_encode_text(data, ecl="M")
            qsz = qr.size

            caption_h = 0
            if caption_lines:
                font_cap = self._get_font(self.FOOTER_SIZE)
                for ln in caption_lines[:3]:
                    if not ln:
                        continue
                    bb = self._measure_draw.textbbox((0, 0), ln, font=font_cap)
                    caption_h += (bb[3] - bb[1]) + 4
                caption_h = min(caption_h + 6, 52)

            pad    = 6
            avail_w = W - 2 * pad
            avail_h = H - 2 * pad - caption_h
            scale   = max(1, min(avail_w // qsz, avail_h // qsz))

            self._draw.rectangle((0, 0, W, H), fill="BLACK")

            qr_px = qsz * scale
            ox    = (W - qr_px) // 2
            oy    = max(pad, (avail_h - qr_px) // 2 + pad)

            self._draw.rectangle([ox - 2, oy - 2, ox + qr_px + 1, oy + qr_px + 1],
                                  fill="WHITE")
            for yy in range(qsz):
                y0 = oy + yy * scale
                for xx in range(qsz):
                    if qr.get_module(xx, yy):
                        x0 = ox + xx * scale
                        self._draw.rectangle(
                            [x0, y0, x0 + scale - 1, y0 + scale - 1],
                            fill="BLACK",
                        )

            if caption_lines:
                font_cap = self._get_font(self.FOOTER_SIZE)
                ycur = min(H - caption_h + 4, oy + qr_px + 6)
                for ln in caption_lines[:3]:
                    if not ln:
                        continue
                    bb = self._measure_draw.textbbox((0, 0), ln, font=font_cap)
                    tw = bb[2] - bb[0]
                    self._draw.text(((W - tw) // 2, ycur), ln,
                                    font=font_cap, fill="WHITE")
                    ycur += (bb[3] - bb[1]) + 4

        except Exception as exc:
            import traceback
            print(f"[QR ERROR] {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
            self._draw_centered_auto(["QR error", data[:18]])

    def _draw_splash(self):
        W, H = self.W, self.H
        self._draw.rectangle((0, 0, W, H), fill="BLACK")
        size = 28
        font = self._get_font(size)
        txt  = "SMARTCHESS"
        bb   = self._measure_draw.textbbox((0, 0), txt, font=font)
        w    = bb[2] - bb[0]
        h    = bb[3] - bb[1]
        self._draw.text(((W - w) // 2, (H - h) // 2 - 10), txt,
                        font=font, fill="WHITE")
