# -*- coding: utf-8 -*-
"""Helpers for parsing Lichess Board API stream payloads."""
from __future__ import annotations
from typing import List, Dict, Any, Tuple, Optional

def extract_moves(payload: Dict[str, Any]) -> List[str]:
    if not payload:
        return []
    if payload.get("type") == "gameFull":
        st = payload.get("state") or {}
        s = st.get("moves") or ""
        return [m for m in s.split() if m]
    if payload.get("type") == "gameState":
        s = payload.get("moves") or ""
        return [m for m in s.split() if m]
    return []

def extract_players(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    if (payload or {}).get("type") != "gameFull":
        return (None, None)
    w = (payload.get("white") or {})
    b = (payload.get("black") or {})
    return (w.get("name") or w.get("id"), b.get("name") or b.get("id"))

def extract_status(payload: Dict[str, Any]) -> Optional[str]:
    if (payload or {}).get("type") == "gameState":
        return payload.get("status")
    if (payload or {}).get("type") == "gameFull":
        st = payload.get("state") or {}
        return st.get("status")
    return None

def extract_winner(payload: Dict[str, Any]) -> Optional[str]:
    if (payload or {}).get("type") == "gameState":
        return payload.get("winner")
    if (payload or {}).get("type") == "gameFull":
        st = payload.get("state") or {}
        return st.get("winner")
    return None
