#!/usr/bin/env python3
"""Fixed-point FFT golden model for the 16-bit-in kernels (paper_fft12).

Arithmetic contract with the RTL:
  * input sample: signed 16-bit per component, any value.
  * the word grows by two bits in stage 1 and by one bit in each later
    stage: stage s has STAGE_W[s] = 17 + s bits per component at its output,
    18, 19, ..., 27.  No stage shifts its result.
      - a butterfly doubles the complex magnitude at most, one bit per stage;
      - a rotation can put the whole magnitude on one component, a factor of
        up to sqrt(2) over the component range of the input; this is paid
        once, by the second bit of stage 1.
    With these widths every word fits its register for every input.
    wrap_count counts the words that did not; it must stay zero.
  * butterfly: (a + b), (a - b) kept at full precision.
  * twiddle multiply on a value v with an 18-bit Q2.16 coefficient c:
    prod = v*c, arithmetic shift right by 16, kept at the stage output width.
    This truncation is the only rounding in a stage.

Schedules:
  * subfft: four independent 256-point R2SDF lanes (stages 1-8), cross
    twiddle and two radix-2 combine layers (stages 9 and 10)
  * pfft_no_exchange: canonical radix-2 DIF, twiddle on the lower branch,
    ten stages on the 4-parallel stream
  * pfft_exchange: the exchanged twiddle schedule, both branches rotated in
    stages 1-9
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence

IN_W = 16
STAGE_W = (None, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27)   # index = stage
OUT_W = STAGE_W[10]
TW_FRAC = 16
TW_MIN = -(1 << 17)
TW_MAX = (1 << 17) - 1


@dataclass(frozen=True)
class ComplexWord:
    real: int
    imag: int


cw = ComplexWord
ZERO = cw(0, 0)


wrap_count = 0          # words that did not fit their register; must stay zero


def wrap(value: int, w: int) -> int:
    """Two's-complement wrap to a signed w-bit register."""
    global wrap_count
    m = 1 << w
    wrapped = (value + (m >> 1)) % m - (m >> 1)
    if wrapped != value:
        wrap_count += 1
    return wrapped


def wrap_c(v: ComplexWord, w: int) -> ComplexWord:
    return cw(wrap(v.real, w), wrap(v.imag, w))


def quantize_twiddle(value: float) -> int:
    scaled = value * (1 << TW_FRAC)
    q = int(math.floor(scaled + 0.5)) if scaled >= 0 else int(math.ceil(scaled - 0.5))
    return min(TW_MAX, max(TW_MIN, q))


def twiddle_coeff(n_points: int, exponent: int) -> tuple[int, int]:
    angle = -2.0 * math.pi * (exponent % n_points) / n_points
    return quantize_twiddle(math.cos(angle)), quantize_twiddle(math.sin(angle))


def mul_twiddle(v: ComplexWord, n_points: int, exponent: int, w: int) -> ComplexWord:
    """18-bit twiddle multiply, product>>>16, kept in a signed w-bit word."""
    wr, wi = twiddle_coeff(n_points, exponent)
    real_wide = v.real * wr - v.imag * wi
    imag_wide = v.real * wi + v.imag * wr
    return cw(wrap(real_wide >> TW_FRAC, w), wrap(imag_wide >> TW_FRAC, w))


def butterfly(a: ComplexWord, b: ComplexWord, w_out: int) -> tuple[ComplexWord, ComplexWord]:
    """Radix-2 butterfly at full precision, results in w_out-bit words."""
    return cw(wrap(a.real + b.real, w_out), wrap(a.imag + b.imag, w_out)), \
           cw(wrap(a.real - b.real, w_out), wrap(a.imag - b.imag, w_out))


def rot_j3(v: ComplexWord, w: int) -> ComplexWord:
    """Multiply by -j: (re,im) -> (im,-re), in w-bit words."""
    return cw(v.imag, wrap(-v.real, w))


def bit_reverse(value: int, bits: int) -> int:
    result = 0
    for _ in range(bits):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


def bit_reverse_order(values: Sequence[ComplexWord]) -> list[ComplexWord]:
    bits = int(math.log2(len(values)))
    return [values[bit_reverse(i, bits)] for i in range(len(values))]


# ---------------------------------------------------------------- lane SDF
def classic_r2sdf_fft_wgrow(values: Sequence[ComplexWord]) -> list[ComplexWord]:
    """256-point, 8-stage single-lane R2SDF, stage output widths 18..25.

    Functional (non-cycle) equivalent of the lane dataflow: each stage
    performs the classic DIF decomposition -- for a stage of logical length
    L = 256/2^(k-1), upper indices hold (a+b), lower hold W^e*(a-b).
    """
    state = [wrap_c(v, IN_W) for v in values]
    for stage in range(1, 9):
        length = 1 << (9 - stage)          # 256..2
        depth = length // 2
        stride = 256 // length
        w_out = STAGE_W[stage]
        result = list(state)
        for base in range(0, 256, length):
            for offset in range(depth):
                a, b = state[base + offset], state[base + depth + offset]
                upper, lower = butterfly(a, b, w_out)
                result[base + offset] = upper
                result[base + depth + offset] = mul_twiddle(
                    lower, 256, offset * stride, w_out)
        state = result
    return bit_reverse_order(state)


def subfft_1024_wgrow(values: Sequence[ComplexWord]) -> list[ComplexWord]:
    """Four independent 256-point lanes, cross twiddle, two combine layers.

    The cross twiddle is a rotation of the stage-8 word and keeps its width;
    the combine layers are stages 9 and 10 and add one bit each.
    """
    lanes = [[wrap_c(values[4 * s + lane], IN_W) for s in range(256)]
             for lane in range(4)]
    natural = [classic_r2sdf_fft_wgrow(lane) for lane in lanes]
    w8, w9, w10 = STAGE_W[8], STAGE_W[9], STAGE_W[10]

    out: list[ComplexWord] = [ZERO] * 1024
    for k in range(256):
        # stage 9: cross twiddle lane*k, then the first radix-2 layer
        crossed = [mul_twiddle(natural[lane][k], 1024, lane * k, w8)
                   for lane in range(4)]
        a0, a2 = butterfly(crossed[0], crossed[2], w9)
        a1, d13 = butterfly(crossed[1], crossed[3], w9)
        a3 = rot_j3(d13, w9)
        # stage 10: second radix-2 layer
        q0, q2 = butterfly(a0, a1, w10)
        q1, q3 = butterfly(a2, a3, w10)
        for q, word in enumerate((q0, q1, q2, q3)):
            out[k + 256 * q] = word
    return out


# ----------------------------------------------------------------- P-SDF
def pfft_no_exchange_1024_wgrow(values: Sequence[ComplexWord]) -> list[ComplexWord]:
    """Canonical radix-2 DIF, 10 stages, stage output widths 18..27."""
    state = [wrap_c(v, IN_W) for v in values]
    for stage in range(1, 11):
        length = 1 << (11 - stage)
        depth = length // 2
        stride = 1024 // length
        w_out = STAGE_W[stage]
        result = list(state)
        for base in range(0, 1024, length):
            for offset in range(depth):
                a, b = state[base + offset], state[base + depth + offset]
                upper, lower = butterfly(a, b, w_out)
                result[base + offset] = upper
                result[base + depth + offset] = mul_twiddle(
                    lower, 1024, offset * stride, w_out)
        state = result
    return bit_reverse_order(state)


# ------------------------------------------------------- exchanged P-SDF
def exchange_phi(n_points: int) -> list[list[int]]:
    """Frozen ``dit_latest`` triangular-matrix exponent placement.

    Same construction as the shipped 35-bit model: phi[stage-1][index]
    accumulates term 1<<(nbits-1-high+low) wherever index[high] and
    index[low] are both set, with stage = nbits-low-1.
    """
    nbits = int(math.log2(n_points))
    if 1 << nbits != n_points:
        raise ValueError("n_points must be a power of two")
    phi = [[0 for _ in range(n_points)] for _ in range(nbits - 1)]
    for high in range(1, nbits):
        for low in range(high):
            latest_stage = nbits - low - 1
            term = 1 << (nbits - 1 - high + low)
            target = phi[latest_stage - 1]
            for index in range(n_points):
                if ((index >> high) & 1) and ((index >> low) & 1):
                    target[index] += term
    return phi


def exchanged_dif_stage_wgrow(
    state: Sequence[ComplexWord], stage: int, phi: Sequence[Sequence[int]],
) -> list[ComplexWord]:
    """One exchanged P-SDF stage.

    Radix-2 DIF butterflies at the stage's stride, then BOTH outputs rotated
    by phi[stage-1][flat index] (stages 1..nbits-1 only).
    """
    n_points = len(state)
    nbits = int(math.log2(n_points))
    length = 1 << (nbits - stage + 1)
    depth_global = length // 2
    w_out = STAGE_W[stage]
    result = list(state)
    for base in range(0, n_points, length):
        for offset in range(depth_global):
            upper_index = base + offset
            lower_index = base + depth_global + offset
            a, b = state[upper_index], state[lower_index]
            upper, lower = butterfly(a, b, w_out)
            if stage <= nbits - 1:
                upper = mul_twiddle(upper, n_points,
                                    phi[stage - 1][upper_index] % n_points,
                                    w_out)
                lower = mul_twiddle(lower, n_points,
                                    phi[stage - 1][lower_index] % n_points,
                                    w_out)
            result[upper_index] = upper
            result[lower_index] = lower
    return result


def pfft_exchange_1024_wgrow(values: Sequence[ComplexWord]) -> list[ComplexWord]:
    """Exchanged-schedule P-SDF, 10 stages, stage output widths 18..27.

    The dit_latest triangular exponent placement applied per output index
    through stages 1-9 (stage 10 carries no twiddle).
    """
    if len(values) != 1024:
        raise ValueError("PFFT requires exactly 1024 complex samples")
    phi = exchange_phi(1024)
    state = [wrap_c(v, IN_W) for v in values]
    for stage in range(1, 11):
        state = exchanged_dif_stage_wgrow(state, stage, phi)
    return bit_reverse_order(state)


# ------------------------------------------------------------- stimulus
def disc_point(rng: random.Random, radius: int) -> ComplexWord:
    """One sample drawn uniformly from the disc |x| <= radius."""
    while True:
        re, im = rng.randint(-radius, radius), rng.randint(-radius, radius)
        if re * re + im * im <= radius * radius:
            return cw(re, im)


def generate_frame_wg(spec: dict) -> list[ComplexWord]:
    frame = [ZERO] * 1024
    g = spec["generator"]
    if g == "constant":
        return [cw(spec["real"], spec["imag"]) for _ in frame]
    if g == "impulse":
        frame[spec["index"]] = cw(spec["real"], spec["imag"])
        return frame
    if g == "lane_impulses":
        for index, real, imag in zip(spec["indices"], spec["real_values"],
                                     spec["imag_values"]):
            frame[index] = cw(real, imag)
        return frame
    if g == "alternating":
        amp = spec["amplitude"]
        return [cw(amp if i % 2 == 0 else -amp,
                   -amp if i % 4 < 2 else amp) for i in range(1024)]
    if g == "signed_ramp":
        shift = spec["left_shift"]
        return [cw((i - 512) << shift, (511 - i) << shift) for i in range(1024)]
    if g == "block_sign":
        # +value on the first half of every block of `period` samples and
        # -value on the second half: the difference of a butterfly of that
        # span is twice the value, at every twiddle exponent of the stage
        half = spec["period"] // 2
        re, im = spec["real"], spec["imag"]
        return [cw(re, im) if (i % spec["period"]) < half else cw(-re, -im)
                for i in range(1024)]
    if g == "uniform_random":
        rng = random.Random(spec["seed"])
        return [cw(rng.randint(spec["min"], spec["max"]),
                   rng.randint(spec["min"], spec["max"])) for _ in range(1024)]
    if g == "uniform_disc":
        # uniform over the disc |x| <= radius
        rng = random.Random(spec["seed"])
        return [disc_point(rng, spec["radius"]) for _ in range(1024)]
    if g == "sparse_random":
        # a fraction `density` of the samples is non-zero, uniform over the
        # full range of both components
        rng = random.Random(spec["seed"])
        out = []
        for _ in range(1024):
            hit = rng.random() < spec["density"]
            point = cw(rng.randint(spec["min"], spec["max"]),
                       rng.randint(spec["min"], spec["max"]))
            out.append(point if hit else ZERO)
        return out
    if g == "circle_random":
        # magnitude = radius (components rounded towards zero), random phase
        rng = random.Random(spec["seed"])
        out = []
        for _ in range(1024):
            angle = 2.0 * math.pi * rng.random()
            out.append(cw(int(spec["radius"] * math.cos(angle)),
                          int(spec["radius"] * math.sin(angle))))
        return out
    if g == "random_sign":
        # every component is +high or low, chosen at random
        rng = random.Random(spec["seed"])
        lo, hi = spec["low"], spec["high"]
        return [cw(hi if rng.random() < 0.5 else lo,
                   hi if rng.random() < 0.5 else lo) for _ in range(1024)]
    if g == "tones":
        # sum of complex exponentials (amplitude, bin, phase in turns),
        # optionally real-only, optionally with uniform noise added
        rng = random.Random(spec.get("seed", 0))
        noise = spec.get("noise", 0)
        out = []
        for n in range(1024):
            re = im = 0.0
            for amp, k, phase in spec["tones"]:
                ang = 2.0 * math.pi * (k * n / 1024.0 + phase)
                re += amp * math.cos(ang)
                im += amp * math.sin(ang)
            if spec.get("real_only"):
                im = 0.0
            re = int(round(re)) + (rng.randint(-noise, noise) if noise else 0)
            im = int(round(im)) + (rng.randint(-noise, noise) if noise else 0)
            out.append(cw(re, im))
        return out
    raise ValueError(f"unknown frame generator: {g}")
