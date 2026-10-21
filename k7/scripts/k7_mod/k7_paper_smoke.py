"""S0 paper-image board smoke: N normal shots + N SEM-injected shots.

Data path contract (k7_paper_host.py):
    WR 10 frames (8 data + 2 zero pad, 40960 B) -> RUN -> RD 10*6912 B;
    first 8 output frames must equal the rand_full golden of the arch's model
    (sim/vectors_wg/rand_full/<model>_expected_8frames.hex) and the last two
    (all-zero inputs) must come back all-zero.

Injection: one essential bit per shot inside the pb_dut floorplan
(pb_dut: CLOCKREGION X0Y4:X0Y6 + X1Y3:X1Y6 -- hard-contained kernel only), exercised as
  SEM 'I' -> 'N <lfa>' -> WR/RUN/RD shot -> 'O' repair -> health shot.
S0/P0 are unprotected: a resident bit corrupts output (SDC), kills the
frame (DUE), or changes nothing observable (MASKED).  ECC (s1/s3/p1) and
TMR (s2/p2) shots additionally record kernel det/cor/unc telemetry
(engine rb rows) so recovery events are attributable per stage.

Usage:
    python scripts/k7_mod/k7_paper_smoke.py --com COM5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_cram"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))

import numpy as np  # noqa: E402

from k7_lab_paths import add_layout_ecc, add_host  # noqa: E402
add_layout_ecc(ROOT)
add_host(ROOT)

from k7_real_data_gate import BoardHost, Wire, read_range, wait_idle  # noqa
from layout_ecc.a7_far import pack_sem_n  # noqa: E402
import k7_paper_host as ph  # noqa: E402

VEC = ROOT / "sim" / "vectors_wg"
ARCHES = ["s0", "s1", "s2", "s3", "p0", "p1", "p2"]
EBD_OF = {a: (ROOT / "build" / "vivado" / f"K7_PAPER_{a.upper()}"
              / f"K7_PAPER_{a.upper()}.runs" / "impl_1"
              / "k7_paper_top.ebd") for a in ARCHES}
GOLDEN_MODEL = {"s0": "subfft", "s1": "subfft", "s2": "subfft", "s3": "subfft",
                "p0": "pfft_no_exchange", "p1": "pfft_exchange", "p2": "pfft_exchange"}
EBD = EBD_OF["s0"]     # rebound per --arch in main()
FTAB = ROOT / "results" / "k7_mod_fft" / "frame_table_k7.json"
PB_REGIONS = {"X0Y4", "X0Y5", "X0Y6", "X1Y3", "X1Y4", "X1Y5", "X1Y6"}
WPW = 101
SEM_MF = 23265


# ---------------------------------------------------------------- vectors

def ensure_vectors():
    """Generate the rand_full set on first use (gitignored, as in qualify_wg.py)."""
    if not (VEC / "rand_full" / "input_10frames.hex").is_file():
        subprocess.run([sys.executable, str(ROOT / "sim" / "gen_vectors_wg.py"),
                        "rand_full"], cwd=ROOT, check=True)


def load_input_raw():
    ensure_vectors()
    lines = [l.strip() for l in (VEC / "rand_full" / "input_10frames.hex").read_text().splitlines()
             if l.strip()]
    assert len(lines) == 10 * ph.FRAME_BEATS
    beats = [int(l, 16) for l in lines]
    return b"".join(b.to_bytes(ph.IN_BEAT_BYTES, "little") for b in beats)


def load_golden(arch="s0"):
    ensure_vectors()
    lines = [l.strip() for l in (VEC / "rand_full" / f"{GOLDEN_MODEL[arch]}_expected_8frames.hex")
             .read_text().splitlines() if l.strip()]
    assert len(lines) == 8 * ph.FRAME_BEATS
    return b"".join(int(l, 16).to_bytes(ph.OUT_BEAT_BYTES, "little") for l in lines)


def check_got(got, golden):
    """got = 10*6912 B.  Returns (ok, n_diff, tail_zero_ok)."""
    exp_len = len(golden)
    n_diff = sum(1 for a, b in zip(got[:exp_len], golden) if a != b)
    tail = got[exp_len:]
    return n_diff == 0 and not any(tail), n_diff, not any(tail)


# ------------------------------------------------------- injection targets

def sample_targets(n, seed):
    words = []
    with EBD.open("r", encoding="ascii", errors="replace") as f:
        for line in f:
            s = line.strip()
            if len(s) == 32 and set(s) <= {"0", "1"}:
                words.append(int(s, 2))
    words = np.asarray(words, dtype=np.uint32).reshape(-1, WPW)

    ftab = json.loads(FTAB.read_text(encoding="utf-8"))
    dead = set(int(x) for x in ftab.get("dead_lfas") or [])
    cols = [c for c in ftab["columns"]
            if c.get("clock_region") in PB_REGIONS
            and c.get("frame_lo") is not None
            and c.get("lfa_source") == "measured"
            and c.get("n_dead_probes", 0) == 0]
    col_of_la = {}
    for c in cols:
        for la in range(c["frame_lo"], c["frame_hi"] + 1):
            col_of_la[la] = c

    bits = np.unpackbits(words.astype(">u4").view(np.uint8).reshape(-1, WPW, 4),
                         axis=2, bitorder="big")          # pos p -> bit 31-p
    ess = np.flatnonzero(bits.ravel())
    pool = []
    for flat in ess:
        fr = int(flat) // (WPW * 32)
        la = fr - 1                                        # idx0 = pad frame
        if la < 0 or la > SEM_MF or la in dead or la not in col_of_la:
            continue
        wd = (int(flat) % (WPW * 32)) // 32
        bt = 31 - (int(flat) % 32)
        if wd == 50:                                       # ECC word: semantics
            continue                                       # not DUT function
        pool.append((la, wd, bt, col_of_la[la]))
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(pool), size=min(n, len(pool)), replace=False)
    out = []
    for i in pick:
        la, wd, bt, c = pool[int(i)]
        out.append({"la": la, "wd": wd, "bt": bt,
                    "lfa_hex": pack_sem_n(la, wd, bt),
                    "clock_region": c["clock_region"],
                    "tile_kind": c["tile_kind"], "tile_x": c["tile_x"]})
    return out, len(pool)


# ------------------------------------------------------------------ shots

def do_shot(host, wire, raw, golden):
    """One WR->RUN->RD cycle.  Returns dict; never raises (err recorded)."""
    rec = {"ok": None, "sha256": None, "err": None, "n_diff": None}
    try:
        res = ph.shot(host, wire, raw, golden=None)
        ok, n_diff, tail_ok = check_got(res["data"], golden)
        rec.update(ok=bool(ok), sha256=res["sha256"], n_diff=n_diff,
                   tail_zero=tail_ok, write_s=res["write_s"],
                   run_s=res["run_s"], read_s=res["read_s"],
                   telemetry=res["telemetry"], data=res["data"])
    except Exception as e:                                   # noqa: BLE001
        rec["err"] = f"{type(e).__name__}: {e}"
        rec["ok"] = False
    return rec


def s(x):
    return x.decode("ascii", "replace") if isinstance(x, (bytes, bytearray)) else str(x)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--com", default="COM5")
    ap.add_argument("--arch", choices=ARCHES, default="s0")
    ap.add_argument("--n-normal", type=int, default=10)
    ap.add_argument("--n-inject", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20261011)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.out is None:
        args.out = ROOT / "results" / f"k7_paper_{args.arch}" / "smoke.jsonl"

    global EBD
    EBD = EBD_OF[args.arch]

    raw = load_input_raw()
    golden = load_golden(args.arch)
    log_path = args.out
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("w", encoding="utf-8")

    def emit(kind, rec):
        rec = {"kind": kind, "t": datetime.now(timezone.utc).isoformat(),
               **{k: v for k, v in rec.items() if k != "data"}}
        log.write(json.dumps(rec) + "\n")
        log.flush()
        print(kind, json.dumps({k: v for k, v in rec.items()
                                if k not in ("kind", "t", "telemetry")},
                               default=str)[:220], flush=True)

    emit("meta", {"arch": args.arch, "ebd": str(EBD), "golden_sha256":
                  hashlib.sha256(golden).hexdigest(),
                  "input_sha256": hashlib.sha256(raw).hexdigest(),
                  "pb_regions": sorted(PB_REGIONS), "seed": args.seed})

    host = BoardHost(log=lambda m: print("  ", m, flush=True))
    wire = None
    try:
        host.start(com=args.com, udp=True)
        wire = Wire()
        wait_idle(host, 60.0)
        st = s(host.uart_cmd_sem("S", timeout_ms=4000))
        emit("sem_status_boot", {"reply": st, "flags": host.last_sem_status})

        # health gate: the image must reproduce golden before anything counts
        h = do_shot(host, wire, raw, golden)
        emit("health", h)
        if not h["ok"]:
            print("STOP: health shot failed (golden not reproduced)")
            return 2

        # ---- normal shots -------------------------------------------------
        n_ok = 0
        for i in range(args.n_normal):
            r = do_shot(host, wire, raw, golden)
            r["shot"] = i
            emit("normal", r)
            n_ok += bool(r["ok"])
        emit("normal_summary", {"ok": n_ok, "n": args.n_normal})

        # ---- injected shots ------------------------------------------------
        targets, pool_n = sample_targets(args.n_inject, args.seed)
        emit("targets", {"pool": pool_n, "picked": targets})
        inj_ok = 0
        for i, t in enumerate(targets):
            rec = {"shot": i, "target": t}
            try:
                s(host.uart_cmd_sem("I", timeout_ms=4000))
                rec["inject_ack"] = bool(s(host.uart_cmd_sem(
                    "N " + t["lfa_hex"], timeout_ms=4000)))
            except Exception as e:                           # noqa: BLE001
                rec["err"] = f"inject: {type(e).__name__}: {e}"
                emit("injected", rec)
                continue
            r = do_shot(host, wire, raw, golden)
            rec.update({"shot_result": {k: v for k, v in r.items()}})
            try:
                rec["sem_S"] = s(host.uart_cmd_sem("S", timeout_ms=4000))
                rec["sem_O"] = s(host.uart_cmd_sem("O", timeout_ms=8000))
                time.sleep(1.0)
                rec["sem_pages"] = [s(p) for p in
                                    host.dump_monitor_pages(n_pages=4)]
            except Exception as e:                           # noqa: BLE001
                rec["sem_err"] = f"{type(e).__name__}: {e}"
            # repair settles asynchronously; health shot proves the image is
            # clean again before the next injection.
            hh = do_shot(host, wire, raw, golden)
            rec["post_health_ok"] = hh["ok"]
            emit("injected", rec)
            inj_ok += bool(r["ok"])
            if not hh["ok"]:
                emit("stop", {"reason": "post-inject health failed",
                              "at_shot": i})
                print("STOP: image did not recover after repair")
                return 3
        emit("inject_summary", {"clean_output": inj_ok, "n": len(targets)})
    finally:
        try:
            if wire:
                pass
            host.stop()
        except Exception:
            pass
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
