#!/usr/bin/env python3
"""Generate and audit the per-stage, unprotected PFFT ROM images for WP4-R."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fixed_fft_wgrow as model  # noqa: E402

ROOT = HERE.parent
ROM_DIR = ROOT / "rtl" / "paper_fft12" / "common"
PHI = model.exchange_phi(1024)


def used_exponents(stage: int, lane: int) -> list[int]:
    return sorted({PHI[stage - 1][4 * beat + lane] % 1024
                   for beat in range(256)})


def rom_specs() -> dict[str, tuple[int, list[int], int]]:
    specs = {}
    for stage in range(2, 8):
        exponents = used_exponents(stage, 0)
        for lane in range(1, 4):
            assert used_exponents(stage, lane) == exponents
        assert exponents == [index << (9 - stage)
                             for index in range(1 << stage)]
        specs[f"pfft_s{stage}_rom_wp4r.mem"] = (9 - stage, exponents, 1)
    for lane in (2, 3):
        assert used_exponents(8, lane) == list(range(0, 512, 2))
    specs["pfft_s8_rom_wp4r.mem"] = (1, used_exponents(8, 2), 2)
    specs["pfft_s9_rom_wp4r.mem"] = (0, used_exponents(9, 1), 1)
    specs["pfft_s9j_rom_wp4r.mem"] = (0, used_exponents(9, 3), 1)
    assert used_exponents(9, 3) == [value + 256 for value in used_exponents(9, 1)]
    return specs


def render(exponents: list[int]) -> str:
    lines = []
    for exponent in exponents:
        re, im = model.twiddle_coeff(1024, exponent)
        lines.append(f"{((re & 0x3FFFF) << 18) | (im & 0x3FFFF):09x}")
    return "\n".join(lines) + "\n"


def main() -> int:
    specs = rom_specs()
    mismatches = []
    for filename, (shift, exponents, reads) in specs.items():
        path = ROM_DIR / filename
        expected = render(exponents)
        if "--check" in sys.argv[1:]:
            if not path.is_file() or path.read_text(encoding="ascii") != expected:
                mismatches.append(filename)
        else:
            path.write_text(expected, encoding="ascii")
        print(f"ROM_MODEL {filename} entries={len(exponents)} "
              f"reads_per_beat={reads} exponent_shift={shift}")
    if mismatches:
        print("PFFT_ROM_WP4R: MISMATCH " + " ".join(mismatches))
        return 1
    print("PFFT_ROM_WP4R: " + ("UP TO DATE" if "--check" in sys.argv[1:]
                                else "GENERATED"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
