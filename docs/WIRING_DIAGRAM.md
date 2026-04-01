# SmarterChess Wiring Diagram
## Reed Switch Upgrade + ILI9341 Touchscreen

---

## New Components Required

| Component | Part Number | Qty | Notes |
|-----------|-------------|-----|-------|
| Reed switches | RI-80GP0510 (Comus/Standex) | 64 | 1.8 × 5 mm, SPST-NO |
| Signal diodes | 1N4148 | 64 | Anti-ghosting, one per switch |
| Decoder IC | 74HC138 (DIP-16) | 1 | 3-to-8 row selector |
| Pull-up resistors | 10 kΩ, 1/4 W | 8 | One per column input |
| Neodymium magnets | 6 mm × 2 mm disc | 64+ | One per chess piece, buy spares |
| TFT display module | ILI9341 2.8" with XPT2046 | 1 | SPI, includes touch controller |

---

## Section 1 — Pico GPIO Overview

```
                        ┌─────────────┐
          UART TX ──GP0 ┤1          40├ VBUS ──── 5V in
          UART RX ──GP1 ┤2          39├ VSYS
                 GND ───┤3          38├ GND
     74HC138 A  ──GP2 ┤4          37├ 3V3_EN
     74HC138 B  ──GP3 ┤5          36├ 3V3 OUT ── 3.3V rail
     74HC138 C  ──GP4 ┤6          35├ ADC_REF
     Reed col a ──GP5 ┤7          34├ GP28 ──── Reed col h
     Reed col b ──GP6 ┤8          33├ GND
     Reed col c ──GP7 ┤9          32├ GP27 ──── Reed col g
     Reed col d ──GP8 ┤10         31├ GP26 ──── Reed col f
        OK btn  ──GP9 ┤11         30├ RUN
     Reed col e ─GP10 ┤12         29├ GP22 ──── WS2812 Board LEDs
       HINT btn ─GP11 ┤13         28├ GND
     SPI1 MISO  ─GP12 ┤14         27├ GP21 ──── XPT2046 IRQ
     ILI9341 CS ─GP13 ┤15         26├ GP20 ──── XPT2046 CS
     SPI1 SCK   ─GP14 ┤16         25├ GP19 ──── ILI9341 BL
     SPI1 MOSI  ─GP15 ┤17         24├ GP18 ──── ILI9341 RST
     WS2812 CP  ─GP16 ┤18         23├ GP17 ──── ILI9341 DC
                GND ───┤19         22├ GND
                ────   ┤20  SWCLK 21├ ────
                        └─────────────┘
```

> **Note:** All 26 usable Pico GPIO pins are used. No free pins remain.

---

## Section 2 — ILI9341 + XPT2046 Touchscreen Module

Both the display and touch controller share the same SPI1 bus, selected by separate CS pins.

| Module Pin | Function | Pico GPIO |
|------------|----------|-----------|
| VCC | Power | 3.3V |
| GND | Ground | GND |
| CS | Display chip select | GP13 |
| RESET | Display reset | GP18 |
| DC | Data / command | GP17 |
| SDI / MOSI | SPI data in | GP15 |
| SCK | SPI clock | GP14 |
| LED | Backlight | GP19 (or tie to 3.3V for always-on) |
| SDO / MISO | SPI data out | GP12 |
| T_CS | Touch chip select | GP20 |
| T_CLK | Touch SPI clock | GP14 (shared) |
| T_DIN | Touch MOSI | GP15 (shared) |
| T_DO | Touch MISO | GP12 (shared) |
| T_IRQ | Touch interrupt | GP21 (optional) |

---

## Section 3 — 74HC138 Decoder (Row Select)

The 74HC138 converts a 3-bit address from the Pico into one of 8 active-LOW outputs,
selecting one rank (row) of the reed switch matrix at a time.

```
  74HC138 (DIP-16)
  ┌──────────────┐
  │ Pin 1  A  ───┼──── GP2  (address LSB)
  │ Pin 2  B  ───┼──── GP3  (address)
  │ Pin 3  C  ───┼──── GP4  (address MSB)
  │ Pin 4  G2A───┼──── GND  (enable, always active)
  │ Pin 5  G2B───┼──── GND  (enable, always active)
  │ Pin 6  G1 ───┼──── 3.3V (enable, always active)
  │ Pin 7  Y7 ───┼──── Row wire rank 8
  │ Pin 8  GND───┼──── GND
  │ Pin 9  Y6 ───┼──── Row wire rank 7
  │ Pin 10 Y5 ───┼──── Row wire rank 6
  │ Pin 11 Y4 ───┼──── Row wire rank 5
  │ Pin 12 Y3 ───┼──── Row wire rank 4
  │ Pin 13 Y2 ───┼──── Row wire rank 3
  │ Pin 14 Y1 ───┼──── Row wire rank 2
  │ Pin 15 Y0 ───┼──── Row wire rank 1
  │ Pin 16 VCC───┼──── 3.3V
  └──────────────┘
```

**Address truth table:**

| GP4 (C) | GP3 (B) | GP2 (A) | Active output |
|---------|---------|---------|---------------|
| 0 | 0 | 0 | Y0 — rank 1 |
| 0 | 0 | 1 | Y1 — rank 2 |
| 0 | 1 | 0 | Y2 — rank 3 |
| 0 | 1 | 1 | Y3 — rank 4 |
| 1 | 0 | 0 | Y4 — rank 5 |
| 1 | 0 | 1 | Y5 — rank 6 |
| 1 | 1 | 0 | Y6 — rank 7 |
| 1 | 1 | 1 | Y7 — rank 8 |

When a row is selected, that Y output goes LOW. All others stay HIGH.

---

## Section 4 — Reed Switch Matrix (8×8)

**Column wiring — one GPIO per file with 10 kΩ pull-up to 3.3V:**

| File | GPIO | Pull-up |
|------|------|---------|
| a | GP5 | 10 kΩ to 3.3V |
| b | GP6 | 10 kΩ to 3.3V |
| c | GP7 | 10 kΩ to 3.3V |
| d | GP8 | 10 kΩ to 3.3V |
| e | GP10 | 10 kΩ to 3.3V |
| f | GP26 | 10 kΩ to 3.3V |
| g | GP27 | 10 kΩ to 3.3V |
| h | GP28 | 10 kΩ to 3.3V |

**Matrix layout (viewed from above):**

```
         file a  file b  file c  file d  file e  file f  file g  file h
          GP5     GP6     GP7     GP8    GP10    GP26    GP27    GP28
           │       │       │       │       │       │       │       │
          10k     10k     10k     10k     10k     10k     10k     10k  (to 3.3V)
           │       │       │       │       │       │       │       │
rank 1    [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S] ── Y0
rank 2    [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S] ── Y1
rank 3    [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S] ── Y2
rank 4    [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S] ── Y3
rank 5    [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S] ── Y4
rank 6    [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S] ── Y5
rank 7    [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S] ── Y6
rank 8    [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S]  [D─S] ── Y7

  D = 1N4148 diode    S = RI-80GP0510 reed switch
```

**Single square detail (e.g. e4 = file e, rank 4):**

```
  GP10 ──┬──── 10 kΩ ──── 3.3V
         │
         └── [anode >|< cathode] ── [reed switch] ──── Y3 (74HC138 pin 12)
                  1N4148                RI-80GP0510

  Diode orientation: anode toward column (GP10), cathode toward reed switch / row side.

  Piece present  → switch closes → GP10 pulled LOW through diode → reads 0 = piece detected
  Square empty   → switch open   → GP10 stays HIGH via pull-up  → reads 1 = empty
```

**Why diodes?** Without them, a HIGH (inactive) row can back-drive a column LOW
through a closed switch in that row, causing false detections on other ranks.
The diode blocks reverse current from inactive rows.

**Scanning sequence (software):**
1. Set GP2/GP3/GP4 to address 0 (rank 1) → Y0 goes LOW
2. Read GP5, GP6, GP7, GP8, GP10, GP26, GP27, GP28 → 8 column states for rank 1
3. Set address to 1 (rank 2) → Y1 goes LOW
4. Read all 8 columns → rank 2 states
5. Repeat for ranks 3–8
6. Total: 8 reads × 8 columns = 64 square states per full scan

---

## Section 5 — Existing Connections (Unchanged)

**WS2812 NeoPixel strips (5V powered):**

```
  GP16 ──── DIN ──── Control panel strip (22 pixels)
  GP22 ──── DIN ──── Chess board strip   (64 pixels)

  Both strips powered from a separate 5V supply, NOT from Pico 3.3V.
  A 300–500 Ω resistor in series on the DIN line is recommended.
```

**UART to Raspberry Pi:**

```
  Pico GP0 (TX) ──── RPi GPIO 15 (RX, UART0)
  Pico GP1 (RX) ──── RPi GPIO 14 (TX, UART0)
  Pico GND      ──── RPi GND
  Baud: 115200
```

**Buttons (kept):**

```
  GP9  ── OK button ──── GND   (active LOW, internal pull-up)
  GP11 ── HINT button ── GND   (active LOW, IRQ on falling edge)
```

---

## Section 6 — Power

| Component | Voltage | Source |
|-----------|---------|--------|
| Pico | 5V | USB or VSYS |
| 74HC138 | 3.3V | Pico 3V3 pin |
| ILI9341 module | 3.3V | Pico 3V3 pin |
| Reed switch logic | 3.3V | Pico 3V3 pin (logic only, negligible current) |
| WS2812 LEDs | 5V | **Separate 5V supply** — do not power from Pico 3V3 |
| Pull-up resistors | 3.3V | Pico 3V3 pin |

> The 3.3V rail from the Pico can supply ~300 mA. The ILI9341 module draws ~60–80 mA
> with backlight on. The 74HC138 draws <1 mA. Reed switches draw no steady-state current.
> This is within budget if the WS2812 strips are powered separately.

---

## Section 7 — Shutdown Button Note

GPIO 6 (formerly the H-file button / 2-second hold shutdown) is now a reed switch
column input. The hardware shutdown trigger must be reassigned in software.

**Recommended:** Replace `cp.shutdown_held()` with a 3-second hold of the OK button (GP9).
Update `Config.Buttons.SHUTDOWN_HOLD_MS` and change the shutdown check to monitor `BTN_OK`
instead of the old `_BTN_SHUT` pin.

---

## Physical Assembly Notes (from reference project)

- Mount reed switches **vertically** (perpendicular to the board surface), not flat.
  This narrows the trigger zone and prevents adjacent squares from false-triggering.
- Glue magnets **flat into the base** of each piece, covered with a thin felt pad.
- Test drill depth carefully — the detection range must cover one square only.
  The RI-80GP0510's 5 mm body length is designed for standard board thicknesses.
- Wire rows with colored hookup wire (one color per rank) to simplify debugging.
- Secure all wiring under the board with clear tape, as shown in the reference build.
- Add a 100 nF decoupling capacitor between VCC and GND on the 74HC138.
