#!/usr/bin/env python3
"""Self-check of the fixed-point model (no simulator needed).

  1. Every word fits its register: the three models are run over the frames
     of the five vector sets and over frames built to reach the largest
     words, and no word may wrap.
  2. No stage has a spare bit: with the width of any one stage reduced by
     one bit, at least one of those frames no longer fits.  One exception is
     known and accepted: stage 1 of the exchanged schedule rotates by
     multiples of 90 degrees only and would fit in 17 bits; all designs use
     the common width table, which gives it 18.
  3. The models compute the transform: on full-range random frames the
     signal-to-error ratio against a double-precision DFT is at least
     SNR_MIN_DB.

  python sim/check_model_wg.py
"""

from __future__ import annotations

import cmath
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fixed_fft_wgrow as mg  # noqa: E402
import gen_vectors_wg as gv  # noqa: E402

SNR_MIN_DB = 80.0
SPARE_OK = {"subfft": [], "pfft_no_exchange": [], "pfft_exchange": [1]}
IN_MAX = gv.IN_MAX


def matched_frame(phase_of_index) -> list:
    """Corner inputs that add up in phase on the real axis of one output word:
    sample n is the corner of the input square that maximises the real part
    of x[n] * exp(-j*phase(n))."""
    frame = []
    for n in range(1024):
        ph = phase_of_index(n)
        c, s = math.cos(ph), math.sin(ph)
        frame.append(mg.cw(IN_MAX if c >= 0 else -IN_MAX,
                           IN_MAX if s >= 0 else -IN_MAX))
    return frame


def extreme_frames() -> list:
    """(name, frame) pairs that reach the largest words of every stage."""
    frames = []
    # a butterfly of stage s sees +v and -v: its difference is 2^s * v/2 ...
    for period in (1024, 512, 256, 128, 64, 32, 16, 8):
        for re, im in ((IN_MAX, IN_MAX), (IN_MAX, -IN_MAX)):
            spec = {"generator": "block_sign", "period": period,
                    "real": re, "imag": im}
            frames.append((f"block_sign_{period}_{re}_{im}",
                           mg.generate_frame_wg(spec)))
    # all 1024 inputs in phase on one bin of the transform
    for k in (1, 3, 5, 129, 511):
        frames.append((f"matched_bin_{k}", matched_frame(
            lambda n, k=k: 2.0 * math.pi * k * n / 1024.0)))
    # all 256 inputs of every lane in phase on one bin of the lane transform
    for m in (1, 3, 127):
        frames.append((f"matched_lane_bin_{m}", matched_frame(
            lambda n, m=m: 2.0 * math.pi * m * (n // 4) / 256.0)))
    return frames


def set_frames() -> list:
    out = []
    for name in gv.SETS:
        ids, frames = gv.build(name)
        out += [(f"{name}/{i}", f) for i, f in zip(ids, frames)]
    return out


def wraps(model, frame) -> int:
    mg.wrap_count = 0
    model(frame)
    return mg.wrap_count


def dft(frame) -> list:
    x = [complex(w.real, w.imag) for w in frame]
    root = [cmath.exp(-2j * math.pi * i / 1024.0) for i in range(1024)]
    return [sum(x[n] * root[(k * n) % 1024] for n in range(1024))
            for k in range(1024)]


def snr_db(model, frame, reference) -> float:
    got = model(frame)
    sig = sum(abs(r) ** 2 for r in reference)
    err = sum(abs(complex(g.real, g.imag) - r) ** 2 for g, r in zip(got, reference))
    return float("inf") if err == 0 else 10.0 * math.log10(sig / err)


def main() -> int:
    ok = True
    frames = set_frames() + extreme_frames()

    # 1. every word fits
    for name, model in gv.MODELS.items():
        bad = [(f, n) for f, n in ((fn, wraps(model, fr)) for fn, fr in frames) if n]
        print(f"fit       {name:17s} {len(frames)} frames, "
              f"{len(bad)} with a word that does not fit")
        for fn, n in bad[:5]:
            print(f"            {fn}: {n} words")
        ok &= not bad

    # 2. no spare bit in any stage
    full = mg.STAGE_W
    for name, model in gv.MODELS.items():
        spare = []
        for stage in range(1, 11):
            reduced = list(full)
            reduced[stage] -= 1
            mg.STAGE_W = tuple(reduced)
            if not any(wraps(model, fr) for _, fr in frames):
                spare.append(stage)
        mg.STAGE_W = full
        print(f"tight     {name:17s} stages with a spare bit: {spare or 'none'}"
              f" (expected {SPARE_OK[name] or 'none'})")
        ok &= spare == SPARE_OK[name]

    # 3. the transform
    random_frames = gv.build("rand_full")[1]
    references = [dft(f) for f in random_frames]
    for name, model in gv.MODELS.items():
        worst = min(snr_db(model, f, r) for f, r in zip(random_frames, references))
        print(f"transform {name:17s} worst SNR over {len(random_frames)} "
              f"random frames: {worst:.1f} dB (limit {SNR_MIN_DB:.0f})")
        ok &= worst >= SNR_MIN_DB

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
