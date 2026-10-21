"""7-series FAR and SEM 40-bit LFA packing (hop-2 of module→CRAM).

Sources (not invented bit widths):
  UG470 / Project X-Ray configuration.html:
    FAR[31:26] reserved
    FAR[25:23] bus/block type (000=CLB/IO/CLK, 001=BRAM content, 010=CFG_CLB)
    FAR[22]    top/bottom half
    FAR[21:17] row
    FAR[16:7]  column
    FAR[6:0]   minor
  Frame = 101 words × 32 bits; word 50 holds ECC (prjxray / PG036).
  Minors/column (prjxray): INT 26 + CLB 12 = 36 for a CLB column.

SEM injection (PG036, 40-bit):
  {8'hC0, lfa[19:0], word[6:0], bit[4:0]}
  i.e. the 32-bit tail is frame[31:12] | word[11:5] | bit[4:0].

  Measured on XC7A35T 2026-09-08, 67 probes: packing the frame at [31:14]
  (an 18-bit field, as this file did until then) makes SEM flip frame 4*N and
  report LA = 4*N, and silently ignores every N above MF/4. That is exactly
  the "INVALID_NO_DETECT above ~1133" wall both A7 campaigns hit. With the
  frame at [31:12] the reported LA equals the injected frame.

LFA (linear frame address) is the SEM scan index, NOT the 32-bit FAR.
SEM's own error report carries both: LA (this linear index) and PA (the
UG470 FAR), so one injection yields the pair directly - a column walk is not
needed to relate them.
"""
from __future__ import annotations

WORDS_PER_FRAME = 101
ECC_WORD = 50  # 0-based index in the 101-word frame
BITS_PER_WORD = 32

# prjxray: interconnect 26 frames + CLB extra 12 → 36. BRAM/DSP config
# columns on the CLB bus are commonly 28 (XAPP538-era tables). Marked
# as literature defaults, overridden by a calibrated table.
MINORS_DEFAULT = {
    "CLB": 36,
    "BRAM": 28,
    "DSP": 28,
    "INT": 26,
    "CFG": 2,
    "OTHER": 36,
}

BLOCK_CLB = 0
BLOCK_BRAM_CONTENT = 1
BLOCK_CFG_CLB = 2


def pack_far(block=0, top=0, row=0, column=0, minor=0) -> int:
    return (
        ((block & 7) << 23)
        | ((top & 1) << 22)
        | ((row & 0x1F) << 17)
        | ((column & 0x3FF) << 7)
        | (minor & 0x7F)
    )


def unpack_far(far: int) -> dict:
    far = int(far) & 0xFFFFFFFF
    return {
        "block": (far >> 23) & 7,
        "top": (far >> 22) & 1,
        "row": (far >> 17) & 0x1F,
        "column": (far >> 7) & 0x3FF,
        "minor": far & 0x7F,
        "far": far,
    }


def pack_sem_n(lfa: int, word: int, bit: int, prefix: int = 0xC0) -> str:
    """10 hex digits for SEM `N` payload (no leading N, no CR)."""
    if not (0 <= int(word) < WORDS_PER_FRAME):
        raise ValueError(f"word {word} not in 0..{WORDS_PER_FRAME - 1}")
    if not (0 <= int(bit) < BITS_PER_WORD):
        raise ValueError(f"bit {bit} not in 0..31")
    raw = ((prefix & 0xFF) << 32) | ((int(lfa) & 0xFFFFF) << 12) | (
        (int(word) & 0x7F) << 5) | (int(bit) & 0x1F)
    return f"{raw:010X}"


def unpack_sem_n(hex10: str) -> dict:
    s = hex10.strip().replace(" ", "").upper()
    if s.startswith("N"):
        s = s[1:]
    raw = int(s, 16)
    return {
        "prefix": (raw >> 32) & 0xFF,
        "lfa": (raw >> 12) & 0xFFFFF,
        "word": (raw >> 5) & 0x7F,
        "bit": raw & 0x1F,
    }


def minors_for(tile_class: str) -> int:
    return MINORS_DEFAULT.get((tile_class or "OTHER").upper(), 36)
