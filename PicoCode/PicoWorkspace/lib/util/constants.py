
# Pins and hardware layout
BUTTON_PINS = [2, 3, 4, 5, 6, 7, 8, 9]
DEBOUNCE_MS = 20

# Special button roles (indices in BUTTON_PINS)
OK_BUTTON_INDEX = 6     # GP8 -> Button 7 (A1 OK)
HINT_BUTTON_INDEX = 7   # GP9 -> Button 8 (Hint / IRQ)

# LEDs
CONTROL_PANEL_LED_PIN = 12
CONTROL_PANEL_LED_COUNT = 22

CHESSBOARD_LED_PIN = 28
BOARD_W, BOARD_H = 8, 8

# Matrix orientation (Arduino NeoMatrix compatible)
MATRIX_ORIGIN_BOTTOM_RIGHT = True
MATRIX_ZIGZAG = True

# Control Panel pixel roles
CP_COORD_START = 0
CP_OK_PIX      = 4
CP_HINT_PIX    = 5

# Colors (RGB)
BLACK   = (0, 0, 0)
WHITE   = (255, 255, 255)
DIMW    = (10, 10, 10)
RED     = (255, 0, 0)
GREEN   = (0, 255, 0)
BLUE    = (0, 0, 255)
CYAN    = (0, 255, 255)
MAGENTA = (255, 0, 255)
YELLOW  = (255, 255, 0)
ORANGE  = (255, 130, 0)

