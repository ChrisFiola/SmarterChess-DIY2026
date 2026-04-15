# SmarterChess DIY 2026

## Project Overview
Physical chess board with Reed switch matrix, Raspberry Pi Zero, Pico W, LED grid, and TFT display.

## Hardware
- **Raspberry Pi Zero** — game logic, Stockfish engine, WiFi/Lichess, UART to Pico
- **Pico W** — button/LED control panel, Reed switch matrix (board detection), UART bridge to Pi
- **Display** — ILI9341 2.8" TFT (240×320 portrait)
- **LEDs** — 22-pixel NeoPixel panel (control panel) + 8×8 chess board LEDs

## Branch Architecture

### `main`
- Display wired to **Pi Zero** via SPI (Waveshare ST7789 LCD_1inch14 driver)
- `display_server.py` runs on Pi: PIL + TTF fonts (WorkSans, ChessSans, DejaVu)
- Nice fonts, chess icons, 15 FPS cap, responsive

### `Version-2` (current)
- Display wired to **Pico W** via SPI1 (ILI9341 tft_ili9341.py)
- Pi sends `DISP:` messages over UART → Pico parses + renders
- Pre-compiled bitmap fonts (freesans12, freesansbold18, freesansbold24)
- Known issues: font quality worse than main, refresh feels slow

## Key Files
| File | Purpose |
|---|---|
| `RaspberryPiCode/piMain.py` | Pi entry point |
| `RaspberryPiCode/screen/display.py` | Display abstraction (IPC to server or UART to Pico) |
| `RaspberryPiCode/screen/display_server.py` | Pi-side PIL renderer (used in main branch) |
| `RaspberryPiCode/core/game_flow.py` | Game state machine |
| `RaspberryPiCode/core/boardlink.py` | UART protocol Pi↔Pico |
| `PicoCode/main/main.py` | Pico entry point |
| `PicoCode/main/tft_ili9341.py` | Pico-side ILI9341 renderer |

## Open Decision (2026-04-15)
**Move display back to Pi Zero?**
- User prefers Pi-driven display: better fonts (TTF/PIL), better refresh, chess icons
- Accepted tradeoff: slow boot time
- `display_server.py` already fully written — just need to rewire hardware + restore server launch in `display.py`
- Check Pi Zero SPI0 pin availability before committing

## Fonts
- `ChessSans.ttf` — chess piece icons (♔♕♖♗♘♙)
- `WorkSans-Medium.ttf` — UI text
- Bitmap fonts on Pico: freesans12, freesansbold18, freesansbold24
