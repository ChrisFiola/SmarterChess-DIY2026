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
import threading
import time
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
        self._touch_local_queue: _queue.Queue = _queue.Queue(maxsize=64)
        self._last_input_is_touch = False
        self._touch_thread: Optional[threading.Thread] = None
        self._touch_stop = threading.Event()
        self._ser_write_lock = threading.Lock()

    def set_touch_queue(self, q: _queue.Queue) -> None:
        """Attach the Display.touch_queue so touch events merge with UART reads."""
        self._touch_queue = q
        if self._touch_thread is None or not self._touch_thread.is_alive():
            self._touch_stop.clear()
            self._touch_thread = threading.Thread(
                target=self._touch_relay_loop,
                daemon=True,
            )
            self._touch_thread.start()

    def _touch_relay_loop(self) -> None:
        """Continuously relay touch events to Pico and mirror them for Pi readers."""
        while not self._touch_stop.is_set():
            if not self._touch_queue:
                time.sleep(0.01)
                continue
            try:
                touch = self._touch_queue.get(timeout=0.05)
            except _queue.Empty:
                continue

            # Always forward to Pico immediately so touch behaves like wired buttons.
            self._forward_touch_to_pico(touch)

            # Mirror touch for Pi-side consumers (read_from_board/try_read_from_board).
            try:
                self._touch_local_queue.put_nowait(touch)
            except _queue.Full:
                try:
                    self._touch_local_queue.get_nowait()
                except _queue.Empty:
                    pass
                try:
                    self._touch_local_queue.put_nowait(touch)
                except _queue.Full:
                    pass

    def close(self):
        self._touch_stop.set()
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
        self._last_input_is_touch = False
        # Also drain mirrored touch queue
        try:
            while True:
                self._touch_local_queue.get_nowait()
        except _queue.Empty:
            pass
        # And drain source touch queue if present
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
        with self._ser_write_lock:
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
        """Return one touch event from the queue, or None.

        When a touch event is found, it is also forwarded to the Pico so that
        blocking loops on the Pico can respond to touch OK/hint events.
        """
        if not self._touch_queue:
            self._last_input_is_touch = False
            return None
        try:
            touch = self._touch_local_queue.get_nowait()
            self._last_input_is_touch = True
            return touch
        except _queue.Empty:
            self._last_input_is_touch = False
            return None

    def _forward_touch_to_pico(self, touch: str) -> None:
        """Send touch event to Pico as 'heyArduinotouch_<action>'."""
        try:
            payload = "heyArduinotouch_" + touch
            with self._ser_write_lock:
                self.ser.write(payload.encode("utf-8") + b"\n")
                self.ser.flush()
            print(f"[-→Pico touch] {payload}")
        except Exception:
            pass

    def last_input_was_touch(self) -> bool:
        return self._last_input_is_touch

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
