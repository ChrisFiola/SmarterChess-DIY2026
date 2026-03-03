#!/home/king/chessenv/bin/python
# -*- coding: utf-8 -*-
"""
SmarterChess — Modular Main Entrypoint (2026)
Single-responsibility modules:
  - mc_display: Display abstraction
  - mc_serial:  BoardLink (UART)
  - mc_engine:  EngineContext + bestmove/hint helpers
  - mc_game:    GameConfig/RuntimeState + setup + unified play loop

Behavior parity with single-file version:
  - UART protocol preserved
  - No pre-OK legality/capture preview (Pico side)
  - Legality validated after OK on Pi
  - Typing previews shown non-blocking and blocking
"""
import time
import traceback

# Allow importing sibling packages (RaspberryPiCode/app) when running from
# RaspberryPiCode/main under systemd.
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from piDisplay import Display
from piSerial import BoardLink
from piEngine import EngineContext
from piGame import GameConfig, RuntimeState, select_mode, mode_dispatch, GoToModeSelect
import chess  # type: ignore

# --- Mode token normalization ---
# Pico can send tokens like "btn_mode_puzzles" while the Pi dispatch expects "puzzles" or similar.
MODE_ALIASES = {
    "btn_mode_vs": "vs_computer",
    "btn_mode_stockfish": "vs_computer",
    "btn_mode_ai": "vs_computer",
    "btn_mode_lichess": "lichess",
    "btn_mode_lichess_online": "lichess",
    "btn_mode_local": "local_2p",
    "btn_mode_local_2p": "local_2p",
    "btn_mode_puzzles": "puzzles",
    # Sometimes Pico sends simple button ids
    "btn_1": "vs_computer",
    "btn_2": "lichess",
    "btn_3": "local_2p",
    "btn_4": "puzzles",
    # Sometimes it sends plain words
    "vs": "vs_computer",
    "stockfish": "vs_computer",
    "computer": "vs_computer",
    "lichess": "lichess",
    "online": "lichess",
    "local": "local_2p",
    "2p": "local_2p",
    "puzzles": "puzzles",
}


def normalize_mode_token(token: str) -> str:
    t = (token or "").strip()
    return MODE_ALIASES.get(t, t)


def main():
    display = Display()
    display.restart_server()
    display.wait_ready()

    # Splash + engine pre-warm before we open UART / ask for mode
    display.banner("SMARTCHESS", delay_s=1.2)  # splash
    display.send("Engine starting...")  # status line prior to mode select

    ctx = EngineContext()
    # Synchronous pre-warm: blocks until stockfish is ready with your current ensure()
    # If stockfish may not be installed, consider Option B below.
    ctx.ensure("/usr/games/stockfish")

    link = BoardLink()
    cfg = GameConfig()
    state = RuntimeState(board=chess.Board(), mode="stockfish")

    while True:
        try:
            forced = (os.environ.get("SMARTCHESS_FORCE_MODE") or "").strip().lower()
            if forced:
                # Useful for testing modes not yet selectable from the Pico UI.
                state.mode = normalize_mode_token(forced)
                display.send(f"Mode forced:\n{forced}")
                time.sleep(1.0)
            else:
                selected = select_mode(link, display, state)
                selected = normalize_mode_token(selected)
                state.mode = selected
            mode_dispatch(link, display, ctx, state, cfg)
        except GoToModeSelect:
            state.board = chess.Board()
            display.send("SMARTCHESS")
            time.sleep(0.4)
            continue
        except KeyboardInterrupt:
            break
        except Exception as e:
            # Any unexpected exception should not leave the Pico UI in a half-state.
            traceback.print_exc()

            # Force Pico back to mode select UI (best-effort).
            try:
                link.sendtoboard("ChooseMode")
            except Exception:
                pass

            # Show error briefly (or until OK) then return to menu.
            try:
                from piGame import wait_ok_or_timeout  # local import to avoid cycles

                short = (str(e) or e.__class__.__name__)[:18]
                display.send(f"ERROR\n{short}\nOK=menu")
                wait_ok_or_timeout(link, timeout_s=2.0)
            except Exception:
                # If anything goes wrong while showing the error, just pause briefly.
                time.sleep(2.0)

            # Reset state and go back to mode select.
            state.board = chess.Board()
            display.send("SMARTCHESS")
            time.sleep(0.4)
            continue

    try:
        link.close()
    except Exception:
        pass
    try:
        ctx.quit()
    except Exception:
        pass


if __name__ == "__main__":
    main()
