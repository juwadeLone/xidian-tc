"""Plot per-shot SDC diff maps from results/k7_paper_s0/sdc_diff.jsonl.

Per shot, two panels:
  left  : heatmap of signed field error over (beat_in_frame x 8 fields),
          only frames containing errors are shown; white = no error
  right : |err| histogram (log-y) marking single-bit-XOR fields

Usage:
    python scripts/k7_mod/plot_sdc_diff.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm

ROOT = Path(__file__).resolve().parents[2]
FRAME_BEATS = 256


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", choices=["s0", "p0"], default="s0")
    args = ap.parse_args()
    SRC = ROOT / "results" / f"k7_paper_{args.arch}" / "sdc_diff.jsonl"
    DST = ROOT / "results" / f"k7_paper_{args.arch}" / "sdc_diff.png"
    shots = []
    for line in SRC.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d.get("kind") == "sdc_diff":
            shots.append(d)
    shots.sort(key=lambda d: d["shot"])
    if not shots:
        sys.exit("no sdc_diff records")

    fig, axes = plt.subplots(len(shots), 2, figsize=(15, 3.4 * len(shots)),
                             gridspec_kw={"width_ratios": [3, 1]},
                             squeeze=False)

    for row, d in enumerate(shots):
        t, diffs = d["target"], d["diffs"]
        frames = sorted({x["frame"] for x in diffs})
        fmap = {f: i for i, f in enumerate(frames)}

        # ---- left: beat x field error map ------------------------------
        H = np.zeros((8 * len(frames), FRAME_BEATS)) * np.nan
        for dd in diffs:
            base = fmap[dd["frame"]] * 8
            for f in dd["fields"]:
                H[base + (7 - f["field"]), dd["beat_in_frame"]] = f["err"]
        vmax = np.nanmax(np.abs(H)) or 1.0
        norm = TwoSlopeNorm(vmin=-vmax, vcenter=0.0, vmax=vmax)
        ax = axes[row][0]
        ax.imshow(np.ma.masked_invalid(H), aspect="auto", cmap="RdBu_r",
                  norm=norm, interpolation="nearest")
        yticks = [i * 8 + 3.5 for i in range(len(frames))]
        ax.set_yticks(yticks, [f"frame {f}" for f in frames])
        for i in range(1, len(frames)):
            ax.axhline(i * 8 - 0.5, color="k", lw=0.6)
        ax.set_xlabel("beat in frame (0..255)")
        ax.set_title(f"shot {d['shot']}  {t['lfa_hex']}  "
                     f"{t['clock_region']} {t['tile_kind']} "
                     f"wd={t['wd']} bt={t['bt']}", fontsize=10, loc="left")
        # minor ticks = field index within each frame block
        for i in range(len(frames)):
            for fld in range(8):
                ax.text(-6, i * 8 + (7 - fld), str(fld), fontsize=5,
                        va="center", ha="right", color="gray")

        # ---- right: |err| histogram ------------------------------------
        errs = np.array([abs(f["err"]) for dd in diffs for f in dd["fields"]])
        pow2 = np.array([f["xor_pow2"] for dd in diffs
                         for f in dd["fields"]], dtype=bool)
        ax = axes[row][1]
        bins = np.arange(0, errs.max() + 2) - 0.5
        ax.hist(errs, bins=bins, log=True, color="0.6",
                label=f"all ({len(errs)})")
        if pow2.any():
            ax.hist(errs[pow2], bins=bins, log=True, color="tab:red",
                    alpha=0.75, label=f"single-bit ({pow2.sum()})")
        ax.set_xlabel("|err| (22-bit LSB)")
        ax.set_ylabel("count")
        ax.legend(fontsize=8)
        ax.set_title(f"beats {d['summary']['n_beats']}/256  "
                     f"fields {d['summary']['n_fields']}", fontsize=9)

    fig.suptitle(f"{args.arch.upper()} injected-shot SDC diffs vs golden "
                 "(output beat = 8 x 22-bit fields)", y=0.995)
    fig.tight_layout()
    DST.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(DST, dpi=150)
    print("wrote", DST)


if __name__ == "__main__":
    main()
