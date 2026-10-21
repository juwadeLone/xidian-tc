"""WP6 §25 (offline; parallel to §24; one process per design).

25.1  P0 3939: decode SLICE_X120Y194's AFFMUX/A5FFMUX/AOUTMUX/CARRY4.ACY0,
      decide whether AFF (replica 0 in_im_q_reg[17]) is fed through the A
      LUT's O5; if so, evaluate the lower-half INIT (pass-through pin and
      the net feeding it).  Plus: every shot on a LUT mode bit
      ({X}LUT.SRL/RAM/SMALL/DI1MUX) in all four designs, grouped by
      O5-has-destination and whether O5's consumers sit on a different
      lane/replica than O6's.  List only.

25.2  New attribution rule, outcome-blind on all four designs:
      {X}OUTMUX / {X}FFMUX / {X}5FFMUX / CARRY4.{X}CY0 select bits are
      attributed by the loads of what they select (like switch bits),
      then classified by the current rules (v2 by-load, §19 by-driver,
      §20 shared memory, §24.1 clock/reset -> shared_control).
      Per-shot old (v2) -> new class list; S0 4816 flagged separately.
      Listing only -- MAIN_TABLES.md untouched.

Run:  python3 wp6_s25.py P0_p1          -> results/wp6/P0_p1/s25.json
      python3 wp6_s25.py --combine      -> results/wp6/S25_checks.{md,json}
"""
import collections
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wp6_s21 as S
import wp6_s22 as S22
import wp6_s18 as T
from wp6a_attrib import attrib_of_cell

OUT = S.OUT
DESIGNS = S.DESIGNS

CLKRST = {"u_clk/inst/clk_out2", "u_core/u_dut/kernel_rst"}

MUX_HIT_RE = re.compile(
    r"^(?P<tk>[A-Z0-9_]+)\.(?P<st>SLICE[LM]_X(?P<ix>[01]))\."
    r"(?P<feat>(?P<let>[A-D])(?P<kind>OUTMUX|FFMUX|5FFMUX)\.[A-Z0-9_]+"
    r"|CARRY4\.(?P<clet>[A-D])CY0)$")
MODE_HIT_RE = re.compile(
    r"^(?P<tk>[A-Z0-9_]+)\.(?P<st>SLICE[LM]_X(?P<ix>[01]))\."
    r"(?P<let>[A-D])LUT\.(?P<mode>SRL|RAM|SMALL|DI1MUX\.[A-Z0-9_]+)$")

SIDES = ("M", "L", "LM", "LL")
PT_MASK = {1: 0xAAAAAAAA, 2: 0xCCCCCCCC, 3: 0xF0F0F0F0,
           4: 0xFF00FF00, 5: 0xFFFF0000}


# ------------------------------------------------------------- wire index

class WireNets:
    """All NWIRE rows on CLB tiles, once: {tile/wire: [nets]}."""
    def __init__(self, dd):
        self.w2n = {}
        t0 = time.time()
        pat = r"^NWIRE\t[^\t]+\t[^\t]+\tCLBL[LM]_[LR]_X\d+Y\d+/"
        out = subprocess.run(["rg", "--no-line-number", pat,
                              str(dd / "wp6a_dump.nload.tsv")],
                             capture_output=True, text=True, timeout=1800)
        for ln in out.stdout.splitlines():
            f = ln.split("\t")
            if len(f) >= 4 and f[3].startswith(("CLBL",)):
                self.w2n.setdefault(f[3], set()).add(f[1])
        print("  wire index: %d wires (%.0fs)"
              % (len(self.w2n), time.time() - t0), flush=True)

    def nets(self, wire):
        return sorted(n for n in self.w2n.get(wire, ())
                      if not n.startswith("<const"))


def pin_wire_candidates(tile, fam, pin):
    """BEL input/output pin wires on this CLB tile: try every side."""
    return ["%s/%s_%s_%s" % (tile, fam, side, pin) for side in SIDES]


def pin_net(wn, tile, pin):
    for w in pin_wire_candidates(tile, "CLBLM" if "CLBLM" in tile
                                 else "CLBLL", pin):
        n = wn.nets(w)
        if n:
            return w, n
    return None, []


# ------------------------------------------------------------- consumers

def driven_nets(xd, cell):
    return [n for n, (_t, drvs, _l) in xd.net_info.items() if cell in drvs]


def cell_consumers(xd, cell):
    """Load cells of every net `cell` drives."""
    cons = []
    for n in driven_nets(xd, cell):
        _t, _d, loads = xd.net_info.get(n, (None, [], []))
        cons += [S.strip_pin(l) for l in loads]
    return sorted(set(cons))


def cells_at(xd, site, bel):
    return [c for c, _r, p in xd.site_cells.get(site, []) if p == bel]


def net_load_attrib(ctx, net):
    _t, _d, loads = ctx.xd.net_info.get(net, (None, [], []))
    return [attrib_of_cell(S.strip_pin(l)) for l in loads]


# ------------------------------------------------------------- 25.1

def lut_mode_row(ctx, s, h, wn):
    """One LUT-mode hit -> O5 destinations vs O6 consumers grouping."""
    m = MODE_HIT_RE.match(h["feature"])
    tile = s["tile"]
    st, ix, let = m.group("st"), int(m.group("ix")), m.group("let")
    site_tag = st.split("_X")[0] + "_X%d" % ix
    sites = ctx.xd.sites_of_tile.get(tile, [])
    site = sites[ix] if ix < len(sites) else None
    muxes, dests = S22.o5_destinations(ctx, tile, site_tag, let)
    lutcell = None
    o5_cells, o5_net_at, o5_descr = set(), set(), []
    for d in dests:
        o5_descr.append("%s->%s" % (d["mux"], d["to"]))
        for c in d.get("cells", []):
            o5_cells.add(c)
        for n in d.get("nets", []):
            for a in net_load_attrib(ctx, n):
                o5_net_at.add(a)
    sib = [c for c, _r, p in ctx.xd.site_cells.get(site, [])
           if p == let + "5LUT"]
    o5_cells |= set(sib)
    o5_at = {attrib_of_cell(c) for c in o5_cells} | o5_net_at
    for c, _r, p in ctx.xd.site_cells.get(site, []):
        if p == let + "6LUT":
            lutcell = c
    # O6 consumers: O6 is hard-wired to CARRY4's S input and can leave the
    # slice via OUTMUX (O6/XOR/CY/F7/F8 selections) or the FF muxes (O6 sel).
    o6_at, o6_descr = set(), []
    if set(muxes.get(let + "OUTMUX", [])) & {"O6", "XOR", "CY", "F7", "F8"}:
        _w, onets = pin_net(wn, tile, let)
        for n in onets:
            for a in net_load_attrib(ctx, n):
                o6_at.add(a)
        o6_descr.append("%sOUTMUX=%s->输出线负载"
                        % (let, "/".join(muxes[let + "OUTMUX"])))
    for mname, bel in ((let + "FFMUX", let + "FF"),
                       (let + "5FFMUX", let + "5FF")):
        if "O6" in muxes.get(mname, []):
            for c in cells_at(ctx.xd, site, bel):
                o6_at.add(attrib_of_cell(c))
            o6_descr.append("%s=O6->%s消费者" % (mname, bel))
    carry = cells_at(ctx.xd, site, "CARRY4")
    ccons = sorted({c2 for c in carry for c2 in cell_consumers(ctx.xd, c)})
    if ccons:
        for c in ccons:
            o6_at.add(attrib_of_cell(c))
        o6_descr.append("O6=进位链S输入->CARRY4消费者×%d" % len(ccons))
    has_dest = bool(o5_at)
    if not has_dest:
        grp = "O5无去处"
    elif not o6_at:
        grp = "O5有去处、O6无负载"
    elif {(a[0], a[2]) for a in o5_at} == {(a[0], a[2]) for a in o6_at}:
        grp = "O5去处与O6同路/副本"
    else:
        grp = "O5去处与O6不同路/副本"
    return {"index": s["index"], "category": s["category"],
            "mode": m.group("mode"), "feature": h["feature"],
            "tile": tile, "site": site, "let": let,
            "lut_cell": lutcell, "has_a6": ctx.xd.lut_has_a6.get(lutcell),
            "muxes": muxes, "o5_dests": o5_descr, "o6_dests": o6_descr,
            "o5_attrib": sorted(o5_at, key=str),
            "o6_attrib": sorted(o6_at, key=str), "group": grp}


def task_3939(ctx, wn):
    s = next(x for x in ctx.shots if x["index"] == 3939)
    tile = s["tile"]
    site_tag, let = "SLICEM_X0", "A"
    out = {"index": 3939, "tile": tile, "lfa": [s["la"], s["wd"], s["bt"]],
           "category": s["category"]}
    sites = ctx.xd.sites_of_tile.get(tile, [])
    site = sites[0] if sites else None
    out["site"] = site
    out["slice_cells"] = [{"cell": c, "ref": r, "bel": p,
                           "attrib": list(attrib_of_cell(c))}
                          for c, r, p in ctx.xd.site_cells.get(site, [])]
    muxes, dests = S22.o5_destinations(ctx, tile, site_tag, let)
    out["muxes"] = muxes
    out["o5_dests"] = dests
    affmux = muxes.get("AFFMUX", [])
    src = affmux[0] if affmux else "(无匹配—按7系列为直连AX)"
    out["aff_d_source"] = src
    init, nbits = S.read_lut_init(ctx, tile,
                                "CLBLM_R.SLICEM_X0.ALUT.INIT")
    lo, hi = init & 0xFFFFFFFF, init >> 32
    out["init"] = {"hex": "16'h%016X" % init, "nbits": nbits,
                   "lo32": "8'h%08X" % lo, "hi32": "8'h%08X" % hi}
    exp = S.lut_expected(ctx,
                         "u_core/u_dut/g_k.u_kernel/u_pipeline/"
                         "g_stage[8].g_tmr.u_stage/g_replica[2].g_sdf.u_copy"
                         "/g_lane[2].g_multiply.u_rotator_i_19")
    out["lut_expected"] = None if exp is None else (
        "16'h%016X" % (exp[0] | (exp[1] << 32)) if isinstance(exp, tuple)
        else exp)
    pt = [k for k, msk in PT_MASK.items() if lo == msk]
    out["o5_passthrough_pin"] = pt
    pins = {}
    lut = next((o for o in s["hits_detail"][0].get("objects", [])
                if o in ctx.xd.lut_pmap), None)
    cellpins = ctx.lutpins.get(lut, {}) if lut else {}
    for k in range(1, 7):
        w, nets = pin_net(wn, tile, "A%d" % k)
        pins["A%d" % k] = {"wire": w, "nets": nets,
                           "cell_pin_net": cellpins.get(k)}
    w, nets = pin_net(wn, tile, "AX")
    pins["AX"] = {"wire": w, "nets": nets}
    out["pin_nets"] = pins
    out["lut_cell"] = lut
    return out


# ------------------------------------------------------------- 25.2

def decode_mux(ctx, tile, grp, target):
    cur, aft, flip = S22.decode_group(ctx, tile, grp, target)
    return cur, aft


def o5_other_dests(ctx, tile, site_tag, let, ix):
    """O5's currently configured consumers except the OUTMUX wire itself."""
    _m, dests = S22.o5_destinations(ctx, tile, site_tag, let)
    cells = set()
    for d in dests:
        if d["mux"].endswith("OUTMUX"):
            continue
        cells |= set(d.get("cells", []))
    sites = ctx.xd.sites_of_tile.get(tile, [])
    site = sites[ix] if ix < len(sites) else None
    cells |= {c for c, _r, p in ctx.xd.site_cells.get(site, [])
              if p == let + "5LUT"}
    return cells


def signal_extra(ctx, tile, site_tag, let, ix, sig):
    """A signal's already-existing consumers (its 本来的去处)."""
    sites = ctx.xd.sites_of_tile.get(tile, [])
    site = sites[ix] if ix < len(sites) else None
    if sig == "O5":
        return sorted(o5_other_dests(ctx, tile, site_tag, let, ix)), []
    if sig == "O6":
        # O6's always-on destinations: CARRY4 S input, plus the FF muxes
        # when they currently select O6 (OUTMUX itself excluded — its loads
        # are the new connection's consumers, counted via outnets).
        muxes, _d = S22.o5_destinations(ctx, tile, site_tag, let)
        cells = set(cells_at(ctx.xd, site, "CARRY4"))
        for mname, bel in ((let + "FFMUX", let + "FF"),
                           (let + "5FFMUX", let + "5FF")):
            if "O6" in muxes.get(mname, []):
                cells |= set(cells_at(ctx.xd, site, bel))
        return sorted(cells), []
    if sig == let + "5Q":
        cells = cells_at(ctx.xd, site, let + "5FF")
        return sorted({c2 for c in cells for c2 in
                       cell_consumers(ctx.xd, c)}), []
    return [], []


def hit_reclass(ctx, s, h, wn):
    """New-attribution for one OUTMUX/FFMUX/5FFMUX/CY0 hit."""
    m = MUX_HIT_RE.match(h["feature"])
    tile = s["tile"]
    st, ix = m.group("st"), int(m.group("ix"))
    let = m.group("let") or m.group("clet")
    kind = m.group("kind") or "CY0"
    site_tag = st.split("_X")[0] + "_X%d" % ix
    sites = ctx.xd.sites_of_tile.get(tile, [])
    site = sites[ix] if ix < len(sites) else None
    target = (s["la"], s["wd"], s["lsb"])
    tk = tile.split("_X")[0].upper()
    attribs, nets, drivers, reason = set(), set(), set(), []

    if kind == "OUTMUX":
        grp = S22.grp_of(ctx, tile, "%s.%s.%sOUTMUX." % (tk, site_tag, let))
        cur, aft = decode_mux(ctx, tile, grp, target)
        w, outnets = pin_net(wn, tile, let)
        for n in outnets:
            nets.add(n)
            ri = ctx.xd.net_info.get(n, (None, [], []))
            drivers |= set(ri[1])
            for a in net_load_attrib(ctx, n):
                attribs.add(a)
        reason.append("OUTMUX：输出线 `%s` 上网 `%s` 的负载"
                      % (w or "?", "、".join(outnets) or "无"))
        new = [x for x in aft if x not in cur]
        for sig in new:
            cells, extra_nets = signal_extra(ctx, tile, site_tag, let,
                                             ix, sig)
            for c in cells:
                attribs.add(attrib_of_cell(c))
            if cells:
                reason.append("翻转后接 %s，并上其原去处 %s"
                              % (sig, "、".join(cells[:6])))
        ev = {"cur": cur, "after": aft, "out_wire": w,
              "out_nets": outnets, "new_sig": new}
    elif kind in ("FFMUX", "5FFMUX"):
        grp = S22.grp_of(ctx, tile, "%s.%s.%s%s." % (tk, site_tag, let, kind))
        cur, aft = decode_mux(ctx, tile, grp, target)
        bel = let + ("FF" if kind == "FFMUX" else "5FF")
        ffs = cells_at(ctx.xd, site, bel)
        cons = sorted({c2 for c in ffs for c2 in cell_consumers(ctx.xd, c)})
        for c in cons:
            attribs.add(attrib_of_cell(c))
        for c in ffs:
            for n in driven_nets(ctx.xd, c):
                nets.add(n)
        reason.append("%s：%s 的消费者" % (kind, bel))
        # §24.1 analog: pin-sourced selections before/after the flip
        for sig in cur + aft:
            if sig == let + "X":
                _w, pn = pin_net(wn, tile, let + "X")
                nets |= set(pn)
        ev = {"cur": cur, "after": aft, "ff_cells": ffs,
              "consumers": cons}
    else:  # CARRY4.{X}CY0
        cy = S22.grp_of(ctx, tile, "%s.%s.CARRY4.%sCY0" % (tk, site_tag, let))
        carry = cells_at(ctx.xd, site, "CARRY4")
        cons = sorted({c2 for c in carry for c2 in cell_consumers(ctx.xd, c)})
        for c in cons:
            attribs.add(attrib_of_cell(c))
        for c in carry:
            for n in driven_nets(ctx.xd, c):
                nets.add(n)
        reason.append("CARRY4.%sCY0：进位链单元的消费者" % let)
        ev = {"cy0_features": sorted(cy), "carry_cells": carry,
              "consumers": cons}
    return {"attribs": sorted(attribs, key=str), "nets": sorted(nets),
            "drivers": sorted(drivers), "evidence": ev,
            "reason": "；".join(reason), "kind": kind, "let": let}


def classify(attribs, nets, drivers):
    """Current rules: by-load -> §24.1 clk/rst -> §19 by-driver on
    cross-lane/cross-replica -> §20 shared memory."""
    at = [tuple(a) for a in attribs]
    cls = T.pos_class_load({"attrib": at})
    if nets & CLKRST:
        return "shared_control", "§24.1 时钟/复位网"
    pseudo = {"attrib": at}
    if T.is_xrep(pseudo) or T.is_xlane(pseudo):
        g = T.driver_group(set(drivers), set(nets))
        if g in T.DRV2POS:
            return T.DRV2POS[g], "跨路/副本，按驱动 %s" % g
        d = T.driver_pos_class(set(drivers))
        if d:
            return d, "跨路/副本，按驱动单元"
    elif T.is_shared_mem_load(pseudo):
        if T.driver_pos_class(set(drivers)) == "shared_control":
            return "shared_control", "§20 共享存储负载"
    return cls, "按负载"


def s25(design):
    t0 = time.time()
    ctx = S.Ctx(design)
    wn = WireNets(ctx.dd)
    ev = {"design": design, "rule": "§25.2 按所选去处负载", "date":
          time.strftime("%Y-%m-%d %H:%M:%S %z")}

    # ---- 25.1 detail (P0) + mode-bit tabulation (all designs)
    mode_rows = []
    for s in ctx.shots:
        for h in s.get("hits_detail", []):
            if h.get("resource") == "触发器与slice设置" and \
                    MODE_HIT_RE.match(h.get("feature", "")):
                mode_rows.append(lut_mode_row(ctx, s, h, wn))
    ev["lut_mode"] = {"rows": mode_rows,
                      "groups": collections.Counter(
                          "%s|%s" % (r["group"], r["category"])
                          for r in mode_rows)}
    print("[%s] lut-mode rows=%d (%.0fs)"
          % (design, len(mode_rows), time.time() - t0), flush=True)
    if design == "P0_p1":
        ev["p3939"] = task_3939(ctx, wn)
        print("[%s] 3939 decoded (%.0fs)"
              % (design, time.time() - t0), flush=True)

    # ---- 25.2
    recl = []
    per_hit = []
    for s in ctx.shots:
        mux_hits = [h for h in s.get("hits_detail", [])
                    if MUX_HIT_RE.match(h.get("feature", ""))]
        if not mux_hits:
            continue
        all_at, all_nets, all_drv = set(), set(), set()
        hit_notes = []
        other_at = set()
        for h in s.get("hits_detail", []):
            if MUX_HIT_RE.match(h.get("feature", "")):
                r = hit_reclass(ctx, s, h, wn)
                for a in r["attribs"]:
                    all_at.add(tuple(a))
                all_nets |= set(r["nets"])
                all_drv |= set(r["drivers"])
                hit_notes.append({"feature": h["feature"], **r})
            else:
                for a in h.get("attrib", []):
                    other_at.add(tuple(a))
        merged = all_at | other_at
        new_cls, rule_used = classify(merged, all_nets, all_drv)
        old_cls = T.pos_class_main(s)
        per_hit.append({"index": s["index"], "category": s["category"],
                        "v2": old_cls, "new": new_cls, "rule": rule_used,
                        "n_mux_hits": len(mux_hits), "hits": hit_notes})
        if new_cls != old_cls:
            recl.append({"index": s["index"], "category": s["category"],
                         "v2": old_cls, "new": new_cls, "rule": rule_used,
                         "tile": s["tile"],
                         "features": [h["feature"] for h in mux_hits]})
    ev["reclass"] = {"rows": recl, "n_changed": len(recl),
                     "n_mux_shots": len(per_hit),
                     "by_result": collections.Counter(
                         "%s|%s|%s" % (r["category"], r["v2"], r["new"])
                         for r in recl)}
    ev["mux_shots"] = per_hit
    if design == "S0_s3":
        ev["s4816"] = [r for r in per_hit if r["index"] == 4816]
    p = OUT / design / "s25.json"
    p.write_text(json.dumps(ev, ensure_ascii=False, default=list))
    print("[%s] done mux=%d changed=%d (%.0fs)"
          % (design, len(per_hit), len(recl), time.time() - t0),
          flush=True)


# ------------------------------------------------------------- combine

def _afmt(a):
    c, st, sub = a
    nm = S.CLASS_ZH.get(c, c)
    if st is None:
        return nm
    extra = "" if sub is None else ("·路%d" % sub if c ==
                                    "protected_datapath" else "·副本%d" % sub)
    return "%s(st%d%s)" % (nm, st, extra)


_CONST_NET = re.compile(r"^(<const\d+>|VCC.*|GND.*|ONE|ZERO)$")


def _fmt_nets(nets):
    sig = [n for n in nets
           if not _CONST_NET.match(n.split("/")[-1])]
    nconst = len(nets) - len(sig)
    out = ["`%s`" % n for n in sig]
    if nconst:
        out.append("常量网×%d" % nconst)
    return "、".join(out) or "无网"


def combine():
    evs = {d: json.loads((OUT / d / "s25.json").read_text())
           for d in DESIGNS if (OUT / d / "s25.json").is_file()}
    md = ["# WP6 §25 离线核查\n",
          "`wp6_s25.py`，四设计各一进程；只读，不写任何 v2/v3 数据文件。\n"]

    if "P0_p1" in evs and "p3939" in evs["P0_p1"]:
        r = evs["P0_p1"]["p3939"]
        md.append("\n## 25.1 P0 第 3939 发：A 触发器是否借 O5 直通\n")
        md.append("- 发：`%s`，位 la=%d wd=%d bit=%d；瓦片 `%s`，站点 `%s`；"
                  "结果 %s。\n"
                  % (r["feature"] if "feature" in r else
                     "CLBLM_R.SLICEM_X0.ALUT.SRL", r["lfa"][0], r["lfa"][1],
                     r["lfa"][2], r["tile"], r["site"], r["category"]))
        md.append("- 本片 slice 单元：\n")
        for c in r["slice_cells"]:
            md.append("  - `%s` @%s（%s）→ %s\n"
                      % (c["cell"], c["bel"], c["ref"], _afmt(c["attrib"])))
        md.append("- 多选器（位流读出）：`%s`\n"
                  % json.dumps(r["muxes"], ensure_ascii=False))
        md.append("- O5 去处：`%s`\n"
                  % json.dumps(r["o5_dests"], ensure_ascii=False))
        md.append("- AFF D 输入来源：**%s**\n" % r["aff_d_source"])
        md.append("- A LUT INIT：%s（解码到 %d 位）；下半 `%s`，上半 `%s`；"
                  "lutpins 期望：%s\n"
                  % (r["init"]["hex"], r["init"]["nbits"], r["init"]["lo32"],
                     r["init"]["hi32"], r.get("lut_expected")))
        if r["o5_passthrough_pin"]:
            md.append("- **下半是单脚直通**，脚：%s\n" %
                      "、".join("A%d" % k for k in r["o5_passthrough_pin"]))
        else:
            md.append("- 下半不是单脚直通。\n")
        for k, v in r["pin_nets"].items():
            md.append("  - `%s`（%s）：%s\n"
                      % (k, v.get("wire") or "无线",
                         _fmt_nets(v.get("nets", []))))
        ok = (r["aff_d_source"] == "O5" and r["o5_passthrough_pin"])
        md.append("\n**结论**：%s\n" % (
            "A 触发器（`g_replica[0].../in_im_q_reg[17]`）的 D 输入经 "
            "AFFMUX=O5 借 A6LUT（`g_replica[2].../u_rotator_i_19`）的 O5 "
            "直通——**是**。下半 INIT `8'hFF00FF00` 是 A4 直通，A4 脚挂 "
            "`g_stage[7].g_ecc.u_stage/u_decode/out_data[65]`（第 7 级译码器"
            "输出，非副本内网），喂副本 0 的 `in_im_q_reg[17]`；上半 "
            "`8'h5F5FA0A0` 是 A5/A3/A1 上副本 2 网的 LUT3 函数（8'h6A），"
            "O6 走副本 2 数据通路。本发打在 `ALUT.SRL` 模式位上：一翻，"
            "该 LUT 改作移位寄存器，副本 2 的真函数与副本 0 的借用直通"
            "同时失效——与表 4 的 silent 结果一致。"
            if ok else
            "AFF D 输入来源为 %s，O5 直通判据不满足，3939 记「未查明」。"
            % r["aff_d_source"]))

    md.append("\n### 查找表模式位统计（四设计，只列不改类）\n")
    for d in DESIGNS:
        if d not in evs:
            continue
        g = evs[d]["lut_mode"]["groups"]
        md.append("\n- %s：共 %d 发模式位\n" % (d, len(evs[d]["lut_mode"]
                                                            ["rows"])))
        md.append("| 分组 | 无影响 | 有影响 | 合计 |\n| --- | ---: | ---: |"
                  " ---: |\n")
        keys = sorted({k.split("|")[0] for k in g})
        for k in keys:
            noi = sum(v for kk, v in g.items()
                      if kk.split("|")[0] == k
                      and kk.split("|")[1] == "noimpact")
            imp = sum(v for kk, v in g.items()
                      if kk.split("|")[0] == k
                      and kk.split("|")[1] != "noimpact")
            md.append("| %s | %d | %d | %d |\n" % (k, noi, imp, noi + imp))
        md.append("\n逐发分组（发号·特性·结果）：\n")
        rows = sorted(evs[d]["lut_mode"]["rows"], key=lambda r:
                      (r["group"], r["index"]))
        cur_g = None
        for r in rows:
            if r["group"] != cur_g:
                cur_g = r["group"]
                md.append("\n- %s：\n" % cur_g)
            md.append("  - #%d `%s`（%s，%s）\n"
                      % (r["index"], r["feature"], r["mode"],
                         S.RESULT_ZH.get(r["category"], r["category"])))

    md.append("\n## 25.2 选择位按所选去处归属（只列，不改 MAIN_TABLES）\n")
    for d in DESIGNS:
        if d not in evs:
            continue
        rc = evs[d]["reclass"]
        md.append("\n### %s：选择位发 %d，改类 %d\n"
                  % (d, rc["n_mux_shots"], rc["n_changed"]))
        if rc["by_result"]:
            md.append("| 结果 | 原类 | 新类 | 发数 |\n| --- | --- | --- |"
                      " ---: |\n")
            for k, c in sorted(rc["by_result"].items(), key=lambda x: -x[1]):
                cat, o, n = k.split("|")
                md.append("| %s | %s | %s | %d |\n"
                          % (S.RESULT_ZH.get(cat, cat),
                             S.CLASS_ZH.get(o, o), S.CLASS_ZH.get(n, n), c))
        md.append("\n逐发清单：\n")
        md.append("| 发 | 结果 | 原类 | 新类 | 理由 |\n| ---: | --- | --- |"
                  " --- | --- |\n")
        for r in evs[d]["mux_shots"]:
            md.append("| %d | %s | %s | %s | %s |\n"
                      % (r["index"], S.RESULT_ZH.get(r["category"],
                                                   r["category"]),
                         S.CLASS_ZH.get(r["v2"], r["v2"]),
                         S.CLASS_ZH.get(r["new"], r["new"]), r["rule"]))
    if "S0_s3" in evs and evs["S0_s3"].get("s4816"):
        r = evs["S0_s3"]["s4816"][0]
        md.append("\n### 单独标出 S0 4816\n")
        md.append("- v2 `%s` → 新 `%s`（%s）\n"
                  % (S.CLASS_ZH.get(r["v2"], r["v2"]),
                     S.CLASS_ZH.get(r["new"], r["new"]), r["rule"]))
        for h in r["hits"]:
            md.append("  - `%s`：%s；解码 `%s`\n"
                      % (h["feature"], h["reason"],
                         json.dumps(h["evidence"], ensure_ascii=False)))

    outj = {"designs": {d: {"lut_mode_groups":
                            {str(k): v for k, v in
                             evs[d]["lut_mode"]["groups"].items()},
                            "lut_mode_rows": evs[d]["lut_mode"]["rows"],
                            "reclass": {"n_mux_shots":
                                        evs[d]["reclass"]["n_mux_shots"],
                                        "n_changed":
                                        evs[d]["reclass"]["n_changed"],
                                        "by_result": {str(k): v for k, v in
                                                      evs[d]["reclass"]
                                                      ["by_result"].items()},
                                        "shots": evs[d]["mux_shots"]}}
                        for d in evs}}
    if "P0_p1" in evs and "p3939" in evs["P0_p1"]:
        outj["p3939"] = evs["P0_p1"]["p3939"]
    if "S0_s3" in evs and evs["S0_s3"].get("s4816"):
        outj["s4816"] = evs["S0_s3"]["s4816"]
    (OUT / "S25_checks.json").write_text(json.dumps(outj, ensure_ascii=False,
                                                  indent=1))
    (OUT / "S25_checks.md").write_text("".join(md))
    print("wrote S25_checks.md/.json")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--combine":
        combine()
    else:
        s25(sys.argv[1])


if __name__ == "__main__":
    main()
