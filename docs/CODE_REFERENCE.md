# SmarterChess — Code Reference

A complete map of every file, every function, and every call relationship.
Use this document to answer "where does X happen?" or "what calls Y?".

---

## File Tree

```
RaspberryPiCode/
│
├── piMain.py                        Entry point. Startup, main loop, error recovery.
│
├── core/                            Shared infrastructure — all modes import from here.
│   ├── boardlink.py                 UART serial link to the Pico.
│   ├── engine.py                    Stockfish process wrapper.
│   ├── game_flow.py                 All shared game logic (move validation, menus, hints…).
│   ├── net_utils.py                 WiFi / AP-mode detection.
│   └── protocol.py                  Message token sets, parsers, and formatters.
│
├── screen/
│   ├── display.py                   High-level LCD API; sends messages over a named pipe.
│   ├── display_server.py            Separate process that reads the pipe and drives the LCD.
│   ├── lcd_pipe.py                  Shared constants (pipe path, ready-flag path).
│   └── qrgen.py                     Self-contained QR encoder (no external dependencies).
│
└── modes/
    ├── vs_computer/
    │   ├── game_controller.py       Game loop for VS Computer mode.
    │   └── stockfish_opponent.py    Maps UI skill 1–8 to Stockfish parameters.
    ├── online/
    │   ├── lichess_client.py        HTTP client for the Lichess Board API.
    │   ├── lichess_game.py          Helpers to extract fields from Board API payloads.
    │   └── online_controller.py     Full Lichess session lifecycle.
    └── puzzles/
        ├── puzzle_controller.py     Fetch, setup, and solve loop for all puzzle modes.
        └── puzzle_ids.txt           Local list of puzzle IDs for the "Mix and match" fallback.
```

---

## Module Dependency Diagram

Arrows mean "imports from".

```
piMain.py
  ├─► core/boardlink.py
  ├─► core/engine.py
  ├─► core/game_flow.py ─────────────────────────────────────────────────────┐
  │     ├─► core/boardlink.py                                                │
  │     ├─► core/engine.py                                                   │
  │     ├─► core/protocol.py                                                 │
  │     └─► screen/display.py                                                │
  ├─► core/protocol.py                                                       │
  └─► screen/display.py                                                      │
                                                                             │
modes/vs_computer/game_controller.py ◄──────── game_flow (shared helpers)   │
  ├─► core/boardlink.py                                                      │
  ├─► core/game_flow.py ◄──────────────────────────────────────────────────┘
  ├─► core/protocol.py
  ├─► modes/vs_computer/stockfish_opponent.py
  └─► screen/display.py

modes/vs_computer/stockfish_opponent.py
  └─► core/engine.py

modes/online/online_controller.py
  ├─► core/boardlink.py
  ├─► core/game_flow.py
  ├─► core/net_utils.py
  ├─► core/protocol.py
  ├─► modes/online/lichess_client.py
  ├─► modes/online/lichess_game.py
  └─► screen/display.py

modes/online/lichess_client.py
  └─► screen/qrgen.py  (indirectly, via display)

modes/puzzles/puzzle_controller.py
  ├─► core/boardlink.py
  ├─► core/game_flow.py
  ├─► core/protocol.py
  ├─► modes/online/lichess_client.py
  └─► screen/display.py

screen/display.py
  └─► screen/lcd_pipe.py

screen/display_server.py
  └─► screen/lcd_pipe.py
```

---

## Architecture Layers

```
┌────────────────────────────────────────────────────────┐
│                     piMain.py                          │  ← top-level orchestration
└───────────────────────────┬────────────────────────────┘
                            │
           ┌────────────────▼──────────────────┐
           │           core/game_flow.py        │  ← shared logic, used by every mode
           │  validate_and_push_move            │
           │  handle_typing_message             │
           │  handle_capq_message               │
           │  send_check_signal                 │
           │  send_turn_notification            │
           │  handle_illegal_move               │
           │  resolve_uci_promotion             │
           │  send_move_hint                    │
           │  notify_game_over                  │
           │  _check_and_handle_draw            │
           │  prompt_next_turn                  │
           │  wait_for_ok / wait_for_mode_sel   │
           └────────────────┬──────────────────┘
                            │ used by
         ┌──────────────────┼──────────────────────┐
         ▼                  ▼                       ▼
  vs_computer/       online/                  puzzles/
  game_controller    online_controller        puzzle_controller
         │                  │                       │
         ▼                  ▼                       ▼
  stockfish_opponent  lichess_client           lichess_client
         │
         ▼
    core/engine.py (Stockfish)
```

---

## Full Call Trace — From Startup to Move

```
piMain.main()
│
├─ display.restart_server()          spawn display_server.py subprocess
├─ display.banner("SMARTCHESS")      splash on LCD
├─ display.wait_ready()              block until display_server signals ready
│
└─ [loop]
    ├─ game_flow.wait_for_mode_selection(link, display, state)
    │     reads Pico until a mode token arrives (1/2/3/4)
    │     returns "stockfish" | "local" | "online" | "puzzle"
    │
    └─ game_flow.run_selected_mode(link, display, ctx, state, cfg)
          │
          ├─[stockfish]─────────────────────────────────────────────────────┐
          │  game_flow._configure_vs_computer()   collect skill/time/colour │
          │  ctx.ensure()                         start Stockfish if needed │
          │  game_controller.GameController(deps, cfg).run_stockfish_game() │
          │    │                                                             │
          │    ├─ [human turn] read_from_board() → parse_payload()          │
          │    │    ├─[TYPING]   handle_typing_message()                    │
          │    │    ├─[CAPQ]     check_move_captures() + format_capture_reply│
          │    │    ├─[HINT]     send_move_hint() → engine.hint()           │
          │    │    ├─[OK]       display.prompt_move() (after check signal) │
          │    │    └─[MOVE]     validate_and_push_move()                   │
          │    │                   resolve_uci_promotion()                  │
          │    │                   chess.Move legality check                │
          │    │                   board.push()                             │
          │    │                   send_check_signal()                      │
          │    │                   prompt_next_turn()                       │
          │    │                                                             │
          │    └─ [engine turn] _play_one_engine_move()                     │
          │         opponent.get_move() → engine.bestmove()                 │
          │         format_engine_move() → send to Pico                     │
          │         board.push()                                            │
          │         defer check signal to next OK press                     │
          │         prompt_next_turn()                                      │
          │                                                                 ─┘
          ├─[local]──────────────────────────────────────────────────────────┐
          │  game_flow._configure_local_game()   set max skill/time         │
          │  game_flow._run_local_game()                                     │
          │    ├─ [every message] handle_capq_message() / typing / hint     │
          │    ├─ [MOVE]         validate_and_push_move()                   │
          │    │                   + send_check_signal() (inside)           │
          │    │                   prompt_next_turn()                       │
          │    ├─ [after push]   _check_and_handle_draw()                   │
          │    └─ [game over]    notify_game_over()                         │
          │                                                                 ─┘
          ├─[online]─────────────────────────────────────────────────────────┐
          │  online_controller.OnlineController(link, display, cfg).run()   │
          │    ├─ net_utils.is_ap_mode()          check WiFi state          │
          │    ├─ lichess_client.get_account()    verify token              │
          │    ├─ lichess_client.stream_events()  poll for gameStart        │
          │    └─ _play_game(game_id, username)                             │
          │         ├─ lichess_client.stream_game()  open game stream       │
          │         ├─ apply_new_moves()              sync board from stream│
          │         │    ├─ send_check_signal()                             │
          │         │    └─ format_engine_move() → send to Pico             │
          │         ├─ [your move]                                          │
          │         │    resolve_uci_promotion()                            │
          │         │    chess.Move legality check  (local, no push yet)    │
          │         │    lichess_client.make_move()  submit to server       │
          │         │    board.push()               push only if accepted   │
          │         │    send_check_signal()                                │
          │         ├─ [resign]    _resign_and_exit() → resign_game()       │
          │         └─ [draw]      _offer_draw()     → offer_draw()         │
          │                                                                 ─┘
          └─[puzzle]─────────────────────────────────────────────────────────
             game_flow._run_puzzle_game()   show submenu (daily/mix/themes)
             puzzle_controller.PuzzleController(client, mode, …).run()
               ├─ _fetch_daily / _fetch_mix / _fetch_theme
               │    └─ lichess_client.get_daily_puzzle / get_puzzle / get_next_puzzle
               │    └─ _build_puzzle_state()   find best start board from PGN
               ├─ _compute_place_steps_from_fen()   generate LED setup steps
               ├─ [setup loop] wait_for_ok() per piece
               └─ [solve loop]
                    handle_typing_message()
                    handle_capq_message()
                    resolve_uci_promotion()
                    chess.Move legality check
                    solution correctness check
                    handle_illegal_move()  (for wrong or illegal moves)
                    format_engine_move()   (for auto-reply moves)
```

---

## File-by-File Reference

---

### `piMain.py`

**Purpose:** Entry point. Boots the display, creates shared objects, and runs the
main `while True` loop that re-shows the mode menu after every game.

| Function | What it does |
|---|---|
| `main()` | Creates `Display`, `BoardLink`, `EngineContext`, `GameConfig`, `GameState`. Runs the mode-select → play loop. Catches `ReturnToMenu` to restart the menu, `KeyboardInterrupt` to exit cleanly, and bare `Exception` to show a short error banner before returning to menu. |

**Environment variable read here:**
- `SMARTCHESS_FORCE_MODE` — skip mode selection and jump straight to a mode (useful for development)

---

### `core/boardlink.py`

**Purpose:** Everything to do with the UART serial link between the Pi and the Pico.
All other code talks to the Pico through this class.

**Protocol:**
- Pi → Pico: `heyArduino<payload>\n`
- Pico → Pi: `heypi<payload>\n`
- Pico → Pi (shutdown): `heypixshutdown`

| Function / Method | What it does | Called from |
|---|---|---|
| `BoardLink.__init__` | Opens serial port, flushes buffers | `piMain.main` |
| `BoardLink.close` | Closes serial port | `piMain.main` (finally) |
| `BoardLink.clear_input` | Drops buffered Pico bytes (`reset_input_buffer`) | `puzzle_controller.run` before setup |
| `BoardLink.send_to_board(text)` | Prepends `heyArduino`, writes + flushes | Everywhere |
| `BoardLink._readline` | Reads one line, UTF-8 decodes, strips; returns None on timeout | `try_read_from_board`, `read_from_board` |
| `BoardLink.try_read_from_board` | Non-blocking: returns payload string or None | Game loops that need to peek |
| `BoardLink.read_from_board` | Blocking: spins until a valid `heypi` line arrives | Main game loops |

---

### `core/protocol.py`

**Purpose:** Defines the shared vocabulary of Pico↔Pi messages. Keeps all
token constants and format strings in one place so no mode hard-codes them.

**Token sets:**

| Constant | Members | Meaning |
|---|---|---|
| `NEW_GAME_MSGS` | `n, new, in, newgame, btn_new` | Back / new game |
| `OK_MSGS` | `ok, btnok, btn_ok` | Acknowledge / confirm |
| `HINT_MSGS` | `hint, btn_hint` | Request a hint |
| `IGNORED_MSGS` | All of the above + `draw, btn_draw` | Silently skip in most loops |
| `RESERVED_NON_MOVES` | NEW + HINT + OK + draw tokens | Should never be parsed as UCI |

| Function | What it does | Called from |
|---|---|---|
| `parse_uci_move(s)` | Strips leading `m`, keeps 4–5 alnum chars; returns None if it matches a reserved token | All move loops |
| `parse_payload(payload)` | Full classifier: returns an `Event(type, payload)` | `game_controller._process_pending_messages` |
| `format_engine_move(uci, is_capture)` | `me2e4` or `me2e4_cap` | `game_controller._play_one_engine_move`, `online_controller.apply_new_moves`, `puzzle_controller.run` |
| `format_hint_move(uci, is_capture)` | `hint_e2e4` or `hint_e2e4_cap` | `game_flow.send_move_hint`, `puzzle_controller.run` |
| `format_capture_reply(is_capture)` | `capr_1` or `capr_0` | `game_flow.handle_capq_message`, `game_controller._handle_event` |
| `piece_name(sym)` | `'P'` → `'PAWN'` etc. | `game_flow.handle_illegal_move`, `puzzle_controller.run` |
| `send_lcd_ack_for_payload(link, payload)` | Sends `lcd_ack_from / _to / _confirm` matching the typing stage | `game_flow.handle_typing_message` |

**Data types:**

| Type | Fields | Purpose |
|---|---|---|
| `EventType` (Enum) | MOVE, HINT, NEW_GAME, SHUTDOWN, TYPING, CAPTURE_QUERY, OK, UNKNOWN | Classifies a raw payload |
| `Event` (frozen dataclass) | `type: EventType`, `payload: str` | Returned by `parse_payload` |

---

### `core/engine.py`

**Purpose:** Wraps a single Stockfish process for the lifetime of the application.
Keeping one process alive avoids repeated startup costs (~1–2 s each).

| Method | What it does | Called from |
|---|---|---|
| `__init__` | Sets `engine = None`; process starts lazily | `piMain.main` |
| `_engine_mod()` | Imports and caches the `chess.engine` module on first call | `ensure`, `bestmove`, `hint` |
| `ensure(path)` | Returns running engine; starts it if not running; retries forever on failure | `stockfish_opponent._ensure_configured`, `hint`, `bestmove` |
| `quit()` | Sends UCI `quit` to Stockfish; clears `self.engine` | `piMain.main` (finally) |
| `bestmove(board, time_ms)` | Calls `engine.play()` with a time limit; returns UCI string | `stockfish_opponent.get_move` |
| `hint(board, time_ms)` | Calls `engine.analyse(multipv=1)`; falls back to `bestmove` on failure | `game_flow.send_move_hint` |

---

### `core/game_flow.py`

**Purpose:** The shared game library. Every mode imports from here. Contains all
move handling, menu helpers, promotion flow, draw detection, and game-over logic.

**Data classes:**

| Type | Fields | Where used |
|---|---|---|
| `GameConfig` | `skill_level`, `move_time_ms`, `human_is_white` | Passed through `run_selected_mode` → every mode |
| `GameState` | `board`, `mode` | Tracks current board and mode string |

**Exception:**

| Type | Purpose |
|---|---|
| `ReturnToMenu` | Raised anywhere in a game loop to cleanly exit back to the mode menu |

**Shared single-call helpers** (used by multiple modes):

| Function | Signature | What it does | Called from |
|---|---|---|---|
| `wait_for_ok` | `(link, display) → bool` | Blocks until `btn_ok`; returns False on back/shutdown | `puzzle_controller`, `game_flow._check_and_handle_draw` |
| `run_in_bg` | `(fn, link, display, *, on_cancel=None)` | Runs `fn()` in a daemon thread while polling serial every 50 ms; calls `on_cancel()` (or raises `ReturnToMenu`) if the user presses OK/back mid-call | `online_controller.run`, `puzzle_controller.run` |
| `handle_typing_message` | `(link, display, payload, board)` | Updates typing-preview LCD + sends matching ACK | All three game modes |
| `handle_capq_message` | `(link, board, msg) → bool` | Answers `capq_` queries using `format_capture_reply`; returns True if handled | All three game modes |
| `send_turn_notification` | `(link, board)` | Sends `turn_white` or `turn_black` | All three game modes, `puzzle_controller` |
| `send_check_signal` | `(link, board)` | Sends `check_{sq}` if board is in check; safe to call always | `validate_and_push_move`, `online_controller` (×2) |
| `resolve_uci_promotion` | `(link, display, board, uci) → str\|None` | Prompts for promotion piece if pawn reaches last rank | `validate_and_push_move`, `online_controller`, `puzzle_controller` |
| `validate_and_push_move` | `(link, display, board, uci) → Move\|None` | Full pipeline: promote → parse → legality → push → check signal | `game_flow._run_local_game`, `game_controller._handle_event` |
| `handle_illegal_move` | `(link, display, board, uci, label) → bool` | Red-trail + put-back prompt + OK wait | `validate_and_push_move`, `puzzle_controller.run` |
| `send_move_hint` | `(link, display, ctx, state, cfg)` | Gets engine hint and shows it on LCD + Pico LEDs | `game_controller._handle_event`, `game_flow._run_local_game` |
| `notify_game_over` | `(link, display, board) → str` | Sends `GameOver:<result>` + LCD message; returns result string | `game_controller`, `online_controller`, `game_flow._run_local_game` |
| `prompt_next_turn` | `(link, display, brd, mode, cfg, last_uci)` | Shows last-move arrow + human/engine label; sends `turn_` if human | `game_controller`, `game_flow._run_local_game` |
| `shutdown_raspberry_pi` | `(link, display)` | Shows shutdown message, calls `sudo shutdown -h now` | All modes when `shutdown` token received |
| `check_move_captures` | `(brd, uci) → bool` | Returns True if the move captures (including en passant) | `handle_capq_message`, `game_controller._handle_event`, `puzzle_controller.run` |

**Private helpers:**

| Function | What it does |
|---|---|
| `_move_needs_promotion(move, brd)` | True if the move is a legal pawn move to the last rank without a promotion piece set |
| `_prompt_promotion_choice(link, display)` | Shows the Q/R/B/N menu; waits for button; returns `'q'/'r'/'b'/'n'` |
| `_get_draw_reason(brd)` | Returns reason string if 5-fold/75-move/3-fold/50-move draw condition is met |
| `_check_and_handle_draw(link, display, brd) → bool` | Calls `_get_draw_reason`; if drawn, sends `GameOver:1/2-1/2` + waits for NEW |
| `_parse_color_choice(s)` | `s1` → True (White), `s2` → False (Black), `s3` → random |
| `_update_typing_display(display, payload, board)` | Parses `from_/to_/confirm_` payload and shows contextual LCD text |
| `_format_piece_name(piece)` | Returns `'White Pawn'` style label for LCD |
| `_is_valid_square(s)` | True if `s` is like `e4` |
| `_get_piece_label(board, sq)` | Returns `'White Pawn'` for the piece on `sq`, or None |
| `_show_new_game_banner(display)` | Shows `NEW GAME` banner for 1 s |
| `_result_to_winner_text(res)` | `'1-0'` → `'White wins'` etc. |
| `_menu_truncate(s, n)` | Truncates to n chars with `…` |
| `_render_paged_menu(title, page, pages, items)` | Formats 4-item page for a 20-char LCD |
| `_paged_menu(link, display, title, options) → str\|None` | Full scrollable menu loop; HINT scrolls, OK/back cancels |

**Mode wrappers (called from `run_selected_mode`):**

| Function | What it does |
|---|---|
| `_configure_vs_computer(link, display, cfg)` | Collects skill / time / colour from Pico |
| `_configure_local_game(link, display, cfg)` | Sets max skill, fastest think time |
| `_run_local_game(link, display, ctx, state, cfg)` | Full 2-player game loop |
| `_run_online_game(link, display, cfg)` | Imports and delegates to `OnlineController` |
| `_run_puzzle_game(link, display)` | Shows puzzle submenu, imports and delegates to `PuzzleController` |
| `run_selected_mode(link, display, ctx, state, cfg)` | Top-level dispatcher: reads `state.mode` and calls the correct path |

**Constants:**

| Constant | Type | Purpose |
|---|---|---|
| `PHASE_THEMES` | `List[Tuple[str,str]]` | Lichess theme tag → display label pairs for the Phases menu |
| `OPENING_GROUPS` | `List[Tuple[str, List[str]]]` | Alphabetical groups of opening names for the Openings menu |

---

### `core/net_utils.py`

**Purpose:** Detects whether the Pi is in WiFi AP (hotspot) mode and returns the
appropriate config URL.

| Function | What it does | Called from |
|---|---|---|
| `_run(cmd, timeout_s)` | Runs a shell command, returns stdout | Internal |
| `_iw_ssid(iface)` | Returns current WiFi SSID via `iw` | `is_ap_mode` |
| `_ipv4_addr(iface)` | Returns IPv4 address via `ip addr` | `wifi_config_url` |
| `_service_active(name)` | Checks if a systemd service is active | `is_ap_mode` |
| `is_ap_mode() → bool` | Heuristic: checks for `hostapd` service or AP SSID | `online_controller.run` |
| `wifi_config_url() → str` | Returns the captive-portal URL when in AP mode | `online_controller.run` |

---

### `screen/display.py`

**Purpose:** High-level LCD API. Sends formatted messages to `display_server.py`
over a named pipe (`/tmp/lcdpipe`). Abstracts message priority so prompts
(like "enter move") can break through status messages.

**Message classification (priority):**

| Class | Examples | Behaviour |
|---|---|---|
| Critical | illegal, game over, promotion, shutdown | Always shown immediately |
| Prompt | move prompt, confirmation | Locks display for 0.65–1.15 s |
| Status | engine thinking, loading | Can be overwritten by anything |
| Normal | everything else | Standard display |

| Method | What it does | Called from |
|---|---|---|
| `__init__` | Sets up pipe path and ready-flag path | `piMain.main` |
| `_classify(message)` | Categorises message by keyword scanning | `send` |
| `_ensure_pipe()` | Creates the FIFO if it doesn't exist | `send`, `show_qr` |
| `restart_server()` | Kills stale process, starts fresh `display_server.py` | `piMain.main` |
| `wait_ready(timeout_s)` | Polls the ready-flag file until server is up | `piMain.main` |
| `send(message, size, force)` | Main method: writes `L1\|L2\|L3\|L4\|size` to pipe | Everywhere |
| `show_qr(data, *captions)` | Sends a QR-mode message (pipe prefix `qr`) | `online_controller.run` |
| `banner(text, delay_s)` | Full-screen centred text with optional delay | `piMain.main`, `game_flow` |
| `show_arrow(uci, suffix, force)` | Shows a move arrow like `e2→e4` with a second line | All modes after moves |
| `prompt_move(side, force)` | Shows `WHITE to move` / `BLACK to move` | All modes |
| `show_hint_result(uci)` | Shows hint arrow on LCD | `game_flow.send_move_hint` |
| `show_invalid(text)` | Shows an invalid-move error | `validate_and_push_move`, move loops |
| `promo_name(promo_letter)` | `'q'` → `'Queen'` | `format_promo_line` |
| `format_promo_line(promo_letter)` | Returns `'=Queen'` style suffix for move display | `prompt_next_turn`, `online_controller` |
| `show_draw(reason, move_no)` | Shows draw reason + move number | `game_flow._check_and_handle_draw` |
| `close()` | Removes the ready-flag file | `piMain.main` (finally) |

---

### `screen/display_server.py`

**Purpose:** Separate process that owns the physical LCD. Reads messages from
the named pipe, parses the `L1|L2|L3|L4|size` format, and renders to the
Waveshare ST7789 display. Caps output at ~10 FPS to avoid flickering.

| Function | What it does |
|---|---|
| `_open_fifo_blocking(path)` | Opens the FIFO for reading (blocks until a writer connects) |
| `_get_font(size)` | Returns a cached TrueType font at the requested size |
| `_find_best_font_size(lines, …)` | Binary search for the largest font that fits all lines |
| `_draw_centered_text_with_size(lines, size, …)` | Renders lines at a fixed font size, vertically centred |
| `_draw_centered_text_auto(lines, …)` | Auto-scales font and renders |
| `_draw_qr(data, caption_lines)` | Renders a QR code matrix plus caption lines |
| `_draw_splash()` | Renders the initial `SMARTCHESS` splash |

---

### `screen/lcd_pipe.py`

**Purpose:** Single source of truth for the IPC file paths shared between
`display.py` and `display_server.py`.

| Constant | Value |
|---|---|
| `PIPE_PATH` | `/tmp/lcdpipe` |
| `READY_FLAG_PATH` | `/tmp/display_server_ready` |

---

### `screen/qrgen.py`

**Purpose:** Pure-Python QR code encoder (Nayuki reference implementation).
No PIL or external QR libraries needed.

| Public entry point | What it does |
|---|---|
| `QrCode.encode_text(text, ecl)` | Encode a string to a QR matrix at the given error-correction level |
| `QrCode.get_module(x, y)` | True if module at (x, y) is dark |

---

### `modes/vs_computer/stockfish_opponent.py`

**Purpose:** Maps the 1–8 UI skill slider to Stockfish's internal parameters
and retrieves moves via the shared `EngineContext`.

**Skill mapping:**

Levels 1–2 use Stockfish's `Skill Level` parameter (UCI_Elo has a ~1320 floor and cannot produce true beginner play). Levels 3–8 use `UCI_Elo` for a realistic human-like curve.

| UI Level | Parameter | Value |
|---|---|---|
| 1 | Skill Level | 0 (~500 Elo) |
| 2 | Skill Level | 1 (~800 Elo) |
| 3 | UCI_Elo | 1000 |
| 4 | UCI_Elo | 1300 |
| 5 | UCI_Elo | 1600 |
| 6 | UCI_Elo | 1800 |
| 7 | UCI_Elo | 2000 |
| 8 | UCI_Elo | 2300 |

| Function / Method | What it does | Called from |
|---|---|---|
| `_clamp(n, lo, hi)` | Clamps an integer to [lo, hi] | Skill setters |
| `_map_skill_to_elo(ui_skill)` | Returns Elo rating for the skill level | `_ensure_configured` when `use_elo=True` |
| `_map_skill_to_stockfish_level(ui_skill)` | Returns Stockfish Skill Level 0–18 | `_ensure_configured` when `use_elo=False` |
| `StockfishOpponent.__init__` | Stores ctx, time, skill; marks unconfigured | `run_selected_mode` |
| `set_time_ms(ms)` | Updates think time for the next move | `game_controller.run_stockfish_game` |
| `_ensure_configured()` | Pushes `UCI_Elo` or `Skill Level` to engine if settings changed | `get_move` |
| `get_move(board) → str\|None` | Ensures engine is configured, calls `ctx.bestmove()` | `game_controller._play_one_engine_move` |

---

### `modes/vs_computer/game_controller.py`

**Purpose:** Game loop for VS Computer mode. Alternates between reading Pico
input (human turn) and asking Stockfish for a reply (engine turn).

| Type / Method | What it does | Called from |
|---|---|---|
| `GameDeps` (dataclass) | `link`, `display`, `opponent` bundled together | `run_selected_mode` |
| `GameController.__init__` | Sets up board and deferred-check state | `run_selected_mode` |
| `_is_human_turn() → bool` | Checks `board.turn` against `human_is_white` | `run_stockfish_game` |
| `_send_turn_notification()` | Delegates to `game_flow.send_turn_notification` | `run_stockfish_game` |
| `_process_pending_messages()` | Drains up to 6 non-blocking events per tick (typing/capq keep display live) | `run_stockfish_game` main loop |
| `run_stockfish_game(move_time_ms)` | Main loop: pending msgs → engine move → wait for human | `run_selected_mode` |
| `_handle_event(typ, payload, nonblocking)` | Switch on EventType: SHUTDOWN / NEW_GAME / TYPING / OK / CAPQ / HINT / MOVE | `run_stockfish_game`, `_process_pending_messages` |
| `_play_one_engine_move()` | Asks Stockfish, sends move to Pico, updates board, defers check signal | `run_stockfish_game` |

**Note on deferred check signal:** When the engine gives check, `_pending_check_sq`
is stored and the `check_{sq}` message is sent only after the player presses OK.
This lets the Pico finish its piece-trail animation before the blink starts.

---

### `modes/online/lichess_client.py`

**Purpose:** HTTP client for the Lichess Board API. All network I/O for online
mode goes through this class.

| Method | Endpoint | Called from |
|---|---|---|
| `__init__(token)` | Reads `LICHESS_TOKEN` env var | `OnlineController.__init__`, `_run_puzzle_game` |
| `get_account()` | `GET /api/account` | `online_controller.run` |
| `stream_events(timeout_s)` | `GET /api/stream/event` (NDJSON) | `online_controller.run` (polling for gameStart) |
| `stream_game(game_id)` | `GET /api/board/game/stream/{id}` (NDJSON) | `online_controller._play_game` |
| `make_move(game_id, uci)` | `POST /api/board/game/{id}/move/{uci}` | `online_controller._play_game` |
| `resign_game(game_id)` | `POST /api/board/game/{id}/resign` | `OnlineController._resign_and_exit` |
| `offer_draw(game_id)` | `POST /api/board/game/{id}/draw/yes` | `OnlineController._offer_draw` |
| `get_daily_puzzle()` | `GET /api/puzzle/daily` | `PuzzleController._fetch_daily` |
| `get_puzzle(puzzle_id)` | `GET /api/puzzle/{id}` | `PuzzleController._fetch_mix`, `_fetch_theme` |
| `get_next_puzzle(angle, …)` | `GET /api/puzzle/next?angle=…` | `PuzzleController._fetch_theme` |

**Helpers:**

| Function | What it does |
|---|---|
| `_slugify_angle(a)` | Converts an opening name like `King's Gambit` to `kings-gambit` for the URL |
| `_iter_ndjson(resp)` | Yields parsed JSON objects from a streaming NDJSON HTTP response |

---

### `modes/online/lichess_game.py`

**Purpose:** Extracts specific fields from the Lichess Board API stream payloads
(`gameFull` and `gameState` events). Keeps the parsing logic out of the controller.

| Function | What it extracts | Called from |
|---|---|---|
| `extract_moves(payload) → List[str]` | Space-separated UCI move list | `online_controller._play_game` |
| `extract_players(payload) → Tuple[str,str]` | `(white_name, black_name)` | `online_controller._play_game` |
| `extract_status(payload) → str` | Game status string (`started`, `mate`, `resign`, …) | `online_controller._play_game` |
| `extract_winner(payload) → str` | `'white'`, `'black'`, or `''` | `online_controller._play_game` |

---

### `modes/online/online_controller.py`

**Purpose:** Manages the complete lifecycle of a Lichess online game: WiFi check,
account auth, lobby polling, and the active game loop.

| Method | What it does | Called from |
|---|---|---|
| `__init__` | Stores link/display/cfg; creates `LichessClient` | `game_flow._run_online_game` |
| `_resign_and_exit(game_id)` | Shows "Resigning…", calls `resign_game()`, raises `ReturnToMenu` | `_play_game` (×2) |
| `_offer_draw(game_id)` | Shows "Offering draw…", calls `offer_draw()` | `_play_game` (×2) |
| `_cancel_to_menu()` | Shows "Cancelling…", sends `ok_back_disable`, raises `ReturnToMenu` | `run` (on OK/back press during lobby) — passed as `on_cancel` to `run_in_bg` |
| `_handle_common(msg, board) → bool` | Handles shutdown / typing / capq / hint — same in every state | `run`, `_play_game` |
| `run()` | AP mode check → account fetch (with retry) → gameStart polling → `_play_game` | `game_flow._run_online_game` |
| `_play_game(game_id, username)` | Active game loop: stream opponent moves, send own moves to Lichess before pushing locally | `run` |

**Key internal closure in `_play_game`:**

| Closure | What it does |
|---|---|
| `send_turn_if_human()` | Calls `send_turn_notification` only when it is our colour's turn |
| `apply_new_moves(move_list, announce_new)` | Replays moves from the stream onto the local board; sends check signal and piece trail for new moves |

---

### `modes/puzzles/puzzle_controller.py`

**Purpose:** Fetches puzzles from Lichess, guides the physical piece placement
on an empty board via LEDs, then runs the solve loop.

**Module-level helpers:**

| Function | What it does | Called from |
|---|---|---|
| `_pgn_opening_info(pgn_text)` | Extracts ECO + Opening headers for debug logging | `_fetch_theme` |
| `_stable_home_dir()` | Resolves correct `$HOME` even under systemd | Cache path constants |
| `_puzzle_index_dir()` | Path to local opening-index cache | `_fetch_theme` |
| `_opening_to_slug(name)` | `King's Gambit` → `kings_gambit` | `_opening_index_file` |
| `_opening_index_file(name)` | Full path to the opening's cached puzzle ID list | `_fetch_theme` |
| `_read_index_ids(path)` | Returns all IDs from a one-per-line file | `_fetch_theme` |
| `_load_seen_cache()` | Loads `seen_puzzles.json` | `PuzzleController.__init__`, `_fetch_theme` |
| `_save_seen_cache(data)` | Atomic write to `seen_puzzles.json` via `.tmp` rename | `_mark_seen`, `_reset_seen_puzzles` |
| `_reset_seen_puzzles(angle)` | Clears seen cache for one angle (or all) when exhausted | `_fetch_theme` |
| `_pick_random_line_seek(path)` | Fast random line from a large file without loading it all | `_fetch_mix`, `_fetch_theme` fallback |
| `_dist(a, b)` | Manhattan distance between two square names (unused in current sort key) | — |
| `_pieces_by_type_and_color(brd)` | Groups squares by (color, piece_type) | — |
| `_compute_place_steps_from_fen(fen)` | Returns sorted placement steps (side, sq, symbol) for LED-guided setup | `PuzzleController.run` |
| `_format_puzzle_label(themes, rating)` | Returns a short LCD label like `Fork • 1450` | `PuzzleController.run` |
| `_board_from_pgn_at_ply(pgn, ply)` | Replays PGN up to a given ply and returns the board | `_find_best_start_board_from_pgn` |
| `_play_solution_prefix_len(b, sol)` | Returns how many solution moves are legal from board `b` | `_find_best_start_board_from_pgn` |
| `_find_best_start_board_from_pgn(pgn, ply, sol, back, forward)` | Scans ±6/+10 plies around `initialPly` to maximise the legal solution prefix | `_build_puzzle_state` |
| `_build_puzzle_state(puzzle_id, pgn, …)` | Constructs a `PuzzleState` from validated fields; calls `_find_best_start_board_from_pgn` | `_fetch_daily`, `_fetch_mix`, `_fetch_theme` (×2) |

**`PuzzleController` methods:**

| Method | What it does | Called from |
|---|---|---|
| `__init__` | Stores client/mode/theme; loads seen-puzzle cache | `game_flow._run_puzzle_game` |
| `_mark_seen(angle, puzzle_id)` | Adds ID to global + per-angle seen set; writes cache | `run` on success |
| `_fetch_daily()` | Fetches `/api/puzzle/daily`; retries once; calls `_build_puzzle_state` | `run` |
| `_fetch_mix()` | Random ID from `puzzle_ids.txt` → `/api/puzzle/{id}`; calls `_build_puzzle_state` | `run` |
| `_fetch_theme(angle)` | 3-strategy fetch: opening index → `/api/puzzle/next` → local fallback | `run` |
| `run(link, display)` | Full puzzle session: fetch → LED setup → solve loop | `game_flow._run_puzzle_game` |

**Constants:**

| Constant | Purpose |
|---|---|
| `THEME_MAP` | Lichess theme tag → human-readable label |
| `PUZZLE_IDS_PATH` | Path to `puzzle_ids.txt` beside the module |
| `SEEN_CACHE_PATH` | Path to `~/.cache/smartchess/seen_puzzles.json` |

---

## Cross-Reference: Who Calls What

### `game_flow.validate_and_push_move`
Called by:
- `game_flow._run_local_game` — local 2-player move input
- `game_controller.GameController._handle_event` — VS Computer human move

**Not** called by:
- `online_controller._play_game` — intentional; must submit to Lichess before pushing

---

### `game_flow.send_check_signal`
Called by:
- `game_flow.validate_and_push_move` — after every local/vs-computer human push
- `online_controller._play_game.apply_new_moves` — after opponent move is pushed (+ sleep)
- `online_controller._play_game` — after your own move is pushed

**Not** called by:
- `game_controller._play_one_engine_move` — check is deferred to `_pending_check_sq` and sent on OK

---

### `game_flow.handle_typing_message`
Called identically by all three active game modes:
- `game_flow._run_local_game`
- `game_controller.GameController._handle_event`
- `online_controller.OnlineController._handle_common`
- `puzzle_controller.PuzzleController.run`

---

### `game_flow.handle_capq_message`
Called identically by all three active game modes:
- `game_flow._run_local_game`
- `online_controller.OnlineController._handle_common`
- `puzzle_controller.PuzzleController.run`

**Note:** `game_controller` handles `CAPTURE_QUERY` via `parse_payload` + `format_capture_reply`
directly, which is equivalent (the event type is already stripped of the `capq_` prefix).

---

### `game_flow.handle_illegal_move`
Called by:
- `game_flow.validate_and_push_move` — when a parsed move is not in `board.legal_moves`
- `puzzle_controller.PuzzleController.run` — for illegal moves and for wrong-but-legal puzzle moves

---

### `lichess_client.LichessClient`
Instantiated by:
- `online_controller.OnlineController.__init__` — for online games
- `game_flow._run_puzzle_game` — for puzzle fetching (shared client passed to `PuzzleController`)

---

## Protocol Quick Reference

### Pico → Pi messages (payload after `heypi`)

| Payload pattern | Meaning |
|---|---|
| `e2e4` | Move entered (4-char UCI) |
| `e7e8q` | Move with promotion |
| `typing_from_e` | Typing preview: from-square, partial |
| `typing_to_e2 → e` | Typing preview: to-square, partial |
| `typing_confirm_e2 → e4` | Typing preview: confirmation stage |
| `capq_e2e4` | Capture query: is e2→e4 a capture? |
| `btn_ok` / `ok` | OK button pressed |
| `btn_hint` / `hint` | HINT button pressed |
| `n` / `btn_new` | Back / new game button |
| `draw` / `btn_draw` | Draw offer button |
| `s1` / `s2` / `s3` | Colour choice: White / Black / Random |
| `heypixshutdown` | Hardware power-off signal |

### Pi → Pico messages (payload after `heyArduino`)

| Payload | Meaning |
|---|---|
| `me2e4` | Engine/opponent played e2→e4 |
| `me2e4_cap` | Engine move that captures |
| `hint_e2e4` | Hint: suggest e2→e4 |
| `hint_e2e4_cap` | Hint with capture |
| `turn_white` / `turn_black` | Whose turn it is |
| `check_e1` | King on e1 is in check |
| `capr_1` / `capr_0` | Capture query reply |
| `GameStart` | A new game is beginning |
| `GameOver:1-0` | White wins |
| `GameOver:0-1` | Black wins |
| `GameOver:1/2-1/2` | Draw |
| `ChooseMode` | Show mode selection on Pico display |
| `SetupComplete` | Configuration done, game starting |
| `EngineStrength` | Prompt Pico to show difficulty input |
| `PlayerColor` | Prompt Pico to show colour choice |
| `promotion_choice_needed` | Ask player to choose promotion piece |
| `puzzle_setup_begin` | Start LED-guided piece placement |
| `setup_place_e4_w` | Place white piece on e4 |
| `setup_clear` | Clear board LEDs |
| `puzzle_setup_done` | Setup complete, solve starts |
| `hint_disable` / `hint_enable` | Enable/disable hint button during setup |
| `lcd_ack_from` | ACK for `typing_from_*` |
| `lcd_ack_to` | ACK for `typing_to_*` |
| `lcd_ack_confirm` | ACK for `typing_confirm_*` |
| `MenuPaged` | Tell Pico a paged menu is active |
| `ok_back_enable` | Enable OK button (green) as a back/cancel button |
| `ok_cancel_enable` | Enable OK button (red) as a cancel button — used during online lobby |
| `ok_back_disable` | Disable the OK-as-back/cancel button when active play begins |
| `error_invalid_<token>` | Unrecognised input |
| `error_unknown_mode` | Unknown mode token |
| `error_puzzle_fetch` | Puzzle could not be fetched |
| `error_puzzle_internal` | Expected solution move is illegal |
| `error_puzzle_parse` | Could not parse opponent reply move |
