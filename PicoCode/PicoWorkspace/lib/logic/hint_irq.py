
from util.constants import WHITE, BLUE
# Module-level dependencies injected by init()
_state = None
_buttons = None
_cp = None
_board = None
_link = None
_ok_pin = None
_hint_pin = None

def init(state, buttons, cp, board, link, ok_index, hint_index):
    global _state, _buttons, _cp, _board, _link, _ok_pin, _hint_pin
    _state = state
    _buttons = buttons
    _cp = cp
    _board = board
    _link = link
    _ok_pin = buttons.btn(ok_index)
    _hint_pin = buttons.btn(hint_index)

def _hint_irq(_pin):
    # ISR: keep it minimal — just set a flag
    _state.hint_irq_flag = True

def enable_hint_irq():
    _hint_pin.irq(trigger=1 << 1, handler=_hint_irq)  # Pin.IRQ_FALLING

def disable_hint_irq():
    _hint_pin.irq(handler=None)

def process_hint_irq():
    """
    Emulate Arduino ISR semantics safely:
      - Real IRQ sets state.hint_irq_flag.
      - If OK (A1) is LOW at processing time -> NEW GAME
      - Else -> HINT
    Returns: 'new' | 'hint' | None
    """
    if _state.game_state != _state.GAME_RUNNING:
        _state.hint_irq_flag = False
        return None

    if not _state.hint_irq_flag:
        return None

    # consume the flag
    _state.hint_irq_flag = False

    # Check A1 (Button 7) level NOW, like Arduino ISR does
    if _ok_pin.value() == 0:
        # NEW GAME
        print("[IRQ] New Game (A1 LOW during Hint IRQ)")
        # Visuals
        _cp.hint(False)
        _cp.fill(WHITE, 0, 5)
        # Notify Pi
        _link.send_to_pi("n")
        # Loading animation (like Arduino)
        var1 = 0
        while var1 < 64:
            var1 = _board.loading_status(var1)
            import time
            time.sleep_ms(25)
        import time
        time.sleep_ms(1000)
        _board.show_markings()
        return "new"

    # Else: Hint
    print("[IRQ] Hint request")
    _link.send_typing_preview("hint", "Hint requested… thinking")

    # During setup, Arduino would do nothing useful; we suppress to avoid noise
    if not _state.in_setup:
        _cp.hint(True, BLUE)
        import time
        time.sleep_ms(100)
        _cp.hint(True, WHITE)
        _link.send_to_pi("btn_hint")
        return "hint"

    return None
