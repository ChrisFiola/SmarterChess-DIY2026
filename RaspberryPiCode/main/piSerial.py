# -*- coding: utf-8 -*-
"""
BoardLink v2 — JSON lines over UART
- send(kind, dict) -> writes one line JSON {"t": kind, "d": {...}}
- get() -> returns dict {t, d} or None
"""
from typing import Optional, Dict, Any
import serial  # type: ignore
import json

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

    # Send one JSON line
    def send(self, kind: str, data: Dict[str, Any] | None = None) -> None:
        if data is None:
            data = {}
        msg = {"t": kind, "d": data}
        s = json.dumps(msg)
        self.ser.write(s.encode('utf-8') + b"\n")
        print(f"[-→Board] {s}")

    # Non-blocking get (only if bytes waiting)
    def get_nonblocking(self) -> Optional[Dict[str, Any]]:
        if self.ser.in_waiting:
            return self._readline_obj()
        return None

    def get(self) -> Optional[Dict[str, Any]]:
        return self._readline_obj()

    def _readline_obj(self) -> Optional[Dict[str, Any]]:
        line = self.ser.readline()
        if not line:
            return None
        try:
            s = line.decode('utf-8').strip()
            if not s:
                return None
            obj = json.loads(s)
            if isinstance(obj, dict) and 't' in obj:
                print(f"[Board→] {s}")
                return obj
        except Exception:
            return None
        return None
