# SmarterChess — Physical Test Guide

Each test is a concrete, step-by-step procedure you can perform on the
actual hardware. Pass/Fail criteria are listed so you know exactly what
to look for on the LCD and LEDs.

Hardware assumed:
- Raspberry Pi running `piMain.py`
- Pico microcontroller managing board LEDs, sensors, and buttons
- 4-line LCD (20 chars wide) connected via the display server
- Buttons: **OK**, **HINT**, **NEW** (back/new game), number buttons **1–4**

---

## 0 — Pre-test checklist

Before running any test, verify:

- [ ] Raspberry Pi and Pico are both powered
- [ ] UART cable is connected (Pi `/dev/serial0` ↔ Pico)
- [ ] LCD is connected and display server process is running
- [ ] Stockfish is installed at `/usr/games/stockfish`
- [ ] `LICHESS_TOKEN` environment variable is set (for online/puzzle tests)

---

## 1 — Startup

### 1-A  Splash screen

**Steps:**
1. Power on the Raspberry Pi (or run `python piMain.py`)

**Expected:**
- LCD shows `SMARTCHESS` banner for ~1 s
- LCD then shows mode selection menu:
  ```
  Choose mode:
  1) Against PC
  2) Lichess Online
  3) Local 2-player
  4) Puzzles
  ```

**Pass:** Menu appears within 5 s of startup.
**Fail:** LCD stays blank, shows an error, or shows garbage characters.

---

### 1-B  Idle chatter is ignored in mode select

**Steps:**
1. While the mode-select menu is displayed, press **HINT** and then **OK**

**Expected:**
- Menu stays unchanged — neither button navigates away

**Pass:** Menu is still visible and unchanged.
**Fail:** Menu is replaced with an error message or a blank screen.

---

## 2 — VS Computer mode

### 2-A  Setup flow (full)

**Steps:**
1. Press **1** on the Pico to select "Against PC"
2. When asked for difficulty, press **5**
3. When asked for move time, press **3**
4. When asked for colour, press **1** (White)

**Expected:**
- LCD prompts `Difficulty level: / 1 to 8 / OK = cancel` then accepts `5`
- LCD prompts `Computer / move time: / 1 to 8 / OK = cancel` then accepts `3`
- LCD prompts `Select a colour: / 1=White 2=Black / 3=Random / OK = cancel`
- LCD shows `Engine loading...` briefly
- LCD shows `WHITE to move` and the board LEDs go into human-turn state

**Pass:** Game starts with human playing White, engine waiting.
**Fail:** Any step stays stuck, shows an error, or skips to the wrong screen.

---

### 2-B  Cancel setup with OK

**Steps:**
1. Press **1** to enter "Against PC"
2. On the difficulty prompt, press **OK**

**Expected:**
- Returns immediately to the mode selection menu

**Pass:** Mode menu reappears.
**Fail:** Setup continues or the game starts with default values silently.

---

### 2-C  Typing preview

**Steps:**
1. Start a VS Computer game as White
2. On the Pico, begin entering a move by typing the first character of a square (e.g. `e`)

**Expected:**
- LCD updates to show `Enter from: / e` as you type
- After a full from-square (e.g. `e2`), LCD shows the piece name: `White Pawn / e2 → / Enter to:`
- After a full move (e.g. `e2e4`), LCD shows `White Pawn / e2 → e4 / OK to send`

**Pass:** Display tracks each character in real time.
**Fail:** LCD stays on the move prompt and doesn't update while typing.

---

### 2-D  Legal move (human)

**Steps:**
1. As White, enter the move `e2e4`

**Expected:**
- Board registers the move
- LED trail shows `e2 → e4`
- LCD shows `ENGINE thinking`
- After a short pause, engine plays its move with a matching LED trail
- LCD shows the engine's move arrow + `WHITE to move`

**Pass:** Both moves appear on the board with correct LED feedback.
**Fail:** Move is rejected, loop hangs, or engine doesn't reply.

---

### 2-E  Illegal move

**Steps:**
1. As White, enter an illegal move (e.g. `e2e5` — pawn can't jump two rows from the centre)

**Expected:**
- LCD shows `ILLEGAL move: / PAWN e2→e5 / Put it back + OK`
- Pico sends a red-trail LED signal from `e5` back to `e2`
- After pressing **OK**, LCD shows `WHITE to move` again

**Pass:** Illegal move is caught, piece trail reverses, prompt returns.
**Fail:** Move is accepted silently, or the board gets stuck waiting.

---

### 2-F  Hint

**Steps:**
1. During your turn in a VS Computer game, press **HINT**

**Expected:**
- LCD shows `Engine Thinking...` briefly
- LCD then shows the suggested move arrow (e.g. `e2 → e4`)
- Pico lights the hint trail on the board

**Pass:** Hint appears on both LCD and board within a few seconds.
**Fail:** `hint_none` is sent (no hint found), error screen, or no response.

---

### 2-G  Pawn promotion

**Steps:**
1. Arrange a position where a White pawn is on `e7` (use a custom FEN or play to that point)
2. Enter the move `e7e8`

**Expected:**
- LCD shows `Promotion! / 1=Queen / 2=Rook / 3=Bishop / 4=Knight`
- Press **1** to promote to Queen
- Move is registered as `e7e8q` and the queen appears on `e8`

**Pass:** Promotion prompt appears and the selected piece is placed.
**Fail:** Move is played without promotion, or the prompt never appears.

---

### 2-H  Check

**Steps:**
1. Play to a position where your move gives the opponent's king check

**Expected:**
- After the move, Pico receives `check_{square}` and blinks the king's square
- LCD shows the engine's next move prompt

**Pass:** King square blinks after a check-giving move.
**Fail:** No blink, or the blink happens on the wrong square.

---

### 2-I  Checkmate / game over

**Steps:**
1. Reach a checkmate position (Scholar's Mate is quickest to set up manually)

**Expected:**
- LCD shows `GAME OVER / White wins` (or Black, depending on who mates)
- `GameOver:1-0` (or `0-1`) is sent to the Pico
- Pressing **NEW** returns to the mode selection menu

**Pass:** Game-over screen appears with correct result.
**Fail:** Game continues after checkmate, or wrong winner is shown.

---

### 2-J  Draw — 50-move rule (auto-declare)

**Steps:**
1. Reach a 50-move-rule draw position (or test by reaching any of: 5-fold repetition, 75-move rule, 3-fold repetition, 50-move rule)

**Expected:**
- Game automatically declares the draw without waiting for a claim
- LCD shows `GAME OVER` + draw reason + move number
- Pico receives `GameOver:1/2-1/2`

**Pass:** Draw is detected and declared automatically.
**Fail:** Game continues past the draw threshold.

---

### 2-K  Skill level extremes

**Steps:**
1. Start a new VS Computer game, select skill **1**
2. Play 5 moves and note engine quality
3. Repeat with skill **8**

**Expected:**
- Skill 1 → engine plays obvious blunders / weak moves
- Skill 8 → engine plays much stronger moves

**Pass:** Noticeable quality difference between the two settings.
**Fail:** Engine plays at the same strength regardless of setting.

---

## 3 — Local 2-player mode

### 3-A  Basic game flow

**Steps:**
1. Select mode **3** (Local 2-player)
2. LCD shows `Local 2-Player / Hints enabled` for 2 s, then `WHITE to move`
3. Enter a move as White, then enter a move as Black

**Expected:**
- Moves alternate between White and Black
- Board turn LED indicator switches sides after each move

**Pass:** Both sides can enter moves and the board tracks turns correctly.
**Fail:** One side's moves are rejected, or turn indicator doesn't switch.

---

### 3-B  Hint in local mode

**Steps:**
1. In a local 2-player game, press **HINT** on either player's turn

**Expected:**
- Engine computes a suggestion and shows it on the LCD + board LEDs
- The hint is for the side currently to move

**Pass:** Hint appears for both White and Black turns.
**Fail:** Hint is refused, shows "online mode hints disabled", or crashes.

---

### 3-C  Illegal move in local mode

**Steps:**
1. As either player, enter an illegal move

**Expected:**
- Identical behaviour to test 2-E: red trail, put-back prompt, OK to continue

**Pass:** Illegal move handling is the same as in VS Computer mode.
**Fail:** Different (or missing) error message compared to VS Computer.

---

## 4 — Lichess Online mode

### 4-A  AP mode (no WiFi)

**Steps:**
1. Put the Pi into WiFi AP mode (or disconnect from all networks)
2. Select mode **2** (Lichess Online)

**Expected:**
- LCD shows a QR code or URL like `http://192.168.4.1/` with "Scan to setup WiFi"
- Pressing **OK** returns to the mode menu

**Pass:** QR code or setup URL is displayed instead of attempting a connection.
**Fail:** Tries to connect anyway and shows a generic error.

---

### 4-B  Offline error handling

**Steps:**
1. Ensure the Pi has WiFi but no internet access (or an invalid `LICHESS_TOKEN`)
2. Select mode **2**

**Expected:**
- After up to 3 retry attempts, LCD shows `Lichess offline / WiFi/DNS error / OK = Menu`
- Pressing **OK** returns to the mode menu

**Pass:** Error screen appears and OK returns to menu.
**Fail:** Hangs indefinitely or crashes.

---

### 4-C  Waiting for game

**Steps:**
1. Ensure the Pi has a valid internet connection and `LICHESS_TOKEN`
2. Select mode **2**

**Expected:**
- LCD shows `Lichess online / Start a game / on lichess.org / OK = cancel`
- Display refreshes with `Waiting for game...` every ~1.5 s
- Pressing **OK** cancels and returns to menu

**Pass:** Waiting banner cycles and OK cancels cleanly.
**Fail:** Freezes on the connecting screen or crashes.

---

### 4-D  Playing a game online

**Steps:**
1. Start a game on lichess.org using the account whose token is configured
2. The board should connect and show `Connected / You are WHITE` (or BLACK)

**Expected:**
- Your turn: LCD shows `WHITE to move` and Pico accepts move input
- After entering a valid move, it is submitted to Lichess
- Opponent's move arrives automatically with LED trail
- LCD shows arrow + `BLACK to move`

**Pass:** Full two-way move exchange works over the network.
**Fail:** Moves aren't sent, opponent moves don't appear, or board state desyncs.

---

### 4-E  Draw offer

**Steps:**
1. During an active online game, send a draw signal from the Pico (button mapped to `draw` or `btn_draw`)

**Expected:**
- LCD shows `Offering draw...` briefly
- Draw offer is sent to Lichess (visible on the Lichess website)

**Pass:** Draw offer appears on Lichess.
**Fail:** Nothing happens, or an error is shown.

---

### 4-F  Resign (back/new button during online game)

**Steps:**
1. During an active online game, press **NEW** (back)

**Expected:**
- LCD shows `Resigning...`
- Game is resigned on Lichess
- Returns to mode selection menu

**Pass:** Resignation is registered on Lichess and menu reappears.
**Fail:** Hangs on "Resigning..." or returns to menu without resigning.

---

## 5 — Puzzle mode

### 5-A  Daily puzzle setup flow

**Steps:**
1. Select mode **4** (Puzzles)
2. Select **1) Daily Puzzle** from the puzzle menu

**Expected:**
- LCD shows `Puzzle / Loading…`
- After loading: `<theme> • <rating> / Setup position / OK = next`
- Each **OK** press steps through piece placement one by one, with LEDs indicating where to place each piece
- After the final piece: `<theme> / Setup done / Puzzle begins`
- Board LEDs switch to the starting position; LCD shows your colour and `Enter move:`

**Pass:** All placement steps appear in order and setup completes cleanly.
**Fail:** Steps are skipped, out of order, or the board state after setup doesn't match the expected FEN.

---

### 5-B  Correct puzzle move

**Steps:**
1. After setup, enter the first move of the puzzle solution

**Expected:**
- LCD shows `Correct move! / e2→e4` (or whichever the move is) for 2 s
- If there is an opponent reply in the solution, the opponent plays automatically with LED trail
- LCD shows `<OPP_COLOUR> played / <move> / OK = continue`
- After pressing **OK**, LCD returns to `Enter move:`

**Pass:** Correct moves are accepted and auto-reply plays.
**Fail:** Correct move is rejected as wrong, or auto-reply doesn't trigger.

---

### 5-C  Wrong puzzle move

**Steps:**
1. After setup, enter any move that is NOT the expected solution move

**Expected:**
- LCD shows `Incorrect move: / <piece> <from>→<to> / Put it back + OK`
- Pico sends red trail to put the piece back
- After **OK**, LCD shows `Enter move:` again to retry

**Pass:** Wrong move triggers the red-trail and retry flow.
**Fail:** Wrong move is accepted as correct, or the board gets stuck.

---

### 5-D  Puzzle hint

**Steps:**
1. During a puzzle, press **HINT**

**Expected:**
- Board LEDs show the expected move trail
- LCD shows `Hint: / e2→e4` (or the actual expected move)

**Pass:** Hint shows the correct next solution move.
**Fail:** Hint shows a different move (engine suggestion instead of solution), or nothing happens.

---

### 5-E  Puzzle solved

**Steps:**
1. Enter all moves in the solution correctly

**Expected:**
- LCD shows `Puzzle solved! / OK = menu`
- Pressing **OK** returns to the mode selection menu

**Pass:** Solved message appears after the final move.
**Fail:** Game continues past the solution, or the menu doesn't return.

---

### 5-F  Mix and match puzzle

**Steps:**
1. Select mode **4**, then **2) Mix and match**

**Expected:**
- A random puzzle is fetched from the local `puzzle_ids.txt` file
- Setup and solve flow is identical to 5-A through 5-E

**Pass:** A puzzle loads and plays correctly.
**Fail:** Error "puzzle_ids.txt missing" when the file exists, or a corrupt puzzle loads.

---

### 5-G  Theme → Phases

**Steps:**
1. Select mode **4 → 3) Themes → 1) Phases**
2. Choose **Endgame**

**Expected:**
- A puzzle tagged with the `endgame` theme is fetched
- Setup and solve flow works as normal

**Pass:** An endgame puzzle loads.
**Fail:** A puzzle from a different phase loads, or fetch fails without error.

---

### 5-H  Theme → Openings navigation

**Steps:**
1. Select mode **4 → 3) Themes → 2) Openings**
2. Choose group **A to E**
3. Choose **Caro-Kann Defense**

**Expected:**
- A puzzle tagged with the Caro-Kann opening is fetched
- HINT button scrolls through pages when a group has more than 4 options

**Pass:** Correct opening puzzle loads.
**Fail:** Wrong opening, or the paged menu doesn't scroll on HINT.

---

## 6 — Cross-mode edge cases

### 6-A  Shutdown signal

**Steps:**
1. In any active game, trigger the `shutdown` signal from the Pico (physical power button or mapped input)

**Expected:**
- LCD shows `Shutting down... / Wait 20s then / disconnect power.`
- Raspberry Pi shuts down after ~2 s

**Pass:** Shutdown message appears and Pi powers off.
**Fail:** Shutdown is ignored, or Pi crashes without the message.

---

### 6-B  NEW button from inside a game

**Steps:**
1. While in any game (VS Computer or Local), press **NEW** mid-game

**Expected:**
- Game exits immediately
- Mode selection menu reappears

**Pass:** Returns to menu from any point in a game.
**Fail:** Game continues, or the Pi crashes.

---

### 6-C  Capture query

**Steps:**
1. In any game, move a piece to a square that contains an opponent piece (capture)
2. The Pico should send `capq_<from><to>` before lighting the capture LED

**Expected:**
- Pi replies `capr_1` (capture confirmed) and Pico uses the capture LED colour

**Steps for non-capture:**
1. Move to an empty square
2. Pico sends `capq_<from><to>`

**Expected:**
- Pi replies `capr_0` and Pico uses the normal move LED colour

**Pass:** `capr_1` for captures and `capr_0` for non-captures in both cases.
**Fail:** Wrong reply code, or capture LEDs don't switch.

---

### 6-D  Paged menu navigation

**Steps:**
1. Enter puzzle mode → Themes → Openings → group "A to E" (has more than 4 items)

**Expected:**
- First page shows 4 openings, with `1/N` page counter on line 1
- Pressing **HINT** advances to the next page
- Pressing **HINT** on the last page wraps back to page 1
- Pressing **OK** cancels the menu

**Pass:** Pages advance correctly and cancel works.
**Fail:** HINT is ignored, pages are in the wrong order, or an opening from the wrong page is selected.

---

## 7 — Regression tests after code changes

Run these whenever a file in `core/` is modified.

| # | Scenario | Module touched |
|---|---|---|
| R1 | Legal move in VS Computer plays correctly | `game_flow.validate_and_push_move` |
| R2 | Check blink appears after a check-giving move | `game_flow.send_check_signal` |
| R3 | Capture query answered correctly in all modes | `game_flow.handle_capq_message` |
| R4 | Typing preview updates on every character | `game_flow.handle_typing_message` |
| R5 | Illegal move shows red trail and retries | `game_flow.handle_illegal_move` |
| R6 | Pawn promotion prompt appears on rank 8/1 | `game_flow.resolve_uci_promotion` |
| R7 | Draw is auto-declared at 50/75/repetition | `game_flow._check_and_handle_draw` |
| R8 | Hint shows solution move in puzzle mode | `puzzle_controller.PuzzleController.run` |
| R9 | Resign submits to Lichess and returns to menu | `online_controller._resign_and_exit` |
| R10 | Engine skill level changes between games | `stockfish_opponent.StockfishOpponent._ensure_configured` |
