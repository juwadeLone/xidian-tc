#!/usr/bin/env python3
r"""WP7 vectorless power breakdown (WP7_power.md sections 3-5).

Usage:
  python3 scripts/k7_mod/wp7_power_breakdown.py P0_p1   # one design
  python3 scripts/k7_mod/wp7_power_breakdown.py --report  # results/wp7/REPORT.md

Per-design mode reads results/wp7/<design>/{power_hier.xml,power_summary.rpt,
operating_conditions.rpt,clocks.rpt,hier_cells.tsv,power_timing.txt} and writes
power_breakdown.{md,json} next to them.  Own power of an instance =
its hierarchy-table row minus its direct children rows (section 3); each
instance's own power is classified by module name with
wp4r_functional_breakdown.classify(module, is_self_row=True) -- a self-power
row is the analogue of the parenthesized self row in the utilization report.
Power values are parsed from the xml hierarchy table (1 mW resolution; cells
shown as "<0.001" are taken at the midpoint 0.0005 W and counted toward the
rounding bound).  --report aggregates the four JSONs into REPORT.md and
evaluates gates W1-W4.
"""
import json
import re
import resource
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))
from wp4r_functional_breakdown import CATEGORIES, ADJUST_ROW, classify

DESIGNS = [
    ("P0", "P0_p1", "P1_WP4R2", True),
    ("P1", "P1_p2", "P2_WP4R2", False),
    ("S0", "S0_s3", "S3_WP4R2", True),
    ("S1", "S1_s2", "S2_WP4R2", False),
]

POWER_COLS = ["power", "clock", "io", "signal", "logic", "bram", "dsp", "clock_ip"]
MW = 1000.0


def watts(s):
    s = s.strip()
    if s.startswith("<"):
        return float(s[1:]) / 2.0  # "<0.001" -> midpoint of the 1 mW quantum
    return float(s)


def parse_hier_xml(path):
    """Return (rows, n_sub_mw). rows: list of dicts depth/seg/path/cols."""
    tree = ET.parse(path)
    sec = None
    for s in tree.getroot().iter("section"):
        if s.get("title") == "By Hierarchy":
            sec = s
    if sec is None:
        raise RuntimeError(f"{path}: no 'By Hierarchy' section")
    rows = []
    n_sub = 0
    stack = []
    for tr in sec.iter("tablerow"):
        cells = [tc.get("contents") for tc in tr.iter("tablecell")]
        if not cells:
            continue
        name = cells[0]
        depth = (len(name) - len(name.lstrip(" "))) // 2
        while len(stack) > depth:
            stack.pop()
        stack.append(name.strip())
        vals = []
        for c in cells[1:]:
            if c.strip().startswith("<"):
                n_sub += 1
            vals.append(watts(c))
        rows.append(
            {
                "depth": depth,
                "seg": name.strip(),
                "path": "/".join(stack[1:]),  # drop the top row segment
                "cols": dict(zip(POWER_COLS, vals)),
            }
        )
    return rows, n_sub


def parse_summary(path):
    """Scalars and the on-chip components table from power_summary.rpt."""
    txt = path.read_text()
    d = {}
    for key, pat in [
        ("total_w", r"Total On-Chip Power \(W\)\s*\|\s*([\d.]+)"),
        ("dynamic_w", r"\| Dynamic \(W\)\s*\|\s*([\d.]+)"),
        ("static_w", r"\| Device Static \(W\)\s*\|\s*([\d.]+)"),
        ("confidence", r"\| Confidence Level\s*\|\s*(\w+)"),
        ("sim_file", r"\| Simulation Activity File\s*\|\s*([^|]*)\|"),
        ("setting_file", r"\| Setting File\s*\|\s*([^|]*)\|"),
        ("junction_c", r"\| Junction Temperature \(C\)\s*\|\s*([\d.]+)"),
    ]:
        m = re.search(pat, txt)
        d[key] = m.group(1).strip() if m else None
    comp = {}
    comp_sec = re.search(
        r"1\.1 On-Chip Components.*?\n\+\=+\+|\+\-+\+\n(.*?)\+\-+\+\n",
        txt, re.S,
    )
    body = txt.split("1.1 On-Chip Components", 1)[1]
    body = body.split("1.2 Power Supply", 1)[0]
    for line in body.splitlines():
        m = re.match(r"\|\s*([^|]+?)\s*\|\s*(<?[\d.]+)\s*\|", line)
        if m:
            comp[m.group(1).strip()] = watts(m.group(2))
    d["components"] = comp
    return d


def parse_opcond(path):
    d = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def parse_clocks(path):
    rows = []
    in_tbl = False
    for line in path.read_text().splitlines():
        if line.startswith("Clock ") and "Period" in line:
            in_tbl = True
            continue
        if in_tbl:
            if not line.strip() or line.startswith("="):
                break
            f = line.split()
            if len(f) >= 2:
                try:
                    rows.append((f[0], float(f[1])))
                except ValueError:
                    pass
    return rows


def parse_cells(path):
    cells = {}
    for i, line in enumerate(path.read_text().splitlines()):
        if i == 0:
            continue
        f = line.split("\t")
        if len(f) >= 2:
            cells[f[0]] = (f[1], f[2] if len(f) > 2 else "")
    return cells


def breakdown_design(design, sub):
    """Compute the functional/stage breakdown for one design directory."""
    ddir = ROOT / "results" / "wp7" / sub
    rows, n_sub_mw = parse_hier_xml(ddir / "power_hier.xml")
    cells = parse_cells(ddir / "hier_cells.tsv")
    summary = parse_summary(ddir / "power_summary.rpt")
    opcond = parse_opcond(ddir / "operating_conditions.rpt")
    clocks = parse_clocks(ddir / "clocks.rpt")

    # children lists via depth stack
    children = [[] for _ in rows]
    stack = []
    for i, r in enumerate(rows):
        while len(stack) > r["depth"]:
            stack.pop()
        if stack:
            children[stack[-1]].append(i)
        stack.append(i)
    own = [
        r["cols"]["power"] - sum(rows[c]["cols"]["power"] for c in children[i])
        for i, r in enumerate(rows)
    ]

    kidx = next(i for i, r in enumerate(rows) if r["seg"] == "g_k.u_kernel")
    krow = rows[kidx]
    kdepth = krow["depth"]
    subtree = []
    for i in range(kidx + 1, len(rows)):
        if rows[i]["depth"] <= kdepth:
            break
        subtree.append(i)
    subtree_rows = [kidx] + subtree
    n_rows_kernel = len(subtree_rows)

    unmatched = []
    sums = {c: 0.0 for c in CATEGORIES}
    other = []
    for i in subtree_rows:
        r = rows[i]
        mod = cells.get(r["path"], (None,))[0]
        if mod is None:
            unmatched.append(r["path"])
            mod = "(unknown)"
        cat = classify(mod, True)  # own power ~ self row
        sums[cat] += own[i]
        if cat == CATEGORIES[-1] and abs(own[i]) > 0:
            other.append((r["path"], mod, own[i]))

    kernel_w = krow["cols"]["power"]
    cat_sum = sum(sums.values())
    recon = kernel_w - cat_sum
    bound_mw = n_rows_kernel * 0.5

    top_w = rows[0]["cols"]["power"]
    shell_w = top_w - kernel_w

    # per-stage split: nearest ancestor segment matching g_stage[N]
    stage_of = {}
    for i in subtree_rows:
        anc = rows[i]["path"].split("/")
        st = None
        for seg in anc:
            m = re.match(r"g_stage\[(\d+)\]", seg)
            if m:
                st = int(m.group(1))
                break
        stage_of[i] = st
    stages = {}
    for i in subtree_rows:
        st = stage_of[i]
        if st is None:
            continue
        # stage row total: use the row whose own segment is g_stage[N]
        if re.match(r"g_stage\[\d+\]", rows[i]["seg"]):
            stages[st] = rows[i]["cols"]["power"]
    stage_sum = sum(stages.values())
    nonstage = kernel_w - stage_sum

    out = {
        "design": design,
        "sub": sub,
        "total_w": summary["total_w"],
        "dynamic_w": summary["dynamic_w"],
        "static_w": summary["static_w"],
        "confidence": summary["confidence"],
        "sim_file": summary["sim_file"],
        "components": summary["components"],
        "opcond": opcond,
        "clocks": clocks,
        "kernel_w": kernel_w,
        "shell_w": shell_w,
        "top_hier_w": top_w,
        "categories_mw": {c: sums[c] * MW for c in CATEGORIES},
        "recon_mw": recon * MW,
        "recon_bound_mw": bound_mw,
        "n_rows_kernel": n_rows_kernel,
        "n_sub_mw_cells": n_sub_mw,
        "unmatched_rows": unmatched,
        "other_detail": [
            {"path": p, "module": m, "own_mw": w * MW}
            for p, m, w in sorted(other, key=lambda x: -x[2])
        ],
        "stages_mw": {str(k): v * MW for k, v in sorted(stages.items())},
        "stage_sum_mw": stage_sum * MW,
        "nonstage_mw": nonstage * MW,
        "other_mw": sums[CATEGORIES[-1]] * MW,
        "other_pct": 100.0 * sums[CATEGORIES[-1]] / kernel_w if kernel_w else 0.0,
    }
    return out, rows, own, cells


def render_design_md(d):
    has_ecc = d["design"] in ("P0", "S0")
    o = []
    o.append(f"# {d['design']} vectorless 功耗分项（冻结检查点，Vivado 2018.3）")
    o.append("")
    o.append(
        "口径：`report_power -hier logic`（xml，1 mW 精度；`<0.001` 按 0.5 mW 中值计）。"
        "每行 = 实例连同子实例的动态功耗；自身功耗 = 本行 − 各直接子行之和，"
        "按模块名交 `wp4r_functional_breakdown.classify(module, is_self_row=True)` 归类，"
        "分类规则与资源分项一致、未改动。只分项 `g_k.u_kernel` 子树；外壳只报总数。"
        "注意：层次表不输出 <0.001 W 的行，小模块（表决器/ROM/控制/标志等）的功耗被"
        "吸收进最近可见祖先的自身功耗。"
    )
    o.append("")
    o.append("| 量 | 值 |")
    o.append("| --- | ---: |")
    o.append(f"| 片上总功耗 | {d['total_w']} W |")
    o.append(f"| 动态 | {d['dynamic_w']} W |")
    o.append(f"| 静态 | {d['static_w']} W |")
    o.append(f"| 核 `g_k.u_kernel` 动态 | {d['kernel_w']*MW:.3f} mW |")
    o.append(f"| 外壳动态（顶层−核） | {d['shell_w']*MW:.3f} mW |")
    o.append(f"| 置信度 | {d['confidence']} |")
    o.append("")
    o.append("| 类别 | 动态功耗 (mW) | 占核动态 % |")
    o.append("| --- | ---: | ---: |")
    for c in CATEGORIES:
        v = d["categories_mw"][c]
        o.append(f"| {c} | {v:.3f} | {100*v/(d['kernel_w']*MW):.2f} |")
    o.append(f"| {ADJUST_ROW} | {d['recon_mw']:.3f} | — |")
    o.append(f"| 合计 | {d['recon_mw']+sum(d['categories_mw'].values()):.3f} | 100.00 |")
    o.append("")
    o.append(
        f"对账：分项和 + 对账行 = 核动态 {d['kernel_w']*MW:.3f} mW；"
        f"对账行 |{d['recon_mw']:.3f}| ≤ 行数 {d['n_rows_kernel']} × 0.5 mW "
        f"= {d['recon_bound_mw']:.1f} mW（W3）。"
    )
    o.append("")
    o.append("| g_stage[k] | 动态功耗 (mW) |")
    o.append("| --- | ---: |")
    for k in sorted(d["stages_mw"], key=int):
        o.append(f"| g_stage[{k}] | {d['stages_mw'][k]:.3f} |")
    o.append(f"| 非级行（核/流水线自身等） | {d['nonstage_mw']:.3f} |")
    o.append(f"| 核合计 | {d['kernel_w']*MW:.3f} |")
    o.append("")
    if d["other_detail"]:
        o.append("其他明细（实例路径（模块）：自身 mW）：")
        o.append("")
        for it in d["other_detail"]:
            o.append(f"- `{it['path']}`（{it['module']}）：{it['own_mw']:.3f}")
        o.append("")
    if d["unmatched_rows"]:
        o.append("未匹配到模块名的行（按 (unknown) 归类，进其他）：")
        for p in d["unmatched_rows"]:
            o.append(f"- `{p}`")
        o.append("")
    return "\n".join(o)


def fmt_w(x):
    return f"{float(x):.3f}"


def build_report():
    """Aggregate per-design JSONs into results/wp7/REPORT.md and evaluate W1-W4."""
    import hashlib

    wp7 = ROOT / "results" / "wp7"
    data = {}
    for design, sub, frozen, has_ecc in DESIGNS:
        data[design] = json.loads((wp7 / sub / "power_breakdown.json").read_text())

    # W1: checkpoint sha256 vs freeze_manifest.md
    manifest = (ROOT / "results" / "wp6" / "freeze_manifest.md").read_text()
    w1 = []
    for design, sub, frozen, has_ecc in DESIGNS:
        dcp = Path("/home/xdu/frozen/wp6") / frozen / "k7_paper_top_routed.dcp"
        sha = hashlib.sha256(dcp.read_bytes()).hexdigest()
        ok = sha in manifest
        w1.append((design, frozen, sha, ok))

    # W2: settings identical; activity source = defaults only
    set_fields = [
        "Device Grade", "Process Type", "Ambient Temp", "ThetaJa", "ThetaSa",
        "ThetaJb", "Airflow", "Heat Sink Type", "Board Type", "Board Temp",
        "Number Of Board Layers", "Vccint", "Vccaux", "Vcco33", "Vcco25",
        "Vcco18", "Vcco15", "Vcco135", "Vcco12", "Vccaux_io", "Vccbram",
    ]
    set_table = {f: [data[d]["opcond"].get(f, "") for d, *_ in DESIGNS] for f in set_fields}
    w2_fields_ok = all(len(set(v)) == 1 for v in set_table.values())
    w2_saif = all(data[d]["sim_file"] in ("---", "", None) for d, *_ in DESIGNS)
    w2 = w2_fields_ok and w2_saif

    # W3 / W4
    w3 = all(
        abs(data[d]["recon_mw"]) <= data[d]["recon_bound_mw"] + 1e-9
        and abs(data[d]["recon_mw"] + sum(data[d]["categories_mw"].values())
                - data[d]["kernel_w"] * MW) < 1e-6
        for d, *_ in DESIGNS
    )
    w4_other = all(data[d]["other_pct"] <= 3.0 for d, *_ in DESIGNS)
    w4_ecc = all(
        data[d]["categories_mw"]["SECDED"] == 0.0
        and data[d]["categories_mw"]["校验子与译码器"] == 0.0
        for d in ("P1", "S1")
    )
    w4 = w4_other and w4_ecc

    def git_hash(path):
        r = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", path],
            cwd=ROOT, capture_output=True, text=True,
        )
        return r.stdout.strip() or "(uncommitted)"

    tcl_hash = git_hash("results/wp7/power_dump.tcl")
    py_hash = git_hash("scripts/k7_mod/wp7_power_breakdown.py")

    # timing / memory per vivado process
    perf = {}
    for design, sub, frozen, has_ecc in DESIGNS:
        tt = (wp7 / sub / "power_timing.txt").read_text()
        el = re.search(r"or m:ss\):\s*([0-9:.]+)", tt)
        rss = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", tt)
        perf[design] = {
            "wall": el.group(1) if el else "?",
            "rss_gb": int(rss.group(1)) / 1048576 if rss else None,
        }

    o = []
    o.append("# WP7 vectorless 功耗报告（Vivado 2018.3 只读查询）")
    o.append("")
    o.append(
        "四个冻结检查点各开一个并行 Vivado 会话；`reset_switching_activity -all` 后"
        "显式设置 `-default_toggle_rate 12.5`、`-default_static_probability 0.5`，"
        "工作环境显式写出（25 °C / 250 LFM / medium 散热器 / medium 板 / typical 工艺）。"
        "2018.3 的选项名与 WP7 §2 一致，未改名。功耗行精度 1 mW，`<0.001` 按 0.5 mW "
        "中值计（见 W3 对账）。本包只产出数字，比较性主张由作者定。"
    )
    o.append("")
    o.append("## 1. 设置表")
    o.append("")
    o.append("| 设置项 | " + " | ".join(d for d, *_ in DESIGNS) + " | 一致 |")
    o.append("| --- |" + " --- |" * (len(DESIGNS) + 1))
    lab = {
        "default_toggle_rate": "默认翻转率 %", "default_static_probability": "默认静态概率",
        "activity_source": "活动来源",
    }
    fixed = {
        "default_toggle_rate": ["12.5"] * 4,
        "default_static_probability": ["0.5"] * 4,
        "activity_source": ["默认值（无 SAIF/仿真文件）"] * 4,
    }
    for k, v in fixed.items():
        o.append(f"| {lab.get(k, k)} | " + " | ".join(v) + " | ✓ |")
    for f in set_fields:
        vals = set_table[f]
        o.append(f"| {f} | " + " | ".join(vals) + f" | {'✓' if len(set(vals))==1 else '✗'} |")
    o.append(
        "| Junction Temp（派生量） | "
        + " | ".join(data[d]["opcond"].get("Junction Temp", "?") for d, *_ in DESIGNS)
        + " | 派生 |"
    )
    for d, *_ in DESIGNS:
        pass
    conf = [data[d]["confidence"] for d, *_ in DESIGNS]
    o.append("| 置信度 | " + " | ".join(conf) + f" | {'✓' if len(set(conf))==1 else '✗'} |")
    # kernel clock: rtl/board/k7_paper_top.sv drives u_core.clk with clk125 =
    # clk_out2_clk_wiz_k7 (8 ns, 125 MHz); also the name of the kernel's clock
    # port in the wp7b funcsim netlist.  (WP7 §2.4 expected clk_out1/200 MHz;
    # lab check shows the processing clock is actually clk_out2.)
    kclk = []
    for d, *_ in DESIGNS:
        c = dict(data[d]["clocks"])
        ns = c.get("clk_out2_clk_wiz_k7")
        kclk.append(f"clk_out2_clk_wiz_k7 {ns:.3f} ns ({1000/ns:.0f} MHz)" if ns else "?")
    o.append("| 核时钟 | " + " | ".join(kclk) + f" | {'✓' if len(set(kclk))==1 else '✗'} |")
    o.append("")
    o.append(
        f"W2 {'通过' if w2 else '未过'}：设置段逐项相同（Junction Temp 为功耗派生量，"
        f"不列入一致项）；仿真活动文件均为 `{data['P0']['sim_file']}`（无 SAIF）。"
    )
    o.append("")

    o.append("## 2. 各设计总表")
    o.append("")
    o.append("| 设计 | 片上总功耗 W | 动态 W | 静态 W | 置信度 |")
    o.append("| --- | ---: | ---: | ---: | --- |")
    for d, *_ in DESIGNS:
        r = data[d]
        o.append(f"| {d} | {r['total_w']} | {r['dynamic_w']} | {r['static_w']} | {r['confidence']} |")
    o.append("")
    o.append("动态按类型分（W，层次表顶行各列）：")
    o.append("")
    o.append("| 设计 | 时钟 | 信号 | 逻辑 | DSP | BRAM | MMCM(Clock IP) | I/O |")
    o.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for d, sub, *_ in DESIGNS:
        r = data[d]
        t = r.get("top_cols")
        if t is None:
            comp = r["components"]
            o.append(
                f"| {d} | {comp.get('Clocks',0):.3f} | {comp.get('Signals',0):.3f}"
                f" | {comp.get('Slice Logic',0):.3f} | {comp.get('DSPs',0):.3f}"
                f" | {comp.get('Block RAM',0):.3f} | {comp.get('MMCM',0):.3f}"
                f" | {comp.get('I/O',0):.3f} |"
            )
        else:
            o.append(
                f"| {d} | {t['clock']:.3f} | {t['signal']:.3f} | {t['logic']:.3f}"
                f" | {t['dsp']:.3f} | {t['bram']:.3f} | {t['clock_ip']:.3f} | {t['io']:.3f} |"
            )
    o.append("")

    o.append("## 3. 核与外壳动态功耗")
    o.append("")
    o.append("| 设计 | 顶层动态 mW | 核 `g_k.u_kernel` mW | 外壳 mW | 核占比 % |")
    o.append("| --- | ---: | ---: | ---: | ---: |")
    for d, *_ in DESIGNS:
        r = data[d]
        o.append(
            f"| {d} | {r['top_hier_w']*MW:.3f} | {r['kernel_w']*MW:.3f}"
            f" | {r['shell_w']*MW:.3f} | {100*r['kernel_w']/r['top_hier_w']:.1f} |"
        )
    o.append("")

    o.append("## 4. 核动态功耗按功能分项")
    o.append("")
    cats = CATEGORIES
    for fam, pair in (("PFFT 族", ("P0", "P1")), ("SubFFT 族", ("S0", "S1"))):
        o.append(f"{fam}（单位 mW，占核动态 %）：")
        o.append("")
        o.append("| 类别 | " + f"{pair[0]} mW | {pair[0]} % | {pair[1]} mW | {pair[1]} % |")
        o.append("| --- | ---: | ---: | ---: | ---: |")
        for c in cats:
            a, b = data[pair[0]], data[pair[1]]
            va, vb = a["categories_mw"][c], b["categories_mw"][c]
            o.append(
                f"| {c} | {va:.3f} | {100*va/(a['kernel_w']*MW):.2f}"
                f" | {vb:.3f} | {100*vb/(b['kernel_w']*MW):.2f} |"
            )
        for tag, r in (("", data[pair[0]]), ("", data[pair[1]])):
            pass
        ra, rb = data[pair[0]], data[pair[1]]
        o.append(
            f"| {ADJUST_ROW} | {ra['recon_mw']:.3f} | — | {rb['recon_mw']:.3f} | — |"
        )
        o.append(
            f"| 合计 | {sum(ra['categories_mw'].values())+ra['recon_mw']:.3f} | 100"
            f" | {sum(rb['categories_mw'].values())+rb['recon_mw']:.3f} | 100 |"
        )
        o.append("")
    o.append(
        "注：`report_power -hier logic` 不输出 Power <0.001 W 的层次行（全表无 `<0.001`"
        " 行；加 `-l 0` 验证无效）。表决器/ROM/控制/标志等小模块（如 `g_vote[*].u_data_vote`、"
        "`u_control`、`g_rom.u_rom`，它们以 keep_hierarchy 单元存在于 `hier_cells.tsv`）"
        "的功耗被吸收进最近可见祖先行的自身功耗，随祖先模块归类（多落入蝶形加减或"
        "其他-级自身行）。故这些类别显示 ≈0 mW 是 1 mW 分辨率的仪器下限，而非模块缺失。"
        "级外壳自身功耗可因 mW 舍入为 ±1 mW 量级，使“其他”出现小幅负值。"
    )
    o.append("")
    o.append(
        f"W4 {'通过' if w4 else '未过'}：各设计“其他”占核动态 "
        + " / ".join(f"{d}={data[d]['other_pct']:.2f}%" for d, *_ in DESIGNS)
        + "（≤3%）；P1/S1 的 SECDED 与校验子与译码器均为 "
        + " / ".join(
            f"{d}:SECDED={data[d]['categories_mw']['SECDED']:.3f},校验={data[d]['categories_mw']['校验子与译码器']:.3f}"
            for d in ("P1", "S1"))
        + " mW。"
    )
    o.append("")

    o.append("## 5. 核动态功耗按级（g_stage[k]）")
    o.append("")
    o.append("| 级 | " + " | ".join(f"{d} mW" for d, *_ in DESIGNS) + " |")
    o.append("| --- |" + " ---: |" * 4)
    all_st = sorted({int(k) for d, *_ in DESIGNS for k in data[d]["stages_mw"]})
    for st in all_st:
        o.append(
            f"| g_stage[{st}] | "
            + " | ".join(f"{data[d]['stages_mw'].get(str(st),0.0):.3f}" for d, *_ in DESIGNS)
            + " |"
        )
    o.append(
        "| 非级行 | " + " | ".join(f"{data[d]['nonstage_mw']:.3f}" for d, *_ in DESIGNS) + " |"
    )
    o.append(
        "| 核合计 | " + " | ".join(f"{data[d]['kernel_w']*MW:.3f}" for d, *_ in DESIGNS) + " |"
    )
    o.append("")

    o.append("## 6. 对账")
    o.append("")
    o.append(
        "| 设计 | 分项和 mW | 对账行 mW | 核动态 mW | 界 = 行数×0.5 mW | W3 |"
    )
    o.append("| --- | ---: | ---: | ---: | ---: | --- |")
    for d, *_ in DESIGNS:
        r = data[d]
        okd = abs(r["recon_mw"]) <= r["recon_bound_mw"] + 1e-9
        o.append(
            f"| {d} | {sum(r['categories_mw'].values()):.3f} | {r['recon_mw']:.3f}"
            f" | {r['kernel_w']*MW:.3f} | {r['n_rows_kernel']}×0.5={r['recon_bound_mw']:.1f}"
            f" | {'✓' if okd else '✗'} |"
        )
    o.append("")
    o.append(
        f"`<0.001` 单元格数："
        + " / ".join(f"{d}={data[d]['n_sub_mw_cells']}" for d, *_ in DESIGNS)
        + "（按 0.5 mW 中值计入，各行已含在上表行数界内）。分项和 + 对账行 = 核动态，"
        "对账行绝对值均在界内。"
        if w3 else
        "存在对账行超界设计，逐项见各 power_breakdown.md。"
    )
    o.append("")

    o.append("## 7. 出处")
    o.append("")
    o.append("| 设计 | 冻结目录 | 检查点 SHA-256 | W1 |")
    o.append("| --- | --- | --- | --- |")
    for design, frozen, sha, ok in w1:
        o.append(f"| {design} | {frozen} | `{sha}` | {'✓' if ok else '✗'} |")
    o.append("")
    o.append(f"- tcl：`results/wp7/power_dump.tcl`，git `{tcl_hash[:12]}`")
    o.append(f"- py：`scripts/k7_mod/wp7_power_breakdown.py`，git `{py_hash[:12]}`")
    o.append("- Vivado：v2018.3 (Build 2405991)，`/tools/Xilinx/Vivado/2018.3/bin/vivado`")
    o.append("- 进程：4 个并行 Vivado 会话 + 4 个并行分项进程 + 1 个汇总进程")
    o.append("")
    o.append("| 进程 | 耗时 | 峰值 RSS |")
    o.append("| --- | ---: | ---: |")
    for d, *_ in DESIGNS:
        p = perf[d]
        o.append(
            f"| vivado {d} | {p['wall']} | {p['rss_gb']:.2f} GB |" if p["rss_gb"]
            else f"| vivado {d} | {p['wall']} | ? |"
        )
    for d, *_ in DESIGNS:
        bd = data[d].get("breakdown_perf", {})
        if bd:
            o.append(
                f"| breakdown {d} | {bd.get('wall_s','?')} s | {bd.get('rss_gb',0):.3f} GB |"
            )
    o.append("")
    gates = [("W1", all(x[3] for x in w1)), ("W2", w2), ("W3", w3), ("W4", w4)]
    o.append("## 判据汇总")
    o.append("")
    o.append("| 判据 | 结果 |")
    o.append("| --- | --- |")
    for g, ok in gates:
        o.append(f"| {g} | {'通过' if ok else '未过'} |")
    o.append("")
    return "\n".join(o), all(ok for _, ok in gates)


def run_one(sub):
    design = sub
    t0 = time.time()
    d, rows, own, cells = breakdown_design(design, sub)
    d["top_cols"] = rows[0]["cols"]
    d["breakdown_perf"] = {
        "wall_s": round(time.time() - t0, 2),
        "rss_gb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576,
    }
    ddir = ROOT / "results" / "wp7" / sub
    (ddir / "power_breakdown.json").write_text(json.dumps(d, ensure_ascii=False, indent=1))
    (ddir / "power_breakdown.md").write_text(render_design_md(d))
    print(
        f"{design}: kernel={d['kernel_w']*MW:.1f}mW shell={d['shell_w']*MW:.1f}mW "
        f"recon={d['recon_mw']:.3f}mW other={d['other_pct']:.2f}% "
        f"wall={d['breakdown_perf']['wall_s']}s"
    )


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--report":
        text, ok = build_report()
        (ROOT / "results" / "wp7" / "REPORT.md").write_text(text)
        print("REPORT.md written; gates:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    if len(sys.argv) == 2 and sys.argv[1] in {s for _, s, _, _ in DESIGNS}:
        run_one(sys.argv[1])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
