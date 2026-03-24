"""Network helpers for SmartChess.

These are best-effort heuristics designed for a Pi Zero class device.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional


def run_command(cmd: list[str], timeout_s: float = 1.5) -> str:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=timeout_s)
        return out.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _iw_ssid(iface: str = "wlan0") -> str:
    # iwgetid -r prints current SSID when in STA mode
    return run_command(["iwgetid", iface, "-r"], timeout_s=1.0)


def _ipv4_addr(iface: str = "wlan0") -> Optional[str]:
    out = run_command(["ip", "-o", "-4", "addr", "show", "dev", iface], timeout_s=1.5)
    # Example: "3: wlan0    inet 192.168.4.1/24 brd ..."
    m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/", out)
    return m.group(1) if m else None


def _service_active(name: str) -> bool:
    out = run_command(["systemctl", "is-active", name], timeout_s=1.5)
    return out.strip() == "active"


def is_ap_mode() -> bool:
    """Heuristic: AP mode tends to have no SSID + hostapd/dnsmasq active."""
    ssid = _iw_ssid("wlan0")
    if ssid:
        return False
    ip = _ipv4_addr("wlan0")
    if not ip:
        return False
    # If hostapd/dnsmasq are present, great signal.
    if _service_active("hostapd") or _service_active("dnsmasq"):
        return True
    # Fallback: common AP ranges.
    return ip.startswith("192.168.") or ip.startswith("10.")


def wifi_config_url() -> Optional[str]:
    ip = _ipv4_addr("wlan0")
    if not ip:
        return None
    return f"http://{ip}/"
