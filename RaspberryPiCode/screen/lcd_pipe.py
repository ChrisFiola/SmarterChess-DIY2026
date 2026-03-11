# -*- coding: utf-8 -*-
"""LCD named-pipe protocol helpers.

display_server.py listens on /tmp/lcdpipe for a single-line message:
  L1|L2|L3|L4|size

Where size is:
  - 'auto' (default): pick a font size that fits
  - integer: fixed font size
  - 'qr': render QR for L1, with optional captions in L2..L4

This module centralizes the protocol so every caller stays consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Iterable, List, Optional

PIPE_PATH: str = "/tmp/lcdpipe"
READY_FLAG_PATH: str = "/tmp/display_server_ready"


def _sanitize(s: str) -> str:
    # display_server uses '|' as a separator
    return (s or "").replace("|", "/").strip()


def _encode_message(lines: Iterable[str], size: str = "auto") -> str:
    parts = [_sanitize(x) for x in list(lines)[:4]]
    while len(parts) < 4:
        parts.append("")
    return "|".join(parts + [str(size)])


@dataclass
class LCDPipeClient:
    """Small helper to write to the display_server named pipe."""

    pipe_path: str = PIPE_PATH

    def wait_ready(self, timeout_s: float = 5.0) -> bool:
        """Wait until display_server.py drops READY_FLAG_PATH."""
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            if os.path.exists(READY_FLAG_PATH):
                return True
            time.sleep(0.05)
        return False

    def send(self, l1: str = "", l2: str = "", l3: str = "", l4: str = "", *, size: str = "auto") -> None:
        msg = _encode_message([l1, l2, l3, l4], size=size)
        # Use a short open/write/close to keep behavior simple and robust.
        with open(self.pipe_path, "w") as f:
            f.write(msg + "\n")
            f.flush()

    def _send_lines(self, lines: List[str], *, size: str = "auto") -> None:
        padded = (lines + [""] * 4)[:4]
        self.send(*padded, size=size)

    def qr(self, data: str, captions: Optional[List[str]] = None) -> None:
        caps = captions or []
        padded = [data] + caps
        self._send_lines(padded, size="qr")
