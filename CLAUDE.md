# SmarterChess DIY 2026

## Project Overview
Physical chess board with Reed switch matrix, Raspberry Pi Zero, Pico W, LED grid, and TFT display.

## Hardware
- **Raspberry Pi Zero** — game logic, Stockfish engine, WiFi/Lichess, UART to Pico, display + touch
- **Pico W** — button/LED control panel, Reed switch matrix (board detection), UART bridge to Pi
- **Display** — ILI9341 2.8" TFT (240×320 portrait) on Pi Zero SPI0
- **Touch** — XPT2046 resistive touch controller on Pi Zero SPI0
- **LEDs** — 22-pixel NeoPixel panel (control panel) + 8×8 chess board LEDs

## Architecture (Version-2 branch)
- Display + touch on Pi Zero via SPI0
- `display_server.py` runs as subprocess: PIL + TTF fonts (WorkSans, ChessSans, DejaVu)
- Touch events polled by display, forwarded to Pico via UART (`heyArduinotouch_*`)
- Pico blocking loops check UART for touch events alongside physical buttons
- Buttons 1-8 for move input only; OK/Hint via touch screen

## Key Files
| File | Purpose |
|---|---|
| `RaspberryPiCode/piMain.py` | Pi entry point |
| `RaspberryPiCode/screen/display.py` | Display abstraction, touch polling, IPC to server |
| `RaspberryPiCode/screen/display_server.py` | Pi-side PIL renderer subprocess |
| `RaspberryPiCode/core/game_flow.py` | Game state machine |
| `RaspberryPiCode/core/boardlink.py` | UART protocol Pi↔Pico, touch forwarding |
| `PicoCode/main/main.py` | Pico entry point, button/LED logic |
| `PicoCode/main/pico_hw.py` | Pico hardware abstraction (LEDs, buttons) |
| `PicoCode/main/reed_matrix.py` | Reed switch matrix (not yet implemented) |

## Fonts
- `ChessSans.ttf` — chess piece icons (♔♕♖♗♘♙)
- `WorkSans-Medium.ttf` — UI text
- `DejaVuSansMono.ttf` — monospace fallback
