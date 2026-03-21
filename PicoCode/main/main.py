from machine import Pin, UART, reset
import time
import gc
import ubinascii
import os as _uos
from pico_hw import configure as _configure_hw, ControlPanel, ChessBoard


# trigger faster
class Config:
    class UART:
        BAUD = 115200
        TX_PIN = 0
        RX_PIN = 1
        TIMEOUT_MS = 10

    class Buttons:
        PINS = [2, 3, 4, 5, 10, 8, 7, 6, 9, 11]  # 1..8 coords, 9=OK, 10=HINT
        OK_INDEX = 8
        HINT_INDEX = 9
        SHUTDOWN_INDEX = 7  # button "8"/H
        DEBOUNCE_MS = 200
        OK_LONG_PRESS_MS = 500
        HINT_HOLD_DRAW_MS = 2000
        SHUTDOWN_HOLD_MS = 2000

    class LEDs:
        PANEL_PIN = 16
        PANEL_COUNT = 22
        CP_ZONE_START = 0
        CP_ZONE_END = 6
        CP_OK_PIX = 4
        CP_HINT_PIX = 5
        FILES = [6, 7, 8, 9, 10, 11, 12, 13]
        RANKS = [14, 15, 16, 17, 18, 19, 20, 21]
        BORDER_COLOR = (40, 40, 40)
        CHESS_PIN = 22
        W = 8
        H = 8
        ORIGIN_BOTTOM_RIGHT = True
        ZIGZAG = True

    class Timing:
        POLL_MS = 10
        FAST_POLL_MS = 5
        GAMEOVER_BLINK_MS = 400
        ENGINE_ACK_POST_MS = 180
        SETUP_TRANSITION_MS = 120
        NEW_GAME_SUPPRESS_MS = 800
        TURN_GAMEOVER_WINDOW_MS = 80
        LOADING_STEP_MS = 25
        LOADING_TICK_MS = 2000
        LOADING_FILL_MS = 15
        LOADING_POST_MS = 350
        BLINK_ON_MS = 220
        BLINK_OFF_MS = 140
        SLOW_POLL_MS = 20
        SHUTDOWN_IDLE_MS = 1000
        CONFIRM_DELAY_MS = 0
        CONFIRM_OK_GRACE_MS = 250

    class Colors:
        BLACK = (0, 0, 0)
        WHITE = (255, 255, 255)
        RED = (255, 0, 0)
        GREEN = (0, 255, 0)
        BLUE = (0, 0, 255)
        CYAN = (0, 255, 255)
        MAGENTA = (255, 0, 255)
        YELLOW = (255, 255, 0)
        ENGINE = BLUE


def _load_brightness():
    try:
        with open("/brightness.txt", "r") as _f:
            return max(1, min(8, int(_f.read().strip())))
    except Exception:
        return 5


def _save_and_reset_brightness(val):
    val = max(1, min(8, int(val)))
    try:
        with open("/brightness.txt", "w") as _f:
            _f.write(str(val))
    except Exception:
        pass
    try:
        ack = "brightness_set_" + str(val)
        for _ in range(3):
            link.send(ack)
            time.sleep_ms(120)
    except Exception:
        pass
    reset()


def _scale(color, brightness):
    r, g, b = color
    f = brightness / 8.0
    return (int(r * f), int(g * f), int(b * f))


_brightness = _load_brightness()
Config.LEDs.BORDER_COLOR = _scale(Config.LEDs.BORDER_COLOR, _brightness)
_C = Config.Colors
BLACK = _C.BLACK  # black is always (0,0,0), no scaling needed
WHITE = _scale(_C.WHITE, _brightness)
RED = _scale(_C.RED, _brightness)
GREEN = _scale(_C.GREEN, _brightness)
BLUE = _scale(_C.BLUE, _brightness)
CYAN = _scale(_C.CYAN, _brightness)
MAGENTA = _scale(_C.MAGENTA, _brightness)
YELLOW = _scale(_C.YELLOW, _brightness)
ENGINE_COLOR = _scale(_C.ENGINE, _brightness)


class Game:
    IDLE = 0
    SETUP = 1
    RUNNING = 2


_PROMO = {1: "btn_q", 2: "btn_r", 3: "btn_b", 4: "btn_n"}


class State:
    def __init__(self):
        self.game_state = Game.IDLE
        self.current_turn = "W"
        self.in_game = False

        self.default_strength = 5
        self.default_move_time = 2000
        self.in_setup = False
        self.in_input = False
        self.engine_ack_pending = False
        self.pending_gameover_result = None
        self.buffered_turn_msg = None
        self.preview_cap_flag = False
        self.suspend_until_new_game = False
        self.ok_back_enabled = False
        self.ok_cancel_enabled = False
        self.wait_exit_enabled = False
        self.hint_enabled = True
        self.puzzle_setup_active = False
        self.persistent_trail_active = False
        self.persistent_trail_type = None
        self.persistent_trail_move = None
        self.persistent_trail_end_color = None


st = State()


class UARTLink:
    def __init__(self):
        self.uart = UART(
            0,
            baudrate=Config.UART.BAUD,
            tx=Pin(Config.UART.TX_PIN),
            rx=Pin(Config.UART.RX_PIN),
            timeout=Config.UART.TIMEOUT_MS,
            rxbuf=512,
        )

    def send(self, kind, payload=""):
        self.uart.write(("heypi" + str(kind) + str(payload) + "\n").encode())

    def read(self):
        if self.uart.any():
            try:
                return self.uart.readline().decode().strip()
            except Exception:
                return None
        return None

    def write_raw(self, s):
        self.uart.write((s + "\n").encode())


class Screen:
    def __init__(self, link, st_):
        self.link = link
        self.st = st_

    def _ok(self):
        return self.st.game_state == Game.RUNNING

    def typing_from(self, text):
        if self._ok():
            self.link.write_raw("heypityping_from_" + text)

    def typing_to(self, move_from, partial_to):
        if self._ok():
            self.link.write_raw("heypityping_to_" + move_from + " -> " + partial_to)

    def typing_confirm(self, move_uci):
        if self._ok():
            frm, to = move_uci[:2], move_uci[2:4]
            self.link.write_raw("heypityping_confirm_" + frm + " -> " + to)

    def clear(self, kind):
        if kind == "confirm":
            time.sleep_ms(Config.Timing.CONFIRM_DELAY_MS)
        elif kind == "to":
            self.link.write_raw("heypityping_to_")
        elif kind == "from":
            self.link.write_raw("heypityping_from_")

    def wait_for_lcd_ack(
        self, expected_ack="heyArduinolcd_ack_confirm", timeout_ms=300
    ):
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        ok_seen = False
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if cp.shutdown_held():
                _shutdown_pico()
            if cp.BTN_OK.value() == 0:
                ok_seen = True
            msg = self.link.read()
            if not msg:
                time.sleep_ms(Config.Timing.FAST_POLL_MS)
                continue
            if msg == expected_ack:
                return True, ok_seen
            if msg.startswith("heyArduinoGameOver"):
                _handle_gameover(msg)
                return False, ok_seen
            if _handle_puzzle_setup_message(msg):
                continue
            if msg.startswith("heyArduinohint_") or msg.startswith("heyArduinom"):
                _handle_overlay_or_gameover(msg)
                continue
        return False, ok_seen


time.sleep_ms(500)
link = UARTLink()
screen = Screen(link, st)
_configure_hw(
    Config,
    {
        "BLACK": BLACK,
        "WHITE": WHITE,
        "RED": RED,
        "GREEN": GREEN,
        "BLUE": BLUE,
        "CYAN": CYAN,
        "MAGENTA": MAGENTA,
        "YELLOW": YELLOW,
        "ENGINE_COLOR": ENGINE_COLOR,
    },
)
gc.collect()
cp = ControlPanel(st)
gc.collect()
board = ChessBoard()
gc.collect()


def _is_alphanumeric(ch):
    if not ch or len(ch) != 1:
        return False
    o = ord(ch)
    return (48 <= o <= 57) or (65 <= o <= 90) or (97 <= o <= 122)


def _map_range(x, in_min, in_max, out_min, out_max):
    return int((x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min)


def _show_overlay(payload, color, trail_type):
    cap = payload.endswith("_cap")
    uci = payload[:-4] if cap else payload
    end_color = MAGENTA if cap else None
    st.persistent_trail_active = True
    st.persistent_trail_type = trail_type
    st.persistent_trail_move = uci
    st.persistent_trail_end_color = end_color
    if trail_type == "hint":
        cp.only_ok(True)
    role = "engine" if trail_type == "engine" else trail_type
    board.overlay_show(role, uci, cap=cap, color_override=color, end_color=end_color)


def _clear_persistent_trail():
    was_hint = st.persistent_trail_type == "hint"
    st.persistent_trail_active = False
    st.persistent_trail_type = None
    st.persistent_trail_move = None
    st.persistent_trail_end_color = None
    board.overlay_clear()
    if was_hint and st.game_state == Game.RUNNING and not st.engine_ack_pending:
        cp.only_input()


def _tick_input_loop():
    if cp.shutdown_held():
        _shutdown_pico()
    irq = _handle_hint_irq()
    if irq == "new":
        return ("new_game",)
    msg = link.read()
    if msg:
        outcome = _handle_overlay_or_gameover(msg)
        if outcome == "gameover":
            return ("gameover",)
        if outcome in ("hint", "engine"):
            cp.reset_edges()
            return ("overlay",)
    b = cp.detect_press_raw()
    if b is not None:
        return ("btn", b)
    time.sleep_ms(Config.Timing.FAST_POLL_MS)
    return None


def _wait_for_trail_clear():
    if not st.persistent_trail_active:
        return None
    while True:
        if cp.shutdown_held():
            _shutdown_pico()
        msg = link.read()
        if msg and _handle_overlay_or_gameover(msg) == "gameover":
            return "gameover"
        b = cp.detect_press_raw()
        if b is not None:
            _clear_persistent_trail()
            cp.only_input()
            cp.reset_edges()
            return b if 1 <= b <= 8 else 0
        time.sleep_ms(Config.Timing.FAST_POLL_MS)


def _check_if_move_captures(uci, timeout_ms=150):
    st.preview_cap_flag = False
    link.send("capq_", uci)
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        msg = link.read()
        if not msg:
            time.sleep_ms(Config.Timing.FAST_POLL_MS)
            continue
        if msg.startswith("heyArduinocapr_"):
            val = msg.split("_", 1)[1].strip()
            st.preview_cap_flag = val.startswith("1")
            return st.preview_cap_flag
    return False


def _trigger_new_game_request(now=None):
    if now is None:
        now = time.ticks_ms()
    st.game_state = Game.SETUP
    st.suspend_until_new_game = True
    st.engine_ack_pending = False
    st.pending_gameover_result = None
    st.buffered_turn_msg = None
    link.send("n")
    board.markings()
    if not st.in_game:
        cp.show_coords_top(WHITE)
    cp.suppress_hints_until_ms = time.ticks_add(now, Config.Timing.NEW_GAME_SUPPRESS_MS)
    return "new"


def _handle_hint_irq():
    if cp.shutdown_held():
        _shutdown_pico()
    now = time.ticks_ms()
    if time.ticks_diff(cp.suppress_hints_until_ms, now) > 0:
        return None
    if (
        cp.BTN_OK.value() == 0
        and cp.BTN_HINT.value() == 0
        and st.game_state == Game.RUNNING
        and (st.in_game or st.wait_exit_enabled)
        and not st.ok_back_enabled
        and not st.puzzle_setup_active
    ):
        return _trigger_new_game_request(now)
    if not cp.hint_irq_flag:
        return None
    cp.hint_irq_flag = False
    if not st.hint_enabled:
        return None
    if st.game_state != Game.RUNNING:
        return None
    if cp.BTN_HINT.value() == 0:
        t0 = time.ticks_ms()
        while cp.BTN_HINT.value() == 0:
            if time.ticks_diff(time.ticks_ms(), t0) >= Config.Buttons.HINT_HOLD_DRAW_MS:
                link.send("btn_draw")
                return "draw"
            time.sleep_ms(Config.Timing.POLL_MS)
    link.send("btn_hint")
    return "hint"


def _handle_overlay_or_gameover(msg):
    if not msg:
        return None
    if msg.startswith("heyArduinoGameOver"):
        res = msg.split(":", 1)[1].strip() if ":" in msg else ""
        _show_game_over_and_ack(res)
        return "gameover"
    if msg.startswith("heyArduinocheck_"):
        sq = msg.split("_", 1)[1].strip() if "_" in msg else ""
        if sq:
            board.blink_square_keep(sq, RED)
        return "check"
    if msg.startswith("heyArduinohint_"):
        _show_overlay(msg[len("heyArduinohint_") :], YELLOW, "hint")
        return "hint"
    if msg.startswith("heyArduinostudy_move_"):
        _show_overlay(msg[len("heyArduinostudy_move_") :], ENGINE_COLOR, "engine")
        return "engine"
    if msg.startswith("heyArduinom"):
        _show_overlay(msg[len("heyArduinom") :], ENGINE_COLOR, "engine")
        return "engine"
    return None


def _select_from_square(seed_btn=None, preset_col=None):
    if st.game_state != Game.RUNNING:
        return None
    cp.reset_ok_hold()
    if cp.shutdown_held():
        _shutdown_pico()
    seed = _wait_for_trail_clear()
    if seed == "gameover":
        return None
    if seed:
        seed_btn = seed
    col = None
    if preset_col is not None:
        col = preset_col
        screen.typing_from(col)
    while col is None:
        if st.game_state != Game.RUNNING:
            return None
        if seed_btn is not None:
            b, seed_btn = seed_btn, None
        else:
            ev = _tick_input_loop()
            if ev is None:
                continue
            if ev[0] != "btn":
                return None  # new_game / gameover / overlay
            b = ev[1]
        if not cp.is_non_coord_button(b):
            col = chr(ord("a") + b - 1)
            screen.typing_from(col)
    while True:
        if st.game_state != Game.RUNNING:
            return None
        if cp.shutdown_held():
            _shutdown_pico()
        if cp.ok_long_hold_fired():
            screen.typing_from("")
            board.markings()
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(Config.Timing.POLL_MS)
            cp.reset_ok_hold()
            cp.reset_edges()
            return ("back_from", None)
        ev = _tick_input_loop()
        if ev is None:
            continue
        if ev[0] != "btn":
            return None
        b = ev[1]
        if not cp.is_non_coord_button(b):
            frm = col + str(b)
            screen.typing_from(frm)
            board.preview_from(frm)
            return frm


def _select_to_square(move_from, preset_col=None):
    if st.game_state != Game.RUNNING:
        return None
    cp.reset_ok_hold()
    if cp.shutdown_held():
        _shutdown_pico()
    seed = _wait_for_trail_clear()
    if seed == "gameover":
        return None
    if seed and preset_col is None:
        preset_col = chr(ord("a") + seed - 1)
    col = None
    if preset_col and len(preset_col) == 1 and "a" <= preset_col <= "h":
        col = preset_col
        screen.typing_to(move_from, col)
    while col is None:
        if st.game_state != Game.RUNNING:
            return None
        if cp.shutdown_held():
            _shutdown_pico()
        if cp.ok_long_hold_fired():
            screen.typing_from(move_from[0])
            board.markings()
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(Config.Timing.POLL_MS)
            cp.reset_ok_hold()
            cp.reset_edges()
            return ("back_to_from_rank", move_from[0])
        ev = _tick_input_loop()
        if ev is None:
            continue
        if ev[0] != "btn":
            return None
        b = ev[1]
        if not cp.is_non_coord_button(b):
            col = chr(ord("a") + b - 1)
            screen.typing_to(move_from, col)
    while True:
        if st.game_state != Game.RUNNING:
            return None
        if cp.shutdown_held():
            _shutdown_pico()
        if cp.ok_long_hold_fired():
            screen.typing_to(move_from, "")
            board.preview_from(move_from)
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(Config.Timing.POLL_MS)
            cp.reset_ok_hold()
            cp.reset_edges()
            return ("back_to_to_file", move_from)
        ev = _tick_input_loop()
        if ev is None:
            continue
        if ev[0] != "btn":
            return None
        b = ev[1]
        if not cp.is_non_coord_button(b):
            to = col + str(b)
            uci = move_from + to
            board.preview_trail(uci, cap=_check_if_move_captures(uci))
            screen.typing_to(move_from, to)
            return to


def _confirm_move(move):
    if st.game_state != Game.RUNNING:
        return None
    cp.only_ok(True)
    while cp.BTN_OK.value() == 0:
        if cp.shutdown_held():
            _shutdown_pico()
        if _handle_hint_irq() == "new":
            cp.only_ok(False)
            return None
        time.sleep_ms(Config.Timing.POLL_MS)
    cp.reset_edges()
    cp.arm_confirm_ok()
    screen.typing_confirm(move)
    acked, ok_seen_during_ack = screen.wait_for_lcd_ack(
        "heyArduinolcd_ack_confirm", timeout_ms=300
    )
    if not acked:
        cp.disarm_confirm_ok()
        cp.only_ok(False)
        return None
    time.sleep_ms(30)
    if ok_seen_during_ack or cp.consume_confirm_ok(window_ms=300):
        cp.disarm_confirm_ok()
        cp.set_ok_led(False)
        screen.clear("confirm")
        cp.wait_for_ok_release()
        cp.reset_edges()
        return "ok"
    while True:
        if st.game_state != Game.RUNNING:
            cp.disarm_confirm_ok()
            cp.only_ok(False)
            return None
        if cp.shutdown_held():
            _shutdown_pico()
        if _handle_hint_irq() == "new":
            cp.disarm_confirm_ok()
            cp.only_ok(False)
            return None
        msg = link.read()
        if msg:
            outcome = _handle_overlay_or_gameover(msg)
            if outcome == "gameover":
                cp.disarm_confirm_ok()
                cp.only_ok(False)
                return None
            if outcome in ("hint", "engine"):
                cp.reset_edges()
                cp.disarm_confirm_ok()
                return None
        if cp.consume_confirm_ok(window_ms=300) or cp.BTN_OK.value() == 0:
            t0 = time.ticks_ms()
            fired = False
            while cp.BTN_OK.value() == 0:
                if cp.shutdown_held():
                    _shutdown_pico()
                if _handle_hint_irq() == "new":
                    cp.disarm_confirm_ok()
                    cp.only_ok(False)
                    return None
                if (
                    not fired
                    and time.ticks_diff(time.ticks_ms(), t0)
                    >= Config.Buttons.OK_LONG_PRESS_MS
                ):
                    fired = True
                    partial = move[:-1]
                    frm = partial[:2]
                    screen.typing_to(frm, partial[2] if len(partial) == 3 else "")
                    board.preview_from(frm)
                time.sleep_ms(Config.Timing.POLL_MS)
            held_ms = time.ticks_diff(time.ticks_ms(), t0)
            cp.reset_ok_hold()
            cp.disarm_confirm_ok()
            if fired:
                screen.clear("confirm")
                return ("backspace_confirm", move[:-1])
            if held_ms < Config.Buttons.OK_LONG_PRESS_MS:
                cp.set_ok_led(False)
                screen.clear("confirm")
                return "ok"
            cp.reset_edges()
            continue
        b = cp.detect_press_raw()
        if b == (Config.Buttons.OK_INDEX + 1):
            cp.disarm_confirm_ok()
            cp.set_ok_led(False)
            screen.clear("confirm")
            cp.wait_for_ok_release()
            cp.reset_edges()
            return "ok"
        if b:
            cp.disarm_confirm_ok()
            cp.set_ok_led(False)
            screen.clear("confirm")
            return ("redo", b)
        time.sleep_ms(Config.Timing.FAST_POLL_MS)


def _retry_to_square(frm, preset_to_col=None):
    while True:
        cp.only_input()
        cp.reset_edges()
        cp.reset_ok_hold()
        move_to = _select_to_square(frm, preset_col=preset_to_col)
        preset_to_col = None
        if isinstance(move_to, tuple):
            tag = move_to[0]
            if tag == "back_to_from_rank":
                return ("restart_from", move_to[1])
            if tag == "back_to_to_file":
                continue
        return move_to  # str or None


def _collect_and_submit_move():
    st.in_input = True
    try:
        seed = None
        preset_from_col = None
        while True:
            if cp.shutdown_held():
                _shutdown_pico()
            cp.only_input()
            cp.reset_edges()
            move_from = _select_from_square(seed_btn=seed, preset_col=preset_from_col)
            seed = None
            preset_from_col = None
            if isinstance(move_from, tuple) and move_from[0] == "back_from":
                continue
            if move_from is None:
                if st.persistent_trail_active and st.game_state == Game.RUNNING:
                    continue
                return
            move_to = _select_to_square(move_from)
            if isinstance(move_to, tuple):
                tag = move_to[0]
                if tag == "back_to_from_rank":
                    preset_from_col = move_to[1]
                    continue
                if tag == "back_to_to_file":
                    result = _retry_to_square(move_from)
                    if isinstance(result, tuple) and result[0] == "restart_from":
                        preset_from_col = result[1]
                        continue
                    if result is None or isinstance(result, tuple):
                        continue
                    move_to = result
            if move_to is None:
                if st.persistent_trail_active:
                    continue
                return
            move = move_from + move_to
            res = _confirm_move(move)
            if res is None:
                if st.persistent_trail_active:
                    continue
                return
            if res == "ok":
                time.sleep_ms(200)
                link.send(move)
                st.preview_cap_flag = False
                board.markings()
                return
            if isinstance(res, tuple) and res[0] == "redo":
                cancel_btn = res[1]
                seed = cancel_btn if 1 <= cancel_btn <= 8 else None
                cp.only_input()
                continue
            while isinstance(res, tuple) and res[0] == "backspace_confirm":
                partial = res[1]
                n = len(partial)
                if n >= 3:  # have from(2) + partial-to-file(1)
                    frm = partial[:2]
                    to_file = partial[2] if n == 3 else None
                    result = _retry_to_square(frm, preset_to_col=to_file)
                    if result is None or (
                        isinstance(result, tuple) and result[0] == "restart_from"
                    ):
                        preset_from_col = result[1] if result else None
                        res = ("restart_from", None)
                        break
                    move = frm + result
                    res = _confirm_move(move)
                    if res is None:
                        res = ("restart_from", None)
                        break
                    continue
                if n == 2:  # have from only
                    result = _retry_to_square(partial)
                    if result is None or (
                        isinstance(result, tuple) and result[0] == "restart_from"
                    ):
                        preset_from_col = result[1] if result else None
                        res = ("restart_from", None)
                        break
                    move = partial + result
                    res = _confirm_move(move)
                    if res is None:
                        res = ("restart_from", None)
                        break
                    continue
                preset_from_col = partial[0] if n == 1 else None
                seed = None
                res = ("restart_from", None)
                break
            if isinstance(res, tuple) and res[0] == "restart_from":
                continue
            if res == "ok":
                time.sleep_ms(200)
                link.send(move)
                st.preview_cap_flag = False
                board.markings()
                return
    finally:
        st.in_input = False


def _show_game_over_and_ack(result_str):
    cp.disable_hint_irq()
    try:
        cp.reset_edges()
        cp.only_ok(True)
        board.scene_gameover()
        if cp.shutdown_held():
            _shutdown_pico()
        while cp.BTN_OK.value() == 0:
            time.sleep_ms(Config.Timing.POLL_MS)
        time.sleep_ms(200)
        cp.reset_edges()
        blink = False
        last = time.ticks_ms()
        while True:
            now = time.ticks_ms()
            if time.ticks_diff(now, last) > Config.Timing.GAMEOVER_BLINK_MS:
                blink = not blink
                cp.set_ok_blink(blink)
                last = now
            if cp.shutdown_held():
                _shutdown_pico()
            b = cp.detect_press_raw()
            if b == (Config.Buttons.OK_INDEX + 1):
                cp.only_ok(False)
                link.send("n")
                break
            time.sleep_ms(Config.Timing.SLOW_POLL_MS)
        board.markings()
    finally:
        cp.enable_hint_irq()


def _run_startup_sequence():
    board.opening()
    lit = 0
    while True:
        if cp.shutdown_held():
            _shutdown_pico()
        lit = board.loading_step(lit)
        time.sleep_ms(Config.Timing.LOADING_TICK_MS)
        msg = link.read()
        if not msg:
            continue
        if msg.startswith("heyArduinoChooseMode") or msg.startswith(
            "heyArduinoMenuPaged"
        ):
            while lit < (board.w * board.h):
                if cp.shutdown_held():
                    _shutdown_pico()
                lit = board.loading_step(lit)
                time.sleep_ms(Config.Timing.LOADING_FILL_MS)
            _enter_setup_mode()
            if msg.startswith("heyArduinoMenuPaged"):
                _handle_menu_paged(msg)
            return


def _play_exit_to_menu_animation():
    cp.show_coords_top(WHITE)
    board.off()
    v = 0
    while v < (board.w * board.h):
        if cp.shutdown_held():
            _shutdown_pico()
        v = board.loading_step(v)
        time.sleep_ms(Config.Timing.LOADING_STEP_MS)
    time.sleep_ms(Config.Timing.LOADING_POST_MS)
    board.markings()


def _enter_setup_mode():
    cp.disable_hint_irq()
    cp.reset_edges()
    board.markings()
    if not st.in_game:
        cp.show_coords_top(WHITE)
    st.game_state = Game.SETUP
    st.suspend_until_new_game = False


def _reset_to_idle():
    st.in_setup = False
    st.game_state = Game.IDLE
    st.suspend_until_new_game = False
    try:
        board.markings()
    except Exception:
        pass
    cp.reset_edges()


def _select_mapped_value(out_min, out_max, *, cancel_to_idle=False):
    cp.reset_edges()
    while True:
        if cp.shutdown_held():
            _shutdown_pico()
        b = cp.detect_press_allowed()
        if b == (Config.Buttons.OK_INDEX + 1):
            link.send("btn_ok")
            if cancel_to_idle:
                _reset_to_idle()
            return None
        if b and 1 <= b <= 8:
            return _map_range(b, 1, 8, out_min, out_max)
        time.sleep_ms(Config.Timing.FAST_POLL_MS)


def _difficulty_board_preview(level):
    base = _C.GREEN
    scaled = _scale(base, _brightness)
    for x in range(8):
        board.set_square(x, 3, scaled if x < level else BLACK)
    board.write()


def _select_difficulty(default_level):
    """Increment/decrement difficulty selector.

    Button 1 = up, Button 2 = down (hold for continuous).
    OK = confirm (returns level 1-8).
    Hint = back (returns None).
    """
    level = max(1, min(8, default_level))
    cp.disable_hint_irq()
    cp.hint_irq_flag = False
    cp.reset_edges()
    _difficulty_board_preview(level)
    link.send("lvl_" + str(level))

    HOLD_INITIAL_MS = 400
    HOLD_REPEAT_MS = 150
    btn1_pin = cp.pins[0]  # button 1
    btn2_pin = cp.pins[1]  # button 2
    hold_pin = None
    hold_dir = 0
    hold_start = 0
    last_repeat = 0

    try:
        while True:
            if cp.shutdown_held():
                _shutdown_pico()

            # Check for OK (confirm) or Hint (back) via edge detection
            b = cp.detect_press_allowed()
            if b == (Config.Buttons.OK_INDEX + 1):
                return level
            if b == (Config.Buttons.HINT_INDEX + 1):
                return None

            # Hold detection for button 1 (up) and button 2 (down)
            now = time.ticks_ms()
            if b == 1:
                hold_pin = btn1_pin
                hold_dir = 1
                hold_start = now
                last_repeat = now
                new_level = min(8, level + 1)
                if new_level != level:
                    level = new_level
                    link.send("lvl_" + str(level))
                    _difficulty_board_preview(level)
            elif b == 2:
                hold_pin = btn2_pin
                hold_dir = -1
                hold_start = now
                last_repeat = now
                new_level = max(1, level - 1)
                if new_level != level:
                    level = new_level
                    link.send("lvl_" + str(level))
                    _difficulty_board_preview(level)

            # Continuous hold repeat
            if hold_pin is not None:
                if hold_pin.value() == 0:
                    elapsed = time.ticks_diff(now, hold_start)
                    since_repeat = time.ticks_diff(now, last_repeat)
                    if elapsed >= HOLD_INITIAL_MS and since_repeat >= HOLD_REPEAT_MS:
                        new_level = max(1, min(8, level + hold_dir))
                        if new_level != level:
                            level = new_level
                            link.send("lvl_" + str(level))
                            _difficulty_board_preview(level)
                        last_repeat = now
                else:
                    hold_pin = None
                    hold_dir = 0

            time.sleep_ms(Config.Timing.FAST_POLL_MS)
    finally:
        cp.hint_irq_flag = False


def _run_game_setup_loop():
    st.in_setup = True
    try:
        while True:
            if cp.shutdown_held():
                _shutdown_pico()
            msg = link.read()
            if not msg:
                time.sleep_ms(Config.Timing.POLL_MS)
                continue
            if msg.startswith("heyArduinodefault_strength_"):
                try:
                    st.default_strength = int(msg.split("_")[-1])
                except Exception:
                    pass
                continue
            if msg.startswith("heyArduinoDifficultySelect"):
                cp.profile.difficulty_select()
                board.markings()
                v = _select_difficulty(st.default_strength)
                if v is None:
                    link.send("btn_hint")
                    return
                link.send(str(v))
                time.sleep_ms(Config.Timing.SETUP_TRANSITION_MS)
                return
            if msg.startswith("heyArduinoPlayerColor"):
                board.markings()
                cp.show_coords_top(WHITE)
                cp.profile.vs_color()
                cp.reset_edges()
                while True:
                    if cp.shutdown_held():
                        _shutdown_pico()
                    b2 = cp.detect_press_allowed()
                    if b2 == (Config.Buttons.OK_INDEX + 1):
                        link.send("btn_ok")
                        return
                    if b2 in (1, 2, 3):
                        link.send("s" + str(b2))
                        return
                    time.sleep_ms(Config.Timing.FAST_POLL_MS)
            if msg.startswith("heyArduinohint_enable"):
                st.hint_enabled = True
                continue
            if msg.startswith("heyArduinohint_disable"):
                st.hint_enabled = False
                continue
            if msg.startswith("heyArduinoSetupComplete"):
                st.game_state = Game.RUNNING
                st.in_setup = False
                st.suspend_until_new_game = False
                return
            if msg.startswith("heyArduinoGameEnd"):
                _handle_game_end(msg)
                continue
            if msg.startswith("heyArduinoMenuConfirm"):
                _enter_setup_mode()
                _handle_menu_paged(msg, ok_color=GREEN)
                continue
            if msg.startswith("heyArduinoWaitForOkConfirm"):
                _handle_wait_for_ok_confirm(msg)
                continue
            if msg.startswith("heyArduinoonly_ok_cancel"):
                _handle_wait_for_ok_confirm(msg)
                continue
            if msg.startswith("heyArduinoWaitForAnnotationPage"):
                _handle_wait_for_annotation_page(msg)
                continue
            if msg.startswith("heyArduinoMenuPaged"):
                _enter_setup_mode()
                _handle_menu_paged(msg)
                continue
            if msg.startswith("heyArduinoGetBrightness"):
                link.send("brightness_" + str(_brightness))
                continue
            if msg.startswith("heyArduinoSetBrightness_"):
                try:
                    _save_and_reset_brightness(int(msg.split("_")[-1]))
                except Exception:
                    pass
                continue
            if msg.startswith("heyArduinoBrightnessControl"):
                cp.profile.brightness()
                link.send("brightness_" + str(_brightness))
                # Show brightness preview: 8 squares in a row, each at its
                # brightness level so the user can see the effect of each step.
                board.clear(BLACK)
                base = _C.WHITE
                for x in range(8):
                    board.set_square(x, 3, _scale(base, x + 1))
                board.write()
                v = _select_mapped_value(1, 8)
                if v is None:
                    return
                link.send(str(v))
                time.sleep_ms(Config.Timing.SETUP_TRANSITION_MS)
                return
            if msg.startswith("heyArduinoUpdateMode"):
                _handle_update_mode(msg)
                return
            if msg.startswith("heyArduinoChooseMode"):
                _enter_setup_mode()
                continue
    finally:
        cp.enable_hint_irq()


def _handle_promotion_choice():
    board.scene_promotion()
    cp.show_coords_top(MAGENTA, keep_border=True)
    cp.reset_edges()
    try:
        while True:
            if cp.shutdown_held():
                _shutdown_pico()
            if _handle_hint_irq() == "new":
                return
            b = cp.detect_press_raw()
            if b in _PROMO:
                link.send(_PROMO[b])
                break
            if b:
                pass  # ignore other buttons
            else:
                time.sleep_ms(Config.Timing.FAST_POLL_MS)
    finally:
        cp.clear_header()
        cp.apply(force=True)
        board.markings()


def _handle_puzzle_setup_message(msg):
    if not msg:
        return False
    if msg.startswith("heyArduinopuzzle_setup_begin"):
        st.puzzle_setup_active = True
        cp.disable_hint_irq()
        cp.reset_edges()
        cp.border(True, force=True)
        cp.only_ok(True)
        board.markings()
        return True
    if msg.startswith("heyArduinopuzzle_setup_done"):
        st.puzzle_setup_active = False
        st.in_game = True
        st.game_state = Game.RUNNING
        st.in_setup = False
        st.suspend_until_new_game = False
        cp.profile.puzzle_play()
        board.markings()
        cp.enable_hint_irq()
        return True
    if msg.startswith("heyArduinoWaitForOkOrSkipSetup"):
        _handle_wait_for_ok_or_skip_setup(msg)
        return True
    if not st.puzzle_setup_active:
        return False
    if msg.startswith("heyArduinosetup_clear"):
        board.markings()
        return True
    if msg.startswith("heyArduinosetup_place_"):
        tail = msg[len("heyArduinosetup_place_") :].strip()
        parts = tail.split("_")
        sq = parts[0].strip() if parts else ""
        side = parts[1].strip().lower() if len(parts) > 1 else "w"
        color = GREEN if side.startswith("w") else ENGINE_COLOR
        board.markings()
        board.puzzle_blink(sq, color, times=2)
        return True
    if msg.startswith("heyArduinosetup_remove_"):
        sq = msg.split("_")[-1].strip()
        board.markings()
        board.puzzle_blink(sq, RED, times=3)
        return True
    if msg.startswith("heyArduinosetup_move_"):
        tail = msg[len("heyArduinosetup_move_") :].strip()
        parts = tail.split("_")
        uci = parts[0].strip() if parts else ""
        side = parts[1].strip().lower() if len(parts) > 1 else "w"
        color = GREEN if side.startswith("w") else ENGINE_COLOR
        board.overlay_show("setup", uci, cap=False, color_override=color)
        return True
    return False


def _shutdown_pico():
    link.send("xshutdown")
    for _ in range(2):
        cp.only_ok(True)
        board.clear(CYAN)
        time.sleep_ms(180)
        cp.only_ok(False)
        board.clear(BLACK)
        time.sleep_ms(180)
    cp.off(force=True)
    board.clear(BLACK)
    cp.disable_hint_irq()
    while True:
        time.sleep_ms(Config.Timing.SHUTDOWN_IDLE_MS)


def _handle_gameover(msg):
    res = msg.split(":", 1)[1].strip() if ":" in msg else ""
    _show_game_over_and_ack(res)


def _handle_reset_board(_msg):
    st.in_input = False
    st.in_setup = False
    st.persistent_trail_active = False
    st.persistent_trail_type = None
    st.persistent_trail_move = None
    cp.disable_hint_irq()
    cp.reset_edges()
    cp.off()
    board.markings()


def _handle_choose_mode(_msg):
    _enter_setup_mode()
    while st.game_state == Game.SETUP:
        _run_game_setup_loop()


def _handle_wait_for_ok_confirm(_msg=None):
    cp.reset_edges()
    if not st.persistent_trail_active:
        board.markings()
    cp.only_ok(True, GREEN, border_on=st.in_game, force=True)
    while True:
        if cp.shutdown_held():
            _shutdown_pico()
        b = cp.detect_press_raw()
        if b == (Config.Buttons.OK_INDEX + 1):
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(Config.Timing.POLL_MS)
            cp.disarm_confirm_ok()
            cp.set_ok_led(False)
            cp.reset_edges()
            if st.persistent_trail_active:
                _clear_persistent_trail()
                board.markings()
            link.send("btn_ok")
            return
        time.sleep_ms(Config.Timing.FAST_POLL_MS)


def _handle_wait_for_annotation_page(_msg=None):
    """Block until OK (skip/continue → btn_ok) or Hint (next page → btn_hint)."""
    cp.reset_edges()
    if not st.persistent_trail_active:
        board.markings()
    cp.only_ok(True, GREEN, border_on=st.in_game, force=True)
    while True:
        if cp.shutdown_held():
            _shutdown_pico()
        if cp.BTN_HINT.value() == 0:
            while cp.BTN_HINT.value() == 0:
                time.sleep_ms(Config.Timing.POLL_MS)
            cp.hint_irq_flag = False  # consume IRQ so main loop doesn't double-fire
            cp.reset_edges()
            link.send("btn_hint")
            return
        b = cp.detect_press_raw()
        if b == (Config.Buttons.OK_INDEX + 1):
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(Config.Timing.POLL_MS)
            cp.disarm_confirm_ok()
            cp.set_ok_led(False)
            cp.reset_edges()
            link.send("btn_ok")
            return
        time.sleep_ms(Config.Timing.FAST_POLL_MS)


def _handle_wait_for_ok_or_skip_setup(_msg=None):
    cp.reset_edges()
    board.markings()
    cp.border(st.in_game or st.puzzle_setup_active, force=True, apply_now=False)
    cp._set_cp_buttons(True, False, True, False, ok_color=GREEN)
    cp.apply(force=True)
    cp.set_allowed([1, Config.Buttons.OK_INDEX + 1])
    while True:
        if cp.shutdown_held():
            _shutdown_pico()
        b = cp.detect_press_allowed()
        if not b:
            time.sleep_ms(Config.Timing.FAST_POLL_MS)
            continue
        if b == 1:
            cp.set_allowed(None)
            cp.reset_edges()
            link.send("1")
            return
        if b == (Config.Buttons.OK_INDEX + 1):
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(Config.Timing.POLL_MS)
            cp.set_allowed(None)
            cp.reset_edges()
            link.send("btn_ok")
            return


def _handle_menu_paged(_msg, *, ok_color=None):
    allow_select = False
    has_next = False
    has_back = True
    try:
        parts = _msg.split("_")
        if len(parts) >= 3:
            allow_select = True
            has_next = parts[-2] == "1"
            has_back = parts[-1] == "1"
    except Exception:
        pass
    cp.profile.menu_paged(
        has_next=has_next,
        has_back=has_back,
        allow_select=allow_select,
        border_on=st.in_game,
        ok_color=ok_color,
    )
    cp.disable_hint_irq()
    cp.reset_edges()
    board.markings()
    link.send("menu_ready")
    while True:
        if cp.shutdown_held():
            _shutdown_pico()
        b = cp.detect_press_allowed()
        if not b:
            time.sleep_ms(Config.Timing.FAST_POLL_MS)
            continue
        if b == (Config.Buttons.OK_INDEX + 1):
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(Config.Timing.POLL_MS)
            cp.reset_edges()
            link.send("btn_ok")
            break
        if b == (Config.Buttons.HINT_INDEX + 1):
            link.send("btn_hint")
            break
        if 1 <= b <= 3:
            link.send(str(b))
            break
    board.markings()
    cp.enable_hint_irq()


def _handle_game_start(_msg):
    st.in_game = True


def _handle_game_end(_msg):
    was_in_game = st.in_game or st.game_state == Game.RUNNING or st.puzzle_setup_active
    st.in_game = False
    st.in_input = False
    st.in_setup = False
    st.puzzle_setup_active = False
    st.engine_ack_pending = False
    st.pending_gameover_result = None
    st.buffered_turn_msg = None
    st.ok_back_enabled = False
    st.wait_exit_enabled = False
    st.suspend_until_new_game = False
    st.game_state = Game.SETUP
    cp.disable_hint_irq()
    cp.reset_edges()
    if was_in_game:
        _play_exit_to_menu_animation()
    else:
        board.markings()
        cp.show_coords_top(WHITE)


def _handle_engine_move(msg):
    _show_overlay(msg[len("heyArduinom") :], ENGINE_COLOR, "engine")
    cp.only_ok(True)
    st.engine_ack_pending = True
    st.pending_gameover_result = None
    st.buffered_turn_msg = None


def _handle_hint_move(msg):
    _show_overlay(msg[len("heyArduinohint_") :], YELLOW, "hint")
    cp.only_ok(True)
    cp.reset_edges()


def _handle_study_move(msg):
    _show_overlay(msg[len("heyArduinostudy_move_") :], ENGINE_COLOR, "engine")
    cp.only_ok(True)
    cp.reset_edges()


def _handle_study_vars(msg):
    """Light up origin and destination squares for each available variation.

    Main line (first UCI) is shown in BLUE; alternatives are shown in YELLOW.
    Main line is drawn last so it takes precedence on any shared squares.
    """
    payload = msg[len("heyArduinostudy_vars_") :]
    ucis = [u for u in payload.split("|") if len(u) >= 4]
    if not ucis:
        return
    board.markings()
    # Draw variation lines first (yellow), then main line (blue) on top
    for uci in ucis[1:]:
        for sq in (uci[:2], uci[2:4]):
            xy = board.algebraic_to_xy(sq)
            if xy:
                board.set_square(xy[0], xy[1], YELLOW)
    for sq in (ucis[0][:2], ucis[0][2:4]):
        xy = board.algebraic_to_xy(sq)
        if xy:
            board.set_square(xy[0], xy[1], BLUE)
    board.write()
    st.persistent_trail_active = True
    st.persistent_trail_type = "study_vars"
    st.persistent_trail_move = None
    st.persistent_trail_end_color = None


def _handle_puzzle_wrong(msg):
    raw = msg[len("heyArduinopuzzle_wrong_") :].strip()
    mv = "".join(ch for ch in raw if _is_alphanumeric(ch))[:4]
    if len(mv) < 4:
        return
    _show_overlay(mv, RED, "wrong")
    cp.only_ok(True)
    cp.reset_edges()
    while True:
        if cp.shutdown_held():
            _shutdown_pico()
        if _handle_hint_irq() == "new":
            link.send("n")
            break
        b = cp.detect_press_raw()
        if b == (Config.Buttons.OK_INDEX + 1):
            while cp.BTN_OK.value() == 0:
                time.sleep_ms(Config.Timing.POLL_MS)
            cp.disarm_confirm_ok()
            cp.reset_edges()
            link.send("btn_ok")
            break
        time.sleep_ms(Config.Timing.POLL_MS)
    _clear_persistent_trail()
    board.markings()
    if st.game_state == Game.RUNNING:
        cp.only_input()
        cp.reset_edges()


def _handle_turn(msg):
    turn_str = msg.split("_", 1)[1].strip().lower()
    st.current_turn = "W" if "w" in turn_str else "B"
    t_start = time.ticks_ms()
    while (
        time.ticks_diff(time.ticks_ms(), t_start)
        < Config.Timing.TURN_GAMEOVER_WINDOW_MS
    ):
        nxt = link.read()
        if not nxt:
            time.sleep_ms(Config.Timing.FAST_POLL_MS)
            continue
        if nxt.startswith("heyArduinoGameOver"):
            _handle_gameover(nxt)
            return
    cp.only_input()
    _collect_and_submit_move()


def _set_ok_back_enabled(enabled, color=GREEN):
    st.ok_back_enabled = enabled
    cp.only_ok(enabled, color)


def _set_ok_indicator(enabled, color=GREEN):
    st.wait_exit_enabled = enabled
    cp.only_ok(enabled, color)


def _handle_set_brightness(msg):
    try:
        _save_and_reset_brightness(int(msg.split("_")[-1]))
    except Exception:
        pass


def _is_safe_update_name(name):
    if not name or "/" in name or "\\" in name or ".." in name:
        return False
    if not name.endswith(".py"):
        return False
    for ch in name:
        if ch not in "._abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
            return False
    return True


def _update_temp_path(name):
    base = name[:-3] if name.endswith(".py") else name
    return "/" + base + "_new.py"


def _send_update_error(stage):
    try:
        link.send("UpdateError_" + str(stage))
    except Exception:
        try:
            link.send("UpdateError")
        except Exception:
            pass


def _handle_update_mode(_msg):
    board.off()
    cp.off(force=True)
    cp.disable_hint_irq()
    gc.collect()
    link.send("UpdateReady")
    temp_paths = {}
    current_name = None
    current_temp = None
    current_file = None

    def _close_current():
        nonlocal current_file, current_name, current_temp
        if current_file is not None:
            current_file.close()
            current_file = None
            gc.collect()
        current_name = None
        current_temp = None

    def _cleanup():
        _close_current()
        for temp_path in temp_paths.values():
            try:
                _uos.remove(temp_path)
            except Exception:
                pass

    chunk_count = 0
    try:
        while True:
            time.sleep_ms(20)
            msg = link.read()
            if msg is None:
                continue
            if msg.startswith("heyArduinoUpdateFile_"):
                _close_current()
                current_name = msg[len("heyArduinoUpdateFile_") :].strip()
                if not _is_safe_update_name(current_name):
                    raise ValueError("bad update filename")
                current_temp = _update_temp_path(current_name)
                temp_paths[current_name] = current_temp
                current_file = open(current_temp, "wb")
                chunk_count = 0
                continue
            if msg.startswith("heyArduinoUpdateChunk_"):
                if current_file is None:
                    current_name = "main.py"
                    current_temp = _update_temp_path(current_name)
                    temp_paths[current_name] = current_temp
                    current_file = open(current_temp, "wb")
                current_file.write(
                    ubinascii.a2b_base64(msg[len("heyArduinoUpdateChunk_") :])
                )
                chunk_count += 1
                if chunk_count % 16 == 0:
                    gc.collect()
                continue
            if msg.startswith("heyArduinoUpdateFileDone"):
                _close_current()
                continue
            if msg.startswith("heyArduinoUpdateDone"):
                _close_current()
                break
            if msg.startswith("heyArduinoUpdateAbort"):
                _cleanup()
                return
        if not temp_paths:
            raise ValueError("empty update")
        for name, temp_path in temp_paths.items():
            target_path = "/" + name
            try:
                _uos.remove(target_path)
            except Exception:
                pass
            _uos.rename(temp_path, target_path)
            gc.collect()
        link.send("UpdateComplete")
        time.sleep_ms(300)
        reset()
    except MemoryError:
        _cleanup()
        _send_update_error("mem")
    except Exception as exc:
        _cleanup()
        _send_update_error(exc.__class__.__name__)


def _route_incoming_message(msg):
    try:
        if _handle_puzzle_setup_message(msg):
            return True
    except Exception:
        pass
    if msg.startswith("heyArduinoGameStart"):
        _handle_game_start(msg)
        return True
    if msg.startswith("heyArduinoGameEnd"):
        _handle_game_end(msg)
        return True
    if msg.startswith("heyArduinoWaitForOkConfirm"):
        _handle_wait_for_ok_confirm(msg)
        return True
    if msg.startswith("heyArduinoWaitForAnnotationPage"):
        _handle_wait_for_annotation_page(msg)
        return True
    if msg.startswith("heyArduinoWaitForOkOrSkipSetup"):
        _handle_wait_for_ok_or_skip_setup(msg)
        return True
    if msg.startswith("heyArduinoonly_ok_cancel"):
        cp.only_ok(True, RED)
        return True
    if msg.startswith("heyArduinook_back_enable"):
        _set_ok_back_enabled(True)
        return True
    if msg.startswith("heyArduinook_cancel_enable"):
        _set_ok_back_enabled(True, RED)
        return True
    if msg.startswith("heyArduinook_back_disable"):
        _set_ok_back_enabled(False)
        return True
    if msg.startswith("heyArduinowait_exit_enable"):
        _set_ok_indicator(True, RED)
        return True
    if msg.startswith("heyArduinowait_exit_disable"):
        _set_ok_indicator(False)
        return True
    if msg.startswith("heyArduinohint_disable"):
        st.hint_enabled = False
        return True
    if msg.startswith("heyArduinohint_enable"):
        st.hint_enabled = True
        return True
    if msg.startswith("heyArduinocheck_"):
        sq = msg.split("_", 1)[1].strip() if "_" in msg else ""
        board.blink_square_keep(sq, RED)
        return True
    if msg.startswith("heyArduinoSetBrightness_"):
        _handle_set_brightness(msg)
        return True
    if msg.startswith("heyArduinoUpdateMode"):
        _handle_update_mode(msg)
        return True
    if msg.startswith("heyArduinoGameOver"):
        _handle_gameover(msg)
        return True
    if msg.startswith("heyArduinoResetBoard"):
        _handle_reset_board(msg)
        return True
    if msg.startswith("heyArduinoChooseMode"):
        _handle_choose_mode(msg)
        return True
    if msg.startswith("heyArduinoMenuConfirm"):
        _handle_menu_paged(msg, ok_color=GREEN)
        return True
    if msg.startswith("heyArduinoMenuPaged"):
        _handle_menu_paged(msg)
        return True
    if msg.startswith("heyArduinom"):
        _handle_engine_move(msg)
        return True
    if msg.startswith("heyArduinopromotion_choice_needed"):
        _handle_promotion_choice()
        return True
    if msg.startswith("heyArduinohint_"):
        _handle_hint_move(msg)
        return True
    if msg.startswith("heyArduinostudy_vars_"):
        _handle_study_vars(msg)
        return True
    if msg.startswith("heyArduinostudy_move_"):
        _handle_study_move(msg)
        return True
    if msg.startswith("heyArduinopuzzle_wrong_"):
        _handle_puzzle_wrong(msg)
        return True
    if msg.startswith("heyArduinoerror"):
        board.illegal_flash()
        cp.only_ok(False)
        return True
    if msg.startswith("heyArduinoturn_"):
        _handle_turn(msg)
        return True
    return False


def _main_loop():
    last_gc_ms = time.ticks_ms()
    while True:
        if time.ticks_diff(time.ticks_ms(), last_gc_ms) >= 5000:
            gc.collect()
            last_gc_ms = time.ticks_ms()
        if cp.shutdown_held():
            _shutdown_pico()
        if (
            st.ok_back_enabled
            and not st.puzzle_setup_active
            and not st.engine_ack_pending
        ):
            b0 = cp.detect_press_raw()
            if b0 == (Config.Buttons.OK_INDEX + 1):
                link.send("btn_ok")
                st.ok_back_enabled = False
                _reset_to_idle()
                time.sleep_ms(50)
                continue
        if st.puzzle_setup_active:
            msg_setup = link.read()
            if msg_setup:
                _handle_puzzle_setup_message(msg_setup)
            if cp.BTN_OK.value() == 0 and cp.BTN_HINT.value() == 0:
                link.send("n")
                st.puzzle_setup_active = False
                cp.only_ok(False)
                cp.enable_hint_irq()
                cp.reset_edges()
                board.opening()
                time.sleep_ms(50)
                continue
            b = cp.detect_press_raw()
            if b == (Config.Buttons.OK_INDEX + 1):
                link.send("btn_ok")
            time.sleep_ms(Config.Timing.POLL_MS)
            continue
        if _handle_hint_irq() == "new":
            cp.disable_hint_irq()
            if not st.in_game:
                cp.off()
            st.engine_ack_pending = False
            st.pending_gameover_result = None
            st.buffered_turn_msg = None
            continue
        if (
            st.game_state == Game.RUNNING
            and not st.in_input
            and not st.engine_ack_pending
            and not st.ok_back_enabled
            and st.wait_exit_enabled
            and not st.puzzle_setup_active
        ):
            time.sleep_ms(Config.Timing.POLL_MS)
        if st.engine_ack_pending:
            nxt = link.read()
            if nxt and nxt.startswith("heyArduinoGameOver"):
                st.pending_gameover_result = (
                    nxt.split(":", 1)[1].strip() if ":" in nxt else ""
                )
                while cp.BTN_OK.value() == 0:
                    time.sleep_ms(Config.Timing.POLL_MS)
                time.sleep_ms(Config.Timing.ENGINE_ACK_POST_MS)
                cp.reset_edges()
                while True:
                    b = cp.detect_press_raw()
                    if b == (Config.Buttons.OK_INDEX + 1):
                        cp.only_ok(False)
                        break
                    time.sleep_ms(15)
                st.engine_ack_pending = False
                _show_game_over_and_ack(st.pending_gameover_result)
                st.pending_gameover_result = None
                st.buffered_turn_msg = None
                continue
            if nxt and nxt.startswith("heyArduinoturn_"):
                st.buffered_turn_msg = nxt
            b = cp.detect_press_raw()
            if b == (Config.Buttons.OK_INDEX + 1):
                link.send("btn_ok")
                st.engine_ack_pending = False
                cp.set_ok_led(False)
                _clear_persistent_trail()
                if st.buffered_turn_msg:
                    turn_str = st.buffered_turn_msg.split("_", 1)[1].strip().lower()
                    st.current_turn = "W" if "w" in turn_str else "B"
                    st.buffered_turn_msg = None
                cp.only_input()
                _collect_and_submit_move()
                continue
            time.sleep_ms(Config.Timing.POLL_MS)
            continue
        msg = link.read()
        if msg and _handle_puzzle_setup_message(msg):
            continue
        if not msg:
            time.sleep_ms(Config.Timing.POLL_MS)
            continue
        if st.suspend_until_new_game or st.game_state != Game.RUNNING:
            if not (
                msg.startswith("heyArduinoChooseMode")
                or msg.startswith("heyArduinoGameEnd")
                or msg.startswith("heyArduinoResetBoard")
                or msg.startswith("heyArduinoUpdateMode")
            ):
                continue
        _route_incoming_message(msg)


def run():
    gc.collect()
    cp.off(force=True)
    board.off()
    cp.reset_edges()
    cp.disable_hint_irq()
    _run_startup_sequence()
    while st.game_state == Game.SETUP:
        _run_game_setup_loop()
    while True:
        _main_loop()


run()
