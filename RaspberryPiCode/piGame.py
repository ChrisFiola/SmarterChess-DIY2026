# -*- coding: utf-8 -*-
"""Backward-compatible re-export shim.

All game logic lives in game_flow.py.  This file keeps the name piGame.py
available for any external references (e.g. systemd ExecStart paths).
"""
from game_flow import *  # noqa
