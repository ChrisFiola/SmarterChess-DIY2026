# Super Smart Chessboard Revival

## UPDATE 2026
I'm trying to revive this project in 2026. i want to be able to play chess IRL and record my moves so I can study them online.

I am starting from the original DIY Machines's project and will update the code to make it work on a raspberry pi pico instead of arduino.

The original code is not compatible with the latest python updates and is hardly running on the latest Stockfish.

This updated project uses:
- Raspberry Pi Zero W or Raspberry Pi 3B+
- Raspberry Pi Pico
- Amazon's BTF-Lighting WS2812E LED Strip 30Led/Meters
- Smaller 3D Print parts that can print with a CR20 218x218 base plate maximum
- Waveshare 1.14" LCD screen

## NEW FEATURES
- Different colored captured square when inputing and confirming a move
- Game Over changes the board color and animation as well as displaying it on the screen
- Promotions changes the board color and animation as well as displaying a promotion menu on the screen
- Hints display in yellow
- IRL 1v1 implemented to play face to face with hints enabled. Both players have a green color trail.
- VS Computer uses green color trail for the user and blue color trail for the computer
- Invalid move lights the whole board red to indicate a wrong move
- Updated to work on Python3. Not depending on old libraries like Python2 that caused a headache in setting up the original DIY Machines project

## Original README
Play remotely over the internet, or locally against the inbuilt computer.

Project video available at: https://youtu.be/Z92TdhsAWD4

Find wiring diagrams, 3d printable parts and more here: https://www.diymachines.co.uk/smart-chess-board-with-remote-and-local-play
