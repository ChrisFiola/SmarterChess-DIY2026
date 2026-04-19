# -*- coding: utf-8 -*-
"""
Display abstraction for SmarterChess.

Drives ILI9341 2.8" TFT (240x320) directly from the Pi via SPI0.
PIL rendering runs in a background thread so game logic is never blocked.
XPT2046 touch events are polled in the same thread and queued as protocol
strings (matching what the Pico previously sent over UART: "ok", "hint",
"n", "1"–"4") so BoardLink can read them transparently.

No display_server subprocess required.
"""
import queue
import threading
import time

# ── Hardware drivers (only imported when running on Pi) ───────────────────────
try:
    from screen.ili9341_pi import ILI9341
    from screen.xpt2046 import XPT2046
    from screen.renderer import Renderer

    _HW_AVAILABLE = True
except Exception as _hw_err:
    print(
        f"[Display] hardware drivers unavailable ({_hw_err}), running headless",
        flush=True,
    )
    _HW_AVAILABLE = False

# Touch zone → protocol string (matches Pico UART payloads expected by game_flow)
_ZONE_TO_PROTO = {
    "item_1": "1",
    "item_2": "2",
    "item_3": "3",
    "item_4": "4",
    "btn_ok": "ok",
    "btn_delete": "delete",
    "game_delete": "delete",
    "game_confirm": "ok",
    "btn_hint": "hint",
    "game_hint": "hint",
    # Footer left action maps to OK semantics in current UI flows (Confirm/Back/Menu labels).
    "game_menu": "ok",
    "page_next": "hint",
    "page_prev": "delete",
    "btn_back": "n",
    "btn_one": "1",
}

_FPS_CAP = 25.0
_MIN_DT = 1.0 / _FPS_CAP
_TOUCH_POLL_S = 0.008  # seconds between touch polls inside render thread


class Display:
    """
    Public API is unchanged from the Version-2 Display class.
    Internally all rendering now happens on the Pi via PIL + ILI9341.
    """

    def __init__(self):
        self._last_message = ""
        self._last_size = "auto"
        self._last_payload = None
        self._lock_until = 0.0
        self._locked_category = None
        self._online_clock = None
        self._header_badge = ""

        # Render queue: (payload_str,)
        self._render_queue: queue.Queue = queue.Queue(maxsize=4)
        # Touch event queue: protocol strings read by BoardLink
        self.touch_queue: queue.Queue = queue.Queue(maxsize=32)

        if _HW_AVAILABLE:
            self._disp = ILI9341()
            self._renderer = Renderer(self._disp.width, self._disp.height)
            self._touch = XPT2046()
            self._disp.Init()
            self._disp.bl_DutyCycle(80)
            # Splash on startup
            splash = self._renderer.render_splash()
            self._disp.ShowImage(splash)
        else:
            self._disp = None
            self._renderer = None
            self._touch = None

        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()

    # ── Render thread ─────────────────────────────────────────────────────────

    def _render_loop(self) -> None:
        last_draw_t = 0.0
        last_drawn = None
        pending = None
        last_touch_t = 0.0
        last_touch_down = False
        tap_start_zone = None
        tap_last_emit_ms = 0

        while True:
            now = time.monotonic()

            # Drain render queue — keep only the newest message
            try:
                while True:
                    pending = self._render_queue.get_nowait()
            except queue.Empty:
                pass

            # Poll touch
            if self._touch and now - last_touch_t >= _TOUCH_POLL_S:
                last_touch_t = now
                try:
                    pt = self._touch.read()
                    touch_down = pt is not None
                    zones = self._renderer.current_touch_zones() if self._renderer else {}

                    def _zone_at_point(point, zone_map):
                        if point is None:
                            return None
                        px, py = point
                        for name, (x0, y0, x1, y1) in zone_map.items():
                            if x0 <= px <= x1 and y0 <= py <= y1:
                                return name
                        return None

                    # Annotation scroll follows finger; redraw immediately on drag.
                    if self._renderer and self._disp:
                        if self._renderer.handle_annotation_drag(pt):
                            active_payload = pending if pending is not None else last_drawn
                            if active_payload is not None:
                                img = self._renderer.render(active_payload)
                                self._disp.ShowImage(img)
                                last_drawn = active_payload
                                last_draw_t = now
                        if self._renderer.handle_menu_drag(pt):
                            active_payload = pending if pending is not None else last_drawn
                            if active_payload is not None:
                                img = self._renderer.render(active_payload)
                                self._disp.ShowImage(img)
                                last_drawn = active_payload
                                last_draw_t = now

                    dragging = self._renderer.annotation_drag_active() if self._renderer else False
                    dragging = dragging or (
                        self._renderer.menu_drag_active() if self._renderer else False
                    )

                    now_ms = int(now * 1000)
                    if touch_down and not last_touch_down:
                        tap_start_zone = _zone_at_point(pt, zones)

                    # Treat a touch as a tap when it is released and no drag happened.
                    if (not touch_down) and last_touch_down and not dragging:
                        if tap_start_zone and (now_ms - tap_last_emit_ms >= self._touch.DEBOUNCE_MS):
                            proto = _ZONE_TO_PROTO.get(tap_start_zone)
                            if proto:
                                try:
                                    self.touch_queue.put_nowait(proto)
                                    tap_last_emit_ms = now_ms
                                except queue.Full:
                                    pass
                        tap_start_zone = None

                    if dragging:
                        tap_start_zone = None

                    last_touch_down = touch_down
                except Exception:
                    pass

            # Nothing to draw yet
            if pending is None:
                if self._renderer and (
                    self._renderer.annotation_drag_active()
                    or self._renderer.menu_drag_active()
                ):
                    time.sleep(0.001)
                else:
                    time.sleep(0.01)
                continue

            # FPS cap
            if now - last_draw_t < _MIN_DT:
                if self._renderer and (
                    self._renderer.annotation_drag_active()
                    or self._renderer.menu_drag_active()
                ):
                    time.sleep(0.001)
                else:
                    time.sleep(0.005)
                continue

            # Dedup
            if pending == last_drawn:
                last_draw_t = now
                pending = None
                continue

            # Render
            if self._renderer and self._disp:
                try:
                    img = self._renderer.render(pending)
                    self._disp.ShowImage(img)
                except Exception as exc:
                    print(f"[Display] render error: {exc}", flush=True)

            last_drawn = pending
            last_draw_t = now
            pending = None

    def scroll_menu(self, lines: int) -> None:
        """Nudge current menu scroll and redraw immediately if a menu is active."""
        if not self._renderer or not self._disp:
            return
        if not getattr(self, "_last_size", "").startswith("menu"):
            return
        if self._renderer.nudge_menu_scroll(lines) and self._last_payload:
            img = self._renderer.render(self._last_payload)
            self._disp.ShowImage(img)

    def current_menu_visible_items(self):
        if not self._renderer:
            return []
        return self._renderer.current_menu_visible_items()

    # ── Internal payload helpers ──────────────────────────────────────────────

    def _format_clock_ms(self, ms: int) -> str:
        ms = max(0, int(ms or 0))
        total_s = ms // 1000
        days, rem = divmod(total_s, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        if days:
            return f"{days}d {hours:02}h"
        if hours:
            return f"{hours}:{minutes:02}:{seconds:02}"
        return f"{minutes}:{seconds:02}"

    def _clock_overlay_lines(self):
        if not self._online_clock:
            return []
        c = self._online_clock
        wl = (
            "YOU"
            if c["you_are_white"]
            else "OPP" if c["you_are_white"] is not None else "W"
        )
        bl = (
            "OPP"
            if c["you_are_white"]
            else "YOU" if c["you_are_white"] is not None else "B"
        )
        wa = "*" if c["active_color"] == "white" else " "
        ba = "*" if c["active_color"] == "black" else " "
        return [
            f"{wa}{wl} {self._format_clock_ms(c['white_ms'])}",
            f"{ba}{bl} {self._format_clock_ms(c['black_ms'])}",
        ]

    def _compose_payload(self, message: str, size: str) -> str:
        parts = message.split("\n")
        return "|".join(parts) + f"|{size}\n"

    def _compose_clock_payload(self) -> str:
        return "|".join(["__clock__"] + self._clock_overlay_lines()) + "|clock\n"

    def _compose_clock_clear_payload(self) -> str:
        return "__clock_clear__|clock\n"

    @staticmethod
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

    def _resolve_size(self, message: str, size: str) -> str:
        if (size or "auto") != "auto":
            return size
        parts = message.split("\n")
        if len(parts) >= 2 and self._is_footer_hint(parts[-1]):
            return "menu"
        return size

    def _classify(self, message: str) -> str:
        m = (message or "").lower()
        if any(
            k in m
            for k in [
                "illegal",
                "invalid",
                "game over",
                "promotion",
                "draw",
                "shutting down",
            ]
        ):
            return "critical"
        if any(
            k in m
            for k in ["enter move", "enter to", "confirm", "ok to send", "press ok"]
        ):
            return "prompt"
        if any(k in m for k in ["engine thinking", "engine starting", "loading"]):
            return "status"
        return "normal"

    def _write_payload(self, payload: str) -> None:
        try:
            self._render_queue.put_nowait(payload.rstrip("\n"))
        except queue.Full:
            # Drop oldest, enqueue newest
            try:
                self._render_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._render_queue.put_nowait(payload.rstrip("\n"))
            except queue.Full:
                pass

    # ── Compatibility stub (no longer routes to Pico) ─────────────────────────

    def set_boardlink(self, boardlink) -> None:
        """No-op: display now renders locally. Kept for call-site compatibility."""

    def restart_server(self) -> None:
        """No-op: no subprocess. Display starts in __init__."""

    def wait_ready(self, timeout_s: float = 10.0) -> None:
        """No-op: hardware is ready after __init__."""

    # ── Public send API ───────────────────────────────────────────────────────

    def send(self, message: str, size: str = "auto", force: bool = False) -> None:
        self._last_message = message
        size = self._resolve_size(message, size or "auto")
        self._last_size = size
        payload = self._compose_payload(message, size)

        now = time.monotonic()
        cat = self._classify(message)

        if (
            not force
            and now < self._lock_until
            and self._locked_category == "prompt"
            and cat not in ("prompt", "critical")
        ):
            return

        if cat == "prompt":
            m = (message or "").lower()
            hold = 0.45 if "confirm" in m or "ok to send" in m else 0.30
            self._lock_until = now + hold
            self._locked_category = "prompt"
        elif cat == "critical" or force:
            self._lock_until = 0.0
            self._locked_category = None

        if payload == self._last_payload:
            return

        self._last_payload = payload
        self._write_payload(payload)

    def show_qr(self, data: str, *caption_lines: str) -> None:
        lines = [data] + [ln for ln in caption_lines if ln]
        self.send("\n".join(lines), size="qr")

    def set_online_clock(
        self, *, white_ms: int, black_ms: int, you_are_white=None, active_color=None
    ) -> None:
        active = None
        if active_color in (True, False):
            active = "white" if active_color else "black"
        elif isinstance(active_color, str):
            low = active_color.strip().lower()
            if low in ("white", "black"):
                active = low
        state = {
            "white_ms": int(max(0, white_ms or 0)),
            "black_ms": int(max(0, black_ms or 0)),
            "you_are_white": you_are_white,
            "active_color": active,
        }
        if state == self._online_clock:
            return
        self._online_clock = state
        self._write_payload(self._compose_clock_payload())

    def clear_online_clock(self) -> None:
        self._online_clock = None
        self._write_payload(self._compose_clock_clear_payload())

    # ── Convenience UI helpers ────────────────────────────────────────────────

    def banner(self, text: str, delay_s: float = 0.0) -> None:
        self.send(text)
        if delay_s > 0:
            time.sleep(delay_s)

    def show_panel(
        self,
        *body_lines: str,
        footer: str = "",
        force: bool = False,
        size: str = "menu",
    ) -> None:
        lines = [ln for ln in body_lines if ln is not None]
        if footer:
            lines.append(footer)
        self.send("\n".join(lines), size=size if footer else "auto", force=force)

    def show_setup_panel(
        self, header: str, *body_lines: str, footer: str = "", force: bool = False
    ) -> None:
        lines = [header] + [ln for ln in body_lines if ln is not None]
        if footer:
            lines.append(footer)
        self.send("\n".join(lines), size="setup", force=force)

    def show_header_panel(
        self, header: str, *body_lines: str, footer: str = "", force: bool = False
    ) -> None:
        lines = [header] + [ln for ln in body_lines if ln is not None]
        if footer:
            lines.append(footer)
        self.send("\n".join(lines), size=self._header_size_token(), force=force)

    def set_header_badge(self, text: str | None) -> None:
        self._header_badge = (text or "").strip()

    def _header_size_token(self) -> str:
        badge = (self._header_badge or "").strip()
        return f"header:{badge}" if badge else "header"

    def show_arrow(self, uci: str, suffix: str = "", force: bool = False) -> None:
        arrow = f"{uci[:2]} → {uci[2:4]}"
        if suffix:
            self.send(f"{arrow}\n{suffix}", force=force)
        else:
            self.send(arrow, force=force)

    def prompt_move(self, side: str, force: bool = False) -> None:
        self.show_header_panel(
            f"You are {side.upper()}",
            "Play move",
            footer="Del=Delete   Hint=Hint   Menu=Exit",
            force=force,
        )

    def show_hint_result(self, uci: str) -> None:
        try:
            frm, to = uci[:2], uci[2:4]
            if len(uci) >= 4:
                self.show_header_panel(
                    "Hint received", f"{frm} → {to}", footer="OK=Clear"
                )
            else:
                self.show_header_panel("Hint received", uci, footer="OK=Clear")
        except Exception:
            self.show_header_panel("Hint received", uci, footer="OK=Clear")

    def show_invalid(self, text: str) -> None:
        self.send(f"Invalid\n{text}\nTry again")

    def promo_name(self, promo_letter: str) -> str:
        return {"q": "Queen", "r": "Rook", "b": "Bishop", "n": "Knight"}.get(
            (promo_letter or "").lower(), (promo_letter or "").upper()
        )

    def format_promo_line(self, promo_letter: str) -> str:
        return f"Promoted to {self.promo_name(promo_letter)}"

    def show_draw(self, reason: str, move_no: int) -> None:
        if reason:
            self.send(f"DRAW\n{reason}\nMove {move_no}")
        else:
            self.send(f"DRAW\nMove {move_no}")

    def close(self) -> None:
        if self._touch:
            try:
                self._touch.close()
            except Exception:
                pass
        if self._disp:
            try:
                self._disp.close()
            except Exception:
                pass
