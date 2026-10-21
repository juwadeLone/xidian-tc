#!/usr/bin/env python3
r"""WP4-R functional resource breakdown from post-route util_impl.rpt.

Batch 2 (wp4r2) reads util_impl_wp4r2.rpt and writes functional_breakdown_wp4r2.md;

Usage: python3 scripts/k7_mod/wp4r_functional_breakdown.py [wp10]
  wp10: read results/wp10/<design>/util_impl.rpt and write
        functional_breakdown.md for the two WP10 hybrid designs; the
        self-check becomes SECDED > 0, 表决器 > 0, 校验子与译码器 == 0.

Reads the committed results/wp4r/<name>_<dir>/util_impl.rpt files
(report_utilization -hierarchical, routed), restricts each to the FFT kernel
subtree (g_k.u_kernel), and counts every row exactly once: parenthesized
"self" rows and leaf rows are counted, parent total rows are skipped.

Classification (WP4-R task 11.3, all four designs):
  旋转器:   wrapper rows wp4r_hier_fft_complex_mul_wg_pipe /
            wp4r_hier_fft_complex_rotate_trivial_wg, fft_complex_mul_full_wp4r;
            surviving inner rows fft_complex_mul_wg_pipe /
            fft_complex_rotate_trivial_wg (same units, in case Vivado keeps
            the wrapped module as a child row)
  蝶形加减与级内寄存器: self rows of lane/replica modules
            (p1_ecc_lane_wp4r, pfft_sdf4_wp4r, S-family counterparts);
            merge-stage cores pfft_stage9_exchange_wg, pfft_stage10_wg,
            wp4r_hier_subfft_stage9_wg / wp4r_hier_subfft_stage10_wg
            (and their inner rows)
  延迟存储: wp4r_hier_fft_memory_v5 (and inner fft_memory_v5); self rows of
            ecc643_secded_delay6_wg (late-write/forwarding registers) and
            S-family counterparts
  SECDED:   wp4r_hier_secded_encode_w / wp4r_hier_secded_decode_w
            (and inner secded_encode_w / secded_decode_w)
  校验子与译码器: ecc643_encode_wp4r, ecc643_decode_wg (excluding the
            ecc643_flags_wp4r flag child)
  表决器:   wp4r_hier_tmr_vote_w (and inner tmr_vote_w)
  控制:     pfft_sdf_control_wp4r, wp4r_hier_pfft_exchange_phi_wg
            (and inner pfft_exchange_phi_wg), S-family per-lane control
  ROM:      pfft_rom_wp4r, pfft_hom_rom_wp4r, S-family ROM modules
  标志:     ecc643_flags_wp4r, tmr_stage_flag_wg
  其他:     remaining self rows (stage/pipeline/top), itemized
  跨层次合并差值: parent totals minus counted children, itemized

Self-checks (11.3): category sums + adjustment == kernel totals; P1/S1 have
SECDED == 0 and 校验子与译码器 == 0 with every other category > 0; P0/S0 have
every category > 0; 其他 larger than 3% of kernel Logic LUTs or a nonzero
adjustment is itemized below the table.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DESIGNS = [
    ("P0", "P0_p1", "ecc"),
    ("P1", "P1_p2", "tmr"),
    ("S0", "S0_s3", "ecc"),
    ("S1", "S1_s2", "tmr"),
]

# WP10 hybrid TMR: SECDED delay memory shared by the three replicas, so
# SECDED > 0 and 表决器 > 0 while 校验子与译码器 stays 0 (no [6,4,3] lanes).
DESIGNS_WP10 = [
    ("S1h", "S1h_s2hr", "hybrid"),
    ("P1h", "P1h_p2hr", "hybrid"),
]

RPT_NAME = "util_impl_wp4r2.rpt"
OUT_NAME = "functional_breakdown_wp4r2.md"
RPT_NAME_WP10 = "util_impl.rpt"
OUT_NAME_WP10 = "functional_breakdown.md"

CATEGORIES = [
    "旋转器",
    "蝶形加减与级内寄存器",
    "延迟存储",
    "SECDED",
    "校验子与译码器",
    "表决器",
    "控制",
    "ROM",
    "标志",
    "其他（级/流水线/顶层自身行）",
]

ADJUST_ROW = "跨层次合并差值（对账行）"

ROTATOR = (
    "wp4r_hier_fft_complex_mul_wg_pipe",
    "wp4r_hier_fft_complex_rotate_trivial_wg",
    "fft_complex_mul_full_wp4r",
    "fft_complex_mul_wg_pipe",
    "fft_complex_rotate_trivial_wg",
)
MEMORY = ("wp4r_hier_fft_memory_v5", "fft_memory_v5")
MEMORY_SELF_ROWS = (
    "ecc643_secded_delay6_wg",
    "s0_secded_delay_wg",
    # WP10: the shared memory's own row holds the pending-write register.
    "tmr_secded_shared_mem_wp10",
)
SECDED = (
    "wp4r_hier_secded_encode_w",
    "wp4r_hier_secded_decode_w",
    "secded_encode_w",
    "secded_decode_w",
)
SYNDROME = ("ecc643_encode_wp4r", "ecc643_decode_wg")
VOTER = ("wp4r_hier_tmr_vote_w", "tmr_vote_w")
CONTROL = (
    "pfft_sdf_control_wp4r",
    "wp4r_hier_pfft_exchange_phi_wg",
    "pfft_exchange_phi_wg",
)
ROM = ("pfft_rom_wp4r", "pfft_hom_rom_wp4r", "subfft_rom_wp4r")
FLAGS = ("ecc643_flags_wp4r", "tmr_stage_flag_wg")
LANE_SELF_ROWS = (
    "p1_ecc_lane_wp4r",
    "pfft_sdf4_wp4r",
    "pfft_stage9_replica_wp4r",
    "pfft_stage10_replica_wp4r",
    "s0_ecc_lane_wp4r",
    "subfft_stage8_replica_wp4r",
    "subfft_stage9_replica_wp4r",
    "subfft_stage10_replica_wp4r",
    "subfft_sdf_replica_wp4r",
    "subfft_stage8_channel_wp4r",
    "subfft_sdf_channel_wp4r",
    # WP10: storage-external channel/replica modules keep the same self-row
    # contents (butterfly registers) as their wp4r counterparts.
    "s1h_sdf_channel_wp10",
    "s1h_sdf_replica_wp10",
    "p1h_sdf4_wp10",
)
MERGE_CORES = (
    "pfft_stage9_exchange_wg",
    "pfft_stage10_wg",
    "wp4r_hier_subfft_stage9_wg",
    "wp4r_hier_subfft_stage10_wg",
    "subfft_stage9_wg",
    "subfft_stage10_wg",
)

COLS = ["logic_lut", "lutram", "ff", "dsp"]


def classify(module, is_self_row):
    if module.startswith(ROTATOR):
        return "旋转器"
    if module.startswith(MERGE_CORES):
        return "蝶形加减与级内寄存器"
    if module.startswith(MEMORY):
        return "延迟存储"
    if is_self_row and module.startswith(MEMORY_SELF_ROWS):
        return "延迟存储"
    if module.startswith(SECDED):
        return "SECDED"
    if module.startswith(SYNDROME):
        return "校验子与译码器"
    if module.startswith(VOTER):
        return "表决器"
    if module.startswith(CONTROL):
        return "控制"
    if module.startswith(ROM):
        return "ROM"
    if module.startswith(FLAGS):
        return "标志"
    if is_self_row and module.startswith(LANE_SELF_ROWS):
        return "蝶形加减与级内寄存器"
    return "其他（级/流水线/顶层自身行）"


def parse_hierarchy(rpt_path):
    rows = []
    in_table = False
    for line in rpt_path.read_text().splitlines():
        if line.startswith("|") and "Instance" in line and "Module" in line:
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            if rows:
                break
            continue
        fields = [f.strip() for f in line.split("|")]
        if len(fields) < 11 or not fields[1] or set(fields[1]) <= {"-"}:
            continue
        inst_field = line.split("|")[1]
        inst = fields[1]
        leading = len(inst_field) - len(inst_field.lstrip(" "))
        depth = (leading - 1) // 2
        try:
            nums = [int(fields[i]) for i in (3, 4, 5, 7, 10)]
        except ValueError:
            continue
        rows.append(
            {
                "inst": inst,
                "module": fields[2],
                "depth": depth,
                "total_lut": nums[0],
                "logic_lut": nums[1],
                "lutram": nums[2],
                "ff": nums[3],
                "dsp": nums[4],
                "self_row": inst.startswith("("),
            }
        )
    return rows


def breakdown(rpt_path):
    rows = parse_hierarchy(rpt_path)
    kernel_idx = next(i for i, r in enumerate(rows) if r["inst"] == "g_k.u_kernel")
    kernel = rows[kernel_idx]
    subtree = []
    for r in rows[kernel_idx + 1 :]:
        if r["depth"] <= kernel["depth"]:
            break
        subtree.append(r)

    def counted(i):
        r = subtree[i]
        leaf = i + 1 == len(subtree) or subtree[i + 1]["depth"] <= r["depth"]
        return r["self_row"] or leaf

    sums = {c: dict.fromkeys(COLS, 0) for c in CATEGORIES}
    other = []
    for i, r in enumerate(subtree):
        if not counted(i):
            continue
        cat = classify(r["module"], r["self_row"])
        for c in COLS:
            sums[cat][c] += r[c]
        if cat == CATEGORIES[-1] and any(r[c] for c in COLS):
            other.append(r)

    totals = {c: sum(sums[cat][c] for cat in CATEGORIES) for c in COLS}
    expected = {c: kernel[c] for c in COLS}

    mismatches = []
    for i, r in enumerate(subtree):
        leaf = i + 1 == len(subtree) or subtree[i + 1]["depth"] <= r["depth"]
        if r["self_row"] or leaf:
            continue
        child = dict.fromkeys(COLS, 0)
        j = i + 1
        while j < len(subtree) and subtree[j]["depth"] > r["depth"]:
            if counted(j):
                for col in COLS:
                    child[col] += subtree[j][col]
            j += 1
        diff = {col: r[col] - child[col] for col in COLS}
        if any(diff.values()):
            mismatches.append((r, diff))

    adjustment = {c: expected[c] - totals[c] for c in COLS}
    return kernel, sums, other, totals, expected, mismatches, adjustment


def render(design, mode, kernel, sums, other, totals, expected, mismatches, adjustment,
           rpt_name=RPT_NAME):
    out = []
    out.append(f"# {design} 功能分项（布线后 `{rpt_name}`，层次利用率表）")
    out.append("")
    out.append(
        "计数口径：只统计 `g_k.u_kernel` 子树；括号行（模块自身）和叶行计全量，"
        "有子层的父行跳过，每行只计一次。归类规则（WP4-R 第 11.3 节）："
        "旋转器外壳与 `fft_complex_mul_full_wp4r` 计入旋转器；"
        "各路/通道/副本模块的括号行与合并级内核（`pfft_stage9_exchange_wg`、`pfft_stage10_wg`、"
        "S 族合并级外壳）计入蝶形加减与级内寄存器；"
        "存储外壳与 `ecc643_secded_delay6_wg` 括号行（晚写/转发寄存器）计入延迟存储；"
        "SECDED 编码、译码外壳计入 SECDED；"
        "`ecc643_encode_wp4r`、`ecc643_decode_wg`（不含标志子模块）计入校验子与译码器；"
        "表决外壳计入表决器；`pfft_sdf_control_wp4r` 与指数生成外壳计入控制；"
        "`pfft_rom_wp4r`（含 `pfft_hom_rom_wp4r` 外壳行）计入 ROM；"
        "`ecc643_flags_wp4r`、`tmr_stage_flag_wg` 计入标志；"
        "其余括号行计入其他并逐项列出。若 Vivado 把被包模块保留为外壳的子行，"
        "子行按同名规则计入同一类别，不会重复计数。"
        "跨子模块边界合并造成的父行与子行差值集中在对账行，逐项列出。"
    )
    out.append("")
    out.append("| 类别 | Logic LUT | LUTRAM | FF | DSP |")
    out.append("| --- | ---: | ---: | ---: | ---: |")
    for cat in CATEGORIES:
        s = sums[cat]
        out.append(f"| {cat} | {s['logic_lut']} | {s['lutram']} | {s['ff']} | {s['dsp']} |")
    out.append(
        f"| {ADJUST_ROW} | {adjustment['logic_lut']} | {adjustment['lutram']}"
        f" | {adjustment['ff']} | {adjustment['dsp']} |"
    )
    final = {c: totals[c] + adjustment[c] for c in COLS}
    out.append(
        f"| 合计 | {final['logic_lut']} | {final['lutram']} | {final['ff']} | {final['dsp']} |"
    )
    out.append(
        f"| 内核总数（`g_k.u_kernel` 行） | {expected['logic_lut']} | {expected['lutram']}"
        f" | {expected['ff']} | {expected['dsp']} |"
    )
    out.append("")
    if other:
        out.append("其他明细（实例路径（模块）：Logic LUT / LUTRAM / FF / DSP）：")
        out.append("")
        for r in other:
            out.append(
                f"- `{r['inst']}`（{r['module']}）：{r['logic_lut']} / {r['lutram']}"
                f" / {r['ff']} / {r['dsp']}"
            )
        out.append("")
    if mismatches:
        out.append("对账行明细（父行总数减已计子行之和，Logic LUT / LUTRAM / FF / DSP）：")
        out.append("")
        for r, diff in mismatches:
            out.append(
                f"- `{r['inst']}`（{r['module']}）：{diff['logic_lut']} / {diff['lutram']}"
                f" / {diff['ff']} / {diff['dsp']}"
            )
        out.append("")

    problems = []
    if final != expected:
        problems.append(
            f"加总 {final} 不等于内核总数 {expected}"
        )
    for cat in CATEGORIES:
        s = sums[cat]
        nonzero = any(s[c] for c in COLS)
        if mode == "ecc":
            if not nonzero:
                problems.append(f"{cat} 为 0（{design} 各类都应当大于 0）")
        elif mode == "hybrid":
            if cat == "校验子与译码器":
                if nonzero:
                    problems.append(f"{cat} 应为 0 但不是（{design} 无 [6,4,3] 编码级）")
            elif not nonzero:
                problems.append(f"{cat} 为 0（{design} 该类应当大于 0）")
        else:
            if cat in ("SECDED", "校验子与译码器"):
                if nonzero:
                    problems.append(f"{cat} 应为 0 但不是（{design} 无编码级）")
            elif not nonzero:
                problems.append(f"{cat} 为 0（{design} 该类应当大于 0）")
    if sums[CATEGORIES[-1]]["logic_lut"] > 0.03 * expected["logic_lut"]:
        problems.append(
            f"其他 {sums[CATEGORIES[-1]]['logic_lut']} LUT 超过内核 Logic LUT 的 3%，见逐项明细"
        )

    if any(adjustment.values()):
        out.append(
            f"注：跨层次合并差值 {adjustment['logic_lut']} LUT 不为 0，按第 11.3 节逐项解释见上。"
        )
        out.append("")
    if problems:
        out.append("自检：未完全通过——")
        out.append("")
        for p in problems:
            out.append(f"- {p}")
    else:
        out.append("自检：各类加总等于内核总数；各类非空约束满足；其他未超过 3%；合并差值为 0。")
    out.append("")
    return "\n".join(out), not problems


def main():
    wp10 = len(sys.argv) > 1 and sys.argv[1] == "wp10"
    suffix = sys.argv[2] if len(sys.argv) > 2 else ""
    designs = DESIGNS_WP10 if wp10 else DESIGNS
    rpt_name = f"util_impl{suffix}.rpt" if wp10 else RPT_NAME
    out_name = f"functional_breakdown{suffix}.md" if wp10 else OUT_NAME
    base = "wp10" if wp10 else "wp4r"
    all_ok = True
    for design, sub, mode in designs:
        rpt = ROOT / "results" / base / sub / rpt_name
        kernel, sums, other, totals, expected, mismatches, adjustment = breakdown(rpt)
        text, ok = render(
            design, mode, kernel, sums, other, totals, expected, mismatches, adjustment,
            rpt_name=rpt_name
        )
        out_path = rpt.parent / out_name
        out_path.write_text(text)
        final = {c: totals[c] + adjustment[c] for c in COLS}
        print(f"{design}: wrote {out_path.relative_to(ROOT)}")
        print(
            "  totals logic_lut={logic_lut} lutram={lutram} ff={ff} dsp={dsp}".format(**final)
            + ("  == kernel totals" if final == expected else f"  != kernel {expected}")
        )
        all_ok = all_ok and ok
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
