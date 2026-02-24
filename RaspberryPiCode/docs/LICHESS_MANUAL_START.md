Manual-start Online Mode (Lichess)

1) Create a Lichess API token with scope: board:play
2) Put it in /home/king/SmarterChess-DIY2026/.env
   LICHESS_TOKEN=lip_...

3) In smartChess.service add:
   EnvironmentFile=/home/king/SmarterChess-DIY2026/.env

4) Restart:
   sudo systemctl daemon-reload
   sudo systemctl restart smartChess.service

5) On the board choose Online mode.
6) Start/accept a game on lichess.org with the same account as the token.
7) The Pi will auto-attach when it sees gameStart.
