from util.constants import WHITE
from util.common import map_range
import time

def wait_for_mode_request(state, board, cp, link):
    """
    Wait for 'heyArduinoChooseMode' from the Pi.
    Show loading animation and fill the chessboard to 64 squares like Arduino.
    """
    print("Waiting for Pi...")
    board.opening_markings()
    chessSquaresLit = 0

    while True:
        chessSquaresLit = board.loading_status(chessSquaresLit)
        import time
        time.sleep_ms(1000)

        msg = link.read_from_pi()
        if not msg:
            # Interrupt may set hint flag; we ignore hints/newgame during handshake
            continue

        if msg.startswith("heyArduinoChooseMode"):
            # Finish fill to 64 (Arduino look)
            while chessSquaresLit < 64:
                chessSquaresLit = board.loading_status(chessSquaresLit)
                time.sleep_ms(15)

            # Turn on control panel coordinate lights (Arduino panel UX)
            cp.fill(WHITE, 0, 5)
            state.game_state = state.GAME_SETUP
            return

def select_game_mode(buttons, board, cp, link):
    """
    1 = PC (Stockfish)
    2 = Online
    3 = Local
    Sends heypibtn_mode_* accordingly. Clears stale edges first to avoid auto-select.
    """
    buttons.reset()
    print("Select mode: 1=PC  2=Online  3=Local")
    while True:
        btn = buttons.detect_press()
        if btn == 1:
            link.send_to_pi("btn_mode_pc")
            # Optional blink ack on chessboard 0,0
            board.set_square(0, 0, (0, 255, 0)); board.write(); time.sleep_ms(120)
            board.set_square(0, 0, (0, 0, 0));   board.write(); time.sleep_ms(120)
            board.set_square(0, 0, (0, 255, 0)); board.write(); time.sleep_ms(120)
            board.set_square(0, 0, (0, 0, 0));   board.write()
            return
        if btn == 2:
            link.send_to_pi("btn_mode_online")
            return
        if btn == 3:
            link.send_to_pi("btn_mode_local")
            return
        time.sleep_ms(5)

def _select_singlepress(label, default_value, out_min, out_max, buttons, link):
    """
    Arduino-style: one press (1..8) selects mapped value.
    No OK required. Button 7/8 are valid numeric choices here (like Arduino).
    """
    buttons.reset()
    print(f"Select {label}: press 1..8 (maps to {out_min}..{out_max})")
    link.send_typing_preview(label, str(default_value))

    while True:
        btn = buttons.detect_press()
        if not btn:
            import time; time.sleep_ms(5)
            continue
        if 1 <= btn <= 8:
            mapped = map_range(btn, 1, 8, out_min, out_max)
            link.send_typing_preview(label, str(mapped))
            return mapped

def select_strength_singlepress(default_value, buttons, link):
    return _select_singlepress("strength", default_value, 1, 20, buttons, link)

def select_time_singlepress(default_value, buttons, link):
    return _select_singlepress("time", default_value, 3000, 12000, buttons, link)

def select_color_choice(buttons, link):
    """
    1 = White, 2 = Black, 3 = Random.
    Sends heypis1/s2/s3 (Pi understands parse_side_choice).
    """
    buttons.reset()
    print("Choose side: 1=White  2=Black  3=Random")
    while True:
        btn = buttons.detect_press()
        if btn == 1:
            link.send_to_pi("s1"); return
        if btn == 2:
            link.send_to_pi("s2"); return
        if btn == 3:
            link.send_to_pi("s3"); return
        import time; time.sleep_ms(5)

def wait_for_setup(state, link, buttons, enable_hint_irq):
    """
    Full Arduino-style setup phase driven by messages from the Pi:
      - 'heyArduinodefault_strength_X' may arrive anytime
      - 'heyArduinoEngineStrength' => choose strength (1..8 -> 1..20)
      - 'heyArduinodefault_time_Y' may arrive anytime
      - 'heyArduinoTimeControl'    => choose time (1..8 -> 3000..12000 ms)
      - 'heyArduinoPlayerColor'    => choose side (1/2/3)
      - 'heyArduinoSetupComplete'  => switch to GAME_RUNNING
    """
    state.in_setup = True
    try:
        while True:
            msg = link.read_from_pi()
            if not msg:
                import time; time.sleep_ms(10)
                continue

            # Defaults may arrive in any order
            if msg.startswith("heyArduinodefault_strength_"):
                try:
                    state.default_strength = int(msg.split("_")[-1])
                    print("Default strength from Pi:", state.default_strength)
                except:
                    pass
                continue

            if msg.startswith("heyArduinodefault_time_"):
                try:
                    state.default_move_time = int(msg.split("_")[-1])
                    print("Default time from Pi:", state.default_move_time)
                except:
                    pass
                continue

            # Prompts for actual user selection (Arduino-style: single press)
            if msg.startswith("heyArduinoEngineStrength"):
                sel = select_strength_singlepress(state.default_strength, buttons, link)
                link.send_to_pi(str(sel))
                return

            if msg.startswith("heyArduinoTimeControl"):
                sel = select_time_singlepress(state.default_move_time, buttons, link)
                link.send_to_pi(str(sel))
                return

            if msg.startswith("heyArduinoPlayerColor"):
                select_color_choice(buttons, link)
                return

            if msg.startswith("heyArduinoSetupComplete"):
                state.game_state = state.GAME_RUNNING
                return
    finally:
        enable_hint_irq()
        # state.in_setup remains True until runtime starts explicitly
