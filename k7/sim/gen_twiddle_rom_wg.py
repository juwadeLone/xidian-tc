#!/usr/bin/env python3
"""Twiddle ROM generator for the paper_fft12 kernels (stdlib only).

Writes, for the 1024-point twiddle table of fixed_fft_wgrow.twiddle_coeff:
  rtl/paper_fft12/common/twiddle_rom_1024_secded.mem  1024 lines, 43-bit SECDED
                                                      codeword (11 hex digits)
  sim/twiddle_rom_1024_expected_wg.hex                1024 lines, 36-bit data
                                                      {re[17:0], im[17:0]} (9 hex digits)

The codeword layout is that of secded_encode_w #(.DATA_W(36), .PARITY(6)) in
rtl/paper_fft12/common/secded_wg.sv: codeword position p (1..42) is bit p-1;
data bit k goes to the k-th non-power-of-two position; parity at position pp
is the XOR of the positions whose index has bit pp set; bit 42 is the XOR of
bits 0..41.

Usage: python sim/gen_twiddle_rom_wg.py           (regenerate both files)
       python sim/gen_twiddle_rom_wg.py --check   (compare files on disk; exit 1 on mismatch)
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fixed_fft_wgrow as m  # noqa: E402

ROOT = HERE.parent
MEM_PATH = ROOT / "rtl" / "paper_fft12" / "common" / "twiddle_rom_1024_secded.mem"
HEX_PATH = ROOT / "sim" / "twiddle_rom_1024_expected_wg.hex"
DATA_W = 36
CODE_W = 43


def data36(exponent: int) -> int:
    re, im = m.twiddle_coeff(1024, exponent)
    return ((re & 0x3FFFF) << 18) | (im & 0x3FFFF)


def secded_encode(data: int) -> int:
    bits = [0] * (CODE_W - 1)                       # codeword positions 1..42
    payload = 0
    for pos in range(1, CODE_W):
        if pos & (pos - 1):
            bits[pos - 1] = (data >> payload) & 1
            payload += 1
    for pp in (1, 2, 4, 8, 16, 32):
        parity = 0
        for pos in range(1, CODE_W):
            if pos & pp:
                parity ^= bits[pos - 1]
        bits[pp - 1] = parity
    overall = 0
    for b in bits:
        overall ^= b
    return sum(b << i for i, b in enumerate(bits)) | (overall << (CODE_W - 1))


def render():
    mem_lines, hex_lines = [], []
    for e in range(1024):
        d = data36(e)
        mem_lines.append("%011x" % secded_encode(d))
        hex_lines.append("%09x" % d)
    return "\n".join(mem_lines) + "\n", "\n".join(hex_lines) + "\n"


def main() -> int:
    mem_text, hex_text = render()
    if "--check" in sys.argv[1:]:
        bad = []
        for path, text in ((MEM_PATH, mem_text), (HEX_PATH, hex_text)):
            if not path.is_file() or path.read_text(encoding="ascii") != text:
                bad.append(str(path))
        if bad:
            print("TWIDDLE_ROM_FILES: MISMATCH " + " ".join(bad))
            return 1
        print("TWIDDLE_ROM_FILES: UP TO DATE")
        return 0
    MEM_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEM_PATH.write_text(mem_text, encoding="ascii")
    HEX_PATH.write_text(hex_text, encoding="ascii")
    print(f"wrote {MEM_PATH.relative_to(ROOT)} and {HEX_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
