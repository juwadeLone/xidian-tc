"""WP6 batch-3 step 2: label audits x4 against the v2 frame table.

WP1c step-D style, python-only, no board.  Imports results/wp1c/audit_pool_r2
UNMODIFIED and monkey-patches its OUT and ROOT:
  * wp1c.OUT      -> results/wp6/<design>  (per-design audit JSON + CSV)
  * wp1c.ROOT     -> synthetic root whose build/vivado/<name>/.../impl_1
                     symlinks at the frozen wp4r2 .ebd/.ll/.bit/.dcp
  * sampler.FTAB  -> results/k7_mod_fft/frame_table_k7_v2.json

audit_one("wp6v2", arch, table, sampler) then checks, per frozen design:
  kernel FF bits are (a) in-pool or (b) excluded-with-reason -- never (c);
  shell FF bits have zero (a)/(b).  Gate: criterion_7 true for all four.

Writes results/wp6/<design>/label_audit_v2.json (copy of the audit dict)
and results/wp6/frameprobe/label_audit_summary.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))
sys.path.insert(0, str(ROOT / "results" / "wp1c"))

FTAB_V2 = ROOT / "results" / "k7_mod_fft" / "frame_table_k7_v2.json"
SUMMARY = ROOT / "results" / "wp6" / "frameprobe" / "label_audit_summary.json"
PHASE = "wp6v2"

# frozen dir -> (design results dir, sampler arch)
DESIGNS = {
    "P1_WP4R2": ("P0_p1", "p1"),
    "P2_WP4R2": ("P1_p2", "p2"),
    "S2_WP4R2": ("S1_s2", "s2"),
    "S3_WP4R2": ("S0_s3", "s3"),
}
FROZEN = Path("/home/xdu/frozen/wp6")
AUDIT_ROOT = ROOT / "results" / "wp6" / "frameprobe" / "audit_root"


def main(argv=None):
    import audit_pool_r2 as wp1c
    import k7_paper_smoke as sampler

    if sampler.PB_REGIONS != wp1c.PB_REGIONS or sampler.WPW != wp1c.WPW:
        raise SystemExit("sampler constants differ from wp1c")
    if not FTAB_V2.exists():
        raise SystemExit("frame table v2 missing: %s" % FTAB_V2)

    sampler.FTAB = FTAB_V2
    table = json.loads(sampler.FTAB.read_text(encoding="utf-8"))

    impl_files = ("k7_paper_top.ebd", "k7_paper_top.ll",
                  "k7_paper_top.bit", "k7_paper_top_routed.dcp")
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    wp1c.ROOT = AUDIT_ROOT

    results = {}
    gate = True
    for frozen, (design, arch) in DESIGNS.items():
        name = "K7_PAPER_%s_%s" % (arch.upper(), PHASE.upper())
        impl = (AUDIT_ROOT / "build" / "vivado" / name
                / ("%s.runs" % name) / "impl_1")
        impl.mkdir(parents=True, exist_ok=True)
        for f in impl_files:
            dst = impl / f
            if not dst.exists():
                dst.symlink_to(FROZEN / frozen / f)
        out = ROOT / "results" / "wp6" / design
        out.mkdir(parents=True, exist_ok=True)
        wp1c.OUT = out
        result, _mask, _frames = wp1c.audit_one(PHASE, arch, table, sampler)
        (out / "label_audit_v2.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        results[design] = {
            "arch": arch, "frozen": str(FROZEN / frozen),
            "criterion_7": result["criterion_7"],
            "ff_counts": result["ff_counts"],
            "pool_size": result["pool_size"],
            "sample_targets_reference_size":
                result["sample_targets_reference_size"],
            "essential_bits": result["essential_bits"],
            "ll_far_alignment": result["ll_far_alignment"],
            "n_fallback_names": result["fallback_names"]
            if isinstance(result["fallback_names"], int)
            else len(result["fallback_names"]),
            "audit_json": "results/wp6/%s/audit_%s_%s_r2.json"
                          % (design, PHASE, arch),
        }
        gate = gate and result["criterion_7"]
        print("LABEL_AUDIT_V2 %-8s criterion_7=%s pool=%d"
              % (design, result["criterion_7"], result["pool_size"]),
              flush=True)

    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps({
        "phase": PHASE,
        "ftab": str(FTAB_V2.relative_to(ROOT)),
        "designs": results,
        "gate_all_criterion_7": gate,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("LABEL_AUDIT_V2_GATE: " + ("PASS" if gate else "FAIL"), flush=True)
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
