# SmarterChess DIY 2026

SmarterChess is a physical smart chessboard built around a Raspberry Pi and a
Raspberry Pi Pico. The board reads moves from the pieces, drives LEDs for move
guidance, shows menus and QR codes on a small LCD, and supports local play,
engine play, Lichess online games, puzzles, and studies from the same board
interface.

This repo is a modernized continuation of the original DIY Machines project.
The current codebase targets Python 3, Stockfish on Raspberry Pi, and a Pico
instead of the original Arduino-based setup.

## Current features

- Local 2-player mode with hint support
- VS Computer mode with selectable strength and board-side choice
- Lichess Online mode with `New Game`, `Ongoing Games`, and
  `Challenge Received`
- Puzzle mode with daily, random, and themed puzzles
- Study mode with paged chapter text and play/watch options
- Guided board setup for resumed and remote positions
- Post-game QR analysis flow for local and VS Computer games
- WiFi captive-portal / AP fallback for online setup
- Color-coded LED feedback for hints, captures, illegal moves, promotion, and
  game-over states

## Hardware used

- Raspberry Pi Zero W or Raspberry Pi 3B+
- Raspberry Pi Pico
- WS2812 LED strip
- Waveshare 1.14" LCD

## Project layout

- `RaspberryPiCode/` - main application, mode controllers, display server, and
  shared game/menu flow helpers
- `PicoCode/` - Pico firmware for buttons, LEDs, sensors, and serial protocol
- `docs/` - current reference and operator guides

## Docs

- [Code Reference](docs/CODE_REFERENCE.md)
- [Menu Guide](docs/MENU_GUIDE.md)
- [Physical Test Guide](docs/PHYSICAL_TEST_GUIDE.md)
- [Lichess Manual Start](docs/LICHESS_MANUAL_START.md)

## Original project

Original DIY Machines project video:
https://youtu.be/Z92TdhsAWD4

Original project page:
https://www.diymachines.co.uk/smart-chess-board-with-remote-and-local-play
