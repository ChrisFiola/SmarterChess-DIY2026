# SmarterChess Menu Guide

This guide documents the current menu system implemented in:

- `RaspberryPiCode/core/game_flow.py`
- `RaspberryPiCode/modes/online/online_controller.py`
- `PicoCode/main/main.py`

It is focused on what appears on the LCD and how to interact with each menu.

## LCD context

The hardware uses a Waveshare 1.14" LCD, but the menu renderer formats menus
as a logical 20-character by 4-line text frame. Full-screen QR codes and
status screens can use the whole display, but the menu layouts below match the
actual menu formatter.

```text
Physical LCD: 1.14"
+--------------------+
|20 chars per line   |
|4 text rows         |
|centered and        |
|auto-sized          |
+--------------------+
```

Notes:

- Paged menus show up to 4 selectable items at once.
- If a menu has fewer than 4 visible items, the title is shown on line 1.
- If a menu has 4 visible items, line 1 becomes item `1)` and may include the
  page number suffix like `1/2`.
- Long labels are truncated to fit the screen.

## Controls

### Paged menus

- `1`, `2`, `3`, `4`: select the visible item on that line
- `HINT`: next page
- `OK`: back/cancel
- `5`, `6`, `7`, `8`: ignored in paged menus

### Numeric setup prompts

- `1` to `8`: choose the displayed value
- `OK`: cancel and go back

These prompts are used for:

- LED brightness

### Difficulty selector (VS Computer)

- `1` (button 1): increase level
- `2` (button 2): decrease level
- Hold `1` or `2`: continuous increment/decrement
- `OK`: confirm selection
- `HINT`: cancel and go back to menu

### Color and promotion prompts

- Color picker: `1=White`, `2=Black`, `3=Random`, `OK=cancel`
- Promotion picker: `1=Queen`, `2=Rook`, `3=Bishop`, `4=Knight`

### Global shortcuts during games

- Tap `HINT`: ask for a hint where hints are enabled
- Press `OK` and `HINT` together: open the "Leave game?" menu (online: offer draw / resign / exit; offline: exit to menu)
- Hold physical button `8` for about 2 seconds: shutdown path on the Pico

## Full menu tree

```text
Game Mode
|- Play Chess!
|  |- Against PC
|  |  |- Difficulty (increment/decrement selector)
|  |  `- Player color
|  |- Local 2-player
|  |- Lichess Online
|  |  |- Online
|  |  |  |- New Game
|  |  |  |  |- Challenge Friend
|  |  |  |  |  |- Friend list (dynamic)
|  |  |  |  |  `- Time Control
|  |  |  |  |- Quick Pairing
|  |  |  |  `- Correspondence
|  |  |  |- Ongoing Games (dynamic)
|  |  |  |- Challenge Received
|  |  |  `- Leave game? (during an active online game)
|  `- Chess.com Daily
|     |- My Turn
|     |- All Games
|     |- Resign (when Connected Board API is configured)
|     `- Change User
|- Puzzles
|  |- Daily Puzzle
|  |- Mix and match
|  `- Themes
|     |- Phases
|     `- Openings
|        |- A to E
|        |- F to I
|        |- K to N
|        |- O to R
|        |- S to V
|        `- W to Z
|- Studies (dynamic, from studies.txt)
|  `- Chapter list (per study)
`- Settings
   |- Brightness
   `- Update
```

## Main menu

`Game Mode` now opens on a single compact page.

### Main page

```text
+--------------------+
|1) Play Chess!      |
|2) Puzzles          |
|3) Studies          |
|4) Settings         |
+--------------------+
```

### Play Chess! submenu

```text
+--------------------+
|1) Against PC       |
|2) Local 2-player   |
|3) Lichess Online   |
|4) Chess.com Daily  |
+--------------------+
```

Interaction:

- Press `1` on the main page for `Play Chess!`, then `1` to `4` to choose a chess mode.
- Press `2` on the main page for `Puzzles`.
- Press `3` on the main page for `Studies`.
- Press `4` on the main page for `Settings`.
- Press `OK` to cancel/back, though the main loop will simply keep you in mode
  selection.

## Against PC

Selecting `Against PC` does not open another paged menu. It enters a sequence
of setup prompts.

### Intro screen

```text
+--------------------+
|VS Computer         |
|Hints enabled       |
|                    |
|                    |
+--------------------+
```

### Difficulty selector

The difficulty selector uses an increment/decrement interface with live Elo
display. Button 1 increases, button 2 decreases, and holding either repeats.
The board shows a bar graph on row 4 (LEDs 1–8) previewing the level.

```text
+--------------------+
|Difficulty:         |
|~1200 Elo           |
|1=Up 2=Down         |
|OK=Go  Hint=Back    |
+--------------------+
```

After confirming with OK:

```text
+--------------------+
|Level 5             |
|~1200 Elo           |
|                    |
|                    |
+--------------------+
```

Move time is no longer a separate prompt — each level has a fixed think time
built into the difficulty ramp (500 ms at level 1 up to 3000 ms at level 8).

### Color prompt

```text
+--------------------+
|Select a colour:    |
|1=White 2=Black     |
|3=Random            |
|OK = cancel         |
+--------------------+
```

Interaction:

- Use `1` (up) and `2` (down) to adjust difficulty; hold for continuous change.
- Press `OK` to confirm difficulty, or `HINT` to cancel back to menu.
- Use `1`, `2`, or `3` to set player color.
- `OK` cancels the colour prompt and returns to the main menu.

## Local 2-player

This mode has no submenu. Selecting it shows an info screen and then starts the
game.

```text
+--------------------+
|Local 2-Player      |
|Hints enabled       |
|                    |
|                    |
+--------------------+
```

## Settings

`Settings` is a 2-item paged menu.

```text
+--------------------+
|Settings            |
|1) Brightness       |
|2) Update           |
|OK=back Hint=next   |
+--------------------+
```

### Brightness

Selecting `Brightness` opens a numeric setup prompt, not another paged menu.

```text
+--------------------+
|LED Brightness      |
|1=dim  8=bright     |
|Current: <level>    |
|OK = cancel         |
+--------------------+
```

Interaction:

- Press `1` to `8` to pick the new brightness level.
- Press `OK` to cancel.
- After a confirmed change, the Pico reboots and the system returns to the main
  menu flow.

### Update

Selecting `Update` immediately starts the update flow. There is no extra
confirmation menu first.

Typical screens are:

```text
+--------------------+
|Checking for        |
|updates...          |
|                    |
|                    |
+--------------------+
```

```text
+--------------------+
|Already up          |
|to date!            |
|                    |
|                    |
+--------------------+
```

## Lichess Online

Before the online menu appears, the system shows connection and account status
screens such as:

```text
+--------------------+
|Lichess             |
|Connecting...       |
|OK = cancel         |
|                    |
+--------------------+
```

If WiFi is in AP setup mode, the display can switch to a full-screen QR code
instead of a text menu.

### Online menu

```text
+--------------------+
|Online              |
|1) New Game         |
|2) Ongoing Games    |
|3) Challenge Receiv.|
+--------------------+
```

The controller also shows a header screen before this menu:

```text
+--------------------+
|Lichess             |
|<your_username>     |
|OK=back             |
|                    |
+--------------------+
```

### New Game menu

```text
+--------------------+
|New Game            |
|1) Challenge Friend |
|2) Quick Pairing    |
|3) Correspondence   |
+--------------------+
```

### Challenge Friend

This branch uses two menus:

1. A dynamic friend-list menu built from the Lichess accounts you follow
2. A fixed `Time Control` menu

Friend-list screen shape:

```text
+--------------------+
|Challenge Friend    |
|1) <friend_name>    |
|2) <friend_name>    |
|3) <friend_name>    |
+--------------------+
```

Notes:

- Long usernames are truncated to fit the line.
- If there are multiple pages, `HINT` advances to the next page.
- If 4 names are visible on a page, the first line becomes item `1)` with a
  page suffix, just like other 4-item menus.

### Time Control menu

This menu appears after selecting a friend.

Page 1 of 2:

```text
+--------------------+
|1) 3+0 Blitz 1/2    |
|2) 5+0 Blitz        |
|3) 5+3 Blitz        |
|4) 10+0 Rapid       |
+--------------------+
```

Page 2 of 2:

```text
+--------------------+
|Time Control 2/2    |
|1) 10+5 Rapid       |
|2) 15+10 Rapid      |
|3) 30+0 Classical   |
|                    |
+--------------------+
```

### Quick Pairing menu

Page 1 of 2:

```text
+--------------------+
|1) 10+0 Rapid 1/2   |
|2) 10+5 Rapid       |
|3) 15+10 Rapid      |
|4) 30+0 Classical   |
+--------------------+
```

Page 2 of 2:

```text
+--------------------+
|1) 30+20 Classi 2/2 |
|2) 3d Corr Random   |
|                    |
|OK=back Hint=next   |
+--------------------+
```

### Correspondence

Selecting this opens your friend list, then challenges the selected friend to a
casual 3-day correspondence game.

Typical screen:

```text
+--------------------+
|Challenging         |
|<friend_name>...    |
|OK = cancel         |
|                    |
+--------------------+
```

### Ongoing Games

This is a dynamic paged menu built from your active games.

Label format:

- `W vs OpponentName`
- `B vs OpponentName`

Example:

```text
+--------------------+
|Ongoing Games       |
|1) W vs Alice       |
|2) B vs Bob         |
|3) W vs Carol       |
+--------------------+
```

If there are multiple pages, a page suffix is added where the menu renderer has
room for it.

### Challenge Received

If Lichess has any incoming challenges waiting, this branch shows a dynamic
paged menu of challengers. Selecting one accepts the challenge and waits for
the game to start.

Typical accept screen:

```text
+--------------------+
|Accepting           |
|<friend_name>...    |
|OK = cancel         |
|                    |
+--------------------+
```

### Leave game? menu

During an active online game, pressing `OK` and `HINT` together opens the
leave menu. The options change depending on whether a draw offer is pending:

Default:

```text
+--------------------+
|1) Offer draw       |
|2) Resign           |
|3) Exit to menu     |
|OK=back Hint=next   |
+--------------------+
```

When opponent has offered a draw:

```text
+--------------------+
|1) Accept draw      |
|2) Decline draw     |
|3) Resign           |
|4) Exit to menu     |
+--------------------+
```

Interaction:

- `Offer draw` sends a draw offer to Lichess
- `Accept/Decline draw` responds to the opponent's draw offer
- `Resign` resigns the Lichess game
- `Exit to menu` leaves the board UI without resigning, so the game can be resumed later
- `OK` backs out of the leave menu and resumes the game

## Chess.com Daily

After selecting `Chess.com Daily`, the controller verifies the configured
username and then shows one of these headers:

```text
+--------------------+
|Chess.com           |
|<your_username>     |
|Connected Board     |
|                    |
+--------------------+
```

or, if the Connected Board API is not configured:

```text
+--------------------+
|Chess.com           |
|<your_username>     |
|Read-only mode      |
|                    |
+--------------------+
```

### Chess.com menu

When the Connected Board API is configured:

```text
+--------------------+
|1) My Turn          |
|2) All Games        |
|3) Resign           |
|4) Change User      |
+--------------------+
```

Without the Connected Board API, the `Resign` item is omitted and the menu is a
3-item paged menu with a title line.

Interaction:

- `My Turn` filters to games where it is your turn
- `All Games` shows ongoing daily games
- `Resign` resigns the currently active game when supported
- `Change User` lets you enter a different Chess.com username from the board

## Puzzles

### Puzzles top menu

```text
+--------------------+
|PUZZLES             |
|1) Daily Puzzle     |
|2) Mix and match    |
|3) Themes           |
+--------------------+
```

### Themes menu

```text
+--------------------+
|THEMES              |
|1) Phases           |
|2) Openings         |
|OK=back Hint=next   |
+--------------------+
```

### Phases menu

Page 1 of 2:

```text
+--------------------+
|1) Opening 1/2      |
|2) Middlegame       |
|3) Endgame          |
|4) Rook endgame     |
+--------------------+
```

Page 2 of 2:

```text
+--------------------+
|1) Bishop endgam 2/2|
|2) Pawn endgame     |
|3) Knight endgame   |
|4) Queen endgame    |
+--------------------+
```

Available phase options:

- Opening
- Middlegame
- Endgame
- Rook endgame
- Bishop endgame
- Pawn endgame
- Knight endgame
- Queen endgame

### Openings group menu

Page 1 of 2:

```text
+--------------------+
|1) A to E 1/2       |
|2) F to I           |
|3) K to N           |
|4) O to R           |
+--------------------+
```

Page 2 of 2:

```text
+--------------------+
|OPENINGS 2/2        |
|1) S to V           |
|2) W to Z           |
|OK=back Hint=next   |
+--------------------+
```

Available opening groups:

- `A to E`
- `F to I`
- `K to N`
- `O to R`
- `S to V`
- `W to Z`

Each group opens another paged menu using the same `1-4 select / HINT next /
OK back` interaction.

Opening names by group:

### A to E

- Alekhine Defense
- Amar Opening
- Amazon Attack
- Anderssen's Opening
- Barnes Defense
- Barnes Opening
- Benko Gambit
- Benko Gambit Accepted
- Benko Gambit Declined
- Benoni Defense
- Bird Opening
- Bishop's Opening
- Blackmar Gambit
- Blackmar Gambit Accepted
- Blackmar Gambit Declined
- Blumenfeld Countergambit
- Bogo-Indian Defense
- Borg Defense
- Canard Opening
- Caro-Kann Defense
- Carr Defense
- Catalan Opening
- Center Game
- Center Counter
- Clemenz Opening
- Czech Defense
- Danish Gambit
- Danish Gambit Accepted
- Danish Gambit Declined
- Dutch Defense
- East Indian Defense
- Elephant Gambit
- English Defense
- English Opening
- Englund Gambit
- Englund Gambit Declined

### F to I

- French Defense
- Fried Fox Defense
- Goldsmith Defense
- Grob Opening
- Grunfeld Defense
- Gunderam Defense
- Hippopotamus Defense
- Horwitz Defense
- Hungarian Opening
- Indian Defense
- Italian Game

### K to N

- Kangaroo Defense
- King's Gambit
- King's Gambit Accepted
- King's Gambit Declined
- King's Indian Attack
- King's Indian Defense
- King's Knight Opening
- King's Pawn Game
- King's Pawn Opening
- Kadas Opening
- Lasker Simul Special
- Latvian Gambit
- Latvian Gambit Accepted
- Lemming Defense
- Lion Defense
- London System
- Mexican Defense
- Mieses Opening
- Mikenas Defense
- Modern Defense
- Neo-Grunfeld Defense
- Nimzo-Indian Defense
- Nimzo-Larsen Attack
- Nimzowitsch Defense

### O to R

- Old Indian Defense
- Owen Defense
- Paleface Attack
- Petrov's Defense
- Philidor Defense
- Pirc Defense
- Polish Defense
- Polish Opening
- Ponziani Opening
- Portuguese Defense
- Pseudo-Queen's Indian Defense
- Pterodactyl Defense
- Queen's Gambit
- Queen's Gambit Accepted
- Queen's Gambit Declined
- Queen's Indian Accelerated
- Queen's Indian Defense
- Queen's Pawn Game
- Rapport-Jobava System
- Rat Defense
- Richter-Veresov Attack
- Robatsch Defense
- Rubinstein Opening
- Ruy Lopez
- Reti Opening

### S to V

- Saragossa Opening
- Scandinavian Defense
- Scotch Game
- Semi-Slav Defense
- Sicilian Defense
- Slav Defense
- Slav Indian
- Sodium Attack
- St. George Defense
- Tarrasch Defense
- Three Knights Game
- Torre Attack
- Trompowsky Attack
- Van Geet Opening
- Van't Kruijs Opening
- Vienna Gambit
- Vienna Game

### W to Z

- Wade Defense
- Ware Defense
- Ware Opening
- Yusupov-Rubinstein System
- Zukertort Opening

## Other interactive menu-like screens

These are not part of the main mode tree, but they are still user-driven
selection screens.

### Promotion picker

```text
+--------------------+
|Promotion!          |
|1=Queen             |
|2=Rook              |
|3=Bishop            |
|4=Knight            |
+--------------------+
```

Interaction:

- Press `1`, `2`, `3`, or `4` to choose the promotion piece.

### Post-game PGN prompt

After local and VS Computer games finish, the player is prompted to view a QR
code that opens the game in Lichess analysis.

```text
+--------------------+
|Press OK            |
|to view analysis    |
|                    |
|                    |
+--------------------+
```

Pressing `OK` shows a full-screen QR code.

Pressing `OK` on the QR screen returns directly to the main menu. There is no
second confirmation prompt after the QR screen.

## Behavior details and quirks

- Menus always use `HINT` for next page. There is no previous-page button.
- The top-level menu and `Play Chess!` submenu use a compact 3-item layout with
  `OK=back Hint=next` on the fourth line.
- Dynamic menus truncate names to fit the LCD width.
- The online and puzzle systems also show non-menu status screens such as
  `Loading...`, `Waiting for opponent...`, and QR codes. Those are not paged
  menus, but they still use the same `OK` cancel/back behavior where noted.
