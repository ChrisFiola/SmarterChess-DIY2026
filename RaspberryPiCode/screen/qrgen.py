"""Minimal QR code generator (pure Python, no deps).

Based on Nayuki's "QR Code generator" reference implementation (public domain/MIT-style).
This file is intentionally self-contained so SmartChess can render QR codes without
installing extra packages.

Only the functionality needed for this project is exposed: encode_text().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


# ---- Public API ----


def encode_text(text: str, *, ecl: str = "M") -> "QrCode":
    """Encode text into a QR code.

    ecl: one of 'L','M','Q','H'. Default 'M'.
    """
    e = {
        "L": QrCode.Ecc.LOW,
        "M": QrCode.Ecc.MEDIUM,
        "Q": QrCode.Ecc.QUARTILE,
        "H": QrCode.Ecc.HIGH,
    }.get((ecl or "M").upper(), QrCode.Ecc.MEDIUM)
    segs = QrSegment.make_segments(text)
    return QrCode.encode_segments(segs, e)


# ---- Implementation (compact port of Nayuki) ----


class QrCode:
    """QR Code symbol."""

    @dataclass(frozen=True)
    class Ecc:
        format_bits: int

    # Error correction levels
    Ecc.LOW = Ecc(1)
    Ecc.MEDIUM = Ecc(0)
    Ecc.QUARTILE = Ecc(3)
    Ecc.HIGH = Ecc(2)

    MIN_VERSION = 1
    MAX_VERSION = 40

    def __init__(self, version: int, errcorlvl: "QrCode.Ecc", data_codewords: bytes, mask: int):
        if version < self.MIN_VERSION or version > self.MAX_VERSION:
            raise ValueError("Version out of range")
        if mask < -1 or mask > 7:
            raise ValueError("Mask out of range")

        self.version = version
        self.error_correction_level = errcorlvl
        self.size = version * 4 + 17

        # Modules: True = black, False = white, None = unset
        self._modules: List[List[Optional[bool]]] = [
            [None] * self.size for _ in range(self.size)
        ]
        self._is_function: List[List[bool]] = [
            [False] * self.size for _ in range(self.size)
        ]

        self._draw_function_patterns()
        all_codewords = self._add_ecc_and_interleave(data_codewords)
        self._draw_codewords(all_codewords)
        self._mask = self._handle_masking(mask)
        self._draw_format_bits(self._mask)
        if version >= 7:
            self._draw_version()

        # Replace None with False
        for y in range(self.size):
            row = self._modules[y]
            for x in range(self.size):
                if row[x] is None:
                    row[x] = False

    def get_module(self, x: int, y: int) -> bool:
        return bool(self._modules[y][x])

    @staticmethod
    def encode_segments(segs: List["QrSegment"], ecl: "QrCode.Ecc", min_version: int = 1, max_version: int = 40, mask: int = -1, boost_ecl: bool = True) -> "QrCode":
        if not (QrCode.MIN_VERSION <= min_version <= max_version <= QrCode.MAX_VERSION):
            raise ValueError("Version range")
        if mask < -1 or mask > 7:
            raise ValueError("Mask")

        # Find minimal version
        for version in range(min_version, max_version + 1):
            data_capacity_bits = QrCode._get_num_data_codewords(version, ecl) * 8
            used_bits = QrSegment.get_total_bits(segs, version)
            if used_bits is not None and used_bits <= data_capacity_bits:
                # Boost ECC if possible
                if boost_ecl:
                    for new_ecl in (QrCode.Ecc.MEDIUM, QrCode.Ecc.QUARTILE, QrCode.Ecc.HIGH):
                        if new_ecl.format_bits == ecl.format_bits:
                            continue
                        cap2 = QrCode._get_num_data_codewords(version, new_ecl) * 8
                        if used_bits <= cap2:
                            ecl = new_ecl
                # Build bit buffer
                bb = _BitBuffer()
                for seg in segs:
                    bb.append_bits(seg.mode.mode_bits, 4)
                    bb.append_bits(seg.num_chars, seg.mode.num_char_count_bits(version))
                    bb.extend(seg.data)
                # Terminator
                bb.append_bits(0, min(4, data_capacity_bits - len(bb)))
                # Pad to byte
                bb.append_bits(0, (-len(bb)) % 8)
                # Pad bytes
                pad_bytes = (data_capacity_bits - len(bb)) // 8
                for i in range(pad_bytes):
                    bb.append_bits(0xEC if i % 2 == 0 else 0x11, 8)
                data_codewords = bb.to_bytes()
                return QrCode(version, ecl, data_codewords, mask)

        raise ValueError("Data too long")

    # ---- Drawing helpers ----

    def _draw_function_patterns(self) -> None:
        # Finder patterns
        self._draw_finder_pattern(3, 3)
        self._draw_finder_pattern(self.size - 4, 3)
        self._draw_finder_pattern(3, self.size - 4)
        # Separators
        self._set_function_module(7, 0, False)
        self._set_function_module(0, 7, False)

        # Timing patterns
        for i in range(8, self.size - 8):
            self._set_function_module(i, 6, i % 2 == 0)
            self._set_function_module(6, i, i % 2 == 0)

        # Alignment patterns
        pos = QrCode._get_alignment_pattern_positions(self.version)
        for i in range(len(pos)):
            for j in range(len(pos)):
                if (i == 0 and j == 0) or (i == 0 and j == len(pos) - 1) or (i == len(pos) - 1 and j == 0):
                    continue
                self._draw_alignment_pattern(pos[i], pos[j])

        # Dark module
        self._set_function_module(8, self.size - 8, True)
        # Format info areas
        for i in range(0, 9):
            if i != 6:
                self._set_function_module(8, i, False)
                self._set_function_module(i, 8, False)
        for i in range(0, 8):
            self._set_function_module(self.size - 1 - i, 8, False)
            self._set_function_module(8, self.size - 1 - i, False)

        # Version info areas
        if self.version >= 7:
            for i in range(6):
                for j in range(3):
                    self._set_function_module(self.size - 11 + j, i, False)
                    self._set_function_module(i, self.size - 11 + j, False)

    def _draw_finder_pattern(self, x: int, y: int) -> None:
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                xx = x + dx
                yy = y + dy
                if 0 <= xx < self.size and 0 <= yy < self.size:
                    dist = max(abs(dx), abs(dy))
                    self._set_function_module(xx, yy, dist != 2 and dist != 4)

    def _draw_alignment_pattern(self, x: int, y: int) -> None:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                self._set_function_module(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)

    def _set_function_module(self, x: int, y: int, is_black: bool) -> None:
        self._modules[y][x] = is_black
        self._is_function[y][x] = True

    def _draw_format_bits(self, mask: int) -> None:
        data = (self.error_correction_level.format_bits << 3) | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ (0x537 if (rem >> 9) & 1 else 0)
        bits = ((data << 10) | rem) ^ 0x5412
        # Draw
        for i in range(0, 6):
            self._set_function_module(8, i, ((bits >> i) & 1) != 0)
        self._set_function_module(8, 7, ((bits >> 6) & 1) != 0)
        self._set_function_module(8, 8, ((bits >> 7) & 1) != 0)
        self._set_function_module(7, 8, ((bits >> 8) & 1) != 0)
        for i in range(9, 15):
            self._set_function_module(14 - i, 8, ((bits >> i) & 1) != 0)
        for i in range(0, 8):
            self._set_function_module(self.size - 1 - i, 8, ((bits >> i) & 1) != 0)
        for i in range(8, 15):
            self._set_function_module(8, self.size - 15 + i, ((bits >> i) & 1) != 0)

    def _draw_version(self) -> None:
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ (0x1F25 if (rem >> 11) & 1 else 0)
        bits = (self.version << 12) | rem
        for i in range(18):
            bit = ((bits >> i) & 1) != 0
            a = self.size - 11 + (i % 3)
            b = i // 3
            self._set_function_module(a, b, bit)
            self._set_function_module(b, a, bit)

    def _draw_codewords(self, data: bytes) -> None:
        i = 0
        right = self.size - 1
        while right >= 1:
            if right == 6:
                right -= 1
            for vert in range(self.size):
                y = self.size - 1 - vert if ((right + 1) & 2) == 0 else vert
                for x in (right, right - 1):
                    if not self._is_function[y][x] and i < len(data) * 8:
                        self._modules[y][x] = ((data[i >> 3] >> (7 - (i & 7))) & 1) != 0
                        i += 1
            right -= 2

    def _handle_masking(self, mask: int) -> int:
        if mask == -1:
            min_penalty = 1 << 30
            best = 0
            for m in range(8):
                self._apply_mask(m)
                self._draw_format_bits(m)
                pen = self._get_penalty_score()
                self._apply_mask(m)  # undo
                if pen < min_penalty:
                    min_penalty = pen
                    best = m
            mask = best
        self._apply_mask(mask)
        return mask

    def _apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if self._is_function[y][x]:
                    continue
                invert = False
                if mask == 0:
                    invert = (x + y) % 2 == 0
                elif mask == 1:
                    invert = y % 2 == 0
                elif mask == 2:
                    invert = x % 3 == 0
                elif mask == 3:
                    invert = (x + y) % 3 == 0
                elif mask == 4:
                    invert = (x // 3 + y // 2) % 2 == 0
                elif mask == 5:
                    invert = (x * y) % 2 + (x * y) % 3 == 0
                elif mask == 6:
                    invert = ((x * y) % 2 + (x * y) % 3) % 2 == 0
                elif mask == 7:
                    invert = ((x + y) % 2 + (x * y) % 3) % 2 == 0
                if invert:
                    self._modules[y][x] = not self._modules[y][x]

    # ---- Penalty scoring (simplified but standards-compliant) ----

    def _get_penalty_score(self) -> int:
        result = 0

        # Adjacent modules in row/column with same color
        for y in range(self.size):
            run_color = False
            run_len = 0
            for x in range(self.size):
                color = bool(self._modules[y][x])
                if x == 0 or color != run_color:
                    run_color = color
                    run_len = 1
                else:
                    run_len += 1
                    if run_len == 5:
                        result += 3
                    elif run_len > 5:
                        result += 1

        for x in range(self.size):
            run_color = False
            run_len = 0
            for y in range(self.size):
                color = bool(self._modules[y][x])
                if y == 0 or color != run_color:
                    run_color = color
                    run_len = 1
                else:
                    run_len += 1
                    if run_len == 5:
                        result += 3
                    elif run_len > 5:
                        result += 1

        # 2x2 blocks
        for y in range(self.size - 1):
            for x in range(self.size - 1):
                c = bool(self._modules[y][x])
                if c == bool(self._modules[y][x + 1]) == bool(self._modules[y + 1][x]) == bool(self._modules[y + 1][x + 1]):
                    result += 3

        # Balance of black modules
        black = 0
        for y in range(self.size):
            for x in range(self.size):
                if self._modules[y][x]:
                    black += 1
        total = self.size * self.size
        k = abs(black * 20 - total * 10) // total
        result += k * 10
        return result

    # ---- ECC and interleave ----

    def _add_ecc_and_interleave(self, data: bytes) -> bytes:
        ver = self.version
        ecl = self.error_correction_level
        num_blocks = QrCode._NUM_ERROR_CORRECTION_BLOCKS[ecl.format_bits][ver]
        block_ecc_len = QrCode._ECC_CODEWORDS_PER_BLOCK[ecl.format_bits][ver]
        raw_codewords = QrCode._get_num_raw_data_modules(ver) // 8
        num_short_blocks = num_blocks - raw_codewords % num_blocks
        short_block_len = raw_codewords // num_blocks

        blocks: List[bytes] = []
        k = 0
        for i in range(num_blocks):
            dat_len = short_block_len - block_ecc_len + (0 if i < num_short_blocks else 1)
            dat = data[k : k + dat_len]
            k += dat_len
            ecc = _reed_solomon_compute_remainder(dat, QrCode._reed_solomon_divisor(block_ecc_len))
            blocks.append(dat + ecc)

        result = bytearray()
        for i in range(short_block_len + 1):
            for blk in blocks:
                if i < len(blk):
                    result.append(blk[i])
        return bytes(result)

    # ---- Static tables ----

    @staticmethod
    def _get_alignment_pattern_positions(version: int) -> List[int]:
        if version == 1:
            return []
        num_align = version // 7 + 2
        step = 26 if version == 32 else ((version * 4 + 17 - 13) // (num_align - 1))
        result = [6]
        for i in range(num_align - 2):
            result.append(result[-1] + step)
        result.append(version * 4 + 17 - 7)
        return result

    @staticmethod
    def _get_num_raw_data_modules(version: int) -> int:
        result = (16 * version + 128) * version + 64
        if version >= 2:
            num_align = version // 7 + 2
            result -= (25 * num_align - 10) * num_align - 55
            if version >= 7:
                result -= 36
        return result

    @staticmethod
    def _get_num_data_codewords(version: int, ecl: "QrCode.Ecc") -> int:
        return QrCode._get_num_raw_data_modules(version) // 8 - QrCode._ECC_CODEWORDS_PER_BLOCK[ecl.format_bits][version] * QrCode._NUM_ERROR_CORRECTION_BLOCKS[ecl.format_bits][version]


class QrSegment:
    """A segment of character/binary/control data."""

    @dataclass(frozen=True)
    class Mode:
        mode_bits: int
        cc_bits: tuple

        def num_char_count_bits(self, ver: int) -> int:
            if 1 <= ver <= 9:
                return self.cc_bits[0]
            if 10 <= ver <= 26:
                return self.cc_bits[1]
            return self.cc_bits[2]

    Mode.NUMERIC = Mode(0x1, (10, 12, 14))
    Mode.ALPHANUMERIC = Mode(0x2, (9, 11, 13))
    Mode.BYTE = Mode(0x4, (8, 16, 16))
    Mode.KANJI = Mode(0x8, (8, 10, 12))

    def __init__(self, mode: "QrSegment.Mode", num_chars: int, data: List[int]):
        self.mode = mode
        self.num_chars = num_chars
        self.data = data

    @staticmethod
    def make_segments(text: str) -> List["QrSegment"]:
        if text == "":
            return []
        data = []
        for b in text.encode("utf-8"):
            for i in range(8):
                data.append((b >> (7 - i)) & 1)
        return [QrSegment(QrSegment.Mode.BYTE, len(text.encode("utf-8")), data)]

    @staticmethod
    def get_total_bits(segs: List["QrSegment"], ver: int) -> Optional[int]:
        result = 0
        for seg in segs:
            ccbits = seg.mode.num_char_count_bits(ver)
            if seg.num_chars >= (1 << ccbits):
                return None
            result += 4 + ccbits + len(seg.data)
        return result


class _BitBuffer(list):
    def append_bits(self, val: int, length: int) -> None:
        if length < 0 or val >> length != 0:
            raise ValueError("Value out of range")
        for i in reversed(range(length)):
            self.append((val >> i) & 1)

    def extend(self, bits: List[int]) -> None:  # type: ignore
        super().extend(bits)

    def to_bytes(self) -> bytes:
        out = bytearray()
        val = 0
        for i, bit in enumerate(self):
            val = (val << 1) | bit
            if (i & 7) == 7:
                out.append(val)
                val = 0
        return bytes(out)


def _reed_solomon_divisor(degree: int) -> bytes:
    result = bytearray([1])
    for i in range(degree):
        result = _reed_solomon_multiply(result, bytes([_reed_solomon_exp(i)]))
    return bytes(result)


def _reed_solomon_multiply(p: bytes, q: bytes) -> bytes:
    res = bytearray(len(p) + len(q) - 1)
    for i, a in enumerate(p):
        for j, b in enumerate(q):
            res[i + j] ^= _reed_solomon_mul(a, b)
    return bytes(res)


def _reed_solomon_compute_remainder(data: bytes, divisor: bytes) -> bytes:
    result = bytearray([0] * (len(divisor) - 1))
    for b in data:
        factor = b ^ result[0]
        result[:] = result[1:] + b"\x00"
        for i in range(len(result)):
            result[i] ^= _reed_solomon_mul(divisor[i + 1], factor)
    return bytes(result)


def _reed_solomon_mul(x: int, y: int) -> int:
    if x == 0 or y == 0:
        return 0
    return _reed_solomon_exp(_reed_solomon_log(x) + _reed_solomon_log(y))


_RS_EXP = [0] * 512
_RS_LOG = [0] * 256


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _RS_EXP[i] = x
        _RS_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _RS_EXP[i] = _RS_EXP[i - 255]


def _reed_solomon_exp(i: int) -> int:
    return _RS_EXP[i]


def _reed_solomon_log(x: int) -> int:
    return _RS_LOG[x]


_init_tables()


# Tables indexed by [ecl.format_bits][version]
QrCode._ECC_CODEWORDS_PER_BLOCK = [
    # M
    [0,
     10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
    # L
    [0,
     7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    # H
    [0,
     17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    # Q
    [0,
     13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
]

QrCode._NUM_ERROR_CORRECTION_BLOCKS = [
    # M
    [0,
     1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
    # L
    [0,
     1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
    # H
    [0,
     1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81],
    # Q
    [0,
     1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
]
