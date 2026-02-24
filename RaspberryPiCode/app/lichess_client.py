# -*- coding: utf-8 -*-
"""Lichess API client for SmartChess (manual-start online).

Token must be provided via environment variable LICHESS_TOKEN (recommended via systemd EnvironmentFile).
"""
from __future__ import annotations

import os
import json
from typing import Dict, Any, Iterator, Optional

import requests
from requests.exceptions import RequestException

LICHESS_BASE = "https://lichess.org"

def _iter_ndjson(resp) -> Iterator[Dict[str, Any]]:
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue

class LichessClient:
    def __init__(self, token: Optional[str] = None):
        tok = token or os.environ.get("LICHESS_TOKEN")
        if not tok:
            raise RuntimeError("LICHESS_TOKEN not found in environment. Set in systemd EnvironmentFile.")
        self.headers = {"Authorization": f"Bearer {tok}"}

    def get_account(self) -> Dict[str, Any]:
        try:
            r = requests.get(f"{LICHESS_BASE}/api/account", headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except RequestException as e:
            return {"_error": str(e)}

    def stream_events(self) -> Iterator[Dict[str, Any]]:
        r = requests.get(f"{LICHESS_BASE}/api/stream/event", headers=self.headers, stream=True, timeout=60)
        r.raise_for_status()
        return _iter_ndjson(r)

    def stream_game(self, game_id: str) -> Iterator[Dict[str, Any]]:
        r = requests.get(f"{LICHESS_BASE}/api/board/game/stream/{game_id}", headers=self.headers, stream=True, timeout=60)
        r.raise_for_status()
        return _iter_ndjson(r)

    def make_move(self, game_id: str, uci: str) -> Dict[str, Any]:
        try:
            r = requests.post(f"{LICHESS_BASE}/api/board/game/{game_id}/move/{uci}", headers=self.headers, timeout=15)
            if r.status_code == 200:
                return {"ok": True}
            return {"ok": False, "status": r.status_code, "text": r.text[:200]}
        except RequestException as e:
            return {"ok": False, "error": str(e)}
