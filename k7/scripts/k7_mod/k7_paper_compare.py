"""SEM injection comparison for the paper kernels (e.g. p1 against p0).

Same shot as k7_paper_smoke.py -- SEM 'I' -> 'N <lfa>' -> WR/RUN/RD with the
bit resident -> 'O' repair -> health shot -- but every injected shot is
graded by how far the output is from the golden vectors, not only by
whether it is bit-identical.  A thresholded arithmetic code restores a
symbol to within its fault-free residual, so a corrected run can differ
from golden by a few LSBs; a byte compare would file that under SDC.

Shot classes (fixed before the campaign):
    EXACT  all 10 output frames equal the expected data
    DEV    the run completes and the data differs; the record carries
           max |deviation| (LSB), deviating fields/beats and the SNR
    DUE    the shot does not complete (ingest / run / read error)
DEV shots are binned by max |deviation|: 1-3, 4-15, 16-255, >=256 LSB.

Targets are drawn uniformly, without replacement, from the essential bits
of the image under test that lie in the pb_dut clock regions (the same pool
as k7_paper_smoke.sample_targets).  Images differ in pool size, so the
summary reports the per-injection fraction and that fraction times the
pool ("estimated bits"), each with a 95 % Wilson interval.

Run one image (board programmed with that image):
    python scripts/k7_mod/k7_paper_compare.py run --arch p0 --n-inject 1000
    python scripts/k7_mod/k7_paper_compare.py run --arch p1 --build-tag iso \
           --n-inject 1000
Compare finished logs:
    python scripts/k7_mod/k7_paper_compare.py summarize A.jsonl B.jsonl

A log is never overwritten: each run writes
results/k7_paper_<arch>[_<tag>]/compare_<utc>.jsonl.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))

OUT_W = 27
FIELDS_PER_BEAT = 8
OUT_BEAT_BYTES = 27
N_FRAMES = 10
FRAME_BEATS = 256
DEV_BINS = (("1-3", 1, 3), ("4-15", 4, 15), ("16-255", 16, 255),
            (">=256", 256, None))
CLASSES = ("EXACT", "DEV", "DUE")


# ------------------------------------------------------------------ grading

def decode_beat(word):
    """216-bit beat -> 8 signed 27-bit fields."""
    out = []
    for i in range(FIELDS_PER_BEAT):
        v = (word >> (OUT_W * i)) & ((1 << OUT_W) - 1)
        if v & (1 << (OUT_W - 1)):
            v -= 1 << OUT_W
        out.append(v)
    return out


def dev_bin(max_dev):
    for name, lo, hi in DEV_BINS:
        if max_dev >= lo and (hi is None or max_dev <= hi):
            return name
    return None


def grade(got, expected):
    """Grade one read-back against the expected bytes (golden + zero tail)."""
    if len(got) != len(expected):
        return {"cls": "DUE", "reason": f"length {len(got)} != {len(expected)}"}
    if got == expected:
        return {"cls": "EXACT", "max_dev": 0, "n_fields": 0, "n_beats": 0}
    n_beats = n_fields = max_dev = 0
    dev_sq = gold_sq = 0
    frames = set()
    for b in range(len(expected) // OUT_BEAT_BYTES):
        lo = b * OUT_BEAT_BYTES
        eb = expected[lo:lo + OUT_BEAT_BYTES]
        ef = decode_beat(int.from_bytes(eb, "little"))
        gold_sq += sum(e * e for e in ef)
        gb = got[lo:lo + OUT_BEAT_BYTES]
        if gb == eb:
            continue
        n_beats += 1
        frames.add(b // FRAME_BEATS)
        for e, g in zip(ef, decode_beat(int.from_bytes(gb, "little"))):
            d = abs(g - e)
            if d:
                n_fields += 1
                dev_sq += d * d
                max_dev = max(max_dev, d)
    return {"cls": "DEV", "max_dev": max_dev, "dev_bin": dev_bin(max_dev),
            "n_fields": n_fields, "n_beats": n_beats,
            "frames": sorted(frames),
            "snr_db": round(10 * math.log10(gold_sq / dev_sq), 2)}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, mid - half), min(1.0, mid + half))


# ---------------------------------------------------------------- summarize

def load_log(path):
    meta, shots = {}, []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["kind"] == "meta":
            meta = r
        elif r["kind"] == "targets":
            meta["pool"] = r["pool"]
        elif r["kind"] == "injected" and "grade" in r:
            shots.append(r)
    return meta, shots


def summary_of(meta, shots):
    n = len(shots)
    out = {"image": meta.get("image"), "pool": meta.get("pool"), "n": n,
           "classes": {c: 0 for c in CLASSES},
           "dev_bins": {b[0]: 0 for b in DEV_BINS},
           "recovery_events": 0, "uncorrectable_flag": 0}
    for r in shots:
        g = r["grade"]
        out["classes"][g["cls"]] += 1
        if g["cls"] == "DEV":
            out["dev_bins"][g["dev_bin"]] += 1
        tel = r.get("telemetry") or {}
        out["recovery_events"] += bool(tel.get("cor_cnt"))
        out["uncorrectable_flag"] += bool(tel.get("unc_cnt"))
    # Three failure counts, each stricter than the next: any difference,
    # a difference above 3 LSB, a difference above 255 LSB.  DUE is in all.
    due = out["classes"]["DUE"]
    bins = out["dev_bins"]
    counts = {"not_exact": n - out["classes"]["EXACT"],
              "dev_gt3_or_due": due + bins["4-15"] + bins["16-255"] + bins[">=256"],
              "dev_gt255_or_due": due + bins[">=256"]}
    out["rates"] = {}
    for name, k in counts.items():
        lo, hi = wilson(k, n)
        pool = out["pool"] or 0
        out["rates"][name] = {
            "k": k, "frac": (k / n) if n else None, "ci95": [lo, hi],
            "est_bits": round(pool * k / n) if n else None,
            "est_bits_ci95": [round(pool * lo), round(pool * hi)]}
    return out


def print_summary(su):
    print(f"image {su['image']}: pool {su['pool']} essential bits, "
          f"{su['n']} injections")
    print("  classes   " + "  ".join(f"{c}={su['classes'][c]}" for c in CLASSES))
    print("  DEV bins  " + "  ".join(f"{b}={v}" for b, v in su["dev_bins"].items()))
    print(f"  shots with a recovery event: {su['recovery_events']}, "
          f"with the uncorrectable flag: {su['uncorrectable_flag']}")
    for name, r in su["rates"].items():
        if r["frac"] is None:
            continue
        print(f"  {name:17s} {r['k']:5d}  {100 * r['frac']:6.2f} % "
              f"[{100 * r['ci95'][0]:.2f}, {100 * r['ci95'][1]:.2f}]  "
              f"est. bits {r['est_bits']} "
              f"[{r['est_bits_ci95'][0]}, {r['est_bits_ci95'][1]}]")


def cmd_summarize(args):
    for path in args.logs:
        meta, shots = load_log(path)
        print(path)
        print_summary(summary_of(meta, shots))
    return 0


# ---------------------------------------------------------------------- run

def cmd_run(args):
    import k7_paper_smoke as sm          # pulls in the board I/O stack
    import k7_paper_host as ph

    image = args.arch + (f"_{args.build_tag}" if args.build_tag else "")
    proj = f"K7_PAPER_{image.upper()}"
    build_root = args.build_root or (ROOT / "build" / "vivado")
    impl = build_root / proj / f"{proj}.runs" / "impl_1"
    ebd, bit = impl / "k7_paper_top.ebd", impl / "k7_paper_top.bit"
    if not ebd.is_file():
        print("no essential-bits file:", ebd)
        return 2
    sm.EBD = ebd

    raw = sm.load_input_raw()
    golden = sm.load_golden(args.arch)
    expected = golden + bytes(N_FRAMES * FRAME_BEATS * OUT_BEAT_BYTES - len(golden))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = args.out or (ROOT / "results" / f"k7_paper_{image}"
                            / f"compare_{stamp}.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        print("refusing to overwrite", out_path)
        return 2
    log = out_path.open("x", encoding="utf-8")

    def emit(kind, rec, show=True):
        rec = {"kind": kind, "t": datetime.now(timezone.utc).isoformat(), **rec}
        log.write(json.dumps(rec) + "\n")
        log.flush()
        if show:
            print(kind, json.dumps({k: v for k, v in rec.items()
                                    if k not in ("kind", "t", "picked")},
                                   default=str)[:200], flush=True)

    def shot():
        """One WR -> RUN -> RD cycle, graded.  Never raises."""
        try:
            res = ph.shot(host, wire, raw, golden=None)
        except Exception as e:                                # noqa: BLE001
            return {"cls": "DUE", "reason": f"{type(e).__name__}: {e}"}, None
        return grade(res["data"], expected), res["telemetry"]

    emit("meta", {
        "image": image, "arch": args.arch, "ebd": str(ebd),
        "bit_sha256": hashlib.sha256(bit.read_bytes()).hexdigest()
        if bit.is_file() else None,
        "golden_sha256": hashlib.sha256(golden).hexdigest(),
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "pb_regions": sorted(sm.PB_REGIONS), "seed": args.seed,
        "n_inject": args.n_inject, "skip": args.skip,
        "dev_bins": [b[0] for b in DEV_BINS]})

    host = sm.BoardHost(log=lambda m: print("  ", m, flush=True))
    wire = None
    shots = []
    try:
        host.start(com=args.com, udp=True)
        wire = sm.Wire()
        sm.wait_idle(host, 60.0)
        emit("sem_status_boot", {"reply": sm.s(host.uart_cmd_sem("S", timeout_ms=4000))})

        # The programmed image must be the one whose essential bits are sampled.
        g, tel = shot()
        emit("health", {"grade": g, "telemetry": tel})
        if g["cls"] != "EXACT":
            print("STOP: health shot is not bit-exact (wrong image, or a "
                  "bit left resident by an earlier run)")
            return 2
        if tel and ph.ARCH_NAMES.get(tel.get("arch")) != args.arch:
            print("STOP: board reports arch", tel.get("arch"), "not", args.arch)
            return 2

        targets, pool_n = sm.sample_targets(args.n_inject, args.seed)
        emit("targets", {"pool": pool_n, "picked": targets}, show=False)
        print(f"pool {pool_n} essential bits, {len(targets)} targets, "
              f"skipping the first {args.skip}")
        for i, t in enumerate(targets):
            if i < args.skip:
                continue
            rec = {"shot": i, "target": t}
            try:
                sm.s(host.uart_cmd_sem("I", timeout_ms=4000))
                rec["inject_ack"] = bool(sm.s(host.uart_cmd_sem(
                    "N " + t["lfa_hex"], timeout_ms=4000)))
            except Exception as e:                            # noqa: BLE001
                rec["inject_err"] = f"{type(e).__name__}: {e}"
                emit("inject_failed", rec)
                continue
            rec["grade"], rec["telemetry"] = shot()
            try:
                rec["sem_S"] = sm.s(host.uart_cmd_sem("S", timeout_ms=4000))
                rec["sem_O"] = sm.s(host.uart_cmd_sem("O", timeout_ms=8000))
                time.sleep(1.0)
            except Exception as e:                            # noqa: BLE001
                rec["sem_err"] = f"{type(e).__name__}: {e}"
            hg, _ = shot()
            rec["post_health"] = hg["cls"]
            emit("injected", rec, show=(rec["grade"]["cls"] != "EXACT"))
            shots.append(rec)
            if hg["cls"] != "EXACT":
                emit("stop", {"reason": "image not bit-exact after repair",
                              "at_shot": i})
                print("STOP: image did not recover after repair; reprogram "
                      f"and resume with --skip {i + 1}")
                break
            if len(shots) % 50 == 0:
                print(f"  {len(shots)} shots done", flush=True)
        su = summary_of({"image": image, "pool": pool_n}, shots)
        emit("summary", su, show=False)
        print_summary(su)
        print("log:", out_path)
    finally:
        try:
            host.stop()
        except Exception:                                     # noqa: BLE001
            pass
        log.close()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--com", default="COM5")
    r.add_argument("--arch", required=True,
                   choices=["s0", "s1", "s2", "s3", "p0", "p1", "p2"])
    r.add_argument("--build-tag", default="",
                   help="tag given to create_k7_paper.tcl (e.g. iso)")
    r.add_argument("--build-root", type=Path, default=None,
                   help="directory holding K7_PAPER_<IMAGE>/ (default: "
                        "build/vivado here); for an image built elsewhere")
    r.add_argument("--n-inject", type=int, default=1000)
    r.add_argument("--skip", type=int, default=0,
                   help="skip the first K targets (resume; keep --n-inject and "
                        "--seed as in the interrupted run)")
    r.add_argument("--seed", type=int, default=20261012)
    r.add_argument("--out", type=Path, default=None)
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("summarize")
    s.add_argument("logs", nargs="+")
    s.set_defaults(fn=cmd_summarize)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
