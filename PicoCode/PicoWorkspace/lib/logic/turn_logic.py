import time

def handle_promotion_choice(state, buttons, link, process_hint_irq):
    """
    Pi requests: 'heyArduinopromotion_choice_needed'
    Buttons: 1=Q  2=R  3=B  4=N
    """
    print("[PROMO] Choose: 1=Q  2=R  3=B  4=N")
    buttons.reset()
    import time
    while True:
        irq = process_hint_irq()
        if irq == "new":
            return

        btn = buttons.detect_press()
        if not btn:
            time.sleep_ms(5)
            continue

        if btn == 1:
            link.send_to_pi("btn_q"); return
        if btn == 2:
            link.send_to_pi("btn_r"); return
        if btn == 3:
            link.send_to_pi("btn_b"); return
        if btn == 4:
            link.send_to_pi("btn_n"); return
        # ignore others

def collect_and_send_move(state, cp, board, buttons, link, process_hint_irq, enter_from_square, enter_to_square, confirm_move_or_reenter):
    """
    Collect a player's move (FROM/TO + OK) and send it to the Pi.
    """
    state.in_input = True
    import time
    try:
        while True:
            print("[TURN] Your turn — Button 8 = Hint  |  Button 7 = OK (A1)")
            cp.coord(True)
            cp.hint(False)
            cp.ok(False)
            buttons.reset()

            print("Enter move FROM")
            move_from = enter_from_square(state, buttons, cp, board, link, process_hint_irq)
            if move_from is None:
                return

            print("Enter move TO")
            move_to = enter_to_square(state, buttons, cp, board, link, process_hint_irq, move_from)
            if move_to is None:
                return

            move = move_from + move_to

            board.light_up_move(move, 'Y')

            buttons.reset()
            result = confirm_move_or_reenter(state, buttons, cp, board, process_hint_irq, move)
            if result is None:
                return
            if result == 'redo':
                cp.coord(True)
                continue

            cp.coord(False)
            link.send_to_pi(move)
            print("[Sent move]", move)
            return
    finally:
        state.in_input = False
