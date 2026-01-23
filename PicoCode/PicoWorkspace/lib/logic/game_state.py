
class GameState:
    GAME_IDLE = 0
    GAME_SETUP = 1
    GAME_RUNNING = 2

    def __init__(self):
        # Flags
        self.confirm_mode = False     # Only TRUE during OK confirmation
        self.in_setup = False         # Setup phase (mode/strength/time/color)
        self.in_input = False         # When entering FROM/TO
        self.hint_irq_flag = False    # Raised by real interrupt
        self.hint_hold_mode = False   # When True, a hint is pinned on the board until OK
        self.hint_waiting = False
        self.showing_hint = False

        # Game phase
        self.game_state = self.GAME_IDLE

        # Defaults (Arduino-like)
        self.default_strength = 5      # 0..20
        self.default_move_time = 2000  # ms (maps to 3000..12000 during setup)
