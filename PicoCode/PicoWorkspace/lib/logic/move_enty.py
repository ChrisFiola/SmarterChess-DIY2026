
from hardware.buttons import ButtonManager
from util.constants import WHITE

def _maybe_clear_hint_on_coord_press(state, board, cp, btn):
    if state.showing_hint and btn and not ButtonManager.is_non_coord_button(btn):
        state.showing_hint = False
        board.show_markings()
        cp.coord(True)

def _read_coord_part(state, buttons, link, process_hint_irq, kind, label, prefix=""):
    """
    kind: "file" or "rank"
    Returns 'a'..'f' or '1'..'6' depending on kind (8-button layout).
    """
    import time
    while True:
        irq = process_hint_irq()
        if irq == "new":
            return None

        btn = buttons.detect_press()
        if not btn:
            time.sleep_ms(5)
            continue

        if ButtonManager.is_non_coord_button(btn):
            continue

        if kind == "file":
            col = chr(ord('a') + btn - 1)  # 1..6 -> a..f
            link.send_typing_preview(label, prefix + col)
            return col
        else:
            row = str(btn)  # 1..6 -> '1'..'6'
            link.send_typing_preview(label, prefix + row)
            return row

def enter_from_square(state, buttons, cp, board, link, process_hint_irq):
    """
    Enter FROM coordinate using buttons 1..6 (8-button layout).
    """
    col = row = None

    cp.coord(True)
    cp.ok(False)
    cp.hint(False)
    buttons.reset()

    import time

    # Column
    while col is None:
        irq = process_hint_irq()
        if irq == "new":
            return None

        btn = buttons.detect_press()
        if not btn:
            time.sleep_ms(5)
            continue

        _maybe_clear_hint_on_coord_press(state, board, cp, btn)
        if ButtonManager.is_non_coord_button(btn):
            continue

        col = chr(ord('a') + btn - 1)
        link.send_typing_preview("from", col)

    # Row
    part = _read_coord_part(state, buttons, link, process_hint_irq, "rank", "from", prefix=col)
    if part is None:
        return None
    row = part
    return col + row

def enter_to_square(state, buttons, cp, board, link, process_hint_irq, move_from):
    """
    Enter TO coordinate; identical handling to FROM.
    """
    col = row = None
    cp.coord(True)
    cp.ok(False)
    buttons.reset()

    import time

    # Column
    while col is None:
        irq = process_hint_irq()
        if irq == "new":
            return None

        btn = buttons.detect_press()
        if not btn:
            time.sleep_ms(5)
            continue

        _maybe_clear_hint_on_coord_press(state, board, cp, btn)
        if ButtonManager.is_non_coord_button(btn):
            continue

        col = chr(ord('a') + btn - 1)
        link.send_typing_preview("to", move_from + " → " + col)

    # Row
    while row is None:
        irq = process_hint_irq()
        if irq == "new":
            return None

        btn = buttons.detect_press()
        if not btn:
            time.sleep_ms(5)
            continue

        if ButtonManager.is_non_coord_button(btn):
            continue

        row = str(btn)
        link.send_typing_preview("to", move_from + " → " + col + row)

    return col + row

def confirm_move_or_reenter(state, buttons, cp, board, process_hint_irq, move_str):
    """
    Hold the move on LEDs and wait for OK (Button 7/A1).
    Returns: 'ok' | 'redo' | None (new game)
    """
    state.confirm_mode = True
    cp.coord(False)
    cp.ok(True)
    buttons.reset()

    import time
    try:
        while True:
            irq = process_hint_irq()
            if irq == "new":
                return None

            btn = buttons.detect_press()
            if not btn:
                time.sleep_ms(5)
                continue

            if btn == 7:
                cp.ok(False)
                return 'ok'

            cp.ok(False)
            board.show_markings()
            return 'redo'
    finally:
        state.confirm_mode = False
