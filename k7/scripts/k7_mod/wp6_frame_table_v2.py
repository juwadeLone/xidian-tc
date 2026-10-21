"""WP6 batch-3 step 2: build frame_table_k7_v2.json from v1 + board reprobes.

Re-segments the columns overlapping LFAs 3880..4030 (the v1 X0Y4 tile_x=23
66-frame wide column and neighbours) from the dense board probes, retests
the 49 v1 dead_lfas, and sanity-checks the coarse sweep for drift.

Algorithm (same as build_frame_table_k7.py): column start = min(lfa-minor)
per (block,top,row,column) probe group; width = next start - this start;
the last rebuilt column closes at the next kept v1 column's frame_lo.
Tile kinds for the new sub-columns are bound from the wp4r2 pblock tile
list (hd_visual pb_dut_FrameTiles.tcl), the same X->tile_type data
tiles_all.csv provided for v1.

Hard-fail sanity checks:
  * every rebuilt column width matches a tile-kind signature
    (36 CLB / 28 BRAM|DSP / 30 IO / 42 col0 / 26 INT_ONLY)
  * v2 coverage == v1 coverage except the rebuilt region, and the rebuilt
    region tiles the removed v1 ranges exactly
  * the old 66-frame [3926,3991] column is preserved: the sub-columns
    covering it sum to exactly 66 frames

Writes results/k7_mod_fft/frame_table_k7_v2.json (v1 untouched) and
results/wp6/frameprobe/v2_build_report.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT / "results" / "k7_mod_fft" / "frame_table_k7.json"
V2 = ROOT / "results" / "k7_mod_fft" / "frame_table_k7_v2.json"
PROBES = ROOT / "results" / "wp6" / "frameprobe" / "la_probe.jsonl"
REPORT = ROOT / "results" / "wp6" / "frameprobe" / "v2_build_report.json"
TILES_TCL = (ROOT / "build" / "vivado" / "K7_PAPER_P1_WP4R2" /
             "K7_PAPER_P1_WP4R2.runs" / "impl_1" / "hd_visual" /
             "pb_dut_FrameTiles.tcl")

SPLIT_LO, SPLIT_HI = 3880, 4030
WIDTH_SIGNATURES = (26, 28, 30, 36, 42)
NAME_XY = re.compile(r"(.+?)_X(\d+)Y(\d+)$")


def tile_kind_for(types):
    """Column-master raw tile type, identical to build_frame_table_k7.py."""
    for t in types:
        if t.startswith(("CLBLL", "CLBLM")):
            return t
        if t.startswith("BRAM") and "INT" not in t:
            return t
        if t.startswith("DSP"):
            return t
        if t.startswith(("LIO", "RIO")):
            return t
    for t in types:
        if "INT_INTERFACE" in t or t.startswith("INT_"):
            return t
    return sorted(types)[0] if types else "OTHER"


def load_tile_kinds(path=TILES_TCL):
    """tile_x -> column-master tile type from the wp4r2 pblock tile list."""
    text = path.read_text(encoding="utf-8")
    names = re.findall(r"([A-Z0-9_]+?_X\d+Y\d+)", text)
    by_x = defaultdict(set)
    for n in names:
        m = NAME_XY.match(n)
        if m:
            by_x[int(m.group(2))].add(m.group(1))
    return {x: tile_kind_for(ts) for x, ts in by_x.items()}


def cr_name(top, row, col):
    # EBD-density-verified relabelling rule (same as v1):
    #   top=1 is the BOTTOM half (row r -> Y(2-r)); top=0 rows count UP
    #   from the centre (row r -> Y(3+r)).  col<=48 -> X0, >=50 -> X1.
    y = (3 + row) if top == 0 else (2 - row)
    x = 0 if col <= 48 else 1
    return "X%dY%d" % (x, y)


def cr_name_v1(top, row, col):
    # the swapped heuristic v1 originally used (kept in cr_label_v1)
    y = row if top == 0 else (4 + row)
    x = 0 if col <= 48 else 1
    return "X%dY%d" % (x, y)


def load_probes(path=PROBES):
    recs = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", type=Path, default=V1)
    ap.add_argument("--probes", type=Path, default=PROBES)
    ap.add_argument("--out", type=Path, default=V2)
    ap.add_argument("--report", type=Path, default=REPORT)
    args = ap.parse_args(argv)

    v1 = json.loads(args.v1.read_text(encoding="utf-8"))
    cols_v1 = v1["columns"]
    probes = load_probes(args.probes)
    x_kind = load_tile_kinds()

    by_lfa = defaultdict(list)
    for r in probes:
        by_lfa[int(r["lfa"])].append(r)

    def probe_valid(r):
        return bool(r.get("pa_fields")) or bool(r.get("la"))

    # ---- dense re-segmentation -------------------------------------------
    dense = [r for r in probes
             if r.get("category") == "dense" and r.get("pa_fields")]
    groups = defaultdict(list)          # (block,top,row,column) -> [(lfa,minor)]
    for r in dense:
        f = r["pa_fields"]
        groups[(f["block"], f["top"], f["row"], f["column"])].append(
            (int(r["lfa"]), int(f["minor"])))
    if not groups:
        raise SystemExit("FATAL: no dense probes with PA fields")

    ginfo = {}
    for key, lm in groups.items():
        lo = min(lfa - minor for lfa, minor in lm)
        ginfo[key] = {"lo": lo, "lfas": sorted(x[0] for x in lm),
                    "min_minor": min(m for _, m in lm),
                    "max_minor": max(m for _, m in lm)}
    order = sorted(ginfo, key=lambda k: ginfo[k]["lo"])

    # removed v1 columns: any column intersecting the dense range
    removed = [c for c in cols_v1
               if c["frame_lo"] <= SPLIT_HI and c["frame_hi"] >= SPLIT_LO]
    removed_ranges = sorted((c["frame_lo"], c["frame_hi"]) for c in removed)
    # next kept v1 start after the removed block (per CR row)
    removed_ids = {id(c) for c in removed}
    next_kept = {}
    for c in cols_v1:
        if id(c) in removed_ids:
            continue
        for (t, r) in {(x["top"], x["row"]) for x in removed}:
            if c["top"] == t and c["row"] == r and c["frame_lo"] > max(
                    x["frame_hi"] for x in removed
                    if x["top"] == t and x["row"] == r):
                key = (t, r)
                if key not in next_kept or c["frame_lo"] < next_kept[key]:
                    next_kept[key] = c["frame_lo"]

    new_cols = []
    split_detail = []
    for i, key in enumerate(order):
        blk, top, row, col = key
        lo = ginfo[key]["lo"]
        if i + 1 < len(order):
            width = ginfo[order[i + 1]]["lo"] - lo
            src = "measured"
        else:
            width = next_kept[(top, row)] - lo
            src = "measured"
        if width not in WIDTH_SIGNATURES:
            raise SystemExit(
                "FATAL: rebuilt column %s width %d not in signatures %s"
                % (key, width, WIDTH_SIGNATURES))
        kind = x_kind.get(col, "OTHER")
        new_cols.append({
            "clock_region": cr_name(top, row, col),
            "tile_x": col,
            "top": top, "row": row, "column": col,
            "tile_kind": kind,
            "frame_lo": lo, "frame_hi": lo + width - 1,
            "n_minors": width, "lfa_source": src,
            "n_dead_probes": 0,
            "cr_label_v1": cr_name_v1(top, row, col),
            "block": blk,
        })

    # coverage: rebuilt ranges must tile the removed span exactly
    # (contiguous, same outer bounds -- the split turns one removed range
    # into several adjacent ones)
    new_ranges = sorted((c["frame_lo"], c["frame_hi"]) for c in new_cols)
    rem_lo = min(lo for lo, _hi in removed_ranges)
    rem_hi = max(hi for _lo, hi in removed_ranges)
    ok = (new_ranges[0][0] == rem_lo and new_ranges[-1][1] == rem_hi
          and all(new_ranges[i + 1][0] == new_ranges[i][1] + 1
                  for i in range(len(new_ranges) - 1)))
    if not ok:
        raise SystemExit("FATAL: rebuilt ranges %s do not tile removed span "
                         "%d..%d" % (new_ranges, rem_lo, rem_hi))

    # the v1 66-frame column must be preserved by its replacement sub-columns
    old66 = [c for c in removed if c["n_minors"] == 66]
    if len(old66) != 1:
        raise SystemExit("FATAL: expected exactly one 66-frame column, got %d"
                         % len(old66))
    o = old66[0]
    subs = [c for c in new_cols
            if c["frame_lo"] <= o["frame_hi"] and c["frame_hi"] >= o["frame_lo"]]
    if sum(c["n_minors"] for c in subs) != o["n_minors"]:
        raise SystemExit("FATAL: 66-frame split not preserved: %s"
                         % [(c["tile_x"], c["frame_lo"], c["frame_hi"])
                            for c in subs])
    split_detail.append({
        "old_column": {k: o[k] for k in
                       ("clock_region", "tile_x", "tile_kind", "frame_lo",
                        "frame_hi", "n_minors")},
        "sub_columns": [{k: c[k] for k in
                         ("clock_region", "tile_x", "tile_kind", "frame_lo",
                          "frame_hi", "n_minors", "lfa_source")}
                        for c in subs],
        "frame_count_preserved": sum(c["n_minors"] for c in subs),
    })

    # ---- dead probes: retest v1 list + collect newly-silent frames -------
    dead_verdicts = []
    new_dead = []
    for lf in sorted(int(x) for x in v1.get("dead_lfas") or []):
        recs = by_lfa.get(lf, [])
        valid = any(probe_valid(r) for r in recs)
        f = next((r["pa_fields"] for r in recs if r.get("pa_fields")), None)
        dead_verdicts.append({
            "lfa": lf,
            "was_dead": True,
            "now_reports": valid,
            "still_silent": not valid,
            "n_records": len(recs),
            "pa_fields": f,
        })
        if not valid:
            new_dead.append(lf)
    # probes that answered the command but produced no LA/PA report at all
    # (e.g. dense LFAs 3988-3991 inside the new column 24): v1 semantics say
    # these join dead_lfas as well.
    newly_silent = sorted({
        int(r["lfa"]) for r in probes
        if r.get("category") in ("dense", "coarse")
        and not r.get("error") and not probe_valid(r)})
    for lf in newly_silent:
        if lf not in new_dead:
            new_dead.append(lf)
            dead_verdicts.append({
                "lfa": lf, "was_dead": False, "now_reports": False,
                "still_silent": True, "n_records": len(by_lfa.get(lf, [])),
                "pa_fields": None, "newly_silent": True,
            })
    new_dead.sort()

    # ---- assemble v2 -------------------------------------------------------
    out_cols = []
    emitted = False
    for c in cols_v1:
        if id(c) in removed_ids:
            if not emitted:
                out_cols.extend(new_cols)
                emitted = True
            continue
        out_cols.append(dict(c))
    if not emitted:
        raise SystemExit("FATAL: removed columns not contiguous in v1 order")

    for c in out_cols:                       # recompute dead counts everywhere
        c["n_dead_probes"] = sum(1 for lf in new_dead
                                 if c["frame_lo"] <= lf <= c["frame_hi"])

    doc = dict(v1)
    doc["columns"] = out_cols
    doc["n_columns"] = len(out_cols)
    doc["n_measured"] = sum(1 for c in out_cols
                            if c.get("lfa_source") == "measured")
    doc["dead_lfas"] = new_dead
    doc["n_dead_probe_lfas"] = len(new_dead)
    doc["n_newly_silent_lfas"] = len(newly_silent)
    doc["source"] = (v1.get("source", "") + " | v2 (2026-10-16): X0Y4 "
                     "tile_x=23 66-frame column split into board-measured "
                     "sub-columns from dense LFAs 3880..4030 reprobe; "
                     "dead_lfas retested individually")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    # ---- coarse drift check ------------------------------------------------
    v2_by_lfa = {}
    for i, c in enumerate(out_cols):
        for la in range(c["frame_lo"], c["frame_hi"] + 1):
            v2_by_lfa[la] = (i, c)
    drift = []
    n_coarse = n_coarse_ok = 0
    for r in probes:
        if r.get("category") != "coarse":
            continue
        n_coarse += 1
        lf = int(r["lfa"])
        f = r.get("pa_fields")
        if not f:
            drift.append({"lfa": lf, "note": "no PA (still silent)"})
            continue
        hit = v2_by_lfa.get(lf)
        if hit is None:
            drift.append({"lfa": lf, "note": "lfa outside v2 coverage",
                          "pa_fields": f})
            continue
        _i, c = hit
        if (f["top"], f["row"], f["column"]) != (c["top"], c["row"],
                                                 c["column"]):
            drift.append({"lfa": lf, "note": "FAR column drift vs v2",
                          "pa_fields": f,
                          "v2_column": {k: c[k] for k in
                                        ("tile_x", "frame_lo", "frame_hi")}})
            continue
        if r.get("la_matches_cmd") is False:
            drift.append({"lfa": lf, "note": "LA != injected lfa",
                          "la": r.get("la")})
            continue
        n_coarse_ok += 1

    report = {
        "built_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "v1": str(args.v1.relative_to(ROOT)),
        "v2": str(args.out.relative_to(ROOT)),
        "n_columns_v1": len(cols_v1),
        "n_columns_v2": len(out_cols),
        "split_detail": split_detail,
        "new_columns": [{k: c[k] for k in
                         ("clock_region", "tile_x", "tile_kind", "frame_lo",
                          "frame_hi", "n_minors", "lfa_source",
                          "n_dead_probes")} for c in new_cols],
        "dead_probe_verdicts": dead_verdicts,
        "dead_lfas_v1": sorted(int(x) for x in v1.get("dead_lfas") or []),
        "dead_lfas_v2": new_dead,
        "newly_silent_lfas": newly_silent,
        "coarse_sweep": {
            "n_probes": n_coarse, "n_consistent": n_coarse_ok,
            "n_drift": len(drift), "drift_notes": drift,
        },
        "sanity_checks": {
            "width_signatures": "PASS",
            "coverage_equals_v1_except_split": "PASS",
            "split_frame_count_preserved": "PASS",
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n",
                           encoding="utf-8")
    print("v2: columns %d -> %d, dead %d -> %d, coarse drift %d/%d" %
          (len(cols_v1), len(out_cols), len(v1.get("dead_lfas") or []),
           len(new_dead), len(drift), n_coarse))
    print("split:", json.dumps(split_detail[0]["sub_columns"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
