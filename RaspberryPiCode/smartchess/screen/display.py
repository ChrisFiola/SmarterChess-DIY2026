# -*- coding: utf-8 -*-
"""
Display abstraction for SmarterChess (modular version)
- Communicates with display_server.py through a named pipe.
- Preserves the same UI messaging style as the single-file version.
"""
import os
import sys
import time
import subprocess

from screen.lcd_pipe import LCDPipeClient, PIPE_PATH, READY_FLAG_PATH

DISPLAY_SERVER_SCRIPT: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "screen", "display_server.py"))


class Display:
    """
    Minimal abstraction around display_server IPC.
    """

    def __init__(self, pipe_path: str = PIPE_PATH, ready_flag: str = READY_FLAG_PATH):
        self.pipe_path = pipe_path
        self.ready_flag = ready_flag
        self.client = LCDPipeClient(pipe_path)
        self._last_payload = None
        self._last_send_t = 0.0
        # Simple "UI lock" to prevent important prompts (typing / confirmations)
        # from being immediately overwritten by background/status messages.
        self._lock_until = 0.0
        self._locked_category = None

    def _classify(self, message: str) -> str:
        m = (message or "").lower()
        # Critical should always break through
        if any(k in m for k in ["illegal", "invalid", "game over", "promotion", "draw", "shutting down"]):
            return "critical"
        # High-salience prompts while user is actively entering a move
        if any(k in m for k in ["enter from", "enter to", "confirm", "ok to send", "press ok"]):
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

    def send(self, message: str, size: str = "auto") -> None:
        parts = message.split("\n")
        payload = "|".join(parts) + f"|{size}\n"

        now = time.monotonic()
        cat = self._classify(message)

        # If we're locked on a prompt, do not let background messages overwrite
        # it for a short window. Critical messages can always break through.
        if now < self._lock_until and self._locked_category == "prompt" and cat not in ("prompt", "critical"):
            return

        # Acquire/refresh prompt lock so the user can read it.
        if cat == "prompt":
            m = (message or "").lower()
            # Confirm prompts need longer (prevents "Engine Thinking..." from
            # overwriting before you can read it).
            hold = 1.15 if "confirm" in m or "ok to send" in m else 0.65
            self._lock_until = now + hold
            self._locked_category = "prompt"
        elif cat == "critical":
            self._lock_until = 0.0
            self._locked_category = None

        # Client-side de-dupe: don’t spam identical frames
        if payload == self._last_payload:
            return

        # (Optional) client-side rate limit (lets server stay quieter too)
        # Comment out if you don't want it here.
        # now = time.monotonic()
        # if now - self._last_send_t < 0.02:   # 50 msg/s max
        #     return
        # self._last_send_t = now

        try:
            self._ensure_pipe()
            self._pipe.write(payload)
            self._last_payload = payload
        except (BrokenPipeError, OSError, ValueError):
            # Server restarted or pipe broke: reopen and retry once
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
                # Avoid crashing game loop if display is down
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

    def show_arrow(self, uci: str, suffix: str = "") -> None:
        arrow = f"{uci[:2]} → {uci[2:4]}"
        if suffix:
            self.send(f"{arrow}\n{suffix}")
        else:
            self.send(arrow)

    def prompt_move(self, side: str) -> None:
        # side is human-friendly descriptor: "WHITE" or "BLACK"
        self.send(f"You are {side.lower()}\nEnter move:")

    def show_hint_result(self, uci: str) -> None:
        """
        Show hint in the format:
        Hint received: e2 → e4
        Falls back gracefully if UCI is shorter than 4.
        """
        try:
            frm, to = uci[:2], uci[2:4]
            if len(uci) >= 4:
                self.send(f"Hint received:\n{frm} → {to}\nPress OK")
            else:
                # Fallback: just show whatever we received
                self.send(f"Hint received:\n{uci}")
        except Exception:
            self.send(f"Hint received:\n{uci}")

    """
        def show_hint_result(self, uci: str) -> None:
            self.show_arrow(uci)
    """

    def show_invalid(self, text: str) -> None:
        self.send(f"Invalid\n{text}\nTry again")

    def show_illegal(self, uci: str, side_name: str) -> None:
        """Show illegal move feedback + which square to return to.

        Assumption: the piece was lifted from uci[:2] and needs to go back there.
        """
        try:
            frm = (uci or "")[:2]
            if len(frm) == 2 and frm[0].isalpha() and frm[1].isdigit():
                self.send(f"Illegal move!\nReturn to {frm}\nPress OK")
            else:
                self.send("Illegal move!\nTry again")
        except Exception:
            self.send("Illegal move!\nTry again")

    def _promo_name(self, promo_letter: str) -> str:
        return {
            "q": "QUEEN",
            "r": "ROOK",
            "b": "BISHOP",
            "n": "KNIGHT",
        }.get((promo_letter or "").lower(), (promo_letter or "").upper())

    def show_promotion(self, who: str, promo_letter: str) -> None:
        """Display a short promotion banner.

        who: "Computer" | "Opponent" | "You"
        promo_letter: one of q r b n
        """
        name = self._promo_name(promo_letter)
        self.send(f"{who} promoted\nto {name}")

    def show_draw(self, reason: str, move_no: int) -> None:
        """Display draw reason. move_no is full move count (approx)."""
        # Keep it short for 3-line LCD
        if reason:
            self.send(f"DRAW\n{reason}\nMove {move_no}")
        else:
            self.send(f"DRAW\nMove {move_no}")

    def close(self):
        return
