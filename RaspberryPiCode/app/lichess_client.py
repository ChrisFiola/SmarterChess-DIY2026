# -*- coding: utf-8 -*-
"""Minimal Lichess Board API client (manual-start workflow).

Uses environment variable LICHESS_TOKEN for auth.
Endpoints (Board API, NDJSON streams):
  - GET  https://lichess.org/api/stream/event
  - GET  https://lichess.org/api/board/game/stream/{gameId}
  - POST https://lichess.org/api/board/game/{gameId}/move/{uci}

Notes:
  - This is designed for *manual start*: you start a game on Lichess (or accept one),
    and this client attaches when it receives a gameStart event.
  - No bot account required (board:play scope).
"""

from __future__ import annotations

import os
import json
import time
from typing import Dict, Iterator, Optional, Any

import requests  # type: ignore


class LichessClient:
    BASE = "https://lichess.org"

    def __init__(self, token_env: str = "LICHESS_TOKEN"):
        token = os.environ.get(token_env)
        if not token:
            raise RuntimeError(f"{token_env} not found in environment")
        self._session = requests.Session()
        self._headers = {"Authorization": f"Bearer {token}"}

    def _stream_ndjson(self, url: str, *, timeout_s: float = 10.0) -> Iterator[Dict[str, Any]]:
        """Yield JSON objects from an NDJSON streaming endpoint, auto-reconnecting."""
        backoff = 1.0
        while True:
            try:
                with self._session.get(url, headers=self._headers, stream=True, timeout=(timeout_s, None)) as r:
                    r.raise_for_status()
                    backoff = 1.0
                    for line in r.iter_lines(decode_unicode=True):
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except Exception:
                            # Ignore malformed chunks
                            continue
            except Exception:
                time.sleep(backoff)
                backoff = min(30.0, backoff * 1.8)

    def stream_events(self) -> Iterator[Dict[str, Any]]:
        return self._stream_ndjson(f"{self.BASE}/api/stream/event")

    def stream_game(self, game_id: str) -> Iterator[Dict[str, Any]]:
        return self._stream_ndjson(f"{self.BASE}/api/board/game/stream/{game_id}")


def get_account(self) -> Dict[str, Any]:
    """Return /api/account JSON (id, username, etc.)."""
    url = f"{self.BASE}/api/account"
    r = self._session.get(url, headers=self._headers, timeout=10)
    r.raise_for_status()
    return r.json()

    def make_move(self, game_id: str, uci: str) -> bool:
        url = f"{self.BASE}/api/board/game/{game_id}/move/{uci}"
        try:
            r = self._session.post(url, headers=self._headers, timeout=10)
            return 200 <= r.status_code < 300
        except Exception:
            return False

    def abort(self, game_id: str) -> bool:
        url = f"{self.BASE}/api/board/game/{game_id}/abort"
        try:
            r = self._session.post(url, headers=self._headers, timeout=10)
            return 200 <= r.status_code < 300
        except Exception:
            return False
