"""WP6 §22.1–22.3 offline analysis (no board, no Vivado).

    22.1  O5 route-through recheck: for every "INIT lower-half, no-A6"
          LUT shot (the 21.4 'no 5LUT sibling' group and, for context, the
          sibling group too), read the slice muxes {X}OUTMUX, {X}FFMUX,
          {X}5FFMUX(IN_A=O5), CARRY4.{X}CY0 from the bitstream and decide
          whether O5 has a real destination.  Re-group impacted rates by
          O5-has-destination / no-destination.
    22.2  P0 4990: enumerate FFs of the other slice in the same CLB
          (CLK_L0 user) and of every slice whose clock mux input lies on
          the same clock-leaf node as the shorted one.
    22.3  P0 3939/4005/4962, S1 2317: slice-config bits -> full slice
          inventory (2130 method); routing switch bits -> decode the
          post-flip connection (21.2 method) with net/lane/replica reach.

Run one process per design:
    python3 wp6_s22.py P0_p1
    python3 wp6_s22.py --combine     # -> results/wp6/S22_offline.{md,json}
"""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wp6_s21 as S
from wp6a_attrib import attrib_of_cell

OUT = S.OUT
DESIGNS = S.DESIGNS
RESULT_ZH = S.RESULT_ZH

S22_TARGETS = {"P0_p1": [3939, 4005, 4962], "S1_s2": [2317],
               "P1_p2": [], "S0_s3": []}

SLICE_RE = re.compile(r"(SLICEL|SLICEM)_X([01])")

# ---------------------------------------------------------------- helpers

def tile_type(tile):
    return tile.split("_X")[0].lower()


def mux_prefix(tile, site_tag, name):
    tk = tile.split("_X")[0].upper()
    return "%s.%s.%s" % (tk, site_tag, name)


def grp_of(ctx, tile, prefix):
    tt = tile_type(tile)
    return {n: b for n, b in ctx.feat_db.get(tt, {}).items()
            if n.startswith(prefix)}


def decode_group(ctx, tile, grp, target=None):
    """Return (matched_now, matched_after, flip_local)."""
    flip = None
    if target is not None:
        for _f, bits in grp.items():
            for fr, b, pos in bits:
                loc = ctx.cidx.resolve(tile, fr, b)
                if loc and (loc[0], loc[1], loc[2]) == target:
                    flip = (fr, b)
    now = ctx.selector_state(tile, grp)
    aft = ctx.selector_state(tile, grp, flip_pos=flip)
    cur = sorted(f.rsplit(".", 1)[-1] for f, ok in now.items() if ok)
    new = sorted(f.rsplit(".", 1)[-1] for f, ok in aft.items() if ok)
    return cur, new, flip


def wire_net(ctx, wire):
    """tile/wire -> net via nload NWIRE rows (f2=node rep, f3=wire)."""
    nets = set()
    for ln in S.rg_lines(re.escape(wire), ctx.dd / "wp6a_dump.nload.tsv"):
        f = ln.split("\t")
        if f[0] == "NWIRE" and (f[2] == wire or f[3] == wire):
            nets.add(f[1])
    return sorted(n for n in nets if not n.startswith("<const"))


def node_of_wire(ctx, wire):
    """Return (net, node_rep, [member wires]) for a tile/wire."""
    node_reps = {}
    for ln in S.rg_lines(re.escape(wire), ctx.dd / "wp6a_dump.nload.tsv"):
        f = ln.split("\t")
        if f[0] == "NWIRE" and f[3] == wire:
            node_reps[f[2]] = f[1]
    if not node_reps:
        return None, None, []
    rep = sorted(node_reps)[0]
    net = node_reps[rep]
    wires = set()
    pat = "^NWIRE\t" + re.escape(net) + "\t" + re.escape(rep) + "\t"
    for ln in S.rg_lines(pat, ctx.dd / "wp6a_dump.nload.tsv"):
        f = ln.split("\t")
        if len(f) >= 4:
            wires.add(f[3])
    return net, rep, sorted(wires)


def net_reach(ctx, net):
    typ, drvs, loads = ctx.xd.net_info.get(net, (None, [], []))
    lanes, repls, stages = set(), set(), set()
    for ld in loads:
        a = attrib_of_cell(S.strip_pin(ld))
        if a[1] is not None:
            stages.add(a[1])
        if a[2] is not None:
            (lanes if a[0] == "protected_datapath"
             else repls).add(a[2])
    return {"net": net, "type": typ, "drivers": drvs,
            "n_loads": len(loads),
            "stages": sorted(stages), "lanes": sorted(lanes),
            "replicas": sorted(repls),
            "loads": [(ld, attrib_of_cell(S.strip_pin(ld)))
                      for ld in loads]}


def slice_inventory(ctx, tile, ix):
    sites = ctx.xd.sites_of_tile.get(tile, [])
    site = sites[ix] if ix < len(sites) else None
    cells = []
    for cell, ref, posn in ctx.xd.site_cells.get(site, []):
        cells.append({"cell": cell, "ref": ref, "bel": posn,
                      "attrib": attrib_of_cell(cell)})
    return site, cells


# ------------------------------------------------------------- 22.1 O5

def o5_destinations(ctx, tile, site_tag, let):
    out = {}
    for name in (let + "OUTMUX", let + "FFMUX", let + "5FFMUX"):
        grp = grp_of(ctx, tile, mux_prefix(tile, site_tag, name) + ".")
        cur, _n, _f = decode_group(ctx, tile, grp)
        out[name] = cur
    cy = grp_of(ctx, tile,
                mux_prefix(tile, site_tag, "CARRY4." + let + "CY0"))
    cyset = False
    for _f, bits in cy.items():
        vals = [(ctx.ebc.bit(*loc), pos) for fr, b, pos in bits
                for loc in [ctx.cidx.resolve(tile, fr, b)] if loc]
        if vals and all(v == (1 if pos else 0) for v, pos in vals):
            cyset = True
    out["CARRY4." + let + "CY0"] = cyset

    dests = []
    if "O5" in out.get(let + "OUTMUX", []):
        fam = "CLBLM" if "CLBLM" in tile else "CLBLL"
        w = None
        for side in ("L", "M", "LL", "LM"):
            cand = "%s/%s_%s_%s" % (tile, fam, side, let)
            if wire_net(ctx, cand):
                w = cand
                break
        nets = wire_net(ctx, w) if w else []
        dests.append({"mux": let + "OUTMUX", "to": "slice %s output wire"
                      % let, "wire": w, "nets": nets,
                      "reach": [net_reach(ctx, n) for n in nets]})
    if "O5" in out.get(let + "FFMUX", []):
        sites = ctx.xd.sites_of_tile.get(tile, [])
        ix = int(site_tag[-1])
        site = sites[ix] if ix < len(sites) else None
        ff = [c for c, _r, p in ctx.xd.site_cells.get(site, [])
              if p == let + "FF"]
        dests.append({"mux": let + "FFMUX", "to": "%sFF D input" % let,
                      "cells": ff,
                      "attrib": [attrib_of_cell(c) for c in ff]})
    if "IN_A" in out.get(let + "5FFMUX", []):
        sites = ctx.xd.sites_of_tile.get(tile, [])
        ix = int(site_tag[-1])
        site = sites[ix] if ix < len(sites) else None
        ff = [c for c, _r, p in ctx.xd.site_cells.get(site, [])
              if p == let + "5FF"]
        dests.append({"mux": let + "5FFMUX", "to": "%s5FF D input" % let,
                      "cells": ff,
                      "attrib": [attrib_of_cell(c) for c in ff]})
    if cyset:
        sites = ctx.xd.sites_of_tile.get(tile, [])
        ix = int(site_tag[-1])
        site = sites[ix] if ix < len(sites) else None
        cy4 = [c for c, _r, p in ctx.xd.site_cells.get(site, [])
               if p == "CARRY4"]
        dests.append({"mux": "CARRY4." + let + "CY0",
                      "to": "carry chain CY0 input", "cells": cy4,
                      "attrib": [attrib_of_cell(c) for c in cy4]})
    return out, dests


def task_o5(ctx):
    """22.1: every INIT lower-half shot on a no-A6 LUT -> O5 destination."""
    rows = []
    for s in ctx.shots:
        for h in s.get("hits_detail", []):
            if h.get("resource") != "查找表内容":
                continue
            m = S.INIT_RE.match(h.get("feature", ""))
            if not m or int(m.group("k")) >= 32:
                continue
            cell = next((o for o in h.get("objects", [])
                         if o in ctx.xd.lut_has_a6), None)
            if cell is None or ctx.xd.lut_has_a6.get(cell, True):
                continue
            tile = s["tile"]
            st, ix, let = m.group("st"), int(m.group("ix")), m.group("let")
            site_tag = "%s_X%d" % (st, ix)
            sites = ctx.xd.sites_of_tile.get(tile, [])
            site = sites[ix] if ix < len(sites) else None
            sib = any(p == let + "5LUT"
                      for c, _r, p in ctx.xd.site_cells.get(site, [])
                      if c != cell)
            muxes, dests = o5_destinations(ctx, tile, site_tag, let)
            real = [d for d in dests
                    if (d["mux"].endswith("OUTMUX") and d["nets"])
                    or (not d["mux"].endswith("OUTMUX") and d["cells"])]
            rows.append({"index": s["index"], "category": s["category"],
                         "tile": tile, "site": site, "let": let,
                         "cell": cell, "sib_5lut": sib,
                         "muxes": muxes, "o5_dests": dests,
                         "o5_has_dest": bool(real)})
    stat = collections.Counter()
    for r in rows:
        sib = "sib" if r["sib_5lut"] else "nosib"
        dst = "dest" if r["o5_has_dest"] else "nodest"
        stat[(sib, dst, "n")] += 1
        if r["category"] != "noimpact":
            stat[(sib, dst, "imp")] += 1
    groups = {}
    for sib in ("sib", "nosib"):
        for dst in ("dest", "nodest"):
            n = stat[(sib, dst, "n")]
            groups["%s_%s" % (sib, dst)] = {
                "n": n, "imp": stat[(sib, dst, "imp")],
                "frac": round(stat[(sib, dst, "imp")] / max(1, n), 4)}
    return {"rows": rows, "groups": groups}


# ------------------------------------------------------------- 22.2

def task_4990(ctx):
    s = next(x for x in ctx.shots if x["index"] == 4990)
    h = s["hits_detail"][0]
    pip_tile, pip = h["pip"].split(" ", 1)
    src_dst = pip.split("->")
    clk_wire = src_dst[1]
    clb_tile = s["tile"]
    grp = grp_of(ctx, pip_tile,
                 "%s.%s." % (pip_tile.split("_X")[0].upper(), clk_wire))
    cur, aft, flip = decode_group(ctx, pip_tile, grp,
                                  (s["la"], s["wd"], s["lsb"]))
    leaf_net, leaf_rep, leaf_wires = None, None, []
    if cur:
        leaf_net, leaf_rep, leaf_wires = node_of_wire(
            ctx, "%s/%s" % (pip_tile, cur[0]))
    seg_tiles = sorted({w.split("/", 1)[0] for w in leaf_wires
                        if w.split("/", 1)[0].startswith("INT_")})
    other = "CLK_L0" if clk_wire == "CLK_L1" else "CLK_L1"
    g0 = grp_of(ctx, pip_tile,
                "%s.%s." % (pip_tile.split("_X")[0].upper(), other))
    sib_cur = None
    if g0:
        sib_cur, _n, _f = decode_group(ctx, pip_tile, g0)

    objs = h.get("objects", [])
    obj_sites = sorted({ctx.xd.cells_row.get(o, (None,))[0]
                        for o in objs})
    sites = ctx.xd.sites_of_tile.get(clb_tile, [])
    clk_slice_ix = None
    for i, st in enumerate(sites):
        if st in obj_sites:
            clk_slice_ix = i
    if clk_slice_ix is None:
        clk_slice_ix = 1 if clk_wire.endswith("1") else 0
    sib_ix = 1 - clk_slice_ix

    out = {"shot_index": 4990, "tile": clb_tile, "pip_tile": pip_tile,
           "clk_wire": clk_wire, "cur": cur, "after": aft,
           "flip_local": flip, "leaf_net": leaf_net,
           "leaf_node": leaf_rep, "leaf_n_wires": len(leaf_wires),
           "leaf_wires": leaf_wires, "seg_tiles": seg_tiles,
           "sibling_clk": {"wire": other, "cur": sib_cur},
           "clk_slice_ix": clk_slice_ix, "slices": []}

    sib_site, sib_cells = slice_inventory(ctx, clb_tile, sib_ix)
    tgt_site, tgt_cells = slice_inventory(ctx, clb_tile, clk_slice_ix)
    out["target_slice"] = {"site": tgt_site, "clk": clk_wire,
                           "cells": tgt_cells}
    out["sibling_slice"] = {"site": sib_site, "clk": other,
                            "cur_src": sib_cur,
                            "same_leaf": bool(sib_cur and leaf_wires and
                                              ("%s/%s" % (pip_tile,
                                                          sib_cur[0])
                                               in leaf_wires)),
                            "cells": sib_cells}

    seg = []
    for t in seg_tiles:
        if t == pip_tile:
            continue
        for cw in ("CLK_L0", "CLK_L1"):
            g = grp_of(ctx, t, "%s.%s." % (t.split("_X")[0].upper(), cw))
            if not g:
                continue
            cc, _n2, _f2 = decode_group(ctx, t, g)
            if not cc:
                continue
            on_seg = any(("%s/%s" % (t, w)) in leaf_wires for w in cc)
            if not on_seg:
                continue
            cand = t.replace("INT_L", "CLBLM_L").replace("INT_R",
                                                         "CLBLM_R")
            for alt in (cand, cand.replace("CLBLM", "CLBLL")):
                if ctx.xd.sites_of_tile.get(alt):
                    cand = alt
                    break
            ix = 1 if cw.endswith("1") else 0
            site, cells = slice_inventory(ctx, cand, ix)
            seg.append({"int_tile": t, "clk": cw, "sel": cc,
                        "clb_tile": cand, "site": site, "cells": cells})
    out["segment_slices"] = seg
    return out


# ------------------------------------------------------------- 22.3

def task_misc(ctx):
    out = {}
    for idx in S22_TARGETS.get(ctx.design, []):
        s = next((x for x in ctx.shots if x["index"] == idx), None)
        if s is None:
            continue
        rec = {"category": s["category"], "tile": s["tile"],
               "lfa": [s["la"], s["wd"], s["bt"]],
               "hits": []}
        for h in s.get("hits_detail", []):
            feat = h.get("feature", "")
            ent = {"feature": feat, "resource": h.get("resource"),
                   "objects": h.get("objects")}
            ms = SLICE_RE.search(feat)
            if h.get("pip"):
                pt, pip = h["pip"].split(" ", 1)
                dst = pip.split("->", 1)[1]
                grp = grp_of(ctx, pt,
                             "%s.%s." % (pt.split("_X")[0].upper(), dst))
                cur, aft, flip = decode_group(
                    ctx, pt, grp, (s["la"], s["wd"], s["lsb"]))
                gained = [w for w in aft if w not in cur]
                lost = [w for w in cur if w not in aft]
                g_nets, l_nets = {}, {}
                for w in gained:
                    for n in wire_net(ctx, "%s/%s" % (pt, w)):
                        g_nets[n] = net_reach(ctx, n)
                for w in lost:
                    for n in wire_net(ctx, "%s/%s" % (pt, w)):
                        l_nets[n] = net_reach(ctx, n)
                d_nets = {}
                for n in wire_net(ctx, "%s/%s" % (pt, dst)):
                    d_nets[n] = net_reach(ctx, n)
                if not lost and gained:
                    kind = ("B_接上了在用的线" if g_nets
                            else "C_接上了空线")
                elif lost and not gained:
                    kind = "A_断开当前输入"
                elif lost and gained:
                    kind = "D_换成另一根"
                elif cur == aft:
                    kind = "状态不变"
                else:
                    kind = "其他"
                ent.update(pip_tile=pt, dst=dst, cur=cur, after=aft,
                           flip_local=flip, decode=kind,
                           dst_nets=d_nets,
                           gained_nets=g_nets, lost_nets=l_nets)
            elif ms:
                ix = int(ms.group(2))
                site, cells = slice_inventory(ctx, s["tile"], ix)
                ent.update(site=site, slice_cells=cells)
            rec["hits"].append(ent)
        rec["telemetry"] = (ctx.camp.get(idx, {}) or {}).get("telemetry")
        out[idx] = rec
    return out


# ------------------------------------------------------------- driver

def s22(design):
    ctx = S.Ctx(design)
    ev = {"design": design}
    ev["o5"] = task_o5(ctx)
    ev["misc"] = task_misc(ctx)
    if design == "P0_p1":
        ev["seg4990"] = task_4990(ctx)
    return ev


# ------------------------------------------------------------- report

def _tel(tel):
    if not tel:
        return "n/a"
    return "%d/%d/%d" % (tel.get("det_cnt", -1), tel.get("unc_cnt", -1),
                         tel.get("cor_cnt", -1))


def _afmt(a):
    if not a:
        return "未解析"
    c, st, sub = a
    nm = S.CLASS_ZH.get(c, c)
    if st is None:
        return nm
    extra = "" if sub is None else ("·路%d" % sub if c ==
                                    "protected_datapath" else "·副本%d" % sub)
    return "%s(st%d%s)" % (nm, st, extra)


def combine():
    evs = {}
    for d in DESIGNS:
        p = OUT / d / "s22.json"
        if p.is_file():
            evs[d] = S.load_j(p)
    md = []
    md.append("# WP6 §22.1–22.3 离线分析（位流+X-Ray+导出数据，不上板）\n")
    md.append("生成：`scripts/k7_mod/wp6_s22.py`；四设计各一进程。\n")

    md.append("\n## 22.1 O5 直通复核（21.4 无-A6 查找表下半发）\n")
    md.append("O5 的合法去处：{X}OUTMUX.O5（接切片输出线）、{X}FFMUX.O5"
              "（接本列 FF 的 D）、{X}5FFMUX.IN_A（接 5FF 的 D——X-Ray 模型"
              "中 IN_A 即 O5）、CARRY4.{X}CY0 置位（接进位链 CY0）。去处"
              "还要有真实消费者（输出线挂网 / FF 或 CARRY4 bel 上有单元）"
              "才算\"O5 有去处\"。\n")
    for d in DESIGNS:
        if d not in evs:
            continue
        g = evs[d]["o5"]["groups"]
        md.append("\n### %s\n" % d)
        md.append("| 组 | 发数 | 有影响 | 比例 |\n| --- | ---: | ---: | ---: |\n")
        for k in ("sib_dest", "sib_nodest", "nosib_dest", "nosib_nodest"):
            if k in g:
                md.append("| %s | %d | %d | %.1f%% |\n"
                          % (k, g[k]["n"], g[k]["imp"], g[k]["frac"] * 100))
    md.append("\n### 残差发（nosib 组中仍有影响的）按 O5 去处分\n")
    for d in DESIGNS:
        if d not in evs:
            continue
        rows = [r for r in evs[d]["o5"]["rows"]
                if not r["sib_5lut"] and r["category"] != "noimpact"]
        md.append("\n- %s：nosib 有影响 %d 发——dest %d / nodest %d\n"
                  % (d, len(rows),
                     sum(1 for r in rows if r["o5_has_dest"]),
                     sum(1 for r in rows if not r["o5_has_dest"])))
        for r in rows:
            ds = "、".join("%s→%s" % (x["mux"], x["to"])
                           for x in r["o5_dests"]) or "无"
            md.append("  - #%d（%s，%s/%s 列 %s）：%s\n"
                      % (r["index"], RESULT_ZH.get(r["category"],
                                                   r["category"]),
                         r["site"], r["tile"], r["let"], ds))
    if "P0_p1" in evs:
        r = next((x for x in evs["P0_p1"]["o5"]["rows"] if x["index"]
                  == 1264), None)
        if r:
            md.append("\n第 1264 发（%s/%s A 列）：多选器状态 %s；"
                      "O5 去处：%s。**更正 21.1 的读法**：该 LUT 的下半"
                      "并非无读出——AFFMUX=O5，O5 直通到第 1 级译码器"
                      "寄存器 q0i_reg[29]（decoder_voter）。这为静默"
                      "提供了一个可能的机理方向（译码域寄存器被改、"
                      "不触发校验计数），但仍仅记\"推测待证\"：是否当拍"
                      "致病由重打复核。路归属结论不变。\n"
                      % (r["tile"], r["site"],
                         json.dumps(r["muxes"], ensure_ascii=False),
                         json.dumps(r["o5_dests"], ensure_ascii=False)))

    if "P0_p1" in evs and "seg4990" in evs["P0_p1"]:
        r = evs["P0_p1"]["seg4990"]
        md.append("\n## 22.2 第 4990 发同叶段清点（P0）\n")
        md.append("- 位流解码：%s 的 %s 多选器 当前=`%s` 翻转后=`%s`。"
                  % (r["pip_tile"], r["clk_wire"],
                     "+".join(r["cur"]), "+".join(r["after"])))
        md.append("- 当前源 `%s` 所属节点 `%s`（网 `%s`，%d 根线，"
                  "覆盖 INT 瓦片 %s）。\n"
                  % (r["cur"][0] if r["cur"] else "?", r["leaf_node"],
                     r["leaf_net"], r["leaf_n_wires"],
                     "、".join(r["seg_tiles"])))
        sb = r["sibling_slice"]
        md.append("- 同 CLB 另一 slice `%s`（时钟线 %s，当前源 %s；与本位"
                  "同叶段：%s）：\n"
                  % (sb["site"], sb["clk"], "+".join(sb["cur_src"] or []),
                     "是" if sb["same_leaf"] else "否"))
        for c in sb["cells"]:
            if c["ref"].startswith(("FD", "LD")) or "FF" in c["bel"]:
                md.append("  - `%s` @%s → %s\n"
                          % (S.fmt_net(c["cell"]), c["bel"], _afmt(c["attrib"])))
        md.append("- 同叶段其余受影响 slice：%s\n"
                  % ("（叶节点只含本瓦片一根线，无其它 INT 瓦片——"
                     "叶段范围即本 CLB 的两片 slice）"
                     if not r["segment_slices"] else ""))
        for x in r["segment_slices"]:
            md.append("  - `%s` %s→`%s`（sel=%s）：\n"
                      % (x["clb_tile"], x["clk"], x["site"], "+".join(x["sel"])))
            for c in x["cells"]:
                if c["ref"].startswith(("FD", "LD")) or "FF" in c["bel"]:
                    md.append("    - `%s` @%s → %s\n"
                              % (S.fmt_net(c["cell"]), c["bel"],
                                 _afmt(c["attrib"])))
        md.append("- **结论（推测待证，方向已定位）**：短接把 "
                  "FAN_BOUNCE5（stage5 译码器 out_data[19]）与时钟叶节点"
                  " `GCLK_L_B11_WEST` 拉成一根；两片 slice 的时钟多选器"
                  "都选这根线 → 节点同时是 SLICEM_X0（CLK_L0）与 "
                  "SLICEL_X1（CLK_L1）的时钟。X0 恰装第 6 级**路 4** 的 "
                  "in_im_q_reg[20..23]、X1 装路 5 的 in_im_q_reg[12..15]"
                  "——两条校验路同时被扰动 → 不可纠 + 功能输出完好，与 "
                  "fbc（unc=824）一致。原 21.2 报告里\"扰动路 3 与输出"
                  "完好一致\"一句已删改：路 3 若在不可纠拍被拉错输出就不"
                  "会完好。短接是否同时把错值推回路 3 数据网，离线不可"
                  "复核。\n")

    md.append("\n## 22.3 三模副本内静默其余四发\n")
    for d in DESIGNS:
        ev = evs.get(d, {})
        for idx, rec in sorted((ev.get("misc") or {}).items(),
                               key=lambda kv: int(kv[0])):
            md.append("\n### %s #%s（%s，遥测 %s）\n"
                      % (d, idx, RESULT_ZH.get(rec["category"],
                                               rec["category"]),
                         _tel(rec.get("telemetry"))))
            seen_decode = set()
            for h in rec["hits"]:
                md.append("- `%s`（%s）\n" % (h["feature"], h["resource"]))
                if h.get("slice_cells"):
                    md.append("  slice `%s` 单元清点：\n" % h["site"])
                    for c in h["slice_cells"]:
                        md.append("  - `%s` @%s → %s\n"
                                  % (S.fmt_net(c["cell"]), c["bel"],
                                     _afmt(c["attrib"])))
                if h.get("pip_tile") and (h["pip_tile"], h["dst"]) \
                        not in seen_decode:
                    seen_decode.add((h["pip_tile"], h["dst"]))
                    md.append("  解码：当前=`%s` 翻转后=`%s`（%s）\n"
                              % ("+".join(h["cur"]), "+".join(h["after"]),
                                 h["decode"]))
                    for n, ri in (h.get("gained_nets") or {}).items():
                        md.append("  新接网 `%s`（驱动 %s；级%s 路%s 副本%s，"
                                  "%d 负载）\n"
                                  % (S.fmt_net(n), "、".join(
                                      S.fmt_net(x) for x in ri["drivers"]),
                                     ri["stages"], ri["lanes"],
                                     ri["replicas"], ri["n_loads"]))
                    for n, ri in (h.get("dst_nets") or {}).items():
                        md.append("  被断/保持的 dst 网 `%s`（驱动 %s；级%s "
                                  "副本%s，%d 负载）\n"
                                  % (S.fmt_net(n), "、".join(
                                      S.fmt_net(x) for x in ri["drivers"]),
                                     ri["stages"], ri["replicas"],
                                     ri["n_loads"]))
            rpls = set()
            for h in rec["hits"]:
                for c in (h.get("slice_cells") or []):
                    a = c.get("attrib")
                    if a and a[0] == "tmr_stage" and a[2] is not None:
                        rpls.add(a[2])
            if len(rpls) >= 2:
                md.append("  **判定：推测待证。** 该 slice 同时装着副本 "
                          "%s 的单元——一个 slice 级设置位可同拍扰动两份"
                          "副本，多数表决被带向错值，可解释静默；是否当拍"
                          "致病由 22.5 重打复核。\n"
                          % "、".join(str(x) for x in sorted(rpls)))
            elif any("u_data_vote" in (n or "") or "vote" in (n or "")
                     for h in rec["hits"]
                     for n in ((h.get("dst_nets") or {}).keys())):
                md.append("  **判定：推测待证。** 断开的是表决器输出网"
                          "（负载跨三副本）——表决后的网悬空，三个副本同"
                          "时拿到错值，可结构性解释静默；断段实际覆盖的"
                          "负载集离线未逐条核对。\n")
    txt = "".join(md)
    (OUT / "S22_offline.md").write_text(txt, encoding="utf-8")
    (OUT / "S22_offline.json").write_text(
        json.dumps(evs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(txt[:2000])


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--combine":
        combine()
        return
    design = sys.argv[1]
    ev = s22(design)
    p = OUT / design / "s22.json"
    p.write_text(json.dumps(ev, ensure_ascii=False, indent=1),
                 encoding="utf-8")
    print("wrote", p)


if __name__ == "__main__":
    main()
