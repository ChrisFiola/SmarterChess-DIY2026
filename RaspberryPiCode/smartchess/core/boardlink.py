# -*- coding: utf-8 -*-
"""
Serial link wrapper for Pico <-> Pi protocol (modular version)
- Preserves UART protocol strings (heyArduino / heypi / heypixshutdown).
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
        """Drop any pending incoming messages from the Pico."""
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass

    def clear_output(self) -> None:
        """Drop any pending outgoing bytes to the Pico."""
        try:
            self.ser.reset_output_buffer()
        except Exception:
            pass

    # Writes
    def send_raw(self, text: str) -> None:
        self.ser.write(text.encode("utf-8") + b"\n")

    def sendtoboard(self, text: str) -> None:
        payload = "heyArduino" + text
        self.ser.write(payload.encode("utf-8") + b"\n")
        self.ser.flush()
        print(f"[-→Board] {payload}")

    # Reads
    def _readline(self) -> Optional[str]:
        line = self.ser.readline()
        if not line:
            return None
        try:
            return line.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None

    def get_raw_from_board(self) -> Optional[str]:
        raw = self._readline()
        if raw is None:
            return None
        low = raw.lower()
        if low.startswith("heypixshutdown"):
            return "heypixshutdown"
        return low

    def getboard_nonblocking(self) -> Optional[str]:
        if self.ser.in_waiting:
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

    def _extract_uci4(text: str) -> str | None:
        # text might be "e2 -> e4" or "e2->e4" etc
        cleaned = "".join(ch for ch in text.lower() if ch.isalnum())

        return cleaned[:4] if len(cleaned) >= 4 else None

    def getboard(self):
        msg = self._readline()
        if not msg:
            return None

        # If pico sent typing_confirm, ACK it immediately (NO LCD CHANGES HERE)
        if msg.startswith("heypityping_confirm_"):
            payload = msg[len("heypityping_confirm_") :].strip()  # "e2 -> e4"
            uci4 = self._extract_uci4(payload)
            if uci4:
                self.sendtoboard(f"typing_ack_confirm_{uci4}")
            return "typing_confirm_" + payload

    def getboard(self) -> Optional[str]:
        while True:
            raw = self.get_raw_from_board()
            if raw is None:
                return None
            if raw.startswith("heypixshutdown"):
                return "shutdown"
            if raw.startswith("heypi"):
                payload = raw[5:]
                print(f"[Board→] {raw}  | payload='{payload}'")
                return payload
