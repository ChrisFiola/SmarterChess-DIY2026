# Lichess Online Mode (manual start)

This mode lets you play online on Lichess using the Board API.

## 1) Create a token
Create a Lichess API token with scope:
- `board:play`

Put it in an env var (recommended via systemd EnvironmentFile), e.g.:

```
LICHESS_TOKEN=lip_...
```

## 2) systemd
Add to your service under `[Service]`:

```
EnvironmentFile=/home/king/SmarterChess-DIY2026/.env
```

## 3) Start a game manually
For the first integration:
- start or accept a game on lichess.org (browser/phone)
- then select "Remote human" on your board

The Pi listens on `/api/stream/event` and will auto-attach to the started game.

## Notes
- Hints are disabled in online mode (engine assistance is not allowed).
- If your color cannot be detected, it defaults to White. Once your account is
  correctly detected via `/api/account`, color should be correct.
