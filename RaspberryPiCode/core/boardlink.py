# -*- coding: utf-8 -*-
"""
Serial link between Raspberry Pi and Pico over UART.

The protocol uses two prefixes:
  - Pi → Pico: "heyArduino" + payload + newline
  - Pico → Pi: "heypi" + payload + newline  (or "heypixshutdown" for shutdown)

Public methods:
  send_to_board(text)        — send a message to the Pico
  read_from_board()          — blocking read; returns payload or "shutdown"
  try_read_from_board()      — non-blocking read; returns None if nothing waiting
  clear_input()              — drop any buffered input from the Pico
"""
from typing import Optional
import serial

SERIAL_PORT: str = "/dev/serial0"
BAUD: int = 115200
SERIAL_TIMEOUT: float = 2.0


class BoardLink:
    def __init__(
        self, port: str = SERIAL_PORT, baud: int = BAUD, timeout: float = SERIAL_TIMEOUT
    ):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        self.ser.flush()

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

    # ── Writes ────────────────────────────────────────────────────────────────

    def send_to_board(self, text: str) -> None:
        """Send a message to the Pico. The "heyArduino" prefix is added automatically."""
        payload = "heyArduino" + text
        self.ser.write(payload.encode("utf-8") + b"\n")
        self.ser.flush()
        print(f"[-→Board] {payload}")

    # ── Reads ─────────────────────────────────────────────────────────────────

    def _readline(self) -> Optional[str]:
        line = self.ser.readline()
        if not line:
            return None
        try:
            return line.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None

    def try_read_from_board(self) -> Optional[str]:
        """Non-blocking read. Returns the payload string, "shutdown", or None."""
        if not self.ser.in_waiting:
            return None
        raw = self._readline()
        if not raw:
            return None
        low = raw.lower()
        if low.startswith("heypixshutdown"):
            return "shutdown"
        if low.startswith("heypi"):
            payload = low[5:]
            print(f"[Board→] {low}  | payload='{payload}'")
            return payload
        return None

    def read_from_board(self) -> Optional[str]:
        """Blocking read. Returns the payload string, "shutdown", or None on timeout."""
        while True:
            raw = self._readline()
            if raw is None:
                return None
            low = raw.lower()
            if low.startswith("heypixshutdown"):
                return "shutdown"
            if low.startswith("heypi"):
                payload = low[5:]
                print(f"[Board→] {raw}  | payload='{payload}'")
                return payload
