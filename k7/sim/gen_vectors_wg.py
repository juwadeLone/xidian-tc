#!/usr/bin/env python3
"""Generate the qualification vectors of the 16-bit-in kernels (paper_fft12).

Input: 10 frames = 8 data frames + 2 zero padding frames (flush).

Every input component may take any signed 16-bit value.  The stage widths of
the model (18, 19, ..., 27 bits) hold every word for every such input; each
model run is checked for that, and nothing is written otherwise.

Vector sets (each 8 data frames):
  qual       the qualification set: zero, impulses, alternating, ramp,
             alternating at full scale, one random frame
             -> vectors_wg/wg_input_10frames.hex and
                vectors_wg/qualification_<model>_expected_8frames_wg.hex
  rand_full  eight random frames, both components uniform over the full
             16-bit range; also the stimulus of the board scripts
  rand_amp   random frames from +-1 up to +-16383, and two sparse frames
  tones      on-bin tones, a two-tone, a real tone, a tone in noise
  edge       frames at the corners of the input range: constants, random
             corners, sign patterns that maximise the difference of a
             butterfly, full-scale tones, random phase at full scale
  -> vectors_wg/<set>/input_10frames.hex and
     vectors_wg/<set>/<model>_expected_8frames.hex

  python sim/gen_vectors_wg.py            write every set
  python sim/gen_vectors_wg.py tones      write the named sets only

Output beats:
  * input beat  = 8 x 16-bit fields  (128 bits), frame = 4096 bytes
  * output beat = 8 x 27-bit fields  (216 bits), frame = 6912 bytes
  * field order per beat: {w0.re,w0.im,w1.re,w1.im,w2.re,w2.im,w3.re,w3.im}
    MSB-first, same convention as the 35-bit vectors
  * output beat b packs:
      subfft          natural[lane*256 + bit_reverse(b,8)]
      pfft variants   natural[bit_reverse(4*b+lane,10)]
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fixed_fft_wgrow as mg  # noqa: E402

OUT_DIR = HERE / "vectors_wg"
IN_W, OUT_W = mg.IN_W, mg.OUT_W
IN_MIN, IN_MAX = -(1 << (IN_W - 1)), (1 << (IN_W - 1)) - 1
MODELS = {"subfft": mg.subfft_1024_wgrow,
          "pfft_no_exchange": mg.pfft_no_exchange_1024_wgrow,
          "pfft_exchange": mg.pfft_exchange_1024_wgrow}


def field_mask(w: int) -> int:
    return (1 << w) - 1


def pack_words(words, w: int) -> str:
    value = 0
    for word in words:
        for component in (word.real, word.imag):
            value = (value << w) | (component & field_mask(w))
    return f"{value:0{8 * w // 4}x}"


SETS = {
    "qual": [
        {"id": "zero", "generator": "constant", "real": 0, "imag": 0},
        {"id": "impulse_first", "generator": "impulse", "index": 0,
         "real": 16384, "imag": -8192},
        {"id": "impulse_last", "generator": "impulse", "index": 1023,
         "real": -16384, "imag": 8192},
        {"id": "four_lane_impulse", "generator": "lane_impulses",
         "indices": [0, 1, 2, 3],
         "real_values": [16384, -16384, 8192, -8192],
         "imag_values": [0, 4096, -4096, 2048]},
        {"id": "alternating", "generator": "alternating", "amplitude": 4096},
        {"id": "ramp", "generator": "signed_ramp", "left_shift": 5},
        {"id": "alternating_full", "generator": "alternating", "amplitude": IN_MAX},
        {"id": "random_seeded", "generator": "uniform_random",
         "min": IN_MIN, "max": IN_MAX, "seed": 20260721}],
    "rand_full": [
        {"id": f"random_{s}", "generator": "uniform_random",
         "min": IN_MIN, "max": IN_MAX, "seed": s}
        for s in range(20261001, 20261009)],
    "rand_amp": [
        {"id": "pm1", "generator": "uniform_random", "min": -1, "max": 1, "seed": 11},
        {"id": "pm7", "generator": "uniform_random", "min": -7, "max": 7, "seed": 12},
        {"id": "pm63", "generator": "uniform_random", "min": -63, "max": 63, "seed": 13},
        {"id": "pm511", "generator": "uniform_random", "min": -511, "max": 511, "seed": 14},
        {"id": "pm4095", "generator": "uniform_random", "min": -4095, "max": 4095, "seed": 15},
        {"id": "pm16383", "generator": "uniform_random", "min": -16383, "max": 16383, "seed": 16},
        {"id": "sparse_1_16", "generator": "sparse_random", "density": 1 / 16,
         "min": IN_MIN, "max": IN_MAX, "seed": 17},
        {"id": "sparse_1_2", "generator": "sparse_random", "density": 1 / 2,
         "min": IN_MIN, "max": IN_MAX, "seed": 18}],
    "tones": [
        {"id": "bin1", "generator": "tones", "tones": [(23000, 1, 0.0)]},
        {"id": "bin5", "generator": "tones", "tones": [(23000, 5, 0.1)]},
        {"id": "bin127", "generator": "tones", "tones": [(23000, 127, 0.3)]},
        {"id": "bin256", "generator": "tones", "tones": [(23000, 256, 0.0)]},
        {"id": "bin513", "generator": "tones", "tones": [(23000, 513, 0.7)]},
        {"id": "two_tone", "generator": "tones",
         "tones": [(11500, 37, 0.0), (11500, 300, 0.25)]},
        {"id": "real_tone", "generator": "tones", "tones": [(23000, 73, 0.05)],
         "real_only": True},
        {"id": "tone_in_noise", "generator": "tones", "tones": [(18000, 201, 0.4)],
         "noise": 3000, "seed": 21}],
    "edge": [
        {"id": "dc_pos_corner", "generator": "constant",
         "real": IN_MAX, "imag": IN_MAX},
        {"id": "dc_neg_corner", "generator": "constant",
         "real": IN_MIN, "imag": IN_MIN},
        {"id": "random_corners", "generator": "random_sign",
         "low": IN_MIN, "high": IN_MAX, "seed": 31},
        {"id": "block_sign_1024", "generator": "block_sign", "period": 1024,
         "real": IN_MAX, "imag": IN_MAX},
        {"id": "block_sign_512", "generator": "block_sign", "period": 512,
         "real": IN_MAX, "imag": -IN_MAX},
        {"id": "tone3_full", "generator": "tones", "tones": [(IN_MAX, 3, 0.0)]},
        {"id": "tone129_full", "generator": "tones",
         "tones": [(IN_MAX, 129, 0.125)]},
        {"id": "circle_random", "generator": "circle_random",
         "radius": IN_MAX, "seed": 33}],
}


def build(name: str):
    """Frames of one set, each checked to be 16-bit input data."""
    ids = [spec["id"] for spec in SETS[name]]
    frames = [mg.generate_frame_wg(spec) for spec in SETS[name]]
    for ident, frame in zip(ids, frames):
        for w in frame:
            if not (IN_MIN <= w.real <= IN_MAX and IN_MIN <= w.imag <= IN_MAX):
                raise SystemExit(f"{name}/{ident}: sample ({w.real}, {w.imag}) "
                                 f"is not a {IN_W}-bit input")
    return ids, frames


def input_text(frames) -> str:
    pad = [[mg.ZERO] * 1024, [mg.ZERO] * 1024]
    lines = []
    for frame in frames + pad:
        for beat in range(256):
            lines.append(pack_words(frame[4 * beat:4 * beat + 4], IN_W))
    return "\n".join(lines) + "\n"


def expected_text(name: str, ids, frames, model_name: str) -> str:
    lines = []
    for ident, frame in zip(ids, frames):
        mg.wrap_count = 0
        natural = MODELS[model_name](frame)
        if mg.wrap_count:
            raise SystemExit(f"{name}/{ident}: {mg.wrap_count} words do not fit "
                             f"their registers in the {model_name} model")
        for beat in range(256):
            if model_name == "subfft":
                idx = tuple(lane * 256 + mg.bit_reverse(beat, 8)
                            for lane in range(4))
            else:
                idx = tuple(mg.bit_reverse(4 * beat + lane, 10)
                            for lane in range(4))
            lines.append(pack_words(tuple(natural[i] for i in idx), OUT_W))
    return "\n".join(lines) + "\n"


def write_set(name: str) -> None:
    ids, frames = build(name)
    texts = {m: expected_text(name, ids, frames, m) for m in MODELS}
    if name == "qual":
        # These four files were first written on Windows with CRLF line ends;
        # writing them the same way keeps a regeneration free of spurious diffs.
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / "wg_input_10frames.hex").write_text(
            input_text(frames), encoding="ascii", newline="\r\n")
        for model, text in texts.items():
            (OUT_DIR / f"qualification_{model}_expected_8frames_wg.hex").write_text(
                text, encoding="ascii", newline="\r\n")
    else:
        out = OUT_DIR / name
        out.mkdir(parents=True, exist_ok=True)
        (out / "input_10frames.hex").write_text(
            input_text(frames), encoding="ascii", newline="\n")
        for model, text in texts.items():
            (out / f"{model}_expected_8frames.hex").write_text(
                text, encoding="ascii", newline="\n")
    print(f"wrote the {name} vectors")


def main(argv) -> int:
    for name in argv or list(SETS):
        write_set(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
