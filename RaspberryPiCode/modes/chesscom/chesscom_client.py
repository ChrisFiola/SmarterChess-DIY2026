# -*- coding: utf-8 -*-
"""Chess.com client for SmartChess Connected Board.

Two layers:
  1. PublicAPI — read-only Published Data API (no auth, username only).
     Used for profile checks and game listing fallback.

  2. ConnectedBoardAPI — authenticated Connected Board API (requires
     partner credentials from Chess.com).  Supports move submission,
     game streaming, and all actions needed for direct board play.

     *** SKELETON — method bodies are stubs waiting for Chess.com to
     provide the Connected Board API documentation. Each stub raises
     NotConfiguredError so callers get a clear signal. ***

Env vars:
  CHESSCOM_USERNAME   — player username (required)
  CHESSCOM_TOKEN      — Connected Board API token (required for live play)
  CHESSCOM_BOARD_ID   — board device identifier (optional, assigned on registration)
"""
from __future__ import annotations

import io
import os
import re
from typing import Any, Dict, Iterator, List, Optional

import chess.pgn
import requests
from requests.exceptions import RequestException

# ── Public Data API (read-only, no auth) ──────────────────────────────────────

CHESSCOM_PUB_API = "https://api.chess.com/pub"

_PUB_HEADERS = {
    "User-Agent": "SmartChess-DIY/1.0 (connected-board)",
    "Accept": "application/json",
}


class PublicAPI:
    """Read-only wrapper around api.chess.com/pub."""

    def __init__(self, username: str):
        self.username = username.strip().lower()

    def get_profile(self) -> Dict[str, Any]:
        try:
            r = requests.get(
                f"{CHESSCOM_PUB_API}/player/{self.username}",
                headers=_PUB_HEADERS, timeout=10,
            )
            if r.status_code == 404:
                return {"_error": "User not found"}
            r.raise_for_status()
            return r.json()
        except RequestException as e:
            return {"_error": str(e)}

    def get_current_daily_games(self) -> List[Dict[str, Any]]:
        try:
            r = requests.get(
                f"{CHESSCOM_PUB_API}/player/{self.username}/games",
                headers=_PUB_HEADERS, timeout=15,
            )
            if r.status_code == 404:
                return []
            r.raise_for_status()
            return (r.json()).get("games") or []
        except RequestException as e:
            print(f"[CHESSCOM] Error fetching games: {e}", flush=True)
            return []


# ── Connected Board API (authenticated, move submission) ──────────────────────


class NotConfiguredError(Exception):
    """Raised when Connected Board API credentials are missing."""


class ConnectedBoardAPI:
    """Chess.com Connected Board API client.

    Placeholder implementation — every method that requires the Connected
    Board API is a clearly-marked stub.  Once Chess.com provides the API
    docs, fill in the real endpoints/auth here.  The rest of the codebase
    calls these methods and will work without further changes.

    Expected env vars (set once Chess.com provides credentials):
      CHESSCOM_TOKEN    — API bearer token or OAuth token
      CHESSCOM_BOARD_ID — unique board device ID (if required)
    """

    def __init__(self, username: str, token: Optional[str] = None):
        self.username = username.strip().lower()
        self.token = token or os.environ.get("CHESSCOM_TOKEN", "")
        self.board_id = os.environ.get("CHESSCOM_BOARD_ID", "")
        self._pub = PublicAPI(self.username)

        # Base URL — will be updated once Chess.com shares the real endpoint
        self._base = os.environ.get(
            "CHESSCOM_API_BASE", "https://api.chess.com/board/v1"
        )

    @property
    def is_configured(self) -> bool:
        """True when Connected Board credentials are present."""
        return bool(self.token)

    def _require_configured(self) -> None:
        if not self.is_configured:
            raise NotConfiguredError(
                "CHESSCOM_TOKEN not set. Apply for Connected Board access at "
                "chess.com and set the token in your environment."
            )

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "SmartChess-DIY/1.0 (connected-board)",
            "Accept": "application/json",
        }

    # ── Account / profile ─────────────────────────────────────────────────

    def get_account(self) -> Dict[str, Any]:
        """Get the authenticated user's account info.

        Falls back to the public API when the Connected Board API is not
        configured, so profile checks always work.
        """
        if not self.is_configured:
            return self._pub.get_profile()

        # STUB: replace with real Connected Board account endpoint
        # Expected response: {"username": "...", "id": "...", ...}
        self._require_configured()
        try:
            r = requests.get(
                f"{self._base}/account",
                headers=self._headers(), timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except RequestException as e:
            return {"_error": str(e)}

    # ── Game listing ──────────────────────────────────────────────────────

    def get_ongoing_games(self) -> List[Dict[str, Any]]:
        """Fetch ongoing games (daily / correspondence).

        When the Connected Board API is not configured, falls back to the
        public Published Data API which provides the same game list.
        """
        if not self.is_configured:
            return self._pub.get_current_daily_games()

        # STUB: replace with real Connected Board ongoing-games endpoint
        # Expected: list of game dicts with url, fen, pgn, turn, white, black, etc.
        self._require_configured()
        try:
            r = requests.get(
                f"{self._base}/games/ongoing",
                headers=self._headers(), timeout=15,
            )
            r.raise_for_status()
            return (r.json()).get("games") or []
        except RequestException as e:
            print(f"[CHESSCOM BOARD] Error fetching games: {e}", flush=True)
            return []

    # ── Game streaming ────────────────────────────────────────────────────

    def stream_game(self, game_id: str) -> Iterator[Dict[str, Any]]:
        """Stream real-time updates for a game.

        STUB — replace with real Connected Board game stream endpoint.
        Expected: NDJSON or SSE stream yielding move/state events.
        """
        self._require_configured()
        # Placeholder — will be replaced with real streaming endpoint
        r = requests.get(
            f"{self._base}/games/{game_id}/stream",
            headers=self._headers(), stream=True, timeout=60,
        )
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                yield {}
                continue
            try:
                import json
                yield json.loads(line)
            except Exception:
                continue

    # ── Move submission ───────────────────────────────────────────────────

    def make_move(self, game_id: str, uci: str) -> Dict[str, Any]:
        """Submit a move from the physical board to Chess.com.

        STUB — replace with real Connected Board move endpoint.
        Expected request:  POST /games/{id}/move  body: {"move": "e2e4"}
        Expected response: {"ok": true} or error.
        """
        self._require_configured()
        try:
            r = requests.post(
                f"{self._base}/games/{game_id}/move",
                headers=self._headers(),
                json={"move": uci},
                timeout=15,
            )
            if r.status_code == 200:
                return {"ok": True}
            return {"ok": False, "status": r.status_code, "text": r.text[:200]}
        except RequestException as e:
            return {"ok": False, "error": str(e)}

    # ── Resign / draw ─────────────────────────────────────────────────────

    def resign_game(self, game_id: str) -> Dict[str, Any]:
        """Resign a running game.

        STUB — replace with real endpoint.
        """
        self._require_configured()
        try:
            r = requests.post(
                f"{self._base}/games/{game_id}/resign",
                headers=self._headers(), timeout=15,
            )
            if r.status_code == 200:
                return {"ok": True}
            return {"ok": False, "status": r.status_code, "text": r.text[:200]}
        except RequestException as e:
            return {"ok": False, "error": str(e)}

    def offer_draw(self, game_id: str) -> Dict[str, Any]:
        """Offer a draw.

        STUB — replace with real endpoint.
        """
        self._require_configured()
        try:
            r = requests.post(
                f"{self._base}/games/{game_id}/draw",
                headers=self._headers(), timeout=15,
            )
            if r.status_code == 200:
                return {"ok": True}
            return {"ok": False, "status": r.status_code, "text": r.text[:200]}
        except RequestException as e:
            return {"ok": False, "error": str(e)}

    # ── Board registration ────────────────────────────────────────────────

    def register_board(self) -> Dict[str, Any]:
        """Register this physical board with Chess.com.

        STUB — replace once API docs are available.
        Expected: assigns a board_id that Chess.com uses to identify
        this device.
        """
        self._require_configured()
        try:
            r = requests.post(
                f"{self._base}/boards/register",
                headers=self._headers(),
                json={"device": "SmartChess-DIY"},
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except RequestException as e:
            return {"_error": str(e)}


# ── Shared helpers (work with both public and board API game dicts) ────────────


def parse_pgn_moves(pgn: str) -> List[str]:
    """Extract UCI moves from a Chess.com PGN string."""
    if not pgn or not pgn.strip():
        return []
    try:
        game = chess.pgn.read_game(io.StringIO(pgn))
        if not game:
            return []
        return [move.uci() for move in game.mainline_moves()]
    except Exception:
        return []


def extract_opponent(game: Dict[str, Any], username: str) -> str:
    username = username.lower()
    white_url = game.get("white") or ""
    black_url = game.get("black") or ""
    white_name = white_url.rstrip("/").split("/")[-1].lower()
    black_name = black_url.rstrip("/").split("/")[-1].lower()
    return black_name if white_name == username else white_name


def extract_player_color(game: Dict[str, Any], username: str) -> str:
    username = username.lower()
    white_url = game.get("white") or ""
    white_name = white_url.rstrip("/").split("/")[-1].lower()
    return "white" if white_name == username else "black"


def is_my_turn(game: Dict[str, Any], username: str) -> bool:
    turn = (game.get("turn") or "").lower()
    return turn == extract_player_color(game, username)


def extract_game_id(game: Dict[str, Any]) -> Optional[str]:
    """Extract the game ID from a game dict (URL-based or direct)."""
    # Direct id field
    gid = game.get("game_id") or game.get("id")
    if gid:
        return str(gid)
    # Parse from URL: https://www.chess.com/game/daily/123456
    url = game.get("url") or ""
    m = re.search(r"/game/(?:daily|live)/(\d+)", url)
    return m.group(1) if m else None
