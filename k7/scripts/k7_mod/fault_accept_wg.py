#!/usr/bin/env python3
"""Fault acceptance for the protected stages of the s3 / p1 kernels.

  python scripts/k7_mod/fault_accept_wg.py <arch> [--stages all|1,3,7-10]
         [--vsets all|qual,rand_full,...] [--points K] [--seed N]
         [--bit-min B] [--low-bits] [--hold] [--exact] [--snapshot DIR]
         [--lane L --bit B --frame F --block K --off O]   (one fixed point)

For every selected stage the testbench sim/tb_ecc_fault_wg.sv is compiled
once; every (vector set, fault point, fault case) is then one xsim run.  A
fault point (stream, bit, beat) is drawn per stage and vector set from the
seed, so a run is reproducible; --lane/--bit/--frame/--block/--off replace
the draw by one fixed point.

Stage kinds and fault cases:
  lane ECC stage   1 functional subtractor   2 functional feedback word, 2nd
                   half   3 functional feedback word, 1st half   4 functional
                   adder   5 functional rotator output   6 check subtractor
                   7 check feedback word
  p1 stage 10      1 functional subtractor   4 functional adder
                   6 check subtractor
  triplicated      9 output register of one replica

The run starts with one fault-free simulation per vector set.

Acceptance (each fault is a one-cycle flip of one bit, bit >= --bit-min so
that its size is well above the decoding threshold):
  fault-free    read-back equals the vectors, no stage reports an event,
                every fault-free syndrome is within the stage's threshold
  lane, stage 10
                the fault is applied in the intended half-period; the stage
                detects and corrects it (two corrections for case 2, one
                otherwise), raises no uncorrectable flag, and no other stage
                reports anything; check-stream faults leave the read-back
                equal to the vectors; functional faults leave it within
                DEV_LIMIT LSB (equal with --exact)
  triplicated   read-back equals the vectors, no stage reports an event

Reported without a verdict:
  --low-bits    the same faults on bits below --bit-min (at or under the
                threshold, where a thresholded check may not act)
  --hold        the bit is stuck at 1 from the fault beat to the end of the
                run (lane stages, cases 1, 4, 5)
  --msb         the flipped bit is the top bit of the stage's output word,
                once in each of the four streams (an error of half the word
                range, whose sign a modulo-2^W syndrome cannot tell)
  --snapshot    the same faults on the pre-change datapath in DIR
"""
import argparse
import json
import math
import os
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIVADO_BIN = Path(os.environ.get("VIVADO_BIN", r"C:\Xilinx\Vivado\2018.3\bin"))


def tool(name):
    return str(VIVADO_BIN / (name + ".bat" if os.name == "nt" else name))
ARCH_SEL = {"s3": 3, "p1": 5}
VSETS = ["qual", "rand_full", "rand_amp", "tones", "edge"]
# stage -> kind, and stage -> index of its event bits in the kernel telemetry
KIND = {"p1": {**{s: "lane" for s in range(1, 8)}, 8: "tmr", 9: "tmr", 10: "s10"},
        "s3": {s: "lane" for s in range(1, 9)}}
TEL = {"p1": {**{s: s - 1 for s in range(1, 8)}, 10: 7},
       "s3": {s: s - 1 for s in range(1, 9)}}
# fault-free syndrome bound = decoding threshold of the delivered kernels
THRESHOLD = {"p1": {**{s: 3 for s in range(1, 7)}, 7: 6, 10: 3},
             "s3": {s: 3 for s in range(1, 9)}}
CASES = {"lane": [1, 2, 3, 4, 5, 6, 7], "s10": [1, 4, 6], "tmr": [9]}
EXPECT_COR = {"lane": {1: 1, 2: 2, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1},
              "s10": {1: 1, 4: 1, 6: 1}, "tmr": {9: 0}}
SECOND_HALF = {1: 1, 2: 1, 3: 0, 4: 1, 6: 1, 7: 0}       # lane stages
BIT_EXACT = {"lane": {6, 7}, "s10": {6}, "tmr": {9}}
HOLD_CASES = [1, 4, 5]
DEV_LIMIT = 16


def stage_w(stage):
    """Input word width of a lane stage (16-bit input, growth held at 22)."""
    return min(15 + stage, 22)


def rot_path(arch, stage, depth):
    if arch == "s3":
        leaf = "triv_rot.r3" if depth <= 2 else "pipe_rot.rotate.out_re"
        return f"`FS.rotators[L].func.{leaf}"
    leaf = "triv_rot.r3" if stage == 1 else "pipe_rot.rotate.out_re"
    return f"`FS.func_butterflies[L].{leaf}"


def parse_stages(text, arch):
    if text == "all":
        return sorted(KIND[arch])
    out = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out += range(int(lo), int(hi or lo) + 1)
    bad = [s for s in out if s not in KIND[arch]]
    if bad:
        raise SystemExit(f"{arch} has no protected stage {bad}")
    return out


def msb_bits(kind, stage):
    """Per fault case, the bit that lands on the top bit of the output word."""
    if kind == "s10":
        return {1: 22, 4: 22, 6: 22}            # 23-bit sum, halved to 22
    w = stage_w(stage)
    wo = min(w + 1, 22)
    # adder / subtractor: bit w of the (w+1)-bit result is the top bit of a
    # growing stage's word and, after the halving, of a held stage's word
    return {1: w, 4: w, 2: wo - 1, 3: wo - 1, 5: wo - 1, 6: wo - 1, 7: wo - 1}


def draw_point(args, arch, stage, vset, k, low):
    """One fault point: stream, bit and the beat counters of every case."""
    pt = draw_point_plain(args, arch, stage, vset, k, low)
    if args.msb and KIND[arch][stage] != "tmr":
        pt["lane"] = k % 4
        pt["bitc"] = msb_bits(KIND[arch][stage], stage)
    return pt


def draw_point_plain(args, arch, stage, vset, k, low):
    kind = KIND[arch][stage]
    rng = random.Random(f"{args.seed}:{arch}:{stage}:{vset}:{k}:{int(low)}")
    fixed = args.lane is not None
    if kind == "lane":
        depth = 128 >> (stage - 1)
        w = stage_w(stage)
        lane = rng.randrange(4)
        bit = rng.randrange(0, args.bit_min) if low else rng.randrange(args.bit_min, w)
        frame, block, off = rng.randrange(8), rng.randrange(128 // depth), rng.randrange(depth)
        if fixed:
            lane, bit, frame = args.lane, args.bit, args.frame
            block, off = args.block, min(args.off, depth - 1)
        second = 256 * frame + 2 * depth * block + depth + off
        first = second + depth
        wk = {1: second, 2: second, 3: first, 4: second,
              5: 256 * frame + (second % 256), 6: second, 7: first}
        return {"lane": lane, "bit": bit, "frame": frame, "block": block,
                "off": off, "wk": wk}
    if kind == "s10":
        lane = rng.randrange(4)
        bit = rng.randrange(1, args.bit_min) if low else rng.randrange(args.bit_min, 22)
        pair = rng.randrange(1024)
        if fixed:
            lane, bit, pair = args.lane, args.bit, 128 * args.frame + args.off
        return {"lane": lane, "bit": bit, "wk": {c: pair for c in CASES["s10"]}}
    lane, bit, beat = rng.randrange(3), rng.randrange(22), rng.randrange(2048)
    if fixed:
        lane, bit, beat = args.lane % 3, args.bit, 256 * args.frame + args.off
    return {"lane": lane, "bit": bit, "wk": {9: beat}}


def ensure_vectors(vsets):
    """Write any vector set that is not on disk yet (they are generated,
    not stored in the repository, apart from the original qual files)."""
    missing = [v for v in vsets if v != "qual" and not
               (ROOT / "sim" / "vectors_wg" / v / "input_10frames.hex").is_file()]
    if missing:
        subprocess.run([sys.executable, str(ROOT / "sim" / "gen_vectors_wg.py"),
                        *missing], cwd=ROOT, check=True)


class Sim:
    def __init__(self, arch, tag, snapshot):
        self.arch, self.snapshot = arch, snapshot
        self.logdir = ROOT / "results" / "xsim_qual"
        self.logdir.mkdir(parents=True, exist_ok=True)
        self.tag = tag
        self.log = None
        self.snap = None

    def run(self, cmd):
        self.log.write("$ " + " ".join(cmd) + "\n")
        self.log.flush()
        p = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, errors="replace")
        self.log.write(p.stdout)
        self.log.flush()
        return p.returncode, p.stdout

    def build(self, stage):
        arch = self.arch
        kind = KIND[arch][stage]
        pre = self.snapshot is not None
        (ROOT / "sim" / "tb_arch_sel.svh").write_text(
            f"localparam integer ARCH_SEL = {ARCH_SEL[arch]};\n", encoding="ascii")
        sel = [f"`define FSTG s{stage}", f"`define FKIND_{kind.upper()}"]
        if kind == "lane":
            sel.append(f"`define FROT(L) {rot_path(arch, stage, 128 >> (stage - 1))}")
            if not pre:
                sel.append("`define FHAS_CHECK_MEM")
        if arch == "p1":
            sel.append("`define FARCH_P1")
        if not pre:
            sel.append("`define FSYN_RED")
        (ROOT / "sim" / "tb_fault_sel.svh").write_text(
            "\n".join(sel) + "\n", encoding="ascii")

        kdir = ROOT / "rtl" / "paper_fft12" / arch
        obs = ROOT / "rtl" / "paper_fft12" / "obs" / arch / f"top_{arch}_kernel.sv"
        kernel = [p for p in sorted(kdir.glob("*.sv"))
                  if p.name != f"top_{arch}_kernel.sv"]
        engine = ROOT / "rtl" / "top" / "paper_fft_stream_engine.sv"
        if pre:
            kernel = [p for p in kernel if p.name != "datapath_wg.sv"]
            kernel.append(self.snapshot / f"{arch}_datapath_wg.sv")
            obs = self.snapshot / f"obs_{arch}_top_{arch}_kernel.sv"
            # the engine sets the kernel thresholds, so it must match
            engine = self.snapshot / "paper_fft_stream_engine.sv"
        sources = [ROOT / "sim" / "tb_ecc_fault_wg.sv", engine, *kernel, obs]
        for s in sources:
            assert s.is_file(), s

        name = f"fault_{arch}_s{stage}{self.tag}"
        self.snap = f"tbflt_{arch}_s{stage}{self.tag}"
        if self.log:
            self.log.close()
        self.log = open(self.logdir / f"{name}.log", "w", encoding="utf-8")
        rc, _ = self.run([tool("xvlog"), "--sv", "-i", "sim",
                          "--work", "work", *map(str, sources)])
        if rc == 0:
            rc, _ = self.run([tool("xelab"), "-debug", "off",
                              "tb_ecc_fault_wg", "-s", self.snap])
        if rc:
            raise SystemExit(f"compile failed, see {self.logdir / (name + '.log')}")

    def shot(self, vset, case, lane=0, wk=0, bit=0, hold=0):
        # xsim.bat splits arguments at '=', so the plusargs go in a file
        optf = self.logdir / f"{self.snap}.f"
        optf.write_text(
            f"-testplusarg VSET={vset} -testplusarg FAULT_CASE={case} "
            f"-testplusarg FAULT_LANE={lane} -testplusarg FAULT_WK={wk} "
            f"-testplusarg FAULT_BIT={bit} -testplusarg FAULT_HOLD={hold}\n",
            encoding="ascii")
        rc, out = self.run([tool("xsim"), self.snap, "-runall",
                            "-f", str(optf)])
        m = re.search(r"FAULT_RESULT .*", out)
        if rc or not m:
            return None
        res = {k: int(v) for k, v in re.findall(r"(\w+)=(-?\d+)", m.group(0))}
        res["stages"] = {int(a): (int(d), int(co), int(u)) for a, d, co, u in
                         re.findall(r"PROBE stage(\d+) det=(\d+) cor=(\d+) unc=(\d+)", out)}
        sm = re.search(r"SYN_MAX (.*)", out)
        res["syn_max"] = ({k: int(v) for k, v in re.findall(r"s(\d+)=(\d+)", sm.group(1))}
                          if sm else {})
        res["snr_db"] = (round(10 * math.log10(res["goldsq"] / res["devsq"]), 1)
                         if res.get("devsq") and res.get("goldsq") else None)
        return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("arch", choices=sorted(ARCH_SEL))
    ap.add_argument("--stages", default="all")
    ap.add_argument("--vsets", default="all")
    ap.add_argument("--points", type=int, default=1,
                    help="fault points per stage and vector set")
    ap.add_argument("--seed", type=int, default=20261012)
    ap.add_argument("--bit-min", type=int, default=6)
    ap.add_argument("--cases", default=None, help="restrict the fault cases")
    ap.add_argument("--low-bits", action="store_true")
    ap.add_argument("--hold", action="store_true")
    ap.add_argument("--msb", action="store_true")
    ap.add_argument("--exact", action="store_true",
                    help="the kernel was built with the untruncated-product check")
    ap.add_argument("--snapshot", default=None)
    ap.add_argument("--no-clean-run", action="store_true",
                    help="skip the fault-free simulations")
    for name in ("lane", "bit", "frame", "block", "off"):
        ap.add_argument(f"--{name}", type=int, default=None)
    args = ap.parse_args()
    if args.lane is not None:
        for name, dflt in (("bit", 10), ("frame", 7), ("block", 0), ("off", 5)):
            if getattr(args, name) is None:
                setattr(args, name, dflt)

    arch = args.arch
    stages = parse_stages(args.stages, arch)
    vsets = VSETS if args.vsets == "all" else args.vsets.split(",")
    only = {int(c) for c in args.cases.split(",")} if args.cases else None
    ensure_vectors(vsets)
    snap_dir = None
    if args.snapshot:
        snap_dir = Path(args.snapshot)
        if not snap_dir.is_absolute():
            snap_dir = ROOT / snap_dir
    if args.msb:
        args.points = 4                     # one point per stream
    judged = not (args.low_bits or args.hold or args.msb or snap_dir)
    tag = ("_pre" if snap_dir else "") + ("_hold" if args.hold else "") + \
          ("_low" if args.low_bits else "") + ("_msb" if args.msb else "")
    sim = Sim(arch, tag, snap_dir)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = sim.logdir / f"fault_{arch}{tag}_{stamp}.jsonl"
    out = out_path.open("x", encoding="utf-8")
    counts = {"run": 0, "pass": 0, "fail": 0, "error": 0}
    fails, rows = [], []

    def record(rec):
        out.write(json.dumps(rec) + "\n")
        out.flush()
        rows.append(rec)
        counts["run"] += 1
        if rec.get("verdict"):
            counts[rec["verdict"].lower()] += 1
            if rec["verdict"] != "PASS":
                fails.append(rec)

    mode = ("resident stuck-at-1" if args.hold else
            "bits below the acceptance range" if args.low_bits else
            "top bit of the output word" if args.msb else "acceptance")
    if snap_dir:
        mode += ", previous datapath"
    print(f"== {arch}: stages {stages}, vector sets {vsets}, {mode}"
          + ("" if judged else " (reported only)"))

    thr = THRESHOLD[arch]
    first = True
    for stage in stages:
        kind = KIND[arch][stage]
        if (args.hold or snap_dir) and kind != "lane":
            continue
        if (args.low_bits or args.msb) and kind == "tmr":
            continue
        sim.build(stage)

        # ---- fault-free, once per vector set (first compiled stage)
        if first and not args.no_clean_run:
            for vset in vsets:
                res = sim.shot(vset, 0)
                rec = {"kind": "clean", "vset": vset}
                if res is None:
                    rec["verdict"] = "ERROR"
                else:
                    events = sum(sum(v) for v in res["stages"].values())
                    over = {s: m for s, m in res["syn_max"].items()
                            if m > (0 if args.exact and int(s) <= 6 else thr.get(int(s), 0))}
                    rec.update(ndiff_beats=res["ndiff_beats"], maxdev=res["maxdev"],
                               events=events, syn_max=res["syn_max"],
                               flow_errors=res["flow_errors"])
                    good = (res["flow_errors"] == 0 and res["ndiff_beats"] == 0
                            and events == 0 and not over)
                    if judged or args.exact:
                        rec["verdict"] = "PASS" if good else "FAIL"
                syn = " ".join(f"s{s}={m}" for s, m in rec.get("syn_max", {}).items())
                print(f"clean {vset:9s} ndiff_beats={rec.get('ndiff_beats')} "
                      f"events={rec.get('events')} syndrome max: {syn}  "
                      f"{rec.get('verdict', '')}", flush=True)
                record(rec)
        first = False

        # ---- faults
        cases = HOLD_CASES if args.hold else CASES[kind]
        if snap_dir:
            cases = [c for c in cases if c not in (6, 7)]   # no check memory there
        if only:
            cases = [c for c in cases if c in only]
        for vset in vsets:
            for k in range(args.points):
                pt = draw_point(args, arch, stage, vset, k, args.low_bits)
                for c in cases:
                    bit = pt.get("bitc", {}).get(c, pt["bit"])
                    res = sim.shot(vset, c, pt["lane"], pt["wk"][c], bit,
                                   int(args.hold))
                    rec = {"kind": kind, "stage": stage, "vset": vset, "case": c,
                           "lane": pt["lane"], "bit": bit, "wk": pt["wk"][c],
                           "hold": int(args.hold)}
                    if res is None:
                        rec["verdict"] = "ERROR"
                        record(rec)
                        print(f"s{stage} {vset} case {c}: simulation failed", flush=True)
                        continue
                    ti = TEL[arch].get(stage)
                    det, cor, unc = res["stages"].get(ti, (0, 0, 0))
                    others = sum(v[0] for i, v in res["stages"].items() if i != ti)
                    rec.update(applied=res["applied"], second=res["second"],
                               det=det, cor=cor, unc=unc, other_det=others,
                               ndiff_beats=res["ndiff_beats"], maxdev=res["maxdev"],
                               snr_db=res["snr_db"], stage_maxsy=res["stage_maxsy"],
                               flow_errors=res["flow_errors"])
                    if judged:
                        good = (res["flow_errors"] == 0 and res["applied"] == 1
                                and others == 0 and unc == 0
                                and cor == EXPECT_COR[kind][c] and det == cor)
                        if kind == "lane" and c in SECOND_HALF:
                            good = good and res["second"] == SECOND_HALF[c]
                        if args.exact or c in BIT_EXACT[kind]:
                            good = good and res["ndiff_beats"] == 0
                        else:
                            good = good and res["maxdev"] <= DEV_LIMIT
                        rec["verdict"] = "PASS" if good else "FAIL"
                    record(rec)
                    print(f"s{stage:<2d} {vset:9s} case {c} lane {pt['lane']} bit "
                          f"{bit:2d} wk {pt['wk'][c]:4d}: applied={res['applied']} "
                          f"det={det} cor={cor} unc={unc} other_det={others} "
                          f"ndiff_beats={res['ndiff_beats']} maxdev={res['maxdev']}"
                          + (f" snr_dB={res['snr_db']}" if res["snr_db"] is not None else "")
                          + f"  {rec.get('verdict', '')}", flush=True)
    if sim.log:
        sim.log.close()

    # ---- summary
    print("\nsummary per stage (faults only):")
    for stage in stages:
        rs = [r for r in rows if r.get("stage") == stage and "maxdev" in r]
        if not rs:
            continue
        line = (f"  s{stage:<2d} {KIND[arch][stage]:4s} runs={len(rs):3d} "
                f"exact={sum(r['ndiff_beats'] == 0 for r in rs):3d} "
                f"max_dev={max(r['maxdev'] for r in rs):6d} "
                f"corrected={sum(r['cor'] > 0 for r in rs):3d} "
                f"unc_flag={sum(r['unc'] > 0 for r in rs):3d}")
        if judged:
            line += f" pass={sum(r.get('verdict') == 'PASS' for r in rs)}"
        print(line)
    summary = {"kind": "summary", **counts, "judged": judged,
               "arch": arch, "stages": stages, "vsets": vsets,
               "seed": args.seed, "points": args.points, "bit_min": args.bit_min,
               "low_bits": args.low_bits, "hold": args.hold, "msb": args.msb,
               "exact": args.exact}
    out.write(json.dumps(summary) + "\n")
    out.close()
    print(f"\n{counts['run']} runs: " + (
        f"{counts['pass']} pass, {counts['fail']} fail, {counts['error']} error"
        if judged else f"reported only, {counts['error']} error"))
    for r in fails:
        print("  NOT PASSED:", json.dumps({k: v for k, v in r.items() if k != "syn_max"}))
    print("log:", out_path)
    return 0 if not fails and counts["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
