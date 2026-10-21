"""WP6 3.3 tile attribution table builder.

For each frozen wp4r2 design (read-only Vivado queries against the routed
checkpoints in /home/xdu/frozen/wp6) produce results/wp6/<design>/tile_table.json
mapping every kernel-region tile to its attribution set, plus row_check.json
verifying the word->row conversion of WP6 3.3 against the design's .ll.

    python scripts/k7_mod/wp6_tile_table.py [--designs P0_p1 ...]
           [--skip-vivado] [--no-retry]

Category vocabulary (WP6 2.4): protected_datapath, tmr_stage, shared_control,
decoder_voter; "mixed" is added when a tile carries more than one category.
Stage-boundary registers are NOT a separate category: cells stay under their
own module's category (decision recorded in the table "_note").  ECC-stage
level unnamed glue (beat/valid alignment pipelines, e.g. S0 6-of-4 vote
alignment) is classified shared_control; S0/P0 u_encode, u_decode and all TMR
voters/flags are decoder_voter.  Anything else is "unmapped" and must stay
below 1% of kernel leaf cells.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FTAB = ROOT / "results" / "k7_mod_fft" / "frame_table_k7.json"
FROZEN = Path("/home/xdu/frozen/wp6")
TCL = ROOT / "results" / "wp6" / "tile_attribution.tcl"
VIVADO = "/tools/Xilinx/Vivado/2018.3/bin/vivado"
OUT = ROOT / "results" / "wp6"

DESIGNS = {"P0_p1": "P1_WP4R2", "P1_p2": "P2_WP4R2",
           "S0_s3": "S3_WP4R2", "S1_s2": "S2_WP4R2"}

DUT_CR = {"X0Y4", "X0Y5", "X0Y6", "X1Y3", "X1Y4", "X1Y5", "X1Y6"}
KERNEL_TILE_X0_MAX = 48          # tile grid X boundary: X0Y* cover X<=48, X1Y* X>=49

STAGE_RE = re.compile(r"^g_stage\[(\d+)\]$")
LANE_RE = re.compile(r"^g_lane\[(\d+)\]$")
REPL_RE = re.compile(r"^g_replica\[(\d+)\]$")
VOTE_RE = re.compile(r"^g_vote\[\d+\]$")
TILE_RE = re.compile(r"^([A-Z0-9_]+)_X(\d+)Y(\d+)$")

KIND_OF = (lambda p: "CLB logic" if p.startswith(("CLBLL", "CLBLM")) else
           "DSP" if p.startswith("DSP") else
           "BRAM" if p.startswith("RAMB") else
           "INT interconnect" if p.startswith("INT") else "other")


# ------------------------------------------------------------- attribution

def classify(rel):
    """Map a hierarchy path relative to u_kernel to (category, stage, lane).

    Vivado joins generate scopes with dots and instance levels with slashes
    (e.g. "g_stage[10].g_tmr.u_stage/g_replica[0].g_s10.u_copy/x_reg"), so
    both separators are treated as hierarchy boundaries.
    """
    segs = [s for part in rel.split("/") for s in part.split(".")]
    stage = None
    for i, s in enumerate(segs):
        m = STAGE_RE.match(s)
        if m:
            stage = int(m.group(1))
            rest = segs[i + 1:]
            break
    if stage is None:
        return ("unmapped", None, None)
    if not rest:
        return ("unmapped", stage, None)
    if rest[0] in ("g_ecc", "g_tmr"):
        if len(rest) < 2 or rest[1] != "u_stage":
            return ("unmapped", stage, None)
        body = rest[2:]
        ecc = rest[0] == "g_ecc"
    elif rest[0] == "u_stage":              # P1 / S1 TMR-only pipelines
        body = rest[1:]
        ecc = False
    else:
        return ("unmapped", stage, None)
    for s in body:
        m = REPL_RE.match(s)
        if m:
            return ("tmr_stage", stage, int(m.group(1)))
        m = LANE_RE.match(s)
        if m:
            return ("protected_datapath", stage, int(m.group(1)))
    if body and (body[0] in ("u_valid_vote", "u_last_vote", "u_flag")
                 or VOTE_RE.match(body[0])):
        return ("decoder_voter", stage, None)
    if ecc:
        if body and body[0] in ("u_encode", "u_decode"):
            return ("decoder_voter", stage, None)
        if body and body[0] == "u_memory":   # P0 stage-level SECDED memory
            return ("protected_datapath", stage, None)
        # stage-level control/phi/ROM or unnamed beat-alignment glue
        return ("shared_control", stage, None)
    return ("tmr_stage", stage, None)        # tmr stage-level glue


# -------------------------------------------------------------- vivado run

def run_vivado(design, arch, force=False):
    outdir = OUT / design
    outdir.mkdir(parents=True, exist_ok=True)
    prefix = outdir / "wp6_dump"
    console = outdir / "dump_console.txt"
    if not force and console.is_file() and \
            "ALL_DONE" in console.read_text(errors="replace"):
        print(f"[{design}] vivado dump already complete, skipping")
        return True
    for attempt in (1, 2):
        print(f"[{design}] vivado dump attempt {attempt} ...", flush=True)
        t0 = time.time()
        with console.open("w") as fh:
            r = subprocess.run(
                [VIVADO, "-mode", "batch", "-nojournal",
                 "-log", str(outdir / "vivado_dump.log"),
                 "-source", str(TCL), "-tclargs",
                 str(FROZEN / arch / "k7_paper_top_routed.dcp"), str(prefix)],
                stdout=fh, stderr=subprocess.STDOUT, cwd=ROOT, timeout=7200)
        ok = r.returncode == 0 and "ALL_DONE" in console.read_text(errors="replace")
        print(f"[{design}] attempt {attempt} rc={r.returncode} "
              f"{time.time() - t0:.0f}s ok={ok}", flush=True)
        if ok:
            return True
        if attempt == 1:
            (outdir / f"dump_console_attempt1.txt").write_text(
                console.read_text(errors="replace"))
    print(f"[{design}] VIVADO DUMP FAILED (log kept at {console})")
    return False


# -------------------------------------------------------------- row check

def row_check(design, arch, ftab):
    """Verify word->row mapping: .ll SLICE site Y must equal base_y + row."""
    cols = [c for c in ftab["columns"]
            if c.get("clock_region") in DUT_CR and c.get("frame_lo") is not None]
    col_lo = {(c["top"], c["row"], c["column"]): c["frame_lo"] for c in cols}
    span = sorted((c["frame_lo"], c) for c in cols)

    def col_of(la):
        lo = 0
        hi = len(span)
        while lo < hi:
            mid = (lo + hi) // 2
            if span[mid][0] <= la:
                lo = mid + 1
            else:
                hi = mid
        if lo == 0:
            return None
        c = span[lo - 1][1]
        return c if la <= c["frame_hi"] else None

    n_checked = n_match = n_mismatch = n_nocol = n_ecc = 0
    examples = []
    ll = FROZEN / arch / "k7_paper_top.ll"
    with ll.open("r", encoding="ascii", errors="replace") as f:
        for line in f:
            if not line.startswith("Bit ") or "Block=SLICE" not in line:
                continue
            parts = line.split()
            far, foff = int(parts[2], 16), int(parts[3])
            m = re.search(r"Block=SLICE_X\d+Y(\d+)", line)
            if not m:
                continue
            site_y = int(m.group(1))
            key = ((far >> 22) & 1, (far >> 17) & 0x1F, (far >> 7) & 0x3FF)
            lo = col_lo.get(key)
            if lo is None:
                n_nocol += 1
                continue
            la = lo + (far & 0x7F)
            c = col_of(la)
            if c is None:
                n_nocol += 1
                continue
            w = foff // 32
            if w == 50:                      # frame ECC word
                n_ecc += 1
                continue
            row = w // 2 if w < 50 else (w - 1) // 2
            base = 50 * int(c["clock_region"].split("Y")[1])
            n_checked += 1
            if site_y == base + row:
                n_match += 1
            else:
                n_mismatch += 1
                if len(examples) < 20:
                    examples.append({"la": la, "word": w, "row": row,
                                     "base_y": base, "site_y": site_y,
                                     "tile_x": c["tile_x"],
                                     "clock_region": c["clock_region"]})
    tol = max(10, n_checked // 10000)        # documented tolerance
    verdict = "PASS" if n_mismatch <= tol else "FAIL"
    res = {"design": design, "ll": str(ll),
           "n_slice_bits": n_checked, "n_match": n_match,
           "n_mismatch": n_mismatch, "n_column_unknown": n_nocol,
           "n_ecc_word_skipped": n_ecc, "tolerance": tol,
           "mapping": "row = word//2 (word<50), (word-1)//2 (word>50), "
                      "word 50 = ECC; site Y = 50*region_y + row",
           "mismatch_examples": examples, "verdict": verdict}
    (OUT / design / "row_check.json").write_text(json.dumps(res, indent=1))
    print(f"[{design}] row_check {verdict}: match={n_match} "
          f"mismatch={n_mismatch} unknown_col={n_nocol} checked={n_checked}")
    return verdict == "PASS"


# ------------------------------------------------------------ table build

def build_table(design):
    outdir = OUT / design
    tiles = {}

    def entry(t):
        if t not in tiles:
            tiles[t] = {"kind": None, "cr": None, "attrib": set(),
                        "special": set()}
        return tiles[t]

    n_cells = n_unmapped = 0
    with (outdir / "wp6_dump.cells.tsv").open() as f:
        for line in f:
            tile, ref, cell = line.rstrip("\n").split("\t")
            if "u_kernel/" not in cell:
                n_cells += 1
                n_unmapped += 1
                entry(tile)
                continue
            rel = cell.split("u_kernel/", 1)[1]
            cat, stage, lane = classify(rel)
            n_cells += 1
            if cat == "unmapped":
                n_unmapped += 1
            e = entry(tile)
            e["attrib"].add((cat, stage, lane))

    net_owners = {}
    with (outdir / "wp6_dump.nets.tsv").open() as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if parts[0] == "NET":
                entry(parts[1])           # ensure tile exists
            elif parts[0] == "NETPINS":
                net_owners[parts[1]] = parts[2].strip("{}").split()
            elif parts[0] == "SPECIAL":
                flag = parts[2]
                # WP4-R 11.8 shell-reset INT tiles are a P1-only special
                # position; they exist geometrically in every design.
                if flag == "shell_related" and design != "P1_p2":
                    flag = None
                entry(parts[1])
                if flag:
                    tiles[parts[1]]["special"].add(flag)

    # attribute tiles from the nets that pass through them
    with (outdir / "wp6_dump.nets.tsv").open() as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if parts[0] != "NET":
                continue
            _, tile, net = parts
            e = entry(tile)
            for cell in net_owners.get(net, []):
                if "u_kernel/" not in cell:
                    continue
                rel = cell.split("u_kernel/", 1)[1]
                cat, stage, lane = classify(rel)
                if cat != "unmapped":
                    e["attrib"].add((cat, stage, lane))

    doc = {
        "_note": ("WP6 2.4/3.3 tile attribution. Category set per tile from "
                  "placed leaf cells (CLB/DSP/BRAM tiles) and kernel-net pips "
                  "(INT tiles). stage_boundary is not a separate category: "
                  "boundary registers stay under their own module's category. "
                  "ECC-stage unnamed glue (beat/valid alignment incl. S0 6-of-4 "
                  "vote alignment) is shared_control; u_encode/u_decode and TMR "
                  "voters/flags are decoder_voter; attrib entries are "
                  "[category, stage, lane_or_replica|null]; 'mixed' is added "
                  "when a tile carries more than one category. shell_related: "
                  "P1 shell-reset-net INT tiles (WP4-R 11.8), tile_x49 column "
                  "and X0Y4 tile_x23 (WP6 3.3 special positions)."),
        "_design": design,
        "tiles": {},
    }
    kind_hist = Counter()
    dropped = 0
    for t, e in tiles.items():
        m = TILE_RE.match(t)
        if m:
            prefix, x, y = m.group(1), int(m.group(2)), int(m.group(3))
            e["kind"] = KIND_OF(prefix)
            rx = 0 if x <= KERNEL_TILE_X0_MAX else 1
            e["cr"] = f"X{rx}Y{y // 50}"
        if e["cr"] not in DUT_CR:
            # e.g. VBRK tiles attached to a region object but sitting above
            # Y349: not kernel-region tiles, never carry cells or net pips
            dropped += 1
            continue
        attrib = sorted(e["attrib"],
                        key=lambda a: (a[0], a[1] or 0, a[2] if a[2] is not None else -1))
        cats = {a[0] for a in attrib}
        if len(cats) > 1:
            attrib.append(["mixed", None, None])
        doc["tiles"][t] = {
            "kind": e["kind"], "cr": e["cr"],
            "attrib": [[a[0], a[1], a[2]] for a in attrib],
            "special": sorted(e["special"]),
        }
        kind_hist[e["kind"]] += 1

    (outdir / "tile_table.json").write_text(json.dumps(doc))
    frac = n_unmapped / max(1, n_cells)
    print(f"[{design}] tiles={len(tiles)} kind_hist={dict(kind_hist)} "
          f"cells={n_cells} unmapped={n_unmapped} ({frac:.4%})")
    return {"design": design, "tiles": len(tiles), "kind_hist": dict(kind_hist),
            "cells": n_cells, "unmapped": n_unmapped,
            "unmapped_frac": round(frac, 6)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--designs", nargs="*", default=list(DESIGNS))
    ap.add_argument("--skip-vivado", action="store_true")
    ap.add_argument("--force-vivado", action="store_true")
    args = ap.parse_args(argv)

    ftab = json.loads(FTAB.read_text())
    summary = {"vivado": {}, "row_check": {}, "tables": {}}
    t_all = time.time()
    for design in args.designs:
        arch = DESIGNS[design]
        if not args.skip_vivado:
            summary["vivado"][design] = run_vivado(design, arch,
                                                   force=args.force_vivado)
            if not summary["vivado"][design]:
                summary["row_check"][design] = "SKIPPED (vivado failed)"
                summary["tables"][design] = "SKIPPED (vivado failed)"
                continue
        summary["row_check"][design] = row_check(design, arch, ftab)
        if not summary["row_check"][design]:
            summary["tables"][design] = "SKIPPED (row check failed)"
            continue
        summary["tables"][design] = build_table(design)
    summary["total_s"] = round(time.time() - t_all, 1)
    (OUT / "wp6_tile_table_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    ok = all(v is True for v in summary["row_check"].values()) and all(
        (not isinstance(v, dict)) or v["unmapped_frac"] < 0.01
        for v in summary["tables"].values())
    print("OVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
