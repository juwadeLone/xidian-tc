#!/usr/bin/env python3
"""WP10 Table C: per-stage (1-7) delay-storage LUTRAM, SECDED logic LUT and
voter logic LUT for the six designs S0/S1/S1h/P0/P1/P1h.

Parses each design's hierarchical post-route utilization report.  Instance
rows are pre-order; children are indented deeper than their parent.  A row
contributes exactly once: parenthesized self rows "(name)" always count;
named instance rows count only when they have no children in the table
(leaves) -- the same dedup rule as wp4r_functional_breakdown.py.  Rows are
grouped by the topmost g_stage[N] ancestor and classified with the shared
classify() categories; only 延迟存储 / SECDED / 表决器 are reported.

Usage: python3 scripts/k7_mod/wp10_stage_table.py
Writes results/wp10/stage_table.json and prints a markdown table.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wp4r_functional_breakdown import classify

ROOT = Path(__file__).resolve().parents[2]

DESIGNS = [
    ("S0", "results/wp4r/S0_s3/util_impl_wp4r2.rpt"),
    ("S1", "results/wp4r/S1_s2/util_impl_wp4r2.rpt"),
    ("S1h-wp10", "results/wp10/S1h_s2hr/util_impl_wp10.rpt"),
    ("S1h", "results/wp10/S1h_s2hr/util_impl_wp10b.rpt"),
    ("P0", "results/wp4r/P0_p1/util_impl_wp4r2.rpt"),
    ("P1", "results/wp4r/P1_p2/util_impl_wp4r2.rpt"),
    ("P1h-wp10", "results/wp10/P1h_p2hr/util_impl_wp10.rpt"),
    ("P1h", "results/wp10/P1h_p2hr/util_impl_wp10b.rpt"),
]

ROW = re.compile(
    r"^\|(\s*)(\(?[A-Za-z0-9_.$\[\]/]+\)?)\s*\|([^|]+)\|"
    r"\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
    r"\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|")
STAGE = re.compile(r"g_stage\[(\d+)\]")
KERNEL = "g_k.u_kernel"


def parse_rows(path):
    """Return list of dicts: indent, name, module, lut, lutram, ff, dsp, self."""
    rows = []
    for line in path.read_text().splitlines():
        m = ROW.match(line)
        if not m:
            continue
        indent_s, name, module, _tot, lut, lutram, _srl, ff, _b36, _b18, dsp = m.groups()
        rows.append({
            "indent": len(indent_s),
            "name": name,
            "module": module.strip(),
            "lut": int(lut),
            "lutram": int(lutram),
            "ff": int(ff),
            "dsp": int(dsp),
            "self": name.startswith("("),
        })
    return rows


def stage_table(path):
    rows = parse_rows(path)
    # Restrict to the kernel subtree and resolve ancestors by indent stack.
    in_kernel = False
    stack = []          # (indent, name)
    children = {}       # id(row) -> count of descendants seen
    counted = []        # (stage, module, self_row, lut, lutram, ff, dsp, has_children)
    out = {}
    for i, r in enumerate(rows):
        while stack and stack[-1][0] >= r["indent"]:
            stack.pop()
        if not in_kernel:
            if r["name"] == KERNEL:
                in_kernel = True
                stack.append((r["indent"], r["name"]))
            continue
        if not stack:
            break  # left the kernel subtree
        if r["self"]:
            continue_self = True
            parent = stack[-1][1] if stack else ""
            children[id(rows[i])] = 0
        has_children = (i + 1 < len(rows) and
                        rows[i + 1]["indent"] > r["indent"] and
                        not r["self"])
        stage = None
        for _ind, nm in stack[1:]:
            sm = STAGE.search(nm)
            if sm:
                stage = int(sm.group(1))
                break
        sm = STAGE.search(r["name"])
        if sm and stage is None:
            stage = int(sm.group(1))
        counted.append((stage, r["module"], r["self"], has_children,
                        r["lut"], r["lutram"], r["ff"], r["dsp"]))
        stack.append((r["indent"], r["name"]))
    # Second pass for leaf detection: a named row is a leaf iff the next
    # row (within the same subtree) is not deeper.
    for (stage, module, is_self, has_children, lut, lutram, ff, dsp) in counted:
        if stage is None or not (1 <= stage <= 7):
            continue
        if not is_self and has_children:
            continue  # parent row: children carry the resources
        cat = classify(module, is_self)
        rec = out.setdefault(stage, {"延迟存储_lutram": 0, "SECDED_lut": 0,
                                     "表决器_lut": 0})
        if cat == "延迟存储":
            rec["延迟存储_lutram"] += lutram
        elif cat == "SECDED":
            rec["SECDED_lut"] += lut
        elif cat == "表决器":
            rec["表决器_lut"] += lut
    return out


def main():
    tables = {}
    for name, rel in DESIGNS:
        tables[name] = stage_table(ROOT / rel)
    (ROOT / "results" / "wp10" / "stage_table.json").write_text(
        json.dumps(tables, ensure_ascii=False, indent=1, sort_keys=True))
    print("| 设计 | 级 | 延迟存储 LUTRAM | SECDED LUT | 表决器 LUT |")
    print("| --- | ---: | ---: | ---: | ---: |")
    for name, _rel in DESIGNS:
        for s in range(1, 8):
            r = tables[name].get(s, {})
            print(f"| {name} | {s} | {r.get('延迟存储_lutram', 0)} | "
                  f"{r.get('SECDED_lut', 0)} | {r.get('表决器_lut', 0)} |")


if __name__ == "__main__":
    main()
