# -*- coding: utf-8 -*-
"""Minimal Lichess Board API client (manual-start).

Requires env var LICHESS_TOKEN with scope: board:play
Uses NDJSON streaming endpoints:
  - GET  https://lichess.org/api/stream/event
  - GET  https://lichess.org/api/board/game/stream/{gameId}
  - POST https://lichess.org/api/board/game/{gameId}/move/{uci}
  - GET  https://lichess.org/api/account
"""

from __future__ import annotations

import os
import json
from typing import Iterator, Optional, Dict, Any

try:
    import requests  # type: ignore
except Exception as e:  # pragma: no cover
    requests = None  # type: ignore

LICHESS_BASE = "https://lichess.org"


class LichessClient:
    def __init__(self, token: Optional[str] = None):
        tok = token or os.environ.get("LICHESS_TOKEN")
        if not tok:
            raise RuntimeError("LICHESS_TOKEN not found. Add it to EnvironmentFile or export it.")
        self.token = tok
        self.headers = {"Authorization": f"Bearer {self.token}"}

        if requests is None:
            raise RuntimeError("Python package 'requests' is required for Lichess online mode.")

    def get_account(self) -> Dict[str, Any]:
        r = requests.get(f"{LICHESS_BASE}/api/account", headers=self.headers, timeout=10)
        r.raise_for_status()
        return r.json()

    def stream_events(self) -> Iterator[Dict[str, Any]]:
        """Stream account events as NDJSON dicts."""
        r = requests.get(f"{LICHESS_BASE}/api/stream/event", headers=self.headers, stream=True, timeout=60)
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue

    def stream_game(self, game_id: str) -> Iterator[Dict[str, Any]]:
        r = requests.get(f"{LICHESS_BASE}/api/board/game/stream/{game_id}", headers=self.headers, stream=True, timeout=60)
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue

    def make_move(self, game_id: str, uci: str) -> bool:
        # Lichess expects UCI without spaces. Promotion like e7e8q.
        u = uci.strip()
        r = requests.post(f"{LICHESS_BASE}/api/board/game/{game_id}/move/{u}", headers=self.headers, timeout=10)
        if r.status_code == 200:
            return True
        return False
