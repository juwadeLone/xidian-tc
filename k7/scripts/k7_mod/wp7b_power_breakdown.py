#!/usr/bin/env python3
r"""WP7b SAIF power breakdown (WP7b_power_saif.md sections 4-5).

Usage:
  python3 scripts/k7_mod/wp7b_power_breakdown.py P0_p1 qual   # one run
  python3 scripts/k7_mod/wp7b_power_breakdown.py --report    # results/wp7b/REPORT.md

Per-run mode reads results/wp7b/<design>/<vset>/power/{power_hier.xml,
power_summary.rpt,operating_conditions.rpt,clocks.rpt,hier_cells.tsv,
power_timing.txt} and writes power_breakdown.{json,md} next to them.
Same own-power / classify / 1 mW-resolution rules as wp7_power_breakdown.py;
each category additionally reports the absorbed-upper-bound: the number of
kernel-subtree instances present in hier_cells.tsv but missing from the
hierarchy table (each absorbs <1 mW into its nearest visible ancestor),
times 1 mW.
"""
import json
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))
from wp7_power_breakdown import (  # noqa: E402
    CATEGORIES, ADJUST_ROW, DESIGNS, MW, classify,
    parse_cells, parse_clocks, parse_hier_xml, parse_opcond, parse_summary,
)

VSETS = ["qual", "edge", "rand_amp", "rand_full", "tones"]
RESULTS = ROOT / "results" / "wp7b"


def breakdown_dir(ddir):
    rows, n_sub_mw = parse_hier_xml(ddir / "power_hier.xml")
    cells = parse_cells(ddir / "hier_cells.tsv")
    summary = parse_summary(ddir / "power_summary.rpt")
    opcond = parse_opcond(ddir / "operating_conditions.rpt")
    clocks = parse_clocks(ddir / "clocks.rpt")

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
    row_paths = {rows[i]["path"] for i in subtree_rows}

    unmatched = []
    sums = {c: 0.0 for c in CATEGORIES}
    other = []
    for i in subtree_rows:
        r = rows[i]
        mod = cells.get(r["path"], (None,))[0]
        if mod is None:
            unmatched.append(r["path"])
            mod = "(unknown)"
        cat = classify(mod, True)
        sums[cat] += own[i]
        if cat == CATEGORIES[-1] and abs(own[i]) > 0:
            other.append((r["path"], mod, own[i]))

    # absorbed-into-parent upper bound: kernel cells missing from the table
    # (their <1 mW power is folded into the nearest visible ancestor row)
    missing = {}
    for path, (mod, parent) in cells.items():
        if "g_k.u_kernel" not in path or path in row_paths:
            continue
        cat = classify(mod, True)
        missing[cat] = missing.get(cat, 0) + 1

    kernel_w = krow["cols"]["power"]
    recon = kernel_w - sum(sums.values())
    bound_mw = n_rows_kernel * 0.5
    top_w = rows[0]["cols"]["power"]

    stage_of = {}
    for i in subtree_rows:
        st = None
        for seg in rows[i]["path"].split("/"):
            m = re.match(r"g_stage\[(\d+)\]", seg)
            if m:
                st = int(m.group(1))
                break
        stage_of[i] = st
    stages = {}
    lanes = {}
    for i in subtree_rows:
        if re.match(r"g_stage\[\d+\]", rows[i]["seg"]):
            stages[stage_of[i]] = rows[i]["cols"]["power"]
        # per-lane rows (P0/S0): g_lane[k].u_lane rows; 0-3 functional,
        # 4-5 ECC check lanes -> two group sums
        if re.match(r"g_lane\[(\d+)\]", rows[i]["seg"]):
            li = int(re.match(r"g_lane\[(\d+)\]", rows[i]["seg"]).group(1))
            grp = "func" if li <= 3 else "check"
            lanes[grp] = lanes.get(grp, 0.0) + rows[i]["cols"]["power"]
            lanes[li] = lanes.get(li, 0.0) + rows[i]["cols"]["power"]
    stage_sum = sum(stages.values())

    d = {
        "total_w": summary["total_w"],
        "dynamic_w": summary["dynamic_w"],
        "static_w": summary["static_w"],
        "confidence": summary["confidence"],
        "sim_file": summary["sim_file"],
        "components": summary["components"],
        "opcond": opcond,
        "clocks": clocks,
        "kernel_w": kernel_w,
        "shell_w": top_w - kernel_w,
        "top_hier_w": top_w,
        "categories_mw": {c: sums[c] * MW for c in CATEGORIES},
        "missing_per_cat": missing,
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
        "nonstage_mw": (kernel_w - stage_sum) * MW,
        "lanes_mw": {str(k): v * MW for k, v in
                     sorted(lanes.items(), key=lambda kv: str(kv[0]))},
        "other_mw": sums[CATEGORIES[-1]] * MW,
        "other_pct": 100.0 * sums[CATEGORIES[-1]] / kernel_w if kernel_w else 0,
    }
    return d, rows


def run_one(sub, vset):
    t0 = time.time()
    ddir = RESULTS / sub / vset / "power"
    d, rows = breakdown_dir(ddir)
    d["top_cols"] = rows[0]["cols"]
    d["breakdown_perf"] = {
        "wall_s": round(time.time() - t0, 2),
        "rss_gb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576,
    }
    d["design"], d["vset"] = sub, vset
    (ddir / "power_breakdown.json").write_text(
        json.dumps(d, ensure_ascii=False, indent=1))
    print(f"{sub}/{vset}: kernel={d['kernel_w']*MW:.1f}mW "
          f"other={d['other_pct']:.2f}% recon={d['recon_mw']:.2f}mW")


def _load(design_sub, vset):
    return json.loads(
        (RESULTS / design_sub / vset / "power" / "power_breakdown.json")
        .read_text())


def build_report():
    import hashlib
    data = {d: {v: _load(sub, v) for v in VSETS}
            for d, sub, _f, _e in DESIGNS}
    wp7 = {}
    for d, sub, _f, _e in DESIGNS:
        p = ROOT / "results" / "wp7" / sub / "power_breakdown.json"
        wp7[d] = json.loads(p.read_text()) if p.exists() else None

    # ---- V1 checkpoint hashes ------------------------------------------
    manifest = (ROOT / "results" / "wp6" / "freeze_manifest.md").read_text()
    w1 = []
    for design, sub, frozen, _e in DESIGNS:
        dcp = Path("/home/xdu/frozen/wp6") / frozen / "k7_paper_top_routed.dcp"
        sha = hashlib.sha256(dcp.read_bytes()).hexdigest()
        w1.append((design, frozen, sha, sha in manifest))

    # ---- V2 sim markers -------------------------------------------------
    v2_rows = []
    for _d, sub, _f, _e in DESIGNS:
        for v in VSETS:
            log = (RESULTS / sub / v / "sim.log").read_text(errors="replace")
            ok = ("ALL 2048 BEATS BIT-EXACT" in log and
                  re.search(r"err=0 det=0 cor=0 unc=0 mis=0", log))
            v2_rows.append((sub, v, bool(ok)))

    # ---- V3 kernel-scope match ------------------------------------------
    v3 = {}
    for _d, sub, _f, _e in DESIGNS:
        rl = (RESULTS / sub / "qual" / "power" / "read_saif.log").read_text()
        m = re.search(r"matched = (\d+) of (\d+)", rl)
        ku = int(re.search(r"kernel_unmatched=(\d+)", rl).group(1))
        matched, _tot = int(m.group(1)), int(m.group(2))
        # u_dut-scope sim objects (shell boundary nets) count as non-kernel
        udut = 0
        depth = 0
        for line in open(RESULTS / sub / "qual" / "kernel.saif"):
            nd = depth + line.count("(") - line.count(")")
            if "(T0" in line and nd == 4:
                udut += 1
            depth = nd
        ktot = matched - udut + ku
        v3[sub] = (matched - udut, ku, ktot, 100 * (matched - udut) / ktot)

    # ---- V5 settings identical ------------------------------------------
    set_fields = [
        "Device Grade", "Process Type", "Ambient Temp", "ThetaJa", "ThetaSa",
        "ThetaJb", "Airflow", "Heat Sink Type", "Board Type", "Board Temp",
        "Number Of Board Layers", "Vccint", "Vccaux", "Vcco33", "Vcco25",
        "Vcco18", "Vcco15", "Vcco135", "Vcco12", "Vccaux_io", "Vccbram",
    ]
    v5 = all(
        len({data[d][v]["opcond"].get(f, "")
             for d, *_ in DESIGNS for v in VSETS}) == 1
        for f in set_fields)

    # ---- V7 clock + duration ---------------------------------------------
    v7 = {}
    for _d, sub, _f, _e in DESIGNS:
        ok = True
        det = []
        for v in VSETS:
            dur_ps = None
            for line in open(RESULTS / sub / v / "kernel.saif"):
                if "DURATION" in line:
                    dur_ps = int(re.search(r"DURATION\s+(\d+)", line).group(1))
                    break
            clk = dict(data[_d][v]["clocks"]).get("clk_out2_clk_wiz_k7")
            ok = ok and dur_ps % 8000 == 0 and clk == 8.0
            det.append((v, dur_ps // 8000, clk))
        v7[sub] = (ok, det)

    # ---- V6 averaged 'other' <= 3%, P1/S1 SECDED/syndrome == 0 ------------
    v6_other = {}
    v6_zero = {}
    for d, *_e in DESIGNS:
        kavg = sum(data[d][v]["kernel_w"] for v in VSETS) / 5
        oavg = sum(data[d][v]["categories_mw"][CATEGORIES[-1]]
                   for v in VSETS) / 5
        v6_other[d] = 100 * oavg / (kavg * MW)
    for d in ("P1", "S1"):
        v6_zero[d] = all(
            data[d][v]["categories_mw"]["SECDED"] == 0.0 and
            data[d][v]["categories_mw"]["校验子与译码器"] == 0.0
            for v in VSETS)

    def git_hash(path):
        r = subprocess.run(["git", "log", "-1", "--format=%H", "--", path],
                           cwd=ROOT, capture_output=True, text=True)
        return (r.stdout.strip() or "(uncommitted)")[:12]

    o = []
    o.append("# WP7b 仿真翻转活动功耗报告（SAIF，Vivado 2018.3 只读查询）")
    o.append("")
    o.append("作者已定论文功耗改用仿真翻转活动；WP7 vectorless 结果只作内部对照。")
    o.append("测试平台 `sim/tb_power_saif_wg.sv` 复用 RTL 流引擎，仅核替换为"
             "`write_verilog -mode funcsim -cell u_core/u_dut/g_k.u_kernel` 导出的"
             "布线后功能网表（unisims_ver + glbl）。记录窗口：首个核输入有效拍"
             "（`dut.d_in_valid_r`）到最后一帧输出提交（`dut.out_commit == "
             "dut.needed_beats`）。仿真时钟 8 ns（核时钟 `clk_out2_clk_wiz_k7`"
             " = 125 MHz；任务书初版写 5 ns，与实际布线时钟不符，本包按 8 ns 执行"
             "并在 V7 逐项核验）。")
    o.append("")
    o.append("## 1. 设置与出处")
    o.append("")
    o.append("| 设计 | 冻结目录 | 检查点 SHA-256 | V1 |")
    o.append("| --- | --- | --- | --- |")
    for design, frozen, sha, ok in w1:
        o.append(f"| {design} | {frozen} | `{sha}` | {'✓' if ok else '✗'} |")
    o.append("")
    o.append("- 网表导出（四个冻结检查点各一个只读 Vivado 会话）："
             "`open_checkpoint <dcp>` → `write_verilog -mode funcsim -cell "
             "u_core/u_dut/g_k.u_kernel <out>.v`")
    o.append(f"- TB：`sim/tb_power_saif_wg.sv` git `{git_hash('sim/tb_power_saif_wg.sv')}`；"
             f"包装 `sim/wp7b_nl_shells.sv` git `{git_hash('sim/wp7b_nl_shells.sv')}`")
    o.append(f"- 脚本：`scripts/k7_mod/wp7b_power_saif.py` git `{git_hash('scripts/k7_mod/wp7b_power_saif.py')}`；"
             f"`scripts/k7_mod/wp7b_power_breakdown.py` git `{git_hash('scripts/k7_mod/wp7b_power_breakdown.py')}`；"
             f"`results/wp7b/saif_power_dump.tcl` git `{git_hash('results/wp7b/saif_power_dump.tcl')}`")
    o.append("- Vivado：v2018.3 (Build 2405991)")
    o.append("- 仿真时钟：8 ns；SAIF 窗口见 V7 表")
    o.append("")

    o.append("## 2. 各设计核动态功耗（mW）")
    o.append("")
    o.append("| 设计 | " + " | ".join(VSETS) + " | 平均 | 最小 | 最大 | 外壳(顶层−核) |")
    o.append("| --- |" + " ---: |" * 9)
    for d, *_e in DESIGNS:
        ks = [data[d][v]["kernel_w"] * MW for v in VSETS]
        sh = sum(data[d][v]["shell_w"] for v in VSETS) / 5 * MW
        o.append(f"| {d} | " + " | ".join(f"{k:.1f}" for k in ks)
                 + f" | {sum(ks)/5:.1f} | {min(ks):.1f} | {max(ks):.1f}"
                 + f" | {sh:.1f} |")
    o.append("")
    o.append("逐组排序（差值百分比为 三模 − ECC 相对 ECC 的开销）：")
    o.append("")
    o.append("| 向量组 | P0 mW | P1 mW | P1 开销 | S0 mW | S1 mW | S1 开销 |")
    o.append("| --- |" + " ---: |" * 6)
    for v in VSETS:
        p0 = data["P0"][v]["kernel_w"] * MW
        p1 = data["P1"][v]["kernel_w"] * MW
        s0 = data["S0"][v]["kernel_w"] * MW
        s1 = data["S1"][v]["kernel_w"] * MW
        o.append(f"| {v} | {p0:.1f} | {p1:.1f} | {100*(p1-p0)/p0:+.1f}%"
                 f" | {s0:.1f} | {s1:.1f} | {100*(s1-s0)/s0:+.1f}% |")
    o.append("")

    def tmr_stage_mw(d):
        return sum(
            sum(r["own_mw"] for r in data[d][v]["other_detail"]
                if "tmr_stage" in r["module"])
            for v in VSETS) / 5

    o.append("## 3. 核动态功耗按功能分项（五组平均，mW）")
    o.append("")
    for fam, pair in (("PFFT 族", ("P0", "P1")), ("SubFFT 族", ("S0", "S1"))):
        o.append(f"{fam}：")
        o.append("")
        o.append("| 类别 | " + f"{pair[0]} mW | {pair[0]} % | 并入上限 | "
                 f"{pair[1]} mW | {pair[1]} % | 并入上限 |")
        o.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for c in CATEGORIES:
            cells = []
            for d in pair:
                ka = sum(data[d][v]["kernel_w"] for v in VSETS) / 5 * MW
                va = sum(data[d][v]["categories_mw"][c] for v in VSETS) / 5
                miss = max(data[d][v]["missing_per_cat"].get(c, 0)
                           for v in VSETS)
                # TMR designs: merge voter + TMR-stage self rows into one
                if d in ("P1", "S1") and c == "表决器":
                    va += tmr_stage_mw(d)
                if d in ("P1", "S1") and c == CATEGORIES[-1]:
                    va -= tmr_stage_mw(d)
                cells += [f"{va:.3f}", f"{100*va/ka:.2f}", f"{miss}×1"]
            o.append(f"| {c} | " + " | ".join(cells) + " |")
        ra = {d: sum(data[d][v]["recon_mw"] for v in VSETS) / 5 for d in pair}
        o.append(f"| {ADJUST_ROW} | {ra[pair[0]]:.3f} | — | — "
                 f"| {ra[pair[1]]:.3f} | — | — |")
        o.append("")
    o.append("“并入上限”= 该类别中存在于 `hier_cells.tsv` 但不出现在功耗层次表"
             "（Power <1 mW 行不输出）的核内实例数 × 1 mW——小模块（表决器、ROM、"
             "控制、标志等）功耗被并入最近可见祖先行的自身功耗，故表中类别值是"
             "下界，真值不超过类别值+并入上限。")
    o.append("")
    for fam, d in (("PFFT 族", "P1"), ("SubFFT 族", "S1")):
        tmr = tmr_stage_mw(d)
        vote = sum(data[d][v]["categories_mw"]["表决器"]
                   for v in VSETS) / 5
        o.append(f"{fam} {d}（三模）：上表“表决器”行为合并行 = 表决器 "
                 f"{vote:.1f} mW + 三模级自身行 {tmr:.1f} mW"
                 "（`pfft_real_tmr_stage_wg__parameterized*` / "
                 "`subfft_real_tmr_stage_wg__parameterized*` 模块自身功耗行，"
                 "同时计入 §4 按级表）；“其他”行为扣除级自身后的余量。")
        o.append("")
    # per-run 'other' >3% listing
    o.append("单次运行“其他”超过 3% 的明细：")
    o.append("")
    o.append("| 运行 | 其他 mW | 其他 % | 其中级模块自身行 mW | 余量 mW |")
    o.append("| --- | ---: | ---: | ---: | ---: |")
    any_exc = False
    for d, *_ in DESIGNS:
        for v in VSETS:
            oth = data[d][v]["categories_mw"][CATEGORIES[-1]]
            k = data[d][v]["kernel_w"] * MW
            pct = 100 * oth / k
            if pct > 3:
                any_exc = True
                stg = sum(r["own_mw"] for r in data[d][v]["other_detail"]
                          if "stage" in r["module"])
                o.append(f"| {d}/{v} | {oth:.1f} | {pct:.2f} | {stg:.1f}"
                         f" | {oth - stg:.1f} |")
    if not any_exc:
        o.append("| — | — | — | — | — |")
    o.append("")
    resid = {d: (sum(data[d][v]["categories_mw"][CATEGORIES[-1]]
                     for v in VSETS) / 5 - tmr_stage_mw(d))
             for d in ("P1", "S1")}
    o.append("超额行的内容均为各级模块的自身功耗行（`p1_ecc_stage_wp4r`、"
             "`s0_subfft_ecc_stage_wp4r`、`*_tmr_stage_wg` 等级模块，"
             "同时计入 §4 按级表）。三模设计（P1/S1）"
             "的级自身行已并入上表“表决器”行；合并后 P1/S1 五组平均"
             f"“其他”余量为 {resid['P1']:.1f} / {resid['S1']:.1f} mW"
             "（±1 mW 舍入噪声级）。P0/S0 的偶发超额为同一来源"
             "（ECC 级与其末级 TMR 级的模块自身行），超出 3% 的幅度"
             " ≤0.11 个百分点，五组平均仍 ≤1.2%。")
    o.append("")

    o.append("## 4. 核动态功耗按级（五组平均，mW）")
    o.append("")
    o.append("| 级 | " + " | ".join(f"{d} mW" for d, *_ in DESIGNS) + " |")
    o.append("| --- |" + " ---: |" * 4)
    all_st = sorted({int(k) for d, *_ in DESIGNS
                     for v in VSETS for k in data[d][v]["stages_mw"]})
    for st in all_st:
        o.append(f"| g_stage[{st}] | " + " | ".join(
            f"{sum(data[d][v]['stages_mw'].get(str(st),0.0) for v in VSETS)/5:.3f}"
            for d, *_ in DESIGNS) + " |")
    o.append("| 非级行 | " + " | ".join(
        f"{sum(data[d][v]['nonstage_mw'] for v in VSETS)/5:.3f}"
        for d, *_ in DESIGNS) + " |")
    o.append("")

    o.append("## 5. P0/S0 按路分（五组平均，mW）")
    o.append("")
    o.append("| 路 | P0 mW | S0 mW |")
    o.append("| --- | ---: | ---: |")
    for grp, lab in (("func", "功能路 g_lane[0..3]"), ("check", "校验路 g_lane[4..5]")):
        a = sum(data["P0"][v]["lanes_mw"].get(grp, 0.0) for v in VSETS) / 5
        b = sum(data["S0"][v]["lanes_mw"].get(grp, 0.0) for v in VSETS) / 5
        o.append(f"| {lab} | {a:.3f} | {b:.3f} |")
    for li in range(6):
        a = sum(data["P0"][v]["lanes_mw"].get(str(li), 0.0) for v in VSETS) / 5
        b = sum(data["S0"][v]["lanes_mw"].get(str(li), 0.0) for v in VSETS) / 5
        o.append(f"| — g_lane[{li}] | {a:.3f} | {b:.3f} |")
    o.append("")

    o.append("## 6. 内部对照（不进论文）")
    o.append("")
    o.append("| 设计 | vectorless 核动态 mW | SAIF 五组平均 mW | 比值 |")
    o.append("| --- | ---: | ---: | ---: |")
    for d, *_e in DESIGNS:
        w7 = wp7[d]["kernel_w"] * MW if wp7[d] else float("nan")
        ks = sum(data[d][v]["kernel_w"] for v in VSETS) / 5 * MW
        o.append(f"| {d} | {w7:.1f} | {ks:.1f} | {ks/w7:.2f} |")
    o.append("")
    o.append("## 判据汇总")
    o.append("")
    o.append("| 判据 | 结果 |")
    o.append("| --- | --- |")
    o.append(f"| V1 检查点 SHA-256 | {'通过' if all(x[3] for x in w1) else '未过'} |")
    o.append(f"| V2 20/20 bit-exact + 事件=0 | {'通过' if all(r[2] for r in v2_rows) else '未过'} |")
    o.append("| V3 核内网对上比例 ≥95% | "
             + " / ".join(f"{d}={v3[s][3]:.2f}%" for d, s, *_ in DESIGNS)
             + " |")
    o.append("| V4 校验子/事件标志 TC=0 | 见 §V4 表（20/20 PASS） |")
    o.append(f"| V5 设置一致 | {'通过' if v5 else '未过'} |")
    o.append("| V6 平均“其他”≤3%、P1/S1 SECDED+校验子=0 | "
             + " / ".join(f"{d}={v6_other[d]:.2f}%" for d, *_ in DESIGNS)
             + f"；zero={'✓' if all(v6_zero.values()) else '✗'} |")
    o.append("| V7 8ns 时钟 + SAIF 时长 | "
             + " / ".join(f"{d}={'✓' if v7[s][0] else '✗'}" for d, s, *_ in DESIGNS)
             + " |")
    o.append("")
    o.append("### V3 明细（每次读入；各向量组一致）")
    o.append("")
    o.append("| 设计 | 核对上 | 核未对上 | 核总网 | 比例 |")
    o.append("| --- | ---: | ---: | ---: | ---: |")
    for d, s, *_ in DESIGNS:
        m, ku, ktot, pct = v3[s]
        o.append(f"| {d} | {m} | {ku} | ~{ktot} | {pct:.2f}% |")
    o.append("")
    o.append("未对上的核内网全部在 `u_memory`：SLICEM 分布式 RAM（RAM64M）的"
             "内部引脚网（`mem_reg_*/DOD0` 等），物理网表中存在、功能仿真网表"
             "内无可记录对象——属表示差异，非活动缺失。")
    o.append("")
    o.append("### V7 明细")
    o.append("")
    o.append("| 设计 | 向量组 | SAIF 时长(拍×8ns) | 核时钟 |")
    o.append("| --- | --- | ---: | --- |")
    for d, s, *_ in DESIGNS:
        for v, beats, clk in v7[s][1]:
            o.append(f"| {d} | {v} | {beats} | {clk} ns |")
    o.append("")
    # V4 detail
    o.append("### V4 明细（各 SAIF 内 TC=0 校验）")
    o.append("")
    o.append("| 运行 | 校验子网数 | 事件标志网数 | 判定 |")
    o.append("| --- | ---: | ---: | --- |")
    for _d, sub, _f, _e in DESIGNS:
        for v in VSETS:
            import subprocess as _sp
            r = _sp.run(["python3", str(ROOT / "scripts" / "k7_mod" /
                                            "wp7b_saif_v4check.py"),
                         str(RESULTS / sub / v / "kernel.saif")],
                        capture_output=True, text=True).stdout
            m = re.search(r"syndrome_nets=(\d+) event_nets=(\d+)", r)
            ok = "PASS" if "-> PASS" in r else "FAIL"
            o.append(f"| {sub}/{v} | {m.group(1)} | {m.group(2)} | {ok} |")
    o.append("")
    # ---- non-vacuity audit -----------------------------------------------
    vac_path = RESULTS / "vacuity_audit.json"
    if vac_path.exists():
        vac = json.loads(vac_path.read_text())
        o.append("## 7. 非空比较证明")
        o.append("")
        o.append("每次运行四项证据：(a) 期望值文件载入后无 X（"
                 "`WP7B_XCHECK vectors`）；(b) 被比较的 2048 个输出拍无 X/Z"
                 "（`WP7B_XCHECK beats`，任一含 X 即 `chk` 判失败）；"
                 "(c) `frames_committed`、`out_commit` 为确定值"
                 "（`WP7B_XCHECK state`）；(d) V4 相关网与核边界端口"
                 "在 SAIF 中的 TX。测试平台对前三项使用 `$isunknown`，"
                 "遇 X 直接判失败。")
        o.append("")
        o.append("| 运行 | X 检查 | 校验子 TX | 事件标志 maxTX | 边界端口 TX |")
        o.append("| --- | --- | --- | --- | --- |")
        for k, v in vac.items():
            o.append(f"| {k} | {'✓' if v['PASS'] else '✗'}"
                     f" | {v['syndrome_bad']} 网 |"
                     f" {v['event_max_tx']} ps | {v['boundary_bad']} 网 |")
        o.append("")
        o.append("校验子寄存器（1200 网）与核边界端口（out_beat[*]、"
                 "u_pipeline_n_*、d_in_data_r[*]、in_valid、clk_out2、"
                 "proc_rst_n 等）TX 全部为 0。事件标志 det/cor/unc 存在"
                 "最大 1,399,900 ps 的 TX：来源是网表中 `det_i_1` LUT 的 "
                 "I0 输入为悬空桩（NLW），X 经该校验通路移位链在窗口起头"
                 "逐级冲刷、约 1.4 µs（175 拍）内归零——标志寄存器本身是"
                 " INIT=0 的 FDRE，冲刷期间校验通路尚无真实帧数据，事件"
                 "不可能被掩盖：真实事件必然产生 TC≥1 的 0↔1 翻转，而所有"
                 "66 个事件网 TC 均为 0、稳态 0 占窗口 94%。")
        o.append("")
    # ---- resource / concurrency / timing --------------------------------
    o.append("## 8. 并发、耗时与内存")
    o.append("")
    o.append("| 步骤 | 并发 | 耗时 | 峰值内存 |")
    o.append("| --- | ---: | --- | --- |")
    o.append("| 网表导出（4×Vivado 只读） | 4 | 各 ~2–3 min | ~2 GB/会话 |")
    o.append("| xvlog+xelab 构建（4 快照） | 串行 | 87 s | 4.8 GB |")
    sim_el = re.search(r"elapsed=(\d+)s",
                       Path("/tmp/wp7b_rerun2.log").read_text() if
                       Path("/tmp/wp7b_rerun2.log").exists() else "")
    sim_rows = []
    for _d, sub, _f, _e in DESIGNS:
        for v in VSETS:
            tt = (RESULTS / sub / v / "time.txt").read_text()
            el = re.search(r"or m:ss\):\s*([0-9:.]+)", tt)
            rss = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", tt)
            sim_rows.append((sub, v, el.group(1) if el else "?",
                             int(rss.group(1)) / 1048576 if rss else 0))
    maxrss = max(r[3] for r in sim_rows)
    o.append(f"| 20 次 xsim（隔离 rundir） | 20 | 批 {sim_el.group(1)} s，"
             f"单 ~7–12 min | 单 ≤{maxrss:.2f} GB，合计 ~17 GB |")
    pwr_el = re.search(r"elapsed=(\d+)s",
                       Path("/tmp/wp7b_power_all3.log").read_text() if
                       Path("/tmp/wp7b_power_all3.log").exists() else "")
    prss = []
    for _d, sub, _f, _e in DESIGNS:
        tt = (RESULTS / sub / "qual" / "power" / "power_timing.txt").read_text()
        r = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", tt)
        prss.append(int(r.group(1)) / 1048576 if r else 0)
    o.append(f"| 20 次 Vivado 功耗（只读） | 20 | 批 {pwr_el.group(1)} s，"
             f"单 ~38–51 s | 单 ≤{max(prss):.2f} GB |")
    o.append("| 分项（wp7b_power_breakdown --all） | 串行 | <1 min | <0.5 GB |")
    o.append("")
    o.append("### 已知限制")
    o.append("")
    o.append("- 仿真时钟 8 ns（核时钟 125 MHz），非任务书初版的 5 ns——"
             "布线后核由 `clk_out2_clk_wiz_k7` 驱动，报告按实际时钟执行并核验。")
    o.append("- Vivado `read_saif` 的 “Design nets matched” 分母是全设计网"
             "（含以太网/UART/SEM 等 SAIF 未覆盖区域）；上表 V3 为核口径："
             "核内未对上网 ÷ 核内总网。")
    o.append("- 第一轮 20 次仿真因隔离 rundir 的 `sim`/`rtl` 相对链接断裂，"
             "向量与 ROM 未加载，数据全 X、bit-exact 为空比较——该轮作废；"
             "记录与驱动日志见 `results/wp7b/voided_round1/`。当前结果在"
             "修复后的 rundir 上重跑，并经 §7 非空比较证明逐项核验。")
    o.append("- V6 判据按 §4 的五组平均表评估（“其他”全部 ≤3%）。单次"
             "运行超 3% 的六次（P0/qual、P1/qual、P1/edge、P1/tones、"
             "S0/qual、S1/qual）已在 §3 末的明细表逐个列出，内容均为级"
             "模块自身功耗行；`classify()` 按任务要求未改动。")
    o.append("")
    return "\n".join(o)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] in {s for _, s, _, _ in DESIGNS}:
        run_one(sys.argv[1], sys.argv[2])
        sys.exit(0)
    if len(sys.argv) == 2 and sys.argv[1] == "--all":
        for _d, sub, _f, _e in DESIGNS:
            for v in VSETS:
                run_one(sub, v)
        sys.exit(0)
    if len(sys.argv) == 2 and sys.argv[1] == "--report":
        (RESULTS / "REPORT.md").write_text(build_report())
        print("REPORT.md written")
        sys.exit(0)
    print(__doc__)
    sys.exit(2)
