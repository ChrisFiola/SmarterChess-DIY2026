from machine import Pin
import time

# Files a-h map to column GPIO pins in order.
_COL_GPIOS = (5, 6, 7, 8, 10, 26, 27, 28)

# 74HC138 address pins: A (LSB), B, C (MSB) select rank 0-7 (Y0-Y7).
_ROW_GPIOS = (2, 3, 4)

# How long to wait after setting the decoder address before reading columns.
_SETTLE_US = 200


class ReedMatrix:
    """
    Drives a 74HC138 3-to-8 decoder to select one rank at a time, then reads
    8 column GPIO pins to detect reed switch closure (= piece present).

    Wiring convention (per WIRING_DIAGRAM.md):
      - 74HC138 Y outputs are active-LOW: selected row is pulled LOW.
      - Each column has a 10 kΩ pull-up to 3.3V.
      - 1N4148 diode between column GPIO and reed switch (anode → column, cathode → row).
      - Switch closed (piece present): column pulled LOW through diode → reads 0.
      - Switch open  (no piece)      : column stays HIGH via pull-up → reads 1.

    scan() returns board[rank][file] as True (piece present) / False (empty).
    rank 0 = rank-1 (a1 side), file 0 = file-a.
    """

    def __init__(self):
        self._row = [Pin(g, Pin.OUT) for g in _ROW_GPIOS]
        self._col = [Pin(g, Pin.IN, Pin.PULL_UP) for g in _COL_GPIOS]
        # Drive all row address lines LOW to start (selects Y0, but that is fine
        # because we always call scan() before using any result).
        for p in self._row:
            p.value(0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self):
        """
        Full board scan.  Returns a list of 8 lists of 8 booleans:
            board[rank][file]  — True means piece present.
        """
        board = []
        for rank in range(8):
            self._select_row(rank)
            time.sleep_us(_SETTLE_US)
            row_state = [self._col[f].value() == 0 for f in range(8)]
            board.append(row_state)
        return board

    def diff(self, prev, curr):
        """
        Compare two board states returned by scan().

        Returns (lifted, placed) where each is a list of (rank, file) tuples:
          lifted — squares that went from True→False (piece picked up)
          placed — squares that went from False→True (piece put down)
        """
        lifted = []
        placed = []
        for rank in range(8):
            for file in range(8):
                was = prev[rank][file]
                now = curr[rank][file]
                if was and not now:
                    lifted.append((rank, file))
                elif not was and now:
                    placed.append((rank, file))
        return lifted, placed

    @staticmethod
    def to_algebraic(rank, file):
        """Convert (rank, file) 0-indexed to algebraic notation, e.g. (0,0) → 'a1'."""
        return chr(ord('a') + file) + str(rank + 1)

    def detect_move(self, prev, curr):
        """
        High-level move detector for a single lift-and-place gesture.

        Returns one of:
          ('normal',  from_sq, to_sq)   — simple move or capture
          ('castle',  king_from, king_to, rook_from, rook_to)
          ('partial', from_sq)          — piece lifted, not yet placed
          None                          — no change detected

        from_sq / to_sq are algebraic strings ('e2', 'e4', etc.)

        Note: promotion is detected by the Pi after it receives the UCI move.
        """
        lifted, placed = self.diff(prev, curr)

        if not lifted and not placed:
            return None

        # Piece lifted but not yet placed (mid-gesture)
        if len(lifted) == 1 and not placed:
            return ('partial', self.to_algebraic(*lifted[0]))

        # Normal move or capture (one piece lifted, one placed; captured piece
        # disappears from the opponent's perspective in the game-state layer,
        # but physically the captured piece is removed first then mover placed —
        # so we also see 1 lifted + 1 placed for a capture)
        if len(lifted) == 1 and len(placed) == 1:
            return (
                'normal',
                self.to_algebraic(*lifted[0]),
                self.to_algebraic(*placed[0]),
            )

        # Kingside castling: king and rook both lift then both place.
        # We get 2 lifted + 2 placed.
        if len(lifted) == 2 and len(placed) == 2:
            froms = [self.to_algebraic(*s) for s in lifted]
            tos   = [self.to_algebraic(*s) for s in placed]
            # Identify king (moves 2 squares) vs rook (moves 2+ squares)
            king_from, rook_from = _identify_castle_pieces(lifted)
            if king_from is not None:
                return (
                    'castle',
                    self.to_algebraic(*king_from),
                    # The king destination is whichever placed square is on
                    # the same rank as the king and 2 files away.
                    _castle_king_dest(king_from, placed),
                    self.to_algebraic(*rook_from),
                    _castle_rook_dest(rook_from, placed, king_from),
                )
            # Fallback: treat as two separate events (shouldn't happen normally)
            return ('normal', froms[0], tos[0])

        # En-passant: lifting one piece + pawn capture removes two (capturing pawn
        # lifts, and the captured pawn disappears when placed).  Physically this
        # looks like 1 lifted (capturer) + 1 placed (capturer's dest) + the
        # captured pawn was already removed by the player before confirming.
        # That case degenerates to 1 lifted + 1 placed and is handled above.

        # Anything else is ambiguous — return None and let main.py re-scan.
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _select_row(self, rank):
        """Drive 74HC138 address pins to select the given rank (0-7)."""
        self._row[0].value((rank >> 0) & 1)  # A — LSB
        self._row[1].value((rank >> 1) & 1)  # B
        self._row[2].value((rank >> 2) & 1)  # C — MSB


# ---------------------------------------------------------------------------
# Module-level castling helpers
# ---------------------------------------------------------------------------

def _identify_castle_pieces(lifted_squares):
    """
    Given two (rank, file) squares that were lifted, return (king_square, rook_square).
    The king is identified as the piece on file 4 (e-file) for either rank 0 or rank 7.
    Returns (None, None) if pattern does not match a known castle start.
    """
    for sq in lifted_squares:
        rank, file = sq
        if file == 4 and rank in (0, 7):
            other = [s for s in lifted_squares if s != sq][0]
            return sq, other
    return None, None


def _castle_king_dest(king_sq, placed_squares):
    """
    Return the algebraic destination of the king after castling.
    King moves to file 6 (kingside) or file 2 (queenside).
    """
    rank = king_sq[0]
    for sq in placed_squares:
        if sq[0] == rank and sq[1] in (2, 6):
            return ReedMatrix.to_algebraic.__func__(None, *sq)
    # Fallback: pick whichever placed square is on the king's rank
    for sq in placed_squares:
        if sq[0] == rank:
            return ReedMatrix.to_algebraic.__func__(None, *sq)
    return ReedMatrix.to_algebraic.__func__(None, *placed_squares[0])


def _castle_rook_dest(rook_sq, placed_squares, king_sq):
    """
    Return the algebraic destination of the rook after castling.
    """
    king_rank = king_sq[0]
    for sq in placed_squares:
        if sq[0] == king_rank and sq[1] in (3, 5):
            return ReedMatrix.to_algebraic.__func__(None, *sq)
    # Fallback: the placed square that isn't the king destination
    for sq in placed_squares:
        if sq != rook_sq:
            return ReedMatrix.to_algebraic.__func__(None, *sq)
    return ReedMatrix.to_algebraic.__func__(None, *placed_squares[0])
