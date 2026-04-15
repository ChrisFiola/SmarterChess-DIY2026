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
import time
import traceback

import chess

from core.boardlink import BoardLink
from core.engine import EngineContext
from core.wifi_ap import ensure_wifi
from core.game_flow import (
    GameConfig,
    GameState,
    ReturnToMenu,
    wait_for_mode_selection,
    run_selected_mode,
)
from core.protocol import IGNORED_MSGS
from screen.display import Display


def main():
    display = Display()
    # Route touch events (ILI9341/XPT2046) into the UART read path so all
    # game-flow code sees touch the same way it saw Pico button presses.
    display.show_header_panel("SMARTCHESS")

    ensure_wifi(display)
    link = BoardLink()
    ctx = EngineContext()
    cfg = GameConfig()
    state = GameState(board=chess.Board(), mode="stockfish")
    link.set_touch_queue(display.touch_queue)
    # Signal Pico Pi is ready — Pico _run_startup_sequence() waits for
    # "heyArduinoChooseMode" before exiting its loading screen.
    link.send_to_board("ChooseMode")

    try:
        while True:
            try:
                forced = (os.environ.get("SMARTCHESS_FORCE_MODE") or "").strip().lower()
                if forced:
                    state.mode = forced
                    display.show_header_panel("SMARTCHESS", "Mode forced:", forced)
                    time.sleep(0.5)
                else:
                    state.mode = wait_for_mode_selection(link, display, state, cfg)
                    print(f"[MODE SELECT] selected={state.mode!r}", flush=True)

                run_selected_mode(link, display, ctx, state, cfg)

            except ReturnToMenu:
                try:
                    link.send_to_board("GameEnd")
                except Exception:
                    pass
                state.board = chess.Board()
                display.show_header_panel("SMARTCHESS")
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
                    display.show_header_panel("Error", short, footer="OK=Menu")
                    t0 = time.monotonic()
                    while time.monotonic() - t0 < 2.0:
                        msg = link.read_from_board()
                        if msg and msg.strip().lower() in IGNORED_MSGS:
                            break
                except Exception:
                    time.sleep(1.0)

                state.board = chess.Board()
                display.show_header_panel("SMARTCHESS")

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
