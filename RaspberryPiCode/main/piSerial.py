# -*- coding: utf-8 -*-
"""
Serial link wrapper for Pico <-> Pi protocol (modular version)
- Preserves UART protocol strings (heyArduino / heypi / heypixshutdown).
"""
from typing import Optional
import serial  # type: ignore 

SERIAL_PORT: str = "/dev/serial0"
BAUD: int = 115200
SERIAL_TIMEOUT: float = 2.0

class BoardLink:
    def __init__(self, port: str = SERIAL_PORT, baud: int = BAUD, timeout: float = SERIAL_TIMEOUT):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        self.ser.flush()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    # Writes 
    def send_raw(self, text: str) -> None:
        self.ser.write(text.encode("utf-8") + b"\n")

    def sendtoboard(self, text: str) -> None:
        payload = "heyArduino" + text
        self.ser.write(payload.encode("utf-8") + b"\n")
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