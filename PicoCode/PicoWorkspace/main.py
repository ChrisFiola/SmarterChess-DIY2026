
from util.constants import (
    CONTROL_PANEL_LED_PIN, CONTROL_PANEL_LED_COUNT,
    CHESSBOARD_LED_PIN, BOARD_W, BOARD_H,
    BUTTON_PINS, OK_BUTTON_INDEX, HINT_BUTTON_INDEX
)
from util.uart_link import Link
from hardware.control_panel import ControlPanel
from hardware.chessboard import Chessboard
from hardware.buttons import ButtonManager
from logic.game_state import GameState
from logic import hint_irq, setup_menu, runtime
import time

def run():
    print("Pico Chess Controller Starting (modular)")

    # Hardware
    cp = ControlPanel(CONTROL_PANEL_LED_PIN, CONTROL_PANEL_LED_COUNT)
    board = Chessboard(CHESSBOARD_LED_PIN, BOARD_W, BOARD_H)
    buttons = ButtonManager(BUTTON_PINS)
    link = Link(uart_id=0, baudrate=115200, tx_pin=0, rx_pin=1, timeout=10)

    # State
    state = GameState()

    # Initial visuals like Arduino setup()
    cp.fill((0, 0, 0))
    board.clear((0, 0, 0))
    board.opening_markings()
    buttons.reset()

    # IRQ (initialized but disabled during handshake)
    hint_irq.init(state, buttons, cp, board, link, OK_BUTTON_INDEX, HINT_BUTTON_INDEX)
    hint_irq.disable_hint_irq()

    # Handshake: wait for mode, then let the user pick
    setup_menu.wait_for_mode_request(state, board, cp, link)
    setup_menu.select_game_mode(buttons, board, cp, link)

    # Pi may call back into us multiple times to get parameters
    while state.game_state == state.GAME_SETUP:
        setup_menu.wait_for_setup(state, link, buttons, hint_irq.enable_hint_irq)

    # Runtime: process messages forever
    while True:
        runtime.main_loop(
            state=state,
            link=link,
            cp=cp,
            board=board,
            buttons=buttons,
            enable_hint_irq=hint_irq.enable_hint_irq,
            disable_hint_irq=hint_irq.disable_hint_irq,
            process_hint_irq=hint_irq.process_hint_irq
        )
        time.sleep_ms(1)

# Start program
run()
