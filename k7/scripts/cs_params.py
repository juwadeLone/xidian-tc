#!/usr/bin/env python3
"""CS radar parameters per frame size -- single source of truth.

The 4096 set (B=300 MHz, Tp=5 us, Rs=4.5 km, Da=0.5 m) was derived for a
4096x4096 frame and **cannot image at 2048x2048**: at 2048 the range pulse
Tp*Fs = 1950 samples is 95% of the frame and the azimuth synthetic aperture
lambda*Rs/Da/(v/PRF) = 2445 pulses is 119% of it, i.e. longer than the frame,
so no target can be focused.  The bit-exact gate still passes on that data
because it only proves RTL == golden model, not that an image is formed.

Both frame-relevant quantities scale linearly with the frame, so the 2048 set
halves them (reproducing the 4096 ratios 48% / 60%):

    Rs: 4500 m -> 2250 m    azimuth aperture 2445 -> 1223 pulses (59.7%)
    Tp: 5 us   -> 2.5 us    range pulse    1950 ->  975 samples (47.6%)

Everything else is untouched, so the azimuth sampling grid, the Doppler band
ratio (PRF/BA = 1.2) and the CS kernels keep the structure the 4096 design was
calibrated with, and the approximations that depend on Rs get *better*: RCM
18.7 -> 9.4 cells, SRC term halves, H1/C4 quadratic-fit residuals scale with Rs.

Changing these values is not free: the seed ROMs are written at synthesis time
(vivado/synth_copy_mem_k7.tcl), so every parameter change means a new bitstream,
and the expected OUT (sim/vectors/cs2048_golden_out.bin, the XOR fold in
rtl/board/generated/cs2048_expected_fold.vh) must be regenerated too.
"""

PARAMS_4096 = {
    "C": 3e8, "lam": 0.0566, "B": 300e6, "Fs": 1.3 * 300e6,
    "Tp": 5e-6, "gamma": 300e6 / 5e-6,
    "v": 100.0, "Da": 0.5, "PRF": 1.2 * (2 * 100.0 / 0.5),
    "Rs": 4.5e3,
}

PARAMS_2048 = {
    "C": 3e8, "lam": 0.0566, "B": 300e6, "Fs": 1.3 * 300e6,
    "Tp": 2.5e-6, "gamma": 300e6 / 2.5e-6,
    "v": 100.0, "Da": 0.5, "PRF": 1.2 * (2 * 100.0 / 0.5),
    "Rs": 2250.0,
}

# Which set each committed fixture was built from.  The 256 and 4096 fixtures
# predate the 2048 port and must keep their parameters or their recorded
# expected outputs stop matching.
PARAMS_BY_SIDE = {256: PARAMS_4096, 4096: PARAMS_4096, 2048: PARAMS_2048}

# The active production set (the K7 board runs 2048x2048).
PARAMS = PARAMS_2048
