#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMARTCHESS — Modular Entrypoint (Protocol v2)
- Orchestrator-based, Lichess-ready
"""
import time
import traceback

from piDisplay import Display
from piSerial import BoardLink
from piEngine import EngineContext
from piGame import GameConfig, RuntimeState, select_mode, mode_dispatch, GoToModeSelect
import chess  # type: ignore


def main():
    display = Display()
    display.restart_server()
    display.wait_ready()

    display.banner("SMARTCHESS", delay_s=1.0)
    display.send("Engine starting...")

    ctx = EngineContext(); ctx.ensure("/usr/games/stockfish")

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
            time.sleep(1.0)
            continue
        except KeyboardInterrupt:
            break
        except Exception:
            traceback.print_exc(); time.sleep(1); continue

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
