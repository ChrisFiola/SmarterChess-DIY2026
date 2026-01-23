
from logic.turn_logic import handle_promotion_choice, collect_and_send_move

def _hard_reset_board(state, cp, board, buttons, disable_hint_irq):
    print("[RESET] Hard board reset")
    state.in_input = False
    state.in_setup = False
    state.confirm_mode = False
    state.hint_irq_flag = False
    state.hint_hold_mode = False
    state.showing_hint = False

    disable_hint_irq()
    buttons.reset()

    cp.fill((0, 0, 0))
    board.clear((0, 0, 0))
    board.show_markings()

def main_loop(state, link, cp, board, buttons, enable_hint_irq, disable_hint_irq, process_hint_irq):
    """
    Core runtime:
      - process asynchronous hint/newgame via IRQ flag
      - react to Pi messages:
          * heyArduinoGameStart
          * heyArduinom<uci>
          * heyArduinoturn_*
          * heyArduinoerror_*
          * heyArduinopromotion_choice_needed
          * heyArduinoChooseMode (re-entry to setup)
    """
    print("Entering main loop")
    import time

    while True:
        # Consume any queued IRQ (hint/newgame)
        irq = process_hint_irq()
        if irq == "new":
            state.showing_hint = False
            state.hint_hold_mode = False
            state.hint_irq_flag = False
            disable_hint_irq()
            cp.hint(False)
            cp.coord(False)
            board.show_markings()
            continue

        msg = link.read_from_pi()
        if not msg:
            time.sleep_ms(10)
            continue

        if msg.startswith("heyArduinoResetBoard"):
            _hard_reset_board(state, cp, board, buttons, disable_hint_irq)
            continue

        # Re-entry to Mode Selection after 'n'
        if msg.startswith("heyArduinoChooseMode"):
            state.showing_hint = False
            state.hint_hold_mode = False
            state.hint_irq_flag = False
            state.hint_waiting = False

            disable_hint_irq()
            buttons.reset()

            cp.hint(False)
            board.show_markings()
            cp.fill((255, 255, 255), 0, 5)  # coord LEDs

            state.game_state = state.GAME_SETUP
            from logic import setup_menu
            setup_menu.select_game_mode(buttons, board, cp, link)
            while state.game_state == state.GAME_SETUP:
                setup_menu.wait_for_setup(state, link, buttons, enable_hint_irq)
            continue

        if msg.startswith("heyArduinoGameStart"):
            continue

        # Pi played a move: heyArduinom<uci>
        if msg.startswith("heyArduinom"):
            mv = msg[11:].strip()
            print("Pi move:", mv)
            board.light_up_move(mv, 'N')
            cp.hint(True, (255, 255, 255))  # white
            time.sleep_ms(250)
            board.show_markings()
            continue

        # Promotion choice needed
        if msg.startswith("heyArduinopromotion_choice_needed"):
            handle_promotion_choice(state, buttons, link, process_hint_irq)
            continue

        # Hint from Pi: heyArduinohint_<uci> (ignore typing echos)
        if msg.startswith("heyArduinohint_") and not msg.startswith("heypityping_"):
            best = msg[len("heyArduinohint_"):].strip()
            state.showing_hint = True
            board.light_up_move(best, 'H')
            link.send_typing_preview("hint", f"Hint: {best} — enter move to continue")
            cp.hint(True, (0, 0, 255))  # blue
            continue

        # Error from Pi -> flash error and re-enter move
        if msg.startswith("heyArduinoerror"):
            print("[ERROR from Pi]:", msg)
            board.error_flash()
            state.hint_hold_mode = False
            state.hint_irq_flag = False
            state.showing_hint = False
            board.show_markings()
            cp.coord(True)

            from logic.move_entry import enter_from_square, enter_to_square, confirm_move_or_reenter
            collect_and_send_move(
                state, cp, board, buttons, link, process_hint_irq,
                enter_from_square, enter_to_square, confirm_move_or_reenter
            )
            continue

        # Human's turn
        if msg.startswith("heyArduinoturn_"):
            from logic.move_entry import enter_from_square, enter_to_square, confirm_move_or_reenter
            collect_and_send_move(
                state, cp, board, buttons, link, process_hint_irq,
                enter_from_square, enter_to_square, confirm_move_or_reenter
            )
            continue

        # Unknown messages can be ignored
