# SmarterChess - Code Reference

This is the current high-level map of the project as it exists today.
It is intended to answer:

- where a feature lives
- which shared helpers a mode reuses
- how game-over, setup, and menu flows currently work

---

## File Tree

```text
RaspberryPiCode/
|
|- piMain.py
|- push_pico_main.py
|
|- core/
|  |- boardlink.py
|  |- engine.py
|  |- game_flow.py
|  |- net_utils.py
|  |- protocol.py
|  `- wifi_ap.py
|
|- screen/
|  |- display.py
|  |- display_server.py
|  |- lcd_pipe.py
|  `- qrgen.py
|
`- modes/
   |- vs_computer/
   |  |- game_controller.py
   |  `- stockfish_opponent.py
   |- online/
   |  |- lichess_client.py
   |  |- lichess_game.py
   |  `- online_controller.py
   |- puzzles/
   |  |- puzzle_controller.py
   |  `- puzzle_ids.txt
   `- studies/
      |- study_controller.py
      `- studies.txt
```

---

## Startup Flow

```text
piMain.main()
  -> Display.restart_server()
  -> Display.banner("SMARTCHESS")
  -> ensure_wifi(display)
  -> create BoardLink / EngineContext / GameConfig / GameState
  -> wait_for_mode_selection(...)
  -> run_selected_mode(...)
  -> on ReturnToMenu:
       send GameEnd
       reset board state
       show SMARTCHESS
       return to mode menu
```

`piMain.py` is the only top-level loop. Modes return by raising `ReturnToMenu`.

---

## Core Modules

### `core/boardlink.py`

UART wrapper between the Pi and Pico.

Main responsibilities:

- send Pi-to-Pico payloads with the `heyArduino` prefix
- read Pico-to-Pi payloads after the `heypi` prefix is removed
- expose blocking and non-blocking reads
- clear stale serial input when a flow changes state

### `core/engine.py`

Thin Stockfish process wrapper used by local hints and VS Computer.

### `core/protocol.py`

String-token and formatting helpers for the serial protocol.

Important shared sets:

- `NEW_GAME_MSGS`
- `OK_MSGS`
- `HINT_MSGS`
- `IGNORED_MSGS`

Important helpers:

- `parse_payload()`
- `parse_uci_move()`
- `format_engine_move()`
- `format_hint_move()`
- `format_capture_reply()`
- `send_lcd_ack_for_payload()`

### `core/net_utils.py`

Network-state helpers used by online mode and WiFi setup.

Important helpers:

- `run_command()`
- `is_ap_mode()`
- `wifi_config_url()`

### `core/wifi_ap.py`

Fallback WiFi setup path.

Responsibilities:

- detect whether WiFi is already connected
- launch a temporary AP and captive portal if needed
- let the user join/configure WiFi
- tear the AP down when connection succeeds

Main public entry point:

- `ensure_wifi(display=None, timeout_s=120.0)`

### `core/game_flow.py`

Shared game/menu/setup logic used across multiple modes.

Key dataclasses and exceptions:

- `GameConfig`
- `GameState`
- `ReturnToMenu`

Most important shared helpers:

- `wait_for_ok(link, display, *, send_prompt=True) -> bool`
- `wait_for_gameover_dismiss(link, display) -> bool`
- `wait_for_ok_or_skip_setup(link, display)`
- `confirm_exit_game(link, display) -> bool`
- `run_in_bg(fn, link, display, *, on_cancel=None)`
- `handle_typing_message(link, display, payload, board, ...)`
- `handle_capq_message(link, board, msg) -> bool`
- `check_move_captures(board, uci) -> bool`
- `resolve_uci_promotion(link, display, board, uci)`
- `validate_and_push_move(link, display, board, uci)`
- `handle_illegal_move(link, display, board, uci, label) -> bool`
- `send_turn_notification(link, board)`
- `send_check_signal(link, board)`
- `send_move_hint(link, display, ctx, state, cfg)`
- `notify_game_over(link, display, board) -> str`
- `post_game_menu(link, display, board) -> never`
- `offer_analysis_qr(link, display, board)`
- `guide_board_setup(link, display, fen, label) -> str | None`
- `confirm_board_ready_or_setup(link, display, board, *, label, start_message) -> bool`
- `wait_for_mode_selection(link, display, state, cfg) -> str`
- `run_selected_mode(link, display, ctx, state, cfg)`

Important current behavior:

- `wait_for_ok()` only sends `WaitForOkConfirm` when `send_prompt=True`.
- `wait_for_gameover_dismiss()` is used after `GameOver:*` so the Pi does not stack a second OK prompt on top of the Pico's built-in game-over scene.
- `post_game_menu()` now waits for the Pico game-over dismiss first, then offers the analysis QR prompt.
- `offer_analysis_qr()` uses the existing `MenuPaged` state for its OK wait and does not layer a second confirmation prompt.

---

## Screen Modules

### `screen/display.py`

High-level LCD API used by the rest of the Pi code.

Main responsibilities:

- restart the display server
- wait until the server signals readiness
- send 4-line text frames to the display pipe
- show QR codes
- provide chess-specific convenience messages like move prompts and arrows

### `screen/display_server.py`

Dedicated process that owns the physical LCD and renders text/QR frames.

Current notes:

- uses `lcd_pipe.py` for the FIFO path and ready-flag path
- no longer carries its own duplicate pipe-path constants
- no longer carries the unused `_prepare_lines()` helper

### `screen/lcd_pipe.py`

Shared IPC constants:

- `PIPE_PATH`
- `READY_FLAG_PATH`

### `screen/qrgen.py`

Pure-Python QR encoder used by the display server.

---

## Mode Overview

### VS Computer

Files:

- `modes/vs_computer/game_controller.py`
- `modes/vs_computer/stockfish_opponent.py`

Flow:

1. Configure difficulty and color in `game_flow.py`
2. Start `GameController.run_stockfish_game()`
3. Human turns go through `validate_and_push_move()`
4. Engine turns go through `StockfishOpponent.get_move()`
5. Game over uses:
   - `notify_game_over()`
   - `post_game_menu()`

### Local 2-player

Implemented mainly in `core/game_flow.py`.

Flow:

1. Configure local mode
2. Reuse shared parsing, typing preview, capture query, hints, legality, and draw helpers
3. Game over uses:
   - `notify_game_over()`
   - `post_game_menu()`

### Lichess Online

File:

- `modes/online/online_controller.py`

Current top-level menu:

- `New Game`
- `Ongoing Games`
- `Challenge Received`

Important internal helpers:

- `_connect_and_get_account()`
- `_wait_for_game_start()`
- `_submit_request_and_wait_for_game()`
- `_run_quick_pairing()`
- `_run_challenge_friend()`
- `_run_correspondence()`
- `_run_challenge_received()`
- `_run_ongoing_games()`
- `_setup_ongoing_board()`
- `_play_game()`

Current behavior notes:

- online mode uses `ensure_wifi()` and can surface AP/captive-portal setup before the menu
- active-game exit/draw handling is specific to online mode and does not use `confirm_exit_game()`
- online game over sends `GameOver:*`, waits for `wait_for_gameover_dismiss()`, then returns to menu

### Puzzles

File:

- `modes/puzzles/puzzle_controller.py`

Current puzzle menu:

- `Daily Puzzle`
- `Mix and match`
- `Themes`

Important helpers:

- `_build_puzzle_state()`
- `_build_state_from_payload()`
- `_fetch_daily()`
- `_fetch_mix()`
- `_fetch_theme()`

Current behavior notes:

- solved puzzles send `GameOver:1-0`
- puzzle mode then waits on `wait_for_gameover_dismiss()`
- puzzle mode returns directly to the main menu after that single dismiss step

### Studies

File:

- `modes/studies/study_controller.py`

Current behavior:

- loads studies from `studies.txt`
- chapter list is dynamic
- mode selection per chapter:
  - `Play as White`
  - `Play as Black`
  - `Watch`

Current refactors:

- page building/rendering is shared by `_paginate_lines()` and `_render_page()`
- title and annotation input semantics remain explicit and separate

---

## Current Menu Structure

### Main Menu

- `Play Chess!`
- `Puzzles`
- `Studies`
- `Settings`

### Play Chess submenu

- `Against PC`
- `Local 2-player`
- `Lichess Online`

### Settings submenu

- `Brightness`
- `Update`

---

## Current Game-over Pattern

There are now two distinct post-action confirmation patterns:

### 1. Explicit OK-confirm pattern

Used for:

- setup steps
- error banners
- non-game-over prompts

Mechanism:

- Pi sends `WaitForOkConfirm`
- Pico enters dedicated OK-confirm mode
- user presses OK
- Pico replies `btn_ok`

### 2. Built-in GameOver dismiss pattern

Used for:

- local game over
- VS Computer game over
- online game over
- puzzle solved screen
- auto-declared draws

Mechanism:

- Pi sends `GameOver:<result>`
- Pico shows its own blocking game-over scene
- user dismisses that scene
- Pi waits with `wait_for_gameover_dismiss()`

Important consequence:

- code must not call plain `wait_for_ok()` immediately after sending `GameOver:*`
- otherwise the Pi stacks a second OK prompt on top of the Pico game-over screen

---

## Current Analysis QR Flow

Local and VS Computer currently do this:

1. send `GameOver:*`
2. wait for Pico game-over dismiss
3. show `Press OK / to view analysis`
4. use the paged-menu state for that OK prompt
5. show QR
6. pressing OK on the QR screen returns to the main menu

There is no extra confirmation layer after the QR screen.

---

## Protocol Highlights

### Pico -> Pi

Important payloads:

- `btn_ok`, `ok`
- `btn_hint`, `hint`
- `n`, `btn_new`
- `shutdown`
- `typing_*`
- `capq_*`
- UCI moves like `e2e4`, `e7e8q`

### Pi -> Pico

Important payloads:

- `GameStart`
- `GameEnd`
- `GameOver:1-0`
- `GameOver:0-1`
- `GameOver:1/2-1/2`
- `ChooseMode`
- `MenuPaged`
- `WaitForOkConfirm`
- `WaitForAnnotationPage`
- `WaitForOkOrSkipSetup`
- `SetupComplete`
- `turn_white`, `turn_black`
- `check_<sq>`
- `hint_*`
- `m<uci>` / engine move payloads

---

## Practical "Where Does This Happen?" Index

- Mode selection: `core/game_flow.py`
- Settings / update flow: `core/game_flow.py`
- Local 2-player loop: `core/game_flow.py`
- VS Computer loop: `modes/vs_computer/game_controller.py`
- Lichess online lifecycle: `modes/online/online_controller.py`
- Puzzle fetch/setup/solve: `modes/puzzles/puzzle_controller.py`
- Study chapter flow: `modes/studies/study_controller.py`
- LCD text rendering: `screen/display.py` + `screen/display_server.py`
- WiFi AP / captive portal: `core/wifi_ap.py`
- Game-over + analysis QR flow: `core/game_flow.py`
