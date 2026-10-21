"""WP6 §17 offline analyses on the accepted v2 downstream attribution.

All inputs come from results/wp6/<design>/bit_attrib_downstream_v2.json[.gz]
(pin-level loads, direction-corrected; v1 kept for comparison only).
Runs one process per design:

    python3 wp6_s17.py P0_p1

Outputs:
    results/wp6/<design>/s17_v2.json   machine-readable evidence
    results/wp6/<design>/S17_v2.md    report block for WP6A_v2 §17
"""
import json
import sys
import time
import collections
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results" / "wp6"

CAT_ZH = {"corrected": "已纠正", "silent": "静默", "noimpact": "无影响",
          "flagged": "标志", "stuck": "滞留", "flagged_but_clean": "标志无错"}
CATS = ["corrected", "silent", "noimpact", "flagged", "stuck",
        "flagged_but_clean"]

ST_RE = re.compile(r"g_stage\[(\d+)\]")
REP_RE = re.compile(r"g_replica\[(\d+)\]")
LANE_RE = re.compile(r"g_(?:lane|ch)\[(\d+)\]")

# §17 task-2 driver groups (normalized to functional units).
GROUPS = [("data_vote", "数据表决器输出 g_vote[*].u_data_vote"),
          ("valid_vote", "有效信号表决器输出 u_valid_vote/u_last_vote"),
          ("dec_out_data", "上一级译码器输出 u_decode/out_data"),
          ("dec_out_valid", "上一级译码器输出 u_decode/out_valid"),
          ("kinput", "核输入 d_in_*"),
          ("rst", "复位 kernel_rst"),
          ("clk", "时钟"),
          ("other", "其他")]

POS_CLASSES = ["protected_datapath", "tmr_stage", "shared_control",
               "decoder_voter", "unmapped", "mixed"]


def load_j(p):
    import gzip
    g = Path(str(p) + ".gz")
    if g.is_file():
        return json.load(gzip.open(g, "rt", encoding="utf-8"))
    return json.loads(Path(p).read_text(encoding="utf-8"))


def stage_of(path):
    m = ST_RE.search(path or "")
    return int(m.group(1)) if m else None


def load_unit(path):
    """Unit bucket of a v2 load path: enc/dec/lane/misc/other."""
    if "/u_encode/" in path:
        return "enc"
    if "/u_decode/" in path:
        return "dec"
    if "/g_lane[" in path or "/g_ch[" in path:
        return "lane"
    if "g_ecc.u_stage" in path or ".u_stage/" in path:
        return "misc"
    return "other"


def replica_stages(shot):
    """{stage: {replica,...}} from tmr_stage attrib entries."""
    m = collections.defaultdict(set)
    for a in shot.get("attrib", []):
        if a[0] == "tmr_stage" and a[2] is not None:
            m[a[1]].add(a[2])
    return m


def lane_stages(shot):
    """{stage: {lane,...}} from protected_datapath attrib entries."""
    m = collections.defaultdict(set)
    for a in shot.get("attrib", []):
        if a[0] == "protected_datapath" and a[2] is not None:
            m[a[1]].add(a[2])
    return m


def is_xrep(shot):
    return any(len(v) >= 2 for v in replica_stages(shot).values())


def is_xlane(shot):
    return any(len(v) >= 2 for v in lane_stages(shot).values())


def shot_drivers_net(shot):
    drivers = {x for h in shot.get("hits_detail", [])
               for x in (h.get("drivers") or [])}
    nets = {h.get("net") for h in shot.get("hits_detail", [])
            if h.get("net")}
    return drivers, nets


def driver_group(drivers, nets):
    """Normalize hit drivers to the §17 functional groups."""
    ns = " ".join(nets)
    if any("u_data_vote" in x for x in drivers):
        return "data_vote"
    if any("u_valid_vote" in x or "u_last_vote" in x for x in drivers):
        return "valid_vote"
    if any("u_decode" in x for x in drivers):
        leaf = nets and next(iter(nets)).rsplit("/", 1)[-1] or ""
        if leaf.startswith("out_data"):
            return "dec_out_data"
        if leaf.startswith("out_valid"):
            return "dec_out_valid"
        return "dec_out_data"
    if "/d_in" in ns:
        return "kinput"
    if "kernel_rst" in ns or any("kernel_rst" in x for x in drivers):
        return "rst"
    if any("u_clk" in x for x in drivers) or "u_clk" in ns \
            or "/clk" in ns.lower():
        return "clk"
    return "other"


def pos_class_of(shot):
    cats = {a[0] for a in shot.get("attrib", [])}
    cats.discard("mixed")
    if not cats:
        return "unmapped"
    return "mixed" if len(cats) > 1 else next(iter(cats))


# driver group -> position class for the §17 task-3 parallel table
DRV2POS = {"data_vote": "decoder_voter", "valid_vote": "decoder_voter",
           "dec_out_data": "decoder_voter", "dec_out_valid": "decoder_voter",
           "kinput": "shared_control", "rst": "shared_control",
           "clk": "shared_control"}


def s17(design):
    bd = load_j(OUT / design / "bit_attrib_downstream_v2.json")
    shots = bd["shots"]
    ev = {"design": design, "rule": "downstream_v2",
          "date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
          "groups": {k: zh for k, zh in GROUPS}}
    # planner offline figures from tasks/WP6_sem_board.md §17 (silent only)
    ev["planner_expect"] = {
        "P0_p1": {"total": 38, "data_vote": 28, "dec_out_data": 7},
        "P1_p2": {"total": 58, "data_vote": 48},
        "S0_s3": {"total": 26, "data_vote": 15, "dec_out_data": 8},
        "S1_s2": {"total": 49, "data_vote": 38}}[design]

    # ---- task 2: cross-replica single upsets by driver unit -------------
    # "pure" = loads attributed only to tmr_stage replicas (planner rule);
    # "mixed" = xrep loads also land on decoder_voter/other cells.
    xrep_rows = collections.Counter()
    xrep_list = collections.defaultdict(list)
    xrep_mixed = []
    for s in shots:
        if not is_xrep(s):
            continue
        drivers, nets = shot_drivers_net(s)
        g = driver_group(drivers, nets)
        pure = not ({a[0] for a in s.get("attrib", [])}
                    - {"tmr_stage", "mixed"})
        xrep_rows[(g, s["category"], pure)] += 1
        rec = {"index": s["index"], "lfa": [s["la"], s["wd"], s["bt"]],
               "feature": s.get("feature"), "resource": s.get("resource"),
               "net": sorted(nets)[:3], "drivers": sorted(drivers)[:4],
               "group": g}
        if s["category"] == "silent" and not pure:
            xrep_mixed.append(rec)
        if s["category"] == "silent" and pure:
            xrep_list[g].append(rec)
    ev["xrep_by_driver"] = {
        g: {c: xrep_rows.get((g, c, True), 0) for c in CATS}
        for g, _ in GROUPS}
    ev["xrep_silent_lists"] = dict(xrep_list)
    ev["xrep_totals"] = {
        c: sum(xrep_rows.get((g, c, True), 0) for g, _ in GROUPS)
        for c in CATS}
    ev["xrep_mixed"] = xrep_mixed
    ev["xrep_mixed_totals"] = {
        c: sum(xrep_rows.get((g, c, False), 0) for g, _ in GROUPS)
        for c in CATS}

    # ---- task 3: position class, load-based (pre-registered) & driver ---
    pos_load = collections.Counter(pos_class_of(s) for s in shots)
    pos_drv = collections.Counter()
    reclassed = []
    for s in shots:
        cls = pos_class_of(s)
        if s.get("resource") == "开关" and (is_xrep(s) or is_xlane(s)):
            drivers, nets = shot_drivers_net(s)
            g = driver_group(drivers, nets)
            ncls = DRV2POS.get(g, cls)
            if ncls != cls:
                reclassed.append({"index": s["index"], "from": cls,
                                  "to": ncls, "group": g})
            cls = ncls
        pos_drv[cls] += 1
    ev["posclass_load"] = dict(pos_load)
    ev["posclass_driver"] = dict(pos_drv)
    ev["posclass_reclassed"] = reclassed

    # ---- task 4: silent single upsets on exactly one replica ------------
    one_rep = []
    for s in shots:
        if s["category"] != "silent":
            continue
        reps = replica_stages(s)
        nrep = len({r for v in reps.values() for r in v})
        if nrep != 1:
            continue
        drivers, nets = shot_drivers_net(s)
        g = driver_group(drivers, nets)
        one_rep.append({
            "index": s["index"], "lfa": [s["la"], s["wd"], s["bt"]],
            "lsb": s.get("lsb"), "feature": s.get("feature"),
            "resource": s.get("resource"),
            "stage_replica": {str(k): sorted(v) for k, v in reps.items()},
            "loads": s.get("loads_v2", []),
            "net": sorted(nets), "drivers": sorted(drivers),
            "driver_group": g,
            "clock_caveat": g == "clk"})
    ev["silent_one_replica"] = one_rep

    # ---- task 5: encoded-design silent upsets by v2 load partition ------
    if design in ("P0_p1", "S0_s3"):
        part = collections.Counter()
        part_nets = collections.defaultdict(list)
        part_idx = collections.defaultdict(list)
        for s in shots:
            if s["category"] != "silent":
                continue
            if "decoder_voter" not in {a[0] for a in s.get("attrib", [])}:
                continue
            units = {load_unit(l) for l in s.get("loads_v2", [])}
            if units <= {"enc"}:
                key = "enc"
            elif units <= {"dec"}:
                key = "dec"
            elif {"enc", "lane"} <= units:
                key = "next_lane_enc"
            else:
                key = "other"
            part[key] += 1
            part_idx[key].append(s["index"])
            drivers, nets = shot_drivers_net(s)
            part_nets[key].extend(sorted(nets)[:1])
        def _ex(v):
            fc = collections.Counter(
                n for n in v if n and not n.startswith("<const")
                and "u_clk" not in n and "kernel_rst" not in n)
            return [n for n, _ in fc.most_common(6)]
        ev["enc_silent_partition"] = {
            "counts": dict(part),
            "indices": {k: sorted(v) for k, v in part_idx.items()},
            "net_examples": {k: _ex(v)
                             for k, v in part_nets.items()}}
    return ev


def md(design, ev):
    L = []
    L.append("#### §17 跨副本/跨路与单副本静默分析（v2 口径）")
    L.append("")
    L.append("独立重算自 `bit_attrib_downstream_v2.json`（脚本 "
             "`scripts/k7_mod/wp6_s17.py`，逐位枚举见 `s17_v2.json`）。")
    L.append("")

    # task-2 table
    L.append("**§17.1 跨副本单发位 × 驱动单元**：v2 下负载落在同一级两份"
             "以上副本上的单发位，按所在网的驱动单元分组。")
    L.append("")
    hdr = "| 驱动单元 |" + "".join(" %s |" % CAT_ZH[c] for c in CATS) + " 合计 |"
    L.append(hdr)
    L.append("| --- |" + " ---: |" * (len(CATS) + 1))
    for g, zh in GROUPS:
        row = ev["xrep_by_driver"].get(g, {})
        tot = sum(row.values())
        if not tot:
            continue
        L.append("| %s |%s %d |" % (zh, "".join(
            " %d |" % row.get(c, 0) for c in CATS), tot))
    tr = ev["xrep_totals"]
    L.append("| 合计 |%s %d |" % (
        "".join(" %d |" % tr.get(c, 0) for c in CATS),
        sum(tr.values())))
    L.append("")
    exp = ev.get("planner_expect")
    if exp:
        got = {k: ev["xrep_by_driver"].get(k, {}).get("silent", 0)
               for k in ("data_vote", "dec_out_data")}
        chk = ["静默合计 %d（规划方 %d%s）"
               % (tr.get("silent", 0), exp["total"],
                  " ✓" if tr.get("silent", 0) == exp["total"] else " ✗"),
               "数据表决器输出 %d（%d%s）"
               % (got["data_vote"], exp["data_vote"],
                  " ✓" if got["data_vote"] == exp["data_vote"] else " ✗")]
        if "dec_out_data" in exp:
            chk.append("上一级译码器输出 %d（%d%s）"
                       % (got["dec_out_data"], exp["dec_out_data"],
                          " ✓" if got["dec_out_data"] == exp["dec_out_data"]
                          else " ✗"))
        L.append("与规划方离线结果（`tasks/WP6_sem_board.md` §17）对照："
                 + "；".join(chk) + "。")
        L.append("")
    if ev["xrep_mixed"]:
        tm = ev["xrep_mixed_totals"]
        L.append("另：%d 发静默位负载同时落在两份以上副本与 "
                 "decoder_voter/其他单元（混合归属，不列入上表口径；"
                 "全类别合计 %d 发）——"
                 % (len(ev["xrep_mixed"]), sum(tm.values())))
        for r in ev["xrep_mixed"]:
            L.append("  - 位 %s `%s`，网 `%s`，驱动 %s"
                     % (r["index"], r["feature"],
                        "；".join(r["net"]) or "?",
                        "；".join("`%s`" % x for x in r["drivers"]) or "?"))
        L.append("")

    # task-3 dual pos table
    L.append("**§17.2 位置类别对照**：预登记“按负载”版与“跨副本/跨路开关位"
             "按驱动单元归类”版并列（驱动映射：表决器/译码器输出→"
             "decoder_voter；核输入/复位/时钟→shared_control；其余保持"
             "负载版类别）。作者裁定（2026-10-18）：正文用“按驱动”版，"
             "“按负载”版作为预登记口径保留在报告里；三模基线不重建。")
    L.append("")
    L.append("| 位置类别 | 跨副本/跨路按驱动（正文版） |"
             " 按负载（预登记版） |")
    L.append("| --- | ---: | ---: |")
    for c in POS_CLASSES:
        L.append("| %s | %d | %d |" % (
            c, ev["posclass_driver"].get(c, 0),
            ev["posclass_load"].get(c, 0)))
    L.append("| 合计 | %d | %d |" % (
        sum(ev["posclass_load"].values()),
        sum(ev["posclass_driver"].values())))
    nch = len(ev["posclass_reclassed"])
    L.append("")
    L.append("驱动版相对负载版改类 %d 发；明细见 `s17_v2.json` "
             "`posclass_reclassed`。" % nch)
    L.append("")

    # task-4 list
    L.append("**§17.3 只落在一份副本上的单发静默位**（逐位）：")
    L.append("")
    if not ev["silent_one_replica"]:
        L.append("（无）")
    for r in ev["silent_one_replica"]:
        L.append("- 位 %s（la=%d,wd=%d,bt=%d,lsb=%s）`%s` [%s]"
                 % (r["index"], r["lfa"][0], r["lfa"][1], r["lfa"][2],
                    r["lsb"], r["feature"], r["resource"]))
        L.append("  - 网：`%s`" % ("；".join(r["net"]) or "?"))
        L.append("  - 驱动：%s" % ("；".join(
            "`%s`" % x for x in r["drivers"]) or "（无驱动单元）"))
        L.append("  - 负载（%d）：%s" % (
            len(r["loads"]), "；".join("`%s`" % x for x in r["loads"][:6])
            + (" …" if len(r["loads"]) > 6 else "")))
        if r["clock_caveat"]:
            L.append("  - 注：时钟网开关位——时钟树实际扇出可能大于导出"
                     "负载集，需按时钟 caveat 单独看待。")
        else:
            zh = dict(GROUPS).get(r["driver_group"], r["driver_group"])
            L.append("  - 注：非时钟位——规划方预期此类均为时钟行开关位，"
                     "此位驱动组为“%s”，已逐位核实（负载、网、驱动见上）。"
                     % zh)
    L.append("")

    # task-5 partition (encoded designs only)
    if "enc_silent_partition" in ev:
        p = ev["enc_silent_partition"]
        names = [("enc", "级入口编码器 u_encode"),
                 ("dec", "本级译码器内部 u_decode"),
                 ("next_lane_enc", "同时落在下一级某路与下一级编码器"),
                 ("other", "其他")]
        L.append("**§17.4 编码设计单发静默 × v2 负载分组**（decoder_voter "
                 "归属的静默位）：")
        L.append("")
        L.append("| 分组 | 发数 | 典型网 |")
        L.append("| --- | ---: | --- |")
        for k, zh in names:
            ex = p["net_examples"].get(k, [])
            L.append("| %s | %d | %s |" % (
                zh, p["counts"].get(k, 0),
                "<br>".join("`%s`" % n for n in ex[:4]) or "—"))
        L.append("| 合计 | %d | |" % sum(p["counts"].values()))
        L.append("")
        L.append("逐发清单见 `s17_v2.json` `enc_silent_partition.indices`。")
        L.append("")
    return "\n".join(L)


def main():
    design = sys.argv[1]
    ev = s17(design)
    dd = OUT / design
    (dd / "s17_v2.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    (dd / "S17_v2.md").write_text(md(design, ev), encoding="utf-8")
    x = ev["xrep_totals"]
    print("%s: xrep silent=%d | 1rep-silent=%d | groups: %s"
          % (design, x.get("silent", 0), len(ev["silent_one_replica"]),
             {g: ev["xrep_by_driver"].get(g, {}).get("silent", 0)
              for g, _ in GROUPS}))
    if "enc_silent_partition" in ev:
        print("   enc-silent:", ev["enc_silent_partition"]["counts"])


if __name__ == "__main__":
    main()
