# Lichess Online Mode

This mode uses the Lichess Board API and now has its own on-board menu flow.

## 1) Create a token

Create a Lichess API token with scope:

- `board:play`

Store it in an environment file or export it directly:

```bash
LICHESS_TOKEN=lip_...
```

## 2) systemd

If you launch the project with `systemd`, add an environment file under
`[Service]`:

```ini
EnvironmentFile=/home/king/SmarterChess-DIY2026/.env
```

## 3) Enter the mode on the board

From the board UI, select:

1. `Play Chess!`
2. `Lichess Online`

If WiFi is not ready yet, the board can show a captive-portal QR code or setup
URL first. Finish WiFi setup, then return to `Lichess Online`.

## 4) Current online menu

After connection and account verification, the board shows:

- `New Game`
- `Ongoing Games`
- `Challenge Received`

Use these for the current supported flows:

- `New Game`
  - `Challenge Friend`
  - `Quick Pairing`
  - `Correspondence`
- `Ongoing Games`
  - resume an active game and guide the board into the current position if
    needed
- `Challenge Received`
  - accept a pending incoming challenge from the board

## 5) Manual start options

You can still start or accept a game from a browser or phone first, then resume
it on the board:

1. create or accept the game on lichess.org
2. go to `Play Chess! -> Lichess Online`
3. choose `Ongoing Games` to attach to it

If someone has just challenged you, `Challenge Received` is the direct path to
accept and start from the board.

## Notes

- Hints are disabled in online mode.
- During an active online game, `OK + HINT` opens the leave menu.
- `Exit to menu` leaves the board UI without resigning, so the game can be
  resumed later from `Ongoing Games`.
