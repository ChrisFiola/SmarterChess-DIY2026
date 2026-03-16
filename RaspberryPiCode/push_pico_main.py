#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import sys
import time
from pathlib import Path

from core.boardlink import BAUD, SERIAL_PORT, SERIAL_TIMEOUT, BoardLink


DEFAULT_PICO_DIR = Path(__file__).resolve().parents[1] / "PicoCode" / "main"
DEFAULT_SOURCES = [
    DEFAULT_PICO_DIR / "main.py",
    DEFAULT_PICO_DIR / "pico_hw.py",
]


def _wait_for_update_ready(
    link: BoardLink,
    *,
    timeout_s: float,
    resend_every_s: float,
) -> bool:
    deadline = time.monotonic() + timeout_s
    last_sync = 0.0

    def _sync() -> None:
        nonlocal last_sync
        link.send_to_board("ChooseMode")
        link.send_to_board("UpdateMode")
        last_sync = time.monotonic()

    _sync()
    while time.monotonic() < deadline:
        msg = link.read_from_board()
        if msg == "updateready":
            return True
        if msg == "updateerror":
            return False
        if time.monotonic() - last_sync >= resend_every_s:
            _sync()
    return False


def _wait_for_update_complete(link: BoardLink, *, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        msg = link.read_from_board()
        if msg == "updatecomplete":
            return True
        if msg and msg.startswith("updateerror"):
            print(f"Pico reported {msg}.", file=sys.stderr)
            return False
    return False


def push_main(
    sources: list[Path],
    *,
    port: str,
    baud: int,
    timeout: float,
    chunk_size: int,
    inter_chunk_delay_s: float,
    ready_timeout_s: float,
    complete_timeout_s: float,
) -> int:
    unique_sources = []
    seen_names = set()
    for source in sources:
        if not source.is_file():
            print(f"File not found: {source}", file=sys.stderr)
            return 2
        if source.name in seen_names:
            continue
        seen_names.add(source.name)
        unique_sources.append(source)

    link = BoardLink(port=port, baud=baud, timeout=timeout)
    try:
        link.clear_input()

        print(f"Connecting to Pico on {port}...")
        if not _wait_for_update_ready(
            link,
            timeout_s=ready_timeout_s,
            resend_every_s=3.0,
        ):
            print("Pico did not enter update mode.", file=sys.stderr)
            try:
                link.send_to_board("UpdateAbort")
            except Exception:
                pass
            return 1

        for source in unique_sources:
            payload = base64.b64encode(source.read_bytes()).decode("ascii")
            print(f"Uploading {source.name} in {chunk_size}-char chunks...")
            link.send_to_board(f"UpdateFile_{source.name}")
            time.sleep(inter_chunk_delay_s)
            for i in range(0, len(payload), chunk_size):
                link.send_to_board(f"UpdateChunk_{payload[i:i + chunk_size]}")
                time.sleep(inter_chunk_delay_s)
            link.send_to_board("UpdateFileDone")
            time.sleep(inter_chunk_delay_s)

        link.send_to_board("UpdateDone")
        print("Waiting for Pico to flash and reboot...")
        if not _wait_for_update_complete(link, timeout_s=complete_timeout_s):
            print("Timed out waiting for UpdateComplete.", file=sys.stderr)
            return 1

        print("Pico update complete.")
        return 0
    finally:
        link.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Push Pico firmware files to the Pico over the Pi UART link."
    )
    parser.add_argument(
        "sources",
        nargs="*",
        default=[str(path) for path in DEFAULT_SOURCES if path.is_file()],
        help="Path(s) to Pico files to upload.",
    )
    parser.add_argument("--port", default=SERIAL_PORT, help="UART device path.")
    parser.add_argument("--baud", type=int, default=BAUD, help="UART baud rate.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=SERIAL_TIMEOUT,
        help="Per-read serial timeout in seconds.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=64,
        help="Base64 chunk size to send per UART message.",
    )
    parser.add_argument(
        "--chunk-delay",
        type=float,
        default=0.02,
        help="Delay between chunk messages in seconds.",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=15.0,
        help="How long to wait for the Pico to enter update mode.",
    )
    parser.add_argument(
        "--complete-timeout",
        type=float,
        default=30.0,
        help="How long to wait for UpdateComplete after upload.",
    )
    args = parser.parse_args()

    return push_main(
        [Path(source).resolve() for source in args.sources],
        port=args.port,
        baud=args.baud,
        timeout=args.timeout,
        chunk_size=args.chunk_size,
        inter_chunk_delay_s=args.chunk_delay,
        ready_timeout_s=args.ready_timeout,
        complete_timeout_s=args.complete_timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
