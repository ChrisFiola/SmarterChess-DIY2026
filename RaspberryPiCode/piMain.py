#!/home/king/chessenv/bin/python
# -*- coding: utf-8 -*-
"""
SmarterChess — Entry point.

Startup sequence:
  1. Splash screen on LCD
  2. Wait for mode selection from Pico (vs computer / online / local / puzzle)
  3. Configure and run the selected mode
  4. On ReturnToMenu, restart from step 2
"""
import os
import sys
import time
import traceback

from display import Display
from boardlink import BoardLink
from engine import EngineContext
from game_flow import (
    GameConfig,
    GameState,
    wait_for_mode_selection,
    run_selected_mode,
    ReturnToMenu,
    OK_MSGS,
    IGNORED_MSGS,
)
import chess


def main():
    display = Display()
    display.restart_server()
    display.banner("SMARTCHESS")
    display.wait_ready()

    ctx = EngineContext()
    link = BoardLink()
    cfg = GameConfig()
    state = GameState(board=chess.Board(), mode="stockfish")

    try:
        while True:
            try:
                forced = (os.environ.get("SMARTCHESS_FORCE_MODE") or "").strip().lower()
                if forced:
                    state.mode = forced
                    display.send(f"Mode forced:\n{forced}")
                    time.sleep(1.0)
                else:
                    state.mode = wait_for_mode_selection(link, display, state)
                    print(f"[MODE SELECT] selected={state.mode!r}", flush=True)

                run_selected_mode(link, display, ctx, state, cfg)

            except ReturnToMenu:
                state.board = chess.Board()
                display.send("SMARTCHESS")
                time.sleep(0.4)
                continue

            except KeyboardInterrupt:
                break

            except Exception as e:
                traceback.print_exc()
                try:
                    link.send_to_board("ChooseMode")
                except Exception:
                    pass
                try:
                    short = (str(e) or e.__class__.__name__)[:18]
                    display.send(f"ERROR\n{short}\nOK=menu")
                    t0 = time.monotonic()
                    while time.monotonic() - t0 < 2.0:
                        msg = link.read_from_board()
                        if msg and msg.strip().lower() in IGNORED_MSGS:
                            break
                except Exception:
                    time.sleep(2.0)

                state.board = chess.Board()
                display.send("SMARTCHESS")
                time.sleep(0.4)

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
