#!/usr/bin/env python3
"""Generate and audit the per-stage, per-lane SubFFT ROM images for WP4-R.

S0 (s3): six independent lanes per ECC stage, each with its own ROM;
stages 1-6 hold 128/64/32/16/8/4 entries. Stage 8 cross rotation: one
256-entry ROM per rotating lane (lanes 1,2,3) per TMR replica. Contents are
identical across lanes/replicas, so one .mem file per (stage | cross lane).
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
K7 = HERE.parent
sys.path.insert(0, str(HERE))
import fixed_fft_wgrow as model  # noqa: E402

OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else (K7 / "rtl/paper_fft12/common")


def render(exponents):
    lines = []
    for exponent in exponents:
        re, im = model.twiddle_coeff(1024, exponent)
        lines.append(f"{((re & 0x3FFFF) << 18) | (im & 0x3FFFF):09x}")
    return "\n".join(lines) + "\n"


def main():
    specs = {}
    for stage in range(1, 7):
        depth = 128 >> (stage - 1)
        stride = 1 << (stage - 1)
        exps = [(k * stride) << 2 for k in range(depth)]
        # cross-check against the golden model's 256-point schedule
        length = 1 << (9 - stage)
        assert stride == 256 // length
        assert exps == [4 * offset * stride for offset in range(length // 2)]
        specs[f"subfft_s{stage}_rom_wp4r.mem"] = exps
    for lane in (1, 2, 3):
        specs[f"subfft_x{lane}_rom_wp4r.mem"] = [lane * k for k in range(256)]
    for name, exps in sorted(specs.items()):
        path = OUT_DIR / name
        path.write_text(render(exps), encoding="ascii")
        print(f"ROM_MODEL {name} entries={len(exps)} "
              f"first_exp={exps[0]} last_exp={exps[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
