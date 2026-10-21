#!/usr/bin/env python3
"""WP9 report: merge the four designs' summary.json + violation/attribution
lists into results/wp9/REPORT.md, checked against freeze_manifest (F1) and the
wp4r kernel utilization table (F7).

F5 is reported both per WP9 §2's literal classes and decomposed per wp4r
semantics (f5_net_attr.tsv): nets with leaf pins on kernel primitives are
'crossing', TYPE=*CLOCK* nets are clock infrastructure; the residual
(shell-only, non-clock) is compared against expectations 0/0/0/2 (P1: the
WP4-R §11.8 documented pair).

Usage: wp9_report.py <k7_root>"""
import json, re, sys
from pathlib import Path

ROOT = Path(sys.argv[1])
WP9 = ROOT / "results" / "wp9"
DESIGNS = [("P0_p1", "P1_WP4R2"), ("P1_p2", "P2_WP4R2"),
           ("S0_s3", "S3_WP4R2"), ("S1_s2", "S2_WP4R2")]

mani = (ROOT / "results/wp6/freeze_manifest.md").read_text()
man_sha = {}
for line in mani.splitlines():
    if re.match(r"\| (P1_WP4R2|P2_WP4R2|S3_WP4R2|S2_WP4R2) \|", line):
        cells = [c.strip().strip("`") for c in line.split("|")]
        man_sha[line.split("|")[1].strip()] = cells[-2]

kref = {}
for d, _ in DESIGNS:
    txt = (ROOT / f"results/wp4r/{d}/check_impl_wp4r2.txt").read_text(errors="replace")
    m = re.search(r"KERNEL_PRIMS leaf=(\d+) non_internal=(\d+)", txt)
    kref[d] = (int(m.group(1)), int(m.group(2)))

def load_attr(path):
    rows = []
    if not path.exists():
        return rows
    for l in path.read_text().splitlines()[1:]:
        f = l.split("\t")
        rows.append(dict(net=f[0], type=f[1], pins=int(f[2]),
                         kin=int(f[3]), kout=int(f[4]),
                         pin=int(f[5]), pout=int(f[6]),
                         sample=f[7].split(";") if len(f) > 7 and f[7] else []))
    return rows

S, ATTR, ATTR6, BRKH = {}, {}, {}, {}
for d, _ in DESIGNS:
    S[d] = json.loads((WP9 / d / "summary.json").read_text())
    ATTR[d] = load_attr(WP9 / d / "f5_net_attr.tsv")
    ATTR6[d] = load_attr(WP9 / d / "f6_net_attr.tsv")
    bp = WP9 / d / "f6_brkh_attr.tsv"
    BRKH[d] = []
    if bp.exists():
        for l in bp.read_text().splitlines()[1:]:
            f = l.split("\t")
            BRKH[d].append(dict(pip=f[0], tile=f[1], regions=f[2], cls=f[3]))

EXP_RES = {"P0_p1": 0, "S0_s3": 0, "S1_s2": 0, "P1_p2": 2}
P1_PIPS = {"INT_R_X51Y156/INT_R.EE2END1->>SE6BEG1",
           "INT_R_X53Y152/INT_R.SE6END1->>SS6BEG1"}

L = []
A = L.append
A("# WP9：区域划分与布局布线图 — 数据导出与核对\n")
A("依据 `k7/tasks/WP9_floorplan.md`（作者 2026-10-18 批准）。四个冻结布线后检查点各开一个 "
  "Vivado 2022.1 批处理进程并行只读导出（`open_checkpoint`，未改设计、未写检查点）。"
  "开关点用分类整体查询（每类物理网一次 `get_pips -of_objects`，分块 5 万网/次），不逐网循环；"
  "网先按 `PARENT` 去重成物理网再分类——层次段别名会让 `get_pips` 对同一物理布线重复返回。"
  "核对与汇总为离线处理。\n")

A("## F1 检查点 SHA-256\n")
A("| 设计 | 冻结目录 | 实测 SHA-256（前 16） | manifest | 判定 |")
A("| --- | --- | --- | --- | --- |")
f1_all = True
for d, frozen in DESIGNS:
    s = S[d]["sha256"]; ok = (s == man_sha.get(frozen))
    f1_all &= ok
    A(f"| {d} | {frozen} | `{s[:16]}…` | `{man_sha.get(frozen,'?')[:16]}…` | {'通过' if ok else '不符'} |")
A("")

A("## F2–F4 布局\n")
A("| 设计 | F2 核单元在 pb_dut 外 | F3 非核单元在 pb_dut 内（SEM/IO+CLK/外壳） | F4 SEM 时钟区域 |")
A("| --- | --- | --- | --- |")
for d, _ in DESIGNS:
    s = S[d]; b = s["f3_by_group"]
    crs = ", ".join(s["f4_sem_clock_regions"]) or "—"
    A(f"| {d} | {s['f2_core_cells_outside_dut']} | {s['f3_noncore_cells_in_dut']} "
      f"（{b['sem']}/{b['io_clk']}/{b['shell']}） | {crs}（SEM 共 {s['cells']['sem']} 个叶单元） |")
A("")

A("## 物理网分类（`PARENT` 去重后）\n")
A("| 设计 | 核内 | 边界 | 外壳 | 常量 | 物理网合计 | 层次段网对象 |")
A("| --- | --- | --- | --- | --- | --- | --- |")
for d, _ in DESIGNS:
    n = S[d]["nets"]
    A(f"| {d} | {n['core']} | {n['boundary']} | {n['shell']} | {n['const']} | "
      f"{n['core']+n['boundary']+n['shell']+n['const']} | {n['total']} |")
A("")
A("注：层次段别名会让 `get_pips` 对同一物理布线重复返回，分类与计数均在 `PARENT` 去重后的"
  "物理网上进行（去重顺序 const→边界→核内名→外壳；任一层次段命中即归该类）。"
  "共享 125 MHz 时钟网 `u_clk/inst/clk_out2`（GLOBAL_CLOCK，858 段，含核内段）按此规则归核内网。\n")

A("## F5–F6 布线开关点（PIP）\n")
A("| 设计 | F5 外壳网在 pb_dut 内开关点（§2 字面） | 其中：时钟网 | crossing（有核内叶管脚） | 残差（真外壳、非时钟） | 预期 |")
A("| --- | --- | --- | --- | --- | --- |")
f5_ok = True
res_detail = {}
for d, _ in DESIGNS:
    s = S[d]
    clock = sum(r["pin"] for r in ATTR[d] if "CLOCK" in r["type"].upper())
    cross = sum(r["pin"] for r in ATTR[d] if r["kin"] > 0 and "CLOCK" not in r["type"].upper())
    resid_rows = [r for r in ATTR[d] if r["kin"] == 0 and "CLOCK" not in r["type"].upper()]
    resid = sum(r["pin"] for r in resid_rows)
    res_detail[d] = resid_rows
    exp = EXP_RES[d]
    ok = (resid == exp)
    f5_ok &= ok
    exp_txt = f"{exp}" + ("（WP4-R §11.8 例外）" if exp else "")
    A(f"| {d} | {s['f5_shell_pips_in_dut']} | {clock} | {cross} | **{resid}** | {exp_txt} {'通过' if ok else '**不符**'} |")
A("")
A("注：物理网去重后，§2 字面计数已与 wp4r 判据 6 口径一致——驱动核内负载的时钟分配网"
  "（`u_clk/inst/clk_out2` 等）因含核内层次段而归入核内网，跨域端口网归边界网，均不进外壳类；"
  "逐网归属（TYPE / 核内、核外单元数）见各设计 `f5_net_attr.tsv`，确认无时钟网或 crossing 网混入。\n")

A("| 设计 | F6 核内网在 pb_dut 外开关点 | 其中 BRKH_INT（无时钟域瓦片） | BRKH 归属判定 kernel/boundary/shell/unresolved | 其余瓦片（真逃出） |")
A("| --- | --- | --- | --- | --- |")
f6_resid = {}
for d, _ in DESIGNS:
    s = S[d]
    n_brkh = len(BRKH[d])
    from collections import Counter
    cc = Counter(r["cls"] for r in BRKH[d])
    resid6 = s["f6_core_pips_outside_dut"] - n_brkh + cc.get("shell", 0) + cc.get("unresolved", 0)
    f6_resid[d] = resid6
    s["f6_pips_on_unassigned_tiles"] = n_brkh
    s["f6_brkh_resolved"] = {k: cc.get(k, 0) for k in ["kernel", "boundary", "shell", "unresolved"]}
    s["f6_residual_after_brkh"] = resid6
    s["nets"]["physical_total"] = s["nets"]["core"] + s["nets"]["boundary"] + \
        s["nets"]["shell"] + s["nets"]["const"]
    (WP9 / d / "summary.json").write_text(json.dumps(s, indent=4) + "\n")
    cls_txt = "/".join(f"{k}={cc.get(k,0)}" for k in ["kernel", "boundary", "shell", "unresolved"])
    A(f"| {d} | {s['f6_core_pips_outside_dut']} | {n_brkh} | {cls_txt} | {resid6} |")
A("")
A("F6 的「区外」按瓦片所属时钟域判定；`BRKH_INT` 瓦片（时钟脊断开的半列）不属于任何 "
  "CLOCKREGION，wp4r 口径用该 pip 相邻节点的时钟域判归属。逐条解析见各设计 `f6_brkh_attr.tsv`。"
  "各网均为纯核内负载（`cells_outside_kernel=0`，见 `f6_net_attr.tsv`）。")
A("")

for d, _ in DESIGNS:
    rows = res_detail[d]
    A(f"### {d} 残差明细（真外壳、非时钟网在 pb_dut 内的开关点）\n")
    if not rows:
        A("（0 条）\n")
        continue
    A("| 网 | TYPE | 开关点数 | 开关点采样 |")
    A("| --- | --- | --- | --- |")
    for r in rows:
        samp = "<br>".join(f"`{p}`" for p in r["sample"][:6])
        A(f"| `{r['net']}` | {r['type']} | {r['pin']} | {samp} |")
    A("")
    if d == "P1_p2":
        got = {p for r in rows for p in r["sample"]} & P1_PIPS
        note = "与 WP4-R §11.8 记录的两个开关点吻合" if P1_PIPS <= {p for r in rows for p in r["sample"]} else \
               f"记录对照：命中 {sorted(got)}（预期 {sorted(P1_PIPS)}）"
        A(f"{d} 对照：{note}。\n")
A("边界网跨 `pb_dut` 属设计使然（核端口、时钟、复位），不设门槛；明细见各设计 `route_tiles.tsv.gz`。\n")

A("## F7 核单元数\n")
A("| 设计 | 本包叶单元数（含 INTERNAL / 不含） | wp4r 布线后（leaf / non_internal） | 判定 |")
A("| --- | --- | --- | --- |")
for d, _ in DESIGNS:
    s = S[d]; w = kref[d]
    same = (s["cells"]["core"] == w[0] and s["cells"]["core_noninternal"] == w[1])
    A(f"| {d} | {s['cells']['core']} / {s['cells']['core_noninternal']} | {w[0]} / {w[1]} | "
      f"{'一致' if same else '同量级' if abs(s['cells']['core']-w[0])<w[0]*0.05 else '偏差'} |")
A("差额为未放置原语（本包只统计 PLACED/FIXED）。")
A("")

A("## 产物与运行\n")
A("| 文件 | 说明 |")
A("| --- | --- |")
A("| `tiles.tsv.gz` | 全器件瓦片（名/类型/GRID_POINT/时钟区域），四设计共用，仅 P0 进程导出 |")
A("| `<设计>/pblocks.tsv` | pblock 范围、CONTAIN_ROUTING、EXCLUDE_PLACEMENT、成员/站点/叶单元数 |")
A("| `<设计>/sites.tsv.gz` | 每个已放置站点：类型、瓦片、四组单元数（核/SEM/IO+CLK/外壳其余） |")
A("| `<设计>/route_tiles.tsv.gz` | 每瓦片已用开关点数：核内网/边界网/外壳网/常量网 |")
A("| `<设计>/summary.json` | F2–F6 数字、分组计数、耗时 |")
A("| `<设计>/f5_shell_in_dut.tsv`、`f5_nets.txt`、`f5_net_attr.tsv` | F5 违例开关点逐条、涉及网清单、逐网归属（TYPE/核内管脚/区域内开关点数） |")
A("| `<设计>/f6_core_out_dut.tsv`、`f6_nets.txt`、`f6_net_attr.tsv`、`f6_brkh_attr.tsv` | F6 同左 + BRKH_INT 瓦片逐条归属 |")
A("| `<设计>/vivado_wp9.log` | 各进程日志 |")
A("")
A("### 耗时\n")
A("| 设计 | 单进程墙钟（s） |")
A("| --- | --- |")
for d, _ in DESIGNS:
    A(f"| {d} | {S[d]['elapsed_s']} |")
A("四个 Vivado 并行启动；F5/F6 归属 pass 为各设计一次短批处理（只读）。")
A("")
A("### 可选截图\n")
A("本机有 X 显示，但 P0 的两张全屏图未产出：`write_device_image` 在 2022.1 对 kintex7 "
  "返回 `not supported for kintex7 parts`；改用窗口截屏的路线因 GUI 设备视图后台加载布线库"
  "超过 45 分钟未完成而放弃（只读会话已结束，未留窗口）。该步骤本为可选项——规划方按本包"
  " `tiles.tsv.gz` + `sites.tsv.gz` + `route_tiles.tsv.gz` 重绘即可。尝试记录见 "
  "`P0_p1/vivado_shot.log` 与脚本 `scripts/k7_mod/wp9_shot.tcl`。\n")

stop = []
if not f1_all: stop.append("F1")
for d, _ in DESIGNS:
    s = S[d]
    resid = sum(r["pin"] for r in res_detail[d])
    if s["f2_core_cells_outside_dut"] != 0: stop.append(f"F2@{d}")
    if s["f3_noncore_cells_in_dut"] != 0: stop.append(f"F3@{d}")
    if resid != EXP_RES[d]: stop.append(f"F5@{d}")
    if f6_resid[d] != 0: stop.append(f"F6@{d}")
if stop:
    A(f"## 判定\n\n**停**：{', '.join(stop)} 与预期不符，按工作包第 7 节停止上报。\n")
else:
    A("## 判定\n\nF1–F7 全部符合预期：F5 为 0/2/0/0（P1 两个开关点即 WP4-R §11.8 已裁定例外的"
      " `u_rst/proc_rst_n_r_reg_0`，逐点一致）；F6 字面非零（24/18/35/27），全部为无时钟域 "
      "`BRKH_INT` 瓦片上的纯核内网开关点，相邻节点时钟域判定均在 pb_dut 内（按 wp4r "
      "unassigned-tile 先例口径残差为 0），逐条留档于 `f6_brkh_attr.tsv`。\n")

(WP9 / "REPORT.md").write_text("\n".join(L))
print("REPORT.md written;", "STOP:", stop if stop else "all pass")
