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

from piDisplay import Display
from piSerial import BoardLink
from piEngine import EngineContext
from piGame import GameConfig, RuntimeState, select_mode, mode_dispatch, GoToModeSelect
import chess  # type: ignore

from systemd.daemon import notify

def main():
    display = Display()
    display.restart_server()
    display.wait_ready()

    # Notify systemd that service is ready
    notify("READY=1")

    # Splash + engine pre-warm before we open UART / ask for mode
    display.banner("SMARTCHESS", delay_s=1.2)   # splash
    display.send("Engine starting...")          # status line prior to mode select

    ctx = EngineContext()
    # Synchronous pre-warm: blocks until stockfish is ready with your current ensure()
    # If stockfish may not be installed, consider Option B below.
    ctx.ensure("/usr/games/stockfish")

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
