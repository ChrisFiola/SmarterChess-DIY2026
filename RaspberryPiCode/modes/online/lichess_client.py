# -*- coding: utf-8 -*-
"""Lichess API client for SmartChess (manual-start online).

Token via env var LICHESS_TOKEN.
"""
from __future__ import annotations

import os, json, re

import unicodedata


def _slugify_angle(a: str) -> str:
    """Convert human labels into the 'training' slug used by Lichess URLs.

    Examples:
      "Caro-Kann Defense" -> "Caro-Kann_Defense"
      "King's Indian Defense" -> "Kings_Indian_Defense"
      "Grünfeld Defense" -> "Grunfeld_Defense"
    """
    s = (a or "").strip()
    if not s:
        return ""
    # Remove accents/diacritics
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    # Drop apostrophes
    s = s.replace("’", "").replace("'", "")
    # Spaces -> underscores
    s = re.sub(r"\s+", "_", s)
    return s


from typing import Dict, Any, Iterator, Optional
import requests
from requests.exceptions import RequestException

LICHESS_BASE = "https://lichess.org"


def _iter_ndjson(resp) -> Iterator[Dict[str, Any]]:
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            # Lichess sends empty keepalive lines every ~1 s. Yield an empty
            # dict so callers can poll the serial port between real events
            # without blocking for the full stream timeout.
            yield {}
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue


def import_game_pgn(pgn: str, timeout_s: float = 8.0) -> Dict[str, Any]:
    """Import a finished game PGN and return the Lichess API response.

    Uses the configured account token when available, but also works
    anonymously so local games can still get a short Lichess URL.
    """
    pgn = (pgn or "").strip()
    if not pgn or pgn == "*":
        return {"_error": "No PGN to import"}

    headers = {"Accept": "application/json"}
    tok = os.environ.get("LICHESS_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"

    try:
        r = requests.post(
            f"{LICHESS_BASE}/api/import",
            headers=headers,
            data={"pgn": pgn},
            timeout=timeout_s,
        )
        if r.status_code not in (200, 201):
            return {"_error": f"HTTP {r.status_code}: {r.text[:120]}"}
        try:
            return r.json()
        except ValueError:
            return {"_error": "Invalid JSON from Lichess import"}
    except RequestException as e:
        return {"_error": str(e)}


class LichessClient:
    def __init__(self, token: Optional[str] = None):
        tok = token or os.environ.get("LICHESS_TOKEN")
        if not tok:
            raise RuntimeError(
                "LICHESS_TOKEN not found in environment. Set in systemd EnvironmentFile."
            )
        self.headers = {"Authorization": f"Bearer {tok}"}

    def get_account(self) -> Dict[str, Any]:
        try:
            r = requests.get(
                f"{LICHESS_BASE}/api/account", headers=self.headers, timeout=10
            )
            r.raise_for_status()
            return r.json()
        except RequestException as e:
            return {"_error": str(e)}

    def stream_events(self, timeout_s: float = 60) -> Iterator[Dict[str, Any]]:
        r = requests.get(
            f"{LICHESS_BASE}/api/stream/event",
            headers=self.headers,
            stream=True,
            timeout=timeout_s,
        )
        r.raise_for_status()
        return _iter_ndjson(r)

    def stream_game(self, game_id: str) -> Iterator[Dict[str, Any]]:
        r = requests.get(
            f"{LICHESS_BASE}/api/board/game/stream/{game_id}",
            headers=self.headers,
            stream=True,
            timeout=60,
        )
        r.raise_for_status()
        return _iter_ndjson(r)

    def make_move(self, game_id: str, uci: str) -> Dict[str, Any]:
        try:
            r = requests.post(
                f"{LICHESS_BASE}/api/board/game/{game_id}/move/{uci}",
                headers=self.headers,
                timeout=15,
            )
            if r.status_code == 200:
                return {"ok": True}
            return {"ok": False, "status": r.status_code, "text": r.text[:200]}
        except RequestException as e:
            return {"ok": False, "error": str(e)}

    def resign_game(self, game_id: str) -> Dict[str, Any]:
        """Resign a running board game."""
        try:
            r = requests.post(
                f"{LICHESS_BASE}/api/board/game/{game_id}/resign",
                headers=self.headers,
                timeout=15,
            )
            if r.status_code == 200:
                return {"ok": True}
            return {"ok": False, "status": r.status_code, "text": r.text[:200]}
        except RequestException as e:
            return {"ok": False, "error": str(e)}

    def offer_draw(self, game_id: str) -> Dict[str, Any]:
        """Offer a draw in a running board game."""
        try:
            r = requests.post(
                f"{LICHESS_BASE}/api/board/game/{game_id}/draw/yes",
                headers=self.headers,
                timeout=15,
            )
            if r.status_code == 200:
                return {"ok": True}
            return {"ok": False, "status": r.status_code, "text": r.text[:200]}
        except RequestException as e:
            return {"ok": False, "error": str(e)}

    def decline_draw(self, game_id: str) -> Dict[str, Any]:
        """Decline an incoming draw offer in a running board game."""
        try:
            r = requests.post(
                f"{LICHESS_BASE}/api/board/game/{game_id}/draw/no",
                headers=self.headers,
                timeout=15,
            )
            if r.status_code == 200:
                return {"ok": True}
            return {"ok": False, "status": r.status_code, "text": r.text[:200]}
        except RequestException as e:
            return {"ok": False, "error": str(e)}

    # -------------------- Online game creation --------------------

    def get_incoming_challenges(self) -> list:
        """Fetch pending incoming challenges. Returns a list of challenge dicts."""
        try:
            r = requests.get(
                f"{LICHESS_BASE}/api/challenge",
                headers=self.headers,
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            return data.get("in") or []
        except RequestException as e:
            return [{"_error": str(e)}]

    def accept_challenge(self, challenge_id: str) -> Dict[str, Any]:
        """Accept an incoming challenge by ID."""
        try:
            r = requests.post(
                f"{LICHESS_BASE}/api/challenge/{challenge_id}/accept",
                headers=self.headers,
                timeout=10,
            )
            if r.status_code == 200:
                return {"ok": True}
            return {"ok": False, "status": r.status_code}
        except RequestException as e:
            return {"ok": False, "_error": str(e)}

    def decline_challenge(self, challenge_id: str) -> None:
        """Decline an incoming challenge by ID."""
        try:
            requests.post(
                f"{LICHESS_BASE}/api/challenge/{challenge_id}/decline",
                headers=self.headers,
                timeout=10,
            )
        except RequestException:
            pass

    def get_ongoing_games(self, timeout_s: float = 10) -> Dict[str, Any]:
        """Fetch the list of currently active games for this account."""
        try:
            r = requests.get(
                f"{LICHESS_BASE}/api/account/playing",
                headers=self.headers,
                timeout=timeout_s,
            )
            r.raise_for_status()
            return r.json()
        except RequestException as e:
            return {"_error": str(e)}

    def get_following(self, max_count: int = 30) -> list:
        """Fetch followed users (friends list) as a list of account dicts."""
        try:
            r = requests.get(
                f"{LICHESS_BASE}/api/rel/following",
                headers=self.headers,
                stream=True,
                timeout=10,
            )
            r.raise_for_status()
            users = []
            for obj in _iter_ndjson(r):
                if obj and isinstance(obj, dict) and obj.get("id"):
                    users.append(obj)
                    if len(users) >= max_count:
                        break
            return users
        except RequestException:
            return []

    def challenge_user(
        self,
        username: str,
        limit_seconds: int,
        increment_seconds: int,
        rated: bool = False,
        color: str = "random",
    ) -> Dict[str, Any]:
        """Challenge a specific Lichess user to a real-time game."""
        data = {
            "rated": "true" if rated else "false",
            "clock.limit": str(limit_seconds),
            "clock.increment": str(increment_seconds),
            "color": color,
        }

        try:
            r = requests.post(
                f"{LICHESS_BASE}/api/challenge/{username}",
                headers=self.headers,
                data=data,
                timeout=15,
            )
            if r.status_code in (200, 201):
                return r.json()
            return {"_error": f"HTTP {r.status_code}: {r.text[:120]}"}
        except RequestException as e:
            return {"_error": str(e)}

    def challenge_user_correspondence(
        self,
        username: str,
        days: int = 3,
        rated: bool = False,
        color: str = "random",
    ) -> Dict[str, Any]:
        """Challenge a specific Lichess user to a correspondence game."""
        if days <= 0:
            return {"_error": "Days per turn must be > 0"}

        data = {
            "rated": "true" if rated else "false",
            "days": str(days),
            "color": color,
        }

        try:
            r = requests.post(
                f"{LICHESS_BASE}/api/challenge/{username}",
                headers=self.headers,
                data=data,
                timeout=15,
            )
            if r.status_code in (200, 201):
                return r.json()
            return {"_error": f"HTTP {r.status_code}: {r.text[:120]}"}
        except RequestException as e:
            return {"_error": str(e)}

    def create_seek(
        self,
        time_minutes: int,
        increment_seconds: int,
        rated: bool = False,
        color: str = "random",
    ) -> None:
        """Create an open real-time seek (quick pairing).

        Blocks until matched or the connection is closed. Intended to run in a
        background thread — the actual game ID arrives via the event stream.
        """
        try:
            r = requests.post(
                f"{LICHESS_BASE}/api/board/seek",
                headers=self.headers,
                data={
                    "rated": "true" if rated else "false",
                    "time": str(time_minutes),
                    "increment": str(increment_seconds),
                    "color": color,
                },
                stream=True,
                timeout=300,  # wait up to 5 min for pairing
            )
            r.raise_for_status()
            # Drain until matched (stream closes when game starts)
            for _ in r.iter_content(chunk_size=32):
                pass
        except Exception:
            pass

    def create_open_challenge(
        self,
        days: int = 3,
        rated: bool = False,
    ) -> Dict[str, Any]:
        """Create an open correspondence challenge (anyone can accept).

        Returns the challenge dict including 'challenge.url' for sharing.
        """
        try:
            r = requests.post(
                f"{LICHESS_BASE}/api/challenge/open",
                headers=self.headers,
                data={
                    "rated": "true" if rated else "false",
                    "days": str(days),
                },
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except RequestException as e:
            return {"_error": str(e)}

    # -------------------- Puzzles --------------------

    def get_daily_puzzle(self) -> Dict[str, Any]:
        """Fetch the current daily puzzle."""
        try:
            r = requests.get(
                f"{LICHESS_BASE}/api/puzzle/daily", headers=self.headers, timeout=15
            )
            r.raise_for_status()
            return r.json()
        except RequestException as e:
            return {"_error": str(e)}

    def get_puzzle(self, puzzle_id: str) -> Dict[str, Any]:
        """Fetch a puzzle by its ID."""
        try:
            r = requests.get(
                f"{LICHESS_BASE}/api/puzzle/{puzzle_id}",
                headers=self.headers,
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except RequestException as e:
            return {"_error": str(e)}

    def get_study_pgn(self, study_id: str, timeout_s: float = 30) -> str:
        """Fetch all chapters of a Lichess study as combined PGN text.

        Returns an empty string on error so callers can check truthiness.
        """
        try:
            r = requests.get(
                f"{LICHESS_BASE}/api/study/{study_id}.pgn",
                headers={**self.headers, "Accept": "application/x-chess-pgn"},
                timeout=timeout_s,
            )
            r.raise_for_status()
            return r.text or ""
        except RequestException:
            return ""

    def get_next_puzzle(
        self,
        *,
        angle: Optional[str] = None,
        theme: Optional[str] = None,
        difficulty: Optional[str] = None,
        nonce: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch the next puzzle.

        Lichess supports filtering the next puzzle by an "angle".
        Angle can be a single theme/motif (from lichess.org/training/themes)
        or an opening name (from lichess.org/training/openings).

        Back-compat: older code in this repo used `theme=`; we still accept it.
        """
        try:
            params: Dict[str, Any] = {}
            # Prefer the newer `angle` param; fall back to `theme` for legacy.
            a = (_slugify_angle(angle) or _slugify_angle(theme) or "").strip()
            if a:
                params["angle"] = a
            d = (difficulty or "").strip()
            if d:
                params["difficulty"] = d
            n = (nonce or "").strip()
            if n:
                params["r"] = n

            r = requests.get(
                f"{LICHESS_BASE}/api/puzzle/next",
                headers={
                    **self.headers,
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
                params=params,
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except RequestException as e:
            return {"_error": str(e)}
