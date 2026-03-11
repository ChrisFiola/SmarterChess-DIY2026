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
        self._last_send_t = 0.0
        # Simple "UI lock" to prevent important prompts (typing / confirmations)
        # from being immediately overwritten by background/status messages.
        self._lock_until = 0.0
        self._locked_category = None
        self._pipe = None

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
            for k in ["enter from", "enter to", "confirm", "ok to send", "press ok"]
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
        subprocess.Popen(
            ["python3", DISPLAY_SERVER_SCRIPT],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def wait_ready(self, timeout_s: float = 10.0) -> None:
        start = time.time()
        while not os.path.exists(self.ready_flag):
            if time.time() - start > timeout_s:
                break
            time.sleep(0.05)

    def send(self, message: str, size: str = "auto", force: bool = False) -> None:
        parts = message.split("\n")
        payload = "|".join(parts) + f"|{size}\n"

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

    def show_qr(self, data: str, *caption_lines: str) -> None:
        """Render a QR code on the LCD.

        Protocol extension: use trailing size token 'qr'.
        Line1 is the QR payload, remaining lines are optional captions.
        """
        lines = [data] + [ln for ln in caption_lines if ln]
        self.send("\n".join(lines), size="qr")

    # Convenience UI helpers
    def banner(self, text: str, delay_s: float = 0.0) -> None:
        self.send(text)
        if delay_s > 0:
            time.sleep(delay_s)

    def show_arrow(self, uci: str, suffix: str = "", force: bool = False) -> None:
        arrow = f"{uci[:2]} → {uci[2:4]}"
        if suffix:
            self.send(f"{arrow}\n{suffix}", force=force)
        else:
            self.send(arrow, force=force)

    def prompt_move(self, side: str, force: bool = False) -> None:
        self.send(f"You are {side.lower()}\nEnter move:", force=force)

    def show_hint_result(self, uci: str) -> None:
        """Show a hint in the format 'Hint received: e2 → e4'."""
        try:
            frm, to = uci[:2], uci[2:4]
            if len(uci) >= 4:
                self.send(f"Hint received:\n{frm} → {to}\nPress OK")
            else:
                self.send(f"Hint received:\n{uci}")
        except Exception:
            self.send(f"Hint received:\n{uci}")

    def show_invalid(self, text: str) -> None:
        self.send(f"Invalid\n{text}\nTry again")

    def promo_name(self, promo_letter: str) -> str:
        return {
            "q": "QUEEN",
            "r": "ROOK",
            "b": "BISHOP",
            "n": "KNIGHT",
        }.get((promo_letter or "").lower(), (promo_letter or "").upper())

    def show_draw(self, reason: str, move_no: int) -> None:
        """Display draw reason. move_no is full move count (approx)."""
        # Keep it short for 3-line LCD
        if reason:
            self.send(f"DRAW\n{reason}\nMove {move_no}")
        else:
            self.send(f"DRAW\nMove {move_no}")

    def close(self):
        return
