"""Fallback WiFi Access Point with captive portal for SmartChess.

When the Pi cannot connect to any known WiFi network, this module:
1. Starts a temporary AP (hostapd + dnsmasq) on wlan0
2. Runs a small HTTP captive portal that lets the user pick a network
3. Connects to the chosen network via wpa_cli
4. Tears down the AP once connected
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Optional
from urllib.parse import parse_qs

from core.net_utils import run_command

_AP_SSID = "SmartChess-Setup"
_AP_IP = "192.168.4.1"
_AP_DHCP_START = "192.168.4.10"
_AP_DHCP_END = "192.168.4.50"
_IFACE = "wlan0"
_WPA_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"

_tmpdir: Optional[str] = None
_hostapd_proc: Optional[subprocess.Popen] = None
_dnsmasq_proc: Optional[subprocess.Popen] = None
_server: Optional[HTTPServer] = None
_connected_ssid: Optional[str] = None


def _run_quiet(cmd: list[str], timeout_s: float = 5.0) -> bool:
    try:
        subprocess.check_call(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout_s,
        )
        return True
    except Exception:
        return False


def is_wifi_connected() -> bool:
    """Return True if wlan0 is associated with an SSID in STA mode."""
    ssid = run_command(["iwgetid", _IFACE, "-r"], timeout_s=1.0)
    return bool(ssid)


def _scan_networks() -> list[dict]:
    """Scan for visible WiFi networks. Returns list of {ssid, signal, security}."""
    _run_quiet(["iw", "dev", _IFACE, "scan", "trigger"])
    time.sleep(2)
    raw = run_command(["iw", "dev", _IFACE, "scan", "dump"], timeout_s=10.0)
    if not raw:
        raw = run_command(["iwlist", _IFACE, "scan"], timeout_s=10.0)
        return _parse_iwlist(raw)
    return _parse_iw_scan(raw)


def _parse_iw_scan(raw: str) -> list[dict]:
    nets: list[dict] = []
    current: dict = {}
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("BSS "):
            if current.get("ssid"):
                nets.append(current)
            current = {"ssid": "", "signal": "", "security": "Open"}
        if line.startswith("SSID:"):
            current["ssid"] = line.split(":", 1)[1].strip()
        if line.startswith("signal:"):
            current["signal"] = line.split(":", 1)[1].strip()
        if "WPA" in line or "RSN" in line:
            current["security"] = "WPA"
    if current.get("ssid"):
        nets.append(current)
    seen = set()
    unique = []
    for n in nets:
        if n["ssid"] not in seen and n["ssid"] != _AP_SSID:
            seen.add(n["ssid"])
            unique.append(n)
    return unique


def _parse_iwlist(raw: str) -> list[dict]:
    nets: list[dict] = []
    current: dict = {}
    for line in raw.splitlines():
        line = line.strip()
        if "Cell " in line and "Address:" in line:
            if current.get("ssid"):
                nets.append(current)
            current = {"ssid": "", "signal": "", "security": "Open"}
        m = re.search(r'ESSID:"(.+?)"', line)
        if m:
            current["ssid"] = m.group(1)
        if "Signal level" in line:
            current["signal"] = line.split("Signal level")[-1].strip(" =:")
        if "WPA" in line:
            current["security"] = "WPA"
    if current.get("ssid"):
        nets.append(current)
    seen = set()
    unique = []
    for n in nets:
        if n["ssid"] not in seen and n["ssid"] != _AP_SSID:
            seen.add(n["ssid"])
            unique.append(n)
    return unique


def _write_hostapd_conf(path: str) -> None:
    with open(path, "w") as f:
        f.write(
            f"interface={_IFACE}\n"
            f"driver=nl80211\n"
            f"ssid={_AP_SSID}\n"
            f"hw_mode=g\n"
            f"channel=7\n"
            f"wmm_enabled=0\n"
            f"macaddr_acl=0\n"
            f"auth_algs=1\n"
            f"ignore_broadcast_ssid=0\n"
        )


def _write_dnsmasq_conf(path: str) -> None:
    with open(path, "w") as f:
        f.write(
            f"interface={_IFACE}\n"
            f"dhcp-range={_AP_DHCP_START},{_AP_DHCP_END},255.255.255.0,24h\n"
            f"address=/#/{_AP_IP}\n"
        )


def _start_ap() -> bool:
    """Start the access point. Returns True on success."""
    global _tmpdir, _hostapd_proc, _dnsmasq_proc

    _tmpdir = tempfile.mkdtemp(prefix="smartchess_ap_")
    hostapd_conf = os.path.join(_tmpdir, "hostapd.conf")
    dnsmasq_conf = os.path.join(_tmpdir, "dnsmasq.conf")

    _write_hostapd_conf(hostapd_conf)
    _write_dnsmasq_conf(dnsmasq_conf)

    # Stop any existing services that might conflict
    _run_quiet(["systemctl", "stop", "hostapd"])
    _run_quiet(["systemctl", "stop", "dnsmasq"])
    _run_quiet(["systemctl", "stop", "wpa_supplicant"])

    # Configure interface for AP
    _run_quiet(["ip", "link", "set", _IFACE, "down"])
    _run_quiet(["ip", "addr", "flush", "dev", _IFACE])
    _run_quiet(["ip", "addr", "add", f"{_AP_IP}/24", "dev", _IFACE])
    _run_quiet(["ip", "link", "set", _IFACE, "up"])
    time.sleep(0.5)

    _hostapd_proc = subprocess.Popen(
        ["hostapd", hostapd_conf],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    if _hostapd_proc.poll() is not None:
        print("[WIFI AP] hostapd failed to start", flush=True)
        _stop_ap()
        return False

    _dnsmasq_proc = subprocess.Popen(
        ["dnsmasq", "-C", dnsmasq_conf, "--no-daemon", "--log-queries"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    if _dnsmasq_proc.poll() is not None:
        print("[WIFI AP] dnsmasq failed to start", flush=True)
        _stop_ap()
        return False

    print(f"[WIFI AP] AP started: SSID={_AP_SSID} IP={_AP_IP}", flush=True)
    return True


def _stop_ap() -> None:
    """Tear down the access point."""
    global _hostapd_proc, _dnsmasq_proc, _tmpdir, _server

    if _server:
        try:
            _server.shutdown()
        except Exception:
            pass
        _server = None

    for proc in (_hostapd_proc, _dnsmasq_proc):
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    _hostapd_proc = None
    _dnsmasq_proc = None

    # Restore interface for STA mode
    _run_quiet(["ip", "addr", "flush", "dev", _IFACE])
    _run_quiet(["ip", "link", "set", _IFACE, "down"])
    _run_quiet(["ip", "link", "set", _IFACE, "up"])
    _run_quiet(["systemctl", "start", "wpa_supplicant"])
    time.sleep(1.0)

    if _tmpdir and os.path.isdir(_tmpdir):
        shutil.rmtree(_tmpdir, ignore_errors=True)
        _tmpdir = None

    print("[WIFI AP] AP stopped", flush=True)


def _add_network_to_wpa(ssid: str, password: str) -> bool:
    """Add a network to wpa_supplicant and attempt to connect."""
    # Use wpa_passphrase to generate the config block
    if password:
        try:
            block = subprocess.check_output(
                ["wpa_passphrase", ssid, password],
                stderr=subprocess.DEVNULL, timeout=3,
            ).decode("utf-8", errors="ignore")
        except Exception:
            return False
    else:
        block = f'\nnetwork={{\n\tssid="{ssid}"\n\tkey_mgmt=NONE\n}}\n'

    try:
        with open(_WPA_CONF, "a") as f:
            f.write(block)
    except Exception:
        return False

    return True


def _connect_sta(ssid: str, password: str) -> bool:
    """Stop AP, add network credentials, and connect in STA mode."""
    global _connected_ssid

    _add_network_to_wpa(ssid, password)
    _stop_ap()

    # Reconfigure wpa_supplicant to pick up new network
    _run_quiet(["wpa_cli", "-i", _IFACE, "reconfigure"], timeout_s=5.0)
    time.sleep(3.0)

    # Wait for connection (up to 15 seconds)
    for _ in range(15):
        if is_wifi_connected():
            _connected_ssid = ssid
            print(f"[WIFI AP] Connected to {ssid!r}", flush=True)
            return True
        time.sleep(1.0)

    print(f"[WIFI AP] Failed to connect to {ssid!r}", flush=True)
    return False


# ── Captive portal HTTP server ──────────────────────────────────────────────

_HTML_HEAD = """\
<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SmartChess WiFi Setup</title>
<style>
body{font-family:sans-serif;margin:20px;background:#1a1a2e;color:#e0e0e0}
h1{color:#f5c542;font-size:1.4em}
.net{display:block;padding:12px;margin:6px 0;background:#16213e;
     border:1px solid #0f3460;border-radius:8px;cursor:pointer;
     color:#e0e0e0;text-decoration:none;font-size:1.1em}
.net:hover{background:#0f3460}
input[type=text],input[type=password]{width:100%;padding:10px;
     margin:6px 0;box-sizing:border-box;border-radius:6px;border:1px solid #555;
     background:#16213e;color:#e0e0e0;font-size:1em}
button{padding:12px 24px;background:#f5c542;color:#1a1a2e;border:none;
       border-radius:8px;font-size:1.1em;cursor:pointer;margin-top:10px}
button:hover{background:#e0b030}
.info{color:#888;font-size:0.9em}
.err{color:#ff6b6b}
.ok{color:#6bff6b}
</style></head><body>
"""

_HTML_FOOT = "</body></html>"


class _CaptiveHandler(BaseHTTPRequestHandler):
    """Minimal captive portal handler."""

    def log_message(self, format, *args):
        pass  # suppress access logs

    def _redirect(self, url: str = "/") -> None:
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def _html(self, body: str, status: int = 200) -> None:
        page = (_HTML_HEAD + body + _HTML_FOOT).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        # Captive portal detection endpoints — redirect to our page
        if path in (
            "/generate_204", "/gen_204", "/hotspot-detect.html",
            "/connecttest.txt", "/ncsi.txt", "/redirect",
            "/canonical.html", "/success.txt",
        ):
            self._redirect("/")
            return

        if path == "/scan":
            nets = _scan_networks()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            data = json.dumps(nets).encode()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/status":
            connected = is_wifi_connected()
            data = json.dumps({"connected": connected, "ssid": _connected_ssid or ""}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # Main page
        self._html(
            "<h1>SmartChess WiFi Setup</h1>"
            "<p>Select a network or enter details manually.</p>"
            '<div id="nets"><p class="info">Scanning...</p></div>'
            '<hr><h2>Manual entry</h2>'
            '<form method="POST" action="/connect">'
            '<input type="text" name="ssid" placeholder="Network name (SSID)" required>'
            '<input type="password" name="password" placeholder="Password (leave empty if open)">'
            "<button type=submit>Connect</button></form>"
            "<script>"
            "fetch('/scan').then(r=>r.json()).then(nets=>{"
            "let h='';"
            "if(!nets.length){h='<p class=\"info\">No networks found. Try refreshing.</p>';}"
            "else{nets.forEach(n=>{"
            "h+='<a class=\"net\" href=\"/pick?ssid='+encodeURIComponent(n.ssid)+'\">';"
            "h+=n.ssid+' <span class=\"info\">('+n.security+')</span></a>';});}"
            "document.getElementById('nets').innerHTML=h;"
            "}).catch(()=>{document.getElementById('nets').innerHTML="
            "'<p class=\"info\">Scan failed. Enter details manually.</p>';});"
            "</script>"
        )

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="ignore")
        params = parse_qs(body)
        ssid = (params.get("ssid") or [""])[0].strip()
        password = (params.get("password") or [""])[0]

        if not ssid:
            self._html("<h1>Error</h1><p class='err'>No SSID provided.</p>"
                       "<a href='/'>Back</a>")
            return

        self._html(
            f"<h1>Connecting...</h1>"
            f"<p>Attempting to connect to <strong>{ssid}</strong>...</p>"
            f"<p class='info'>The access point will shut down. "
            f"If connection fails, it will restart.</p>"
        )

        # Connect in background so the response is sent first
        Thread(target=_connect_sta, args=(ssid, password), daemon=True).start()

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()


class _PickHandler(_CaptiveHandler):
    """Extends captive handler with /pick page for pre-filled SSID."""

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/pick":
            qs = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            ssid = (qs.get("ssid") or [""])[0]
            self._html(
                f"<h1>Connect to: {ssid}</h1>"
                f'<form method="POST" action="/connect">'
                f'<input type="hidden" name="ssid" value="{ssid}">'
                f'<input type="password" name="password" '
                f'placeholder="Password (leave empty if open)">'
                f"<button type=submit>Connect</button></form>"
                f'<a href="/">Back to list</a>'
            )
            return
        super().do_GET()


def _start_portal() -> None:
    """Start the captive portal HTTP server (blocking)."""
    global _server
    _server = HTTPServer(("0.0.0.0", 80), _PickHandler)
    print(f"[WIFI AP] Captive portal at http://{_AP_IP}/", flush=True)
    _server.serve_forever()


# ── Public API ──────────────────────────────────────────────────────────────


def ensure_wifi(display=None, timeout_s: float = 120.0) -> bool:
    """Ensure WiFi connectivity, starting a setup AP if needed.

    Args:
        display: optional Display instance for showing status on LCD
        timeout_s: max seconds to wait for user to configure WiFi

    Returns True if WiFi is connected (either already or after setup).
    """
    if is_wifi_connected():
        return True

    print("[WIFI AP] No WiFi connection — starting setup AP", flush=True)
    if display:
        display.show_header_panel("WiFi setup", "No WiFi found", "Starting setup AP", _AP_SSID)

    if not _start_ap():
        print("[WIFI AP] Could not start AP", flush=True)
        if display:
            display.show_header_panel(
                "WiFi setup",
                "AP setup failed",
                "Check hostapd",
                "& dnsmasq",
            )
        time.sleep(3)
        return False

    portal_thread = Thread(target=_start_portal, daemon=True)
    portal_thread.start()

    url = f"http://{_AP_IP}/"
    if display:
        if hasattr(display, "show_qr"):
            display.show_qr(url, f"Join WiFi: {_AP_SSID}", "then scan this code")
        else:
            display.show_header_panel(
                "WiFi setup",
                "Join WiFi:",
                _AP_SSID,
                f"Open: {url}",
            )

    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        if is_wifi_connected():
            _stop_ap()
            ssid = run_command(["iwgetid", _IFACE, "-r"], timeout_s=1.0)
            print(f"[WIFI AP] Now connected to {ssid!r}", flush=True)
            if display:
                display.show_header_panel("WiFi setup", "Connected!", ssid)
            time.sleep(2)
            return True
        time.sleep(2.0)

    # Timeout — tear down AP and continue without WiFi
    print("[WIFI AP] Timeout waiting for WiFi config", flush=True)
    _stop_ap()
    if display:
        display.show_header_panel(
            "WiFi setup",
            "Timed out",
            "Continuing offline",
        )
    time.sleep(2)
    return False
