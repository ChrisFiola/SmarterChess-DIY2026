# -*- coding: utf-8 -*-
"""
Serial link between Raspberry Pi and Pico over UART.

The protocol uses two prefixes:
  - Pi → Pico: "heyArduino" + payload + newline
  - Pico → Pi: "heypi" + payload + newline  (or "heypixshutdown" for shutdown)

Touch events from the ILI9341/XPT2046 (via Display.touch_queue) are merged
transparently: read_from_board() and try_read_from_board() drain the touch
queue first so callers see touch events as if they came from the Pico.

Public methods:
  send_to_board(text)        — send a message to the Pico
  read_from_board()          — blocking read; returns payload or "shutdown"
  try_read_from_board()      — non-blocking read; returns None if nothing waiting
  clear_input()              — drop any buffered input from the Pico
  set_touch_queue(q)         — attach Display.touch_queue
"""
import queue as _queue
from typing import Optional
import serial

SERIAL_PORT: str = "/dev/serial0"
BAUD: int = 115200
SERIAL_TIMEOUT: float = 0.05  # short timeout so touch queue is polled regularly


class BoardLink:
    def __init__(
        self,
        port: str = SERIAL_PORT,
        baud: int = BAUD,
        timeout: float = SERIAL_TIMEOUT,
    ):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        self.ser.flush()
        self._touch_queue: Optional[_queue.Queue] = None

    def set_touch_queue(self, q: _queue.Queue) -> None:
        """Attach the Display.touch_queue so touch events merge with UART reads."""
        self._touch_queue = q

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def clear_input(self) -> None:
        """Drop any buffered incoming bytes from the Pico."""
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        # Also drain touch queue
        if self._touch_queue:
            try:
                while True:
                    self._touch_queue.get_nowait()
            except _queue.Empty:
                pass

    # ── Writes ────────────────────────────────────────────────────────────────

    def send_to_board(self, text: str) -> None:
        """Send a message to the Pico. The "heyArduino" prefix is added automatically."""
        payload = "heyArduino" + text
        self.ser.write(payload.encode("utf-8") + b"\n")
        self.ser.flush()
        print(f"[-→Board] {payload}")

    # ── Internal parse ────────────────────────────────────────────────────────

    def _readline(self) -> Optional[str]:
        line = self.ser.readline()
        if not line:
            return None
        try:
            return line.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None

    def _parse_raw(self, raw: str) -> Optional[str]:
        """Return payload string, 'shutdown', or None for unrecognised lines."""
        if not raw:
            return None
        low = raw.lower()
        if low.startswith("heypixshutdown"):
            return "shutdown"
        if low.startswith("heypi"):
            payload = low[5:]
            print(f"[Board→] {raw}  | payload='{payload}'")
            return payload
        return None

    def _poll_touch(self) -> Optional[str]:
        """Return one touch event from the queue, or None."""
        if not self._touch_queue:
            return None
        try:
            return self._touch_queue.get_nowait()
        except _queue.Empty:
            return None

    # ── Reads ─────────────────────────────────────────────────────────────────

    def try_read_from_board(self) -> Optional[str]:
        """Non-blocking read. Returns the payload string, 'shutdown', or None."""
        # Prefer UART when a complete board message is already available.
        if self.ser.in_waiting:
            raw = self._readline()
            result = self._parse_raw(raw)
            if result is not None:
                return result

        # Touch events are still delivered when no board data is pending.
        touch = self._poll_touch()
        if touch is not None:
            print(f"[Touch→] {touch}")
            return touch
        return None

    def read_from_board(self) -> Optional[str]:
        """Blocking read with 2-second equivalent timeout.

        Polls UART in 50 ms slices and checks the touch queue each slice so
        touch events are still delivered while avoiding stale touch hijacks.
        Returns the payload string, 'shutdown', or None on timeout.
        """
        _MAX_ITERS = 40  # 40 × 50 ms = 2 s total timeout

        for _ in range(_MAX_ITERS):
            # UART first so board input is not hijacked by stale touch events.
            raw = self._readline()
            result = self._parse_raw(raw)
            if result is not None:
                return result

            touch = self._poll_touch()
            if touch is not None:
                print(f"[Touch→] {touch}")
                return touch

        return None
