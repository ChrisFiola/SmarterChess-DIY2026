# -*- coding: utf-8 -*-
"""
Display abstraction for SmarterChess.
Communicates with display_server.py via a named pipe (FIFO at /tmp/lcdpipe).
Use send(message) for all LCD output; helper methods cover common UI patterns.
"""
import os
import subprocess
import time

from screen.lcd_pipe import PIPE_PATH, READY_FLAG_PATH

DISPLAY_SERVER_SCRIPT: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "display_server.py")


class Display:
    """
    Minimal abstraction around display_server IPC.
    """

    def __init__(self, pipe_path: str = PIPE_PATH, ready_flag: str = READY_FLAG_PATH):
        self.pipe_path = pipe_path
        self.ready_flag = ready_flag
        self._last_payload = None
        self._last_message = ""
        self._last_size = "auto"
        self._last_send_t = 0.0
        # Simple "UI lock" to prevent important prompts (typing / confirmations)
        # from being immediately overwritten by background/status messages.
        self._lock_until = 0.0
        self._locked_category = None
        self._pipe = None
        self._online_clock = None
        self._header_badge = ""

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

        white_ms = self._online_clock["white_ms"]
        black_ms = self._online_clock["black_ms"]
        you_are_white = self._online_clock["you_are_white"]
        active_color = self._online_clock["active_color"]

        if you_are_white is None:
            white_label = "W"
            black_label = "B"
        else:
            white_label = "YOU" if you_are_white else "OPP"
            black_label = "OPP" if you_are_white else "YOU"

        white_active = "*" if active_color == "white" else " "
        black_active = "*" if active_color == "black" else " "

        return [
            f"{white_active}{white_label} {self._format_clock_ms(white_ms)}",
            f"{black_active}{black_label} {self._format_clock_ms(black_ms)}",
        ]

    def _compose_payload(self, message: str, size: str) -> str:
        parts = message.split("\n")
        return "|".join(parts) + f"|{size}\n"

    def _header_size_token(self) -> str:
        badge = (self._header_badge or "").strip()
        return f"header:{badge}" if badge else "header"

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

    def _compose_clock_payload(self) -> str:
        return "|".join(["__clock__"] + self._clock_overlay_lines()) + "|clock\n"

    def _compose_clock_clear_payload(self) -> str:
        return "__clock_clear__|clock\n"

    def _classify(self, message: str) -> str:
        m = (message or "").lower()
        # Critical should always break through
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
        # High-salience prompts while user is actively entering a move
        if any(
            k in m
            for k in ["enter move", "enter to", "confirm", "ok to send", "press ok"]
        ):
            return "prompt"
        # Low-value transient status
        if any(k in m for k in ["engine thinking", "engine starting", "loading"]):
            return "status"
        return "normal"

    def _ensure_pipe(self) -> None:
        if self._pipe is None:
            if not os.path.exists(self.pipe_path):
                try:
                    os.mkfifo(self.pipe_path)
                except FileExistsError:
                    pass
            self._pipe = open(self.pipe_path, "w", buffering=1)

    def _write_payload(self, payload: str) -> None:
        try:
            self._ensure_pipe()
            self._pipe.write(payload)
            self._last_payload = payload
        except (BrokenPipeError, OSError, ValueError):
            try:
                if self._pipe:
                    self._pipe.close()
            except Exception:
                pass
            self._pipe = None
            try:
                self._ensure_pipe()
                self._pipe.write(payload)
                self._last_payload = payload
            except Exception:
                self._pipe = None
                return

    def restart_server(self) -> None:
        # Close our writer end first
        try:
            if self._pipe:
                self._pipe.close()
        except Exception:
            pass
        self._pipe = None
        self._last_payload = None

        # Remove stale ready flag so wait_ready() is meaningful
        try:
            if os.path.exists(self.ready_flag):
                os.remove(self.ready_flag)
        except Exception:
            pass

        # Kill any existing server (block until done)
        subprocess.run(
            "pkill -f display_server.py",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Ensure FIFO exists
        if not os.path.exists(self.pipe_path):
            try:
                os.mkfifo(self.pipe_path)
            except FileExistsError:
                pass

        # Start server with same interpreter as piMain
        # stdout/stderr intentionally inherited so errors appear in journalctl
        subprocess.Popen(
            ["python3", DISPLAY_SERVER_SCRIPT],
        )

    def wait_ready(self, timeout_s: float = 10.0) -> None:
        start = time.time()
        while not os.path.exists(self.ready_flag):
            if time.time() - start > timeout_s:
                break
            time.sleep(0.05)

    def send(self, message: str, size: str = "auto", force: bool = False) -> None:
        self._last_message = message
        size = self._resolve_size(message, size or "auto")
        self._last_size = size
        payload = self._compose_payload(message, size)

        now = time.monotonic()
        cat = self._classify(message)

        # If we're locked on a prompt, do not let background messages overwrite
        # it for a short window. Critical messages can always break through.
        if (
            (not force)
            and now < self._lock_until
            and self._locked_category == "prompt"
            and cat not in ("prompt", "critical")
        ):
            return

        # Acquire/refresh prompt lock so the user can read it.
        if cat == "prompt":
            m = (message or "").lower()
            hold = 1.15 if "confirm" in m or "ok to send" in m else 0.65
            self._lock_until = now + hold
            self._locked_category = "prompt"
        elif cat == "critical" or force:
            self._lock_until = 0.0
            self._locked_category = None

        # Client-side de-dupe: don’t spam identical frames
        if payload == self._last_payload:
            return

        self._write_payload(payload)

    def show_qr(self, data: str, *caption_lines: str) -> None:
        """Render a QR code on the LCD.

        Protocol extension: use trailing size token 'qr'.
        Line1 is the QR payload, remaining lines are optional captions.
        """
        lines = [data] + [ln for ln in caption_lines if ln]
        self.send("\n".join(lines), size="qr")

    def set_online_clock(
        self,
        *,
        white_ms: int,
        black_ms: int,
        you_are_white=None,
        active_color=None,
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

    # Convenience UI helpers
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
        self,
        header: str,
        *body_lines: str,
        footer: str = "",
        force: bool = False,
    ) -> None:
        lines = [header] + [ln for ln in body_lines if ln is not None]
        if footer:
            lines.append(footer)
        self.send("\n".join(lines), size="setup", force=force)

    def show_header_panel(
        self,
        header: str,
        *body_lines: str,
        footer: str = "",
        force: bool = False,
    ) -> None:
        lines = [header] + [ln for ln in body_lines if ln is not None]
        if footer:
            lines.append(footer)
        self.send("\n".join(lines), size=self._header_size_token(), force=force)

    def set_header_badge(self, text: str | None) -> None:
        self._header_badge = (text or "").strip()

    def show_arrow(self, uci: str, suffix: str = "", force: bool = False) -> None:
        arrow = f"{uci[:2]} → {uci[2:4]}"
        if suffix:
            self.send(f"{arrow}\n{suffix}", force=force)
        else:
            self.send(arrow, force=force)

    def prompt_move(self, side: str, force: bool = False) -> None:
        self.show_header_panel(f"You are {side.upper()}", "Enter move", force=force)

    def show_hint_result(self, uci: str) -> None:
        """Show a hint in the format 'Hint received: e2 → e4'."""
        try:
            frm, to = uci[:2], uci[2:4]
            if len(uci) >= 4:
                self.show_panel(
                    "Hint received:",
                    f"{frm} → {to}",
                    footer="OK = clear",
                )
            else:
                self.send(f"Hint received:\n{uci}")
        except Exception:
            self.send(f"Hint received:\n{uci}")

    def show_invalid(self, text: str) -> None:
        self.send(f"Invalid\n{text}\nTry again")

    def promo_name(self, promo_letter: str) -> str:
        return {
            "q": "Queen",
            "r": "Rook",
            "b": "Bishop",
            "n": "Knight",
        }.get((promo_letter or "").lower(), (promo_letter or "").upper())

    def format_promo_line(self, promo_letter: str) -> str:
        """Return 'Promoted to {NAME}' for a promotion letter (q/r/b/n)."""
        return f"Promoted to {self.promo_name(promo_letter)}"

    def show_draw(self, reason: str, move_no: int) -> None:
        """Display draw reason. move_no is full move count (approx)."""
        # Keep it short for 3-line LCD
        if reason:
            self.send(f"DRAW\n{reason}\nMove {move_no}")
        else:
            self.send(f"DRAW\nMove {move_no}")

    def close(self):
        return
