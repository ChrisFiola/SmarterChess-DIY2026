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

from mc_display import Display
from mc_serial import BoardLink
from mc_engine import EngineContext
from mc_game import GameConfig, RuntimeState, select_mode, mode_dispatch, GoToModeSelect
import chess  # type: ignore


def main():
    display = Display()
    display.restart_server()
    display.wait_ready()

    ctx = EngineContext()
    link = BoardLink()
    cfg = GameConfig()
    state = RuntimeState(board=chess.Board(), mode="stockfish")

    while True:
        try:
            selected = select_mode(link, display, state)
            state.mode = selected
            mode_dispatch(link, display, ctx, state, cfg)
        except GoToModeSelect:
            state.board = chess.Board()
            display.send("SMARTCHESS")
            time.sleep(2.5)
            continue
        except KeyboardInterrupt:
            break
        except Exception:
            traceback.print_exc()
            time.sleep(1)
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
