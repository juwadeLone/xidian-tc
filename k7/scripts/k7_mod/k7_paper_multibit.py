"""Multi-bit injection demo: chain several SEM 'N' commands in one
injection session ('I' -> N x k -> shot -> 'O') and diff vs golden.

Usage:
    python scripts/k7_mod/k7_paper_multibit.py --arch s0 --com COM5 \
        --targets C000EC8286 C0019BF279
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_cram"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))

from k7_lab_paths import add_layout_ecc, add_host  # noqa: E402
add_layout_ecc(ROOT)
add_host(ROOT)

from k7_real_data_gate import BoardHost, Wire, wait_idle  # noqa: E402
import k7_paper_host as ph  # noqa: E402
from k7_paper_smoke import load_input_raw, load_golden, do_shot, s  # noqa
from k7_paper_sdc_probe import diff_shot, summarize  # noqa


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--com", default="COM5")
    ap.add_argument("--arch", choices=["s0", "p0"], default="s0")
    ap.add_argument("--targets", nargs="+", required=True,
                    help="lfa_hex words (as printed in smoke logs)")
    args = ap.parse_args(argv)

    raw = load_input_raw()
    golden = load_golden(args.arch)

    host = BoardHost(log=lambda m: print("  ", m, flush=True))
    try:
        host.start(com=args.com, udp=True)
        wire = Wire()
        wait_idle(host, 60.0)
        print("sem S:", s(host.uart_cmd_sem("S", timeout_ms=4000)))

        h = do_shot(host, wire, raw, golden)
        print("health", h["ok"], "n_diff", h["n_diff"])
        if not h["ok"]:
            s(host.uart_cmd_sem("O", timeout_ms=8000))
            time.sleep(1.0)
            h = do_shot(host, wire, raw, golden)
            print("post-O health", h["ok"])
            if not h["ok"]:
                return 2

        s(host.uart_cmd_sem("I", timeout_ms=4000))
        for lfa in args.targets:
            r = s(host.uart_cmd_sem("N " + lfa, timeout_ms=4000))
            print(f"N {lfa} -> {r}")
        try:
            res = ph.shot(host, wire, raw, golden=None)
            diffs = diff_shot(res["data"], golden)
            summ = summarize(diffs)
            print(f"multi-bit({len(args.targets)}) sha={res['sha256'][:16]} "
                  f"beats={summ['n_beats']} fields={summ['n_fields']} "
                  f"pow2={summ['pow2_fields']} frames={summ['frames_hit']} "
                  f"bpf={summ['beats_per_frame']}")
        finally:
            print("O ->", s(host.uart_cmd_sem("O", timeout_ms=8000)))
            time.sleep(1.0)
        hh = do_shot(host, wire, raw, golden)
        print("post_health", hh["ok"], "n_diff", hh["n_diff"])
    finally:
        host.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
