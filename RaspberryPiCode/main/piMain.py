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


def main():
    display = Display()
    display.restart_server()
    display.wait_ready()

    # Splash + engine pre-warm before we open UART / ask for mode
    display.banner("SMARTCHESS\nLoading...")  # splash
    # display.send("Engine starting...")          # status line prior to mode select

    ctx = EngineContext()
    # ctx.ensure("/usr/games/stockfish")

    from piGame import (
        GameConfig,
        RuntimeState,
        select_mode,
        mode_dispatch,
        GoToModeSelect,
        wait_ok_or_timeout,
    )
    import chess  # type: ignore

    link = BoardLink()
    cfg = GameConfig()
    state = RuntimeState(board=chess.Board(), mode="stockfish")

    try:
        while True:
            try:
                forced = (os.environ.get("SMARTCHESS_FORCE_MODE") or "").strip().lower()
                if forced:
                    state.mode = forced
                    display.send(f"Mode forced:\n{forced}")
                    time.sleep(1.0)
                else:
                    selected = select_mode(link, display, state)
                    state.mode = selected
                    print(f"[MODE SELECT] selected={selected!r}", flush=True)

                mode_dispatch(link, display, ctx, state, cfg)

            except GoToModeSelect:
                state.board = chess.Board()
                display.send("SMARTCHESS")
                time.sleep(0.4)
                continue

            except KeyboardInterrupt:
                break

            except Exception as e:
                traceback.print_exc()

                # Force Pico back to mode select UI (best-effort).
                try:
                    link.sendtoboard("ChooseMode")
                except Exception:
                    pass

                # Show error briefly (or until OK) then return to menu.
                try:

                    short = (str(e) or e.__class__.__name__)[:18]
                    display.send(f"ERROR\n{short}\nOK=menu")
                    wait_ok_or_timeout(link, timeout_s=2.0)
                except Exception:
                    time.sleep(2.0)

                state.board = chess.Board()
                display.send("SMARTCHESS")
                time.sleep(0.4)
                continue

    finally:
        try:
            display.close()
        except Exception:
            pass

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
