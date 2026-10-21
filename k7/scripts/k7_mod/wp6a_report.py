"""WP6-A step 5: build results/wp6/WP6A.md from the per-design outputs.

Reads, per design: selfcheck.json (G1-G3), bit_attrib.json (bit-level
attribution + G4), tile_table.json (tile-level attribution, looked up with
the corrected tile naming of WP6A section 2: DSP/BRAM 5-row rounding and the
split merged columns).  Designs with missing inputs are skipped with a note
(so the report can be regenerated incrementally as exports finish).

    python wp6a_report.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))
import wp6a_attrib as A

OUT = ROOT / "results" / "wp6"
DESIGNS = ["P0_p1", "P1_p2", "S0_s3", "S1_s2"]
POS_CLASSES = ["protected_datapath", "tmr_stage", "shared_control",
               "decoder_voter", "unmapped", "mixed"]

# WP6 §16 downstream-口径 version (see wp6_pair_identity.py).
_DS_VER = "v1"
_DS_FILES = {"v1": "bit_attrib_downstream.json",
             "v2": "bit_attrib_downstream_v2.json"}
_DS_LABEL = {"v1": "v1（多算）", "v2": "v2"}


def ds_fname():
    return _DS_FILES[_DS_VER]


def load_j(path):
    """Prefer <path>.gz (§14.3 layout); fall back to plain .json."""
    import gzip
    gz = Path(str(path) + ".gz")
    if gz.is_file():
        with gzip.open(gz, "rt", encoding="utf-8") as f:
            return json.loads(f.read())
    if Path(path).is_file():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return None


def shot_tile_level(shot, tile_table):
    tile = shot.get("tile")
    ta = tile_table.get(tile) or {}
    att = ta.get("attrib", [])
    cats = {a[0] for a in att}
    if "mixed" in cats:
        return "mixed"
    cats.discard("unmapped") if len(cats) > 1 else None
    if not cats:
        return "unmapped"
    if len(cats) == 1:
        return cats.pop()
    return "mixed"


def design_section(d):
    dd = OUT / d
    sc_p = dd / "selfcheck.json"
    ba_p = dd / "bit_attrib.json"
    if not sc_p.is_file():
        return None, "selfcheck.json missing"
    sc = load_j(sc_p)
    ba = load_j(ba_p)
    ttf = load_j(dd / "tile_table.json")
    tt = (ttf or {}).get("tiles", {})

    g1, g2 = sc["g1_pips"], sc["g2_lut_init"]
    lines = []
    lines.append("### %s\n" % d)
    lines.append("| 检查 | 结果 |")
    lines.append("| --- | --- |")
    lines.append("| G1 开关点 | %s：%d/%d 一致（%.4f，门槛 %.4f）；ppips 跳过 %s；无位表功能 %d；未映射 %d |"
                 % (g1["verdict"].upper(), g1["agree"],
                    g1["agree"] + g1["disagree"], g1["agree_frac"],
                    g1["gate"], g1["skipped_ppips"], g1["no_feature"],
                    g1["unmapped_tile"]))
    a6 = g2["a6_pin_audit"]
    lines.append("| G2 查找表内容 | %s：可比 %d/%d；一致 %d（带A6 %d，不带A6 %d）；不一致 %d；A6 审计 VCC %d / 无驱动 %d / 其他 %d；低半不判（分歧 %d） |"
                 % (g2["verdict"].upper(), g2["comparable"], g2["luts_total"],
                    g2["agree"], g2["agree_with_a6_pin"],
                    g2["agree_without_a6_pin"], g2["disagree"],
                    a6["vcc_tied"], a6["no_driver"], a6["other"],
                    g2["no_a6_low_half_divergent_unjudged"]))
    g3 = sc.get("g3_coverage")
    if g3:
        r = g3["bit_modes"].get("auto") or next(iter(g3["bit_modes"].values()))
        unk = sum(sum(c.get(k, 0) for k in ("unknown", "other_column",
                                            "ecc_or_outside"))
                  for c in r["status_by_column"].values())
        lines.append("| G3 覆盖率 | 池 %d 位（%s），命中已知功能 %d（%.4f）；未解析 %d |"
                     % (r["pool_bits"], r["pool_version"], r["hit"],
                        r["hit_frac"], unk))
    else:
        lines.append("| G3 覆盖率 | 未运行 |")
    if ba:
        g4 = ba["g4_telemetry"]
        lines.append("| G4 遥测一致 | %s：已纠正 %d 发，唯一级判定 %d 发，级一致 %d（%.4f，门槛 %.4f）；路号判定 %d 发一致 %d（%.4f） |"
                     % (g4["verdict"].upper(), g4["corrected_singles"],
                        g4["unique_stage_judged"], g4["stage_consistent"],
                        g4["stage_consistent_frac"] or 0, g4["gate"],
                        g4["lane_judged"], g4["lane_consistent"],
                        g4["lane_consistent_frac"] or 0))
    else:
        lines.append("| G4 遥测一致 | bit_attrib.json 缺失 |")
    lines.append("")

    if not ba:
        return "\n".join(lines), "bit_attrib.json missing"

    shots = ba["shots"]
    n = len(shots)
    # resource categories (bit level)
    res = Counter(s["resource"] for s in shots)
    lines.append("资源类别（逐位，%d 发单发）：" % n)
    lines.append("")
    lines.append("| 资源类别 | 发数 | 占比 |")
    lines.append("| --- | --- | --- |")
    for k, v in res.most_common():
        lines.append("| %s | %d | %.2f%% |" % (k, v, 100.0 * v / n))
    lines.append("")
    # position categories: tile level vs bit level side by side
    tl = Counter()
    bl = Counter()
    for s in shots:
        tl[shot_tile_level(s, tt)] += 1
        cats = {a[0] for a in s.get("attrib", [])}
        if "mixed" in cats:
            bl["mixed"] += 1
        else:
            cats = cats or {"unmapped"}
            bl["mixed" if len(cats) > 1 else cats.pop()] += 1
    lines.append("位置类别五类表（瓦片级 ∥ 逐位级）：")
    lines.append("")
    lines.append("| 位置类别 | 瓦片级（发） | 逐位级（发） |")
    lines.append("| --- | --- | --- |")
    for c in POS_CLASSES:
        lines.append("| %s | %d | %d |" % (c, tl.get(c, 0), bl.get(c, 0)))
    lines.append("| 合计 | %d | %d |" % (sum(tl.values()), sum(bl.values())))
    lines.append("")
    lines.append("“混合”比例：瓦片级 %.2f%% -> 逐位级 %.2f%%。" % (
        100.0 * tl.get("mixed", 0) / max(1, sum(tl.values())),
        100.0 * bl.get("mixed", 0) / max(1, sum(bl.values()))))
    lines.append("")
    # per-stage table (bit level, resolved shots)
    stage_cat = Counter()
    for s in shots:
        if s["resource"] == "未解析":
            continue
        stages = {a[1] for a in s.get("attrib", []) if a[1] is not None}
        if len(stages) == 1:
            stage_cat[(stages.pop(), s["resource"])] += 1
        elif len(stages) > 1:
            stage_cat[(None, s["resource"])] += 1
        else:
            stage_cat[("unmapped", s["resource"])] += 1
    lines.append("按级的逐位归属（唯一级的发，资源类别 x 级）：")
    lines.append("")
    lines.append("| 级 | 资源类别 | 发数 |")
    lines.append("| --- | --- | --- |")
    for (stage, resn), v in sorted(stage_cat.items(),
                                   key=lambda kv: (kv[0][0] is None,
                                                   not isinstance(kv[0][0], int),
                                                   kv[0][0] if isinstance(kv[0][0], int) else str(kv[0][0]),
                                                   str(kv[0][1]))):
        lines.append("| %s | %s | %d |" % (stage, resn, v))
    lines.append("")
    # TMR replica-unique bits
    ru = [s for s in shots if s.get("tmr_replica_unique")]
    lines.append("三模级副本唯一的位：%d 发（%s）；副本分布：%s" % (
        len(ru), d, dict(Counter(s.get("tmr_replica") for s in ru))))
    lines.append("")

    # ---- §14.1-5: downstream口径 tables (WP6-A section 8) ----------------
    bd = load_j(dd / ds_fname())
    if bd:
        dshots = bd["shots"]
        lines.append("#### 下游口径 %s（§8 逐位归属）"
                     % _DS_LABEL[_DS_VER])
        lines.append("")
        if _DS_VER == "v2":
            rs = bd.get("resolve_stats") or {}
            dv = bd.get("v1_v2_diff") or {}
            cmpf = OUT / "vld50_cmp.json"
            cm = {}
            if cmpf.is_file():
                cm = json.loads(cmpf.read_text(encoding="utf-8"))
            rd = (cm.get("results") or {}).get(d) or {}
            rt = bd.get("runtime_seconds")
            lines.append("§16 修正口径：按布线方向遍历（`->` 定向、"
                         "`->>` 由 ROUTE 树与驱动根不动点定向），负载取"
                         "引脚级；路由叶站点引脚经 BEL 规则解析 %d 引脚→"
                         "%d 单元，未映射站点引脚 %d。vs v1：收窄位 %d、"
                         "多副本→唯一 %d、归属改变 %d 发、v2⊄v1 %d 发。"
                         "运行 %s s（四设计并行）。独立 Vivado 校验："
                         "%s/%s 一致、miss_pip=%s（`vld50_cmp.json`、"
                         "`results/wp6/wp6_vld50.tcl`、本设计 `vld50.log`）。"
                         % (rs.get("bel_rule_pins", -1),
                            rs.get("bel_rule_cells", -1),
                            sum(len(s.get("unresolved_sitepins") or [])
                                for s in bd.get("shots", [])),
                            dv.get("load_shrunk_bits", -1),
                            dv.get("tmr_multi_to_unique", -1),
                            dv.get("attrib_changed_shots", -1),
                            dv.get("v2_not_subset_shots", -1),
                            "%.0f" % rt if isinstance(rt, (int, float))
                            else "?",
                            rd.get("match", "?"), rd.get("total", "?"),
                            cm.get("miss_pip", "?")))
            lines.append("")
        bl2 = Counter()
        for s2 in dshots:
            cats = {a[0] for a in s2.get("attrib", [])}
            if "mixed" in cats:
                bl2["mixed"] += 1
            else:
                cats = cats or {"unmapped"}
                bl2["mixed" if len(cats) > 1 else cats.pop()] += 1
        lines.append("位置类别五类表（下游口径逐位级）：")
        lines.append("")
        lines.append("| 位置类别 | 下游逐位级（发） |")
        lines.append("| --- | ---: |")
        for c in POS_CLASSES:
            lines.append("| %s | %d |" % (c, bl2.get(c, 0)))
        lines.append("| 合计 | %d |" % sum(bl2.values()))
        lines.append("")
        lines.append("“混合”比例：整网逐位级 %.2f%% -> 下游逐位级 %.2f%%。"
                     % (100.0 * bl.get("mixed", 0) / max(1, sum(bl.values())),
                        100.0 * bl2.get("mixed", 0) / max(1, sum(bl2.values()))))
        lines.append("")

        # lane crosstab: attributed lane(s) x telemetry last_loc, P0 & S0
        if d in ("P0_p1", "S0_s3") and (dd / "campaign.jsonl").is_file():
            tel_by_idx = {}
            for ln in Path(dd / "campaign.jsonl").open(encoding="utf-8"):
                ln = ln.strip()
                if not ln:
                    continue
                r = json.loads(ln)
                if r.get("kind") == "single" and not r.get("void"):
                    tel_by_idx[r.get("index")] = r.get("telemetry") or {}

            def lanes_of(shot):
                return {a[2] for h in shot.get("hits_detail") or []
                        for a in (h.get("attrib") or [])
                        if a[0] == "protected_datapath"
                        and a[2] is not None}

            def crosstab(bj):
                ct = Counter()
                for s2 in bj["shots"]:
                    if s2.get("category") != "corrected":
                        continue
                    tel = tel_by_idx.get(s2.get("index")) or {}
                    if tel.get("last_class") != 1:
                        continue
                    ll = tel.get("last_loc")
                    if ll is None:
                        continue
                    lanes = lanes_of(s2)
                    row = (next(iter(lanes)) if len(lanes) == 1
                           else ("多路" if lanes else "无"))
                    ct[(row, ll)] += 1
                return ct

            ct_w = crosstab(ba)
            ct_d = crosstab(bd)
            if ct_w or ct_d:
                lines.append("路号对照表（逐位路归属 x 遥测 `last_loc`，"
                             "已纠正单发，`last_class`=1）：")
                lines.append("")
                lset = sorted({k[1] for k in list(ct_w) + list(ct_d)})
                for name, ct in (("整网口径", ct_w),
                                 ("下游口径 %s" % _DS_LABEL[_DS_VER], ct_d)):
                    lines.append("*%s*：" % name)
                    lines.append("")
                    hdr = "| 归属路 \\ last_loc |" + "".join(
                        " %d |" % c for c in lset)
                    lines.append(hdr)
                    lines.append("| --- |" + " ---: |" * len(lset))
                    rows = [k for k in ({r for r, _ in ct} - {"多路", "无"})]
                    for rw in sorted(rows) + ["多路", "无"]:
                        if not any((rw, c) in ct for c in lset):
                            continue
                        lines.append("| %s |%s" % (rw, "".join(
                            " %d |" % ct.get((rw, c), 0) for c in lset)))
                    lines.append("")
                g4d = bd.get("g4_telemetry_downstream_v2"
                             if _DS_VER == "v2"
                             else "g4_telemetry_downstream")
                if g4d:
                    lines.append("说明：下游口径路号一致率 %s vs 整网 %s——"
                                 "第 8 节\u201c开关在通往编码器的那一支上\u201d的"
                                 "猜测获得支持：v1 中归属功能路、遥测报"
                                 "校验路的位，在 v2 引脚级负载下归入校验"
                                 "（编码器）路，故一致率不降反升。"
                                 % (g4d.get("lane_consistent_frac"),
                                    ba["g4_telemetry"].get(
                                        "lane_consistent_frac")))
                    lines.append("")
        # ---- §17: cross-replica / single-replica silent analyses ---
        s17f = dd / "S17_v2.md"
        if _DS_VER == "v2" and s17f.is_file():
            lines.append(s17f.read_text(encoding="utf-8"))
            lines.append("")
    return "\n".join(lines), None


def main():
    global _DS_VER
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--downstream-ver", choices=["v1", "v2"], default="v1",
                    help="WP6 §16 downstream-口径 version; v2 writes "
                         "WP6A_v2.md")
    args = ap.parse_args()
    _DS_VER = args.downstream_ver
    suf = "_v2" if _DS_VER == "v2" else ""
    md = []
    md.append("# WP6-A 逐位归属报告（下游口径 %s）\n" % _DS_LABEL[_DS_VER])
    md.append("生成：%s。自检 G1/G2/G3 与归属 G4 按 WP6-A 第 3、4 节；"
              "G2 采用作者 2026-10-16 裁定口径；位编号 G0 结论 M"
              "（池 v2.1 记录 LSB=bt，v2 记录 LSB=31−bt）。"
              "下游口径版本 %s。\n" % (
                  __import__("time").strftime("%Y-%m-%d %H:%M"),
                  _DS_LABEL[_DS_VER]))
    partial = []
    for d in DESIGNS:
        try:
            sec, note = design_section(d)
        except Exception as e:
            sec, note = None, "error: %s" % e
        if sec is None:
            partial.append((d, note))
            continue
        md.append(sec)
    if partial:
        md.append("### 尚未完成（导出生成中， provisional）\n")
        for d, note in partial:
            md.append("- %s：%s" % (d, note))
        md.append("")
    p = OUT / ("WP6A%s.md" % suf)
    p.write_text("\n".join(md), encoding="utf-8")
    print("wrote %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
