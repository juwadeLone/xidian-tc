"""WP6 §27 TNS-style single-upset classification (offline only).

Per WP6 task card §27 (author approved 2026-10-19): classify every
single-event shot three ways, independent of the §26 recompute:

  27.1  outcome names    -- header mapping only (静默=SDC, 标出=detected/
                            uncorrectable, 卡住=SEFI; 已纠正/无影响 kept)
  27.2  resource x effect-- per the §27.2 table; per-design
                            resource x effect x outcome count table +
                            per-shot list of every failed shot's pairs
  27.3  mechanism        -- by structure only, priority order:
                            site CMF / clock-routing CMF / route-through /
                            recovery-output single point / two-domain
                            routing / outside protection / single domain.
                            A bit matching several rules takes the first;
                            detail records ALL matched rules.

Versions: the attribution version is a parameter ("v2" = v2 + §24.1 main
text version; "v31" = the §26 v3.1 file once it exists).  Connectivity
evidence (post-flip decode, dst subtree, gained nets, O5/O6 parties) is
structural and identical under both versions; the position class used by
the "outside protection" rule is the version's class.

No rule may read the outcome category -- results only feed distributions
and the failed-shot lists (§27.4 stop condition).

Usage: wp6_s27.py <design> <v2|v31>  |  wp6_s27.py --combine <v2|v31>
Outputs: results/wp6/<design>/S27_tns.json + S27_tns.md (per design),
         results/wp6/S27_tns_tables_v2.md (+ _v31.md later).
"""
import collections
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wp6_s21 as S
import wp6_s18 as T
import wp6_s22 as S22
import wp6_s25 as S25
import wp6a_attrib_v3 as A3
from wp6a_attrib import attrib_of_cell

OUT = S.OUT
DESIGNS = S.DESIGNS
CATS = T.CATS
CAT_ZH = T.CAT_ZH
POS_ZH = dict(T.POS_ROWS)

SET_RE = re.compile(
    r"^(?P<tk>[A-Z0-9_]+)\.(?P<st>SLICE[LM]_X(?P<ix>[01]))\."
    r"(?P<rest>.+)$")
INIT_RE = re.compile(
    r"^(?P<tk>[A-Z0-9_]+)\.(?P<st>SLICE[LM]_X(?P<ix>[01]))\."
    r"(?P<let>[A-D])LUT\.INIT\[(?P<k>\d+)\]")
LUT_MODE_RE = re.compile(
    r"^([A-D])LUT\.(SRL|RAM|SMALL|DI1MUX\..*)$")
MUX_SEL_RE = re.compile(
    r"^([A-D])(OUTMUX|FFMUX|5FFMUX)\.|^CARRY4\.([A-D])CY0$")
FFSET_RE = re.compile(r"^([A-D])(5?)FF\.(ZINI|ZRST|INIT)")
WA_RE = re.compile(r"^(WA\d+USED|WEMUX\.)")
CLK_WIRE_RE = re.compile(r"^(CLK_L\d|CLK\d|GCLK|BUFH|BUFG|CMT_)")
CLK_NETS = {"u_clk/inst/clk_out2"}

# per-hit resource bucket names (§27.2 rows)
R_SWITCH = "布线开关"
R_LUTV = "查找表内容"
R_LUTM = "查找表模式"
R_MUX = "slice 选择位"
R_SET = "slice/触发器设置"
R_DSP = "DSP 设置及其他"
R_UNCL = "未分类"

FAILED_CATS = {"silent", "flagged", "stuck", "flagged_but_clean"}

# verification shots from §§21-25 (design, index, expected mechanism)
CHECK_SHOTS = [
    ("P0_p1", 3939, "site_cmf"), ("P0_p1", 2130, "site_cmf"),
    ("P0_p1", 4962, "site_cmf"), ("S1_s2", 2317, "site_cmf"),
    ("P0_p1", 4990, "clock_cmf"), ("S1_s2", 144, "clock_cmf"),
    ("P1_p2", 1377, "clock_cmf"),
    ("P0_p1", 1264, "route_through"),
    ("P0_p1", 4005, "recovery_sp"),
]

MECH_ORDER = ["site_cmf", "clock_cmf", "route_through", "recovery_sp",
              "two_domain_route", "outside_protection", "single_domain"]
MECH_ZH = {"site_cmf": "site 共模",
           "clock_cmf": "时钟布线共模",
           "route_through": "直通",
           "recovery_sp": "恢复电路输出单点",
           "two_domain_route": "两域共用布线",
           "outside_protection": "保护之外",
           "single_domain": "只在一个域内"}

EFF_ZH = {"open": "开路（断开当前输入）",
          "short": "短接（多接一根在用的线）",
          "load": "加载（多接一根空线）",
          "bridge": "桥接（换成另一根，新线有驱动）",
          "antenna": "天线（换成另一根，新线无驱动）",
          "none": "接法不变（未用开关）",
          "unres": "接法未解析",
          "lut_o6": "LUT值：O6 一侧改变",
          "lut_o5": "LUT值：O5 一侧（5LUT/直通）改变",
          "ctrl": "控制位改变",
          "mux_used": "mux select：新选接在用信号",
          "mux_float": "mux select：新选悬空/未用",
          "mux_open": "mux select：原选断开（悬空）",
          "mux_same": "mux select：接法不变",
          "mux_na": "mux select（接法未解析）",
          "dsp": "DSP/其它设置位",
          "uncl": "未分类",
          "compound": "复合（同位多种资源/效应）"}

# canonical effect list per resource bucket -- the table lists every
# one of these rows even at zero count (§27.5.4: 计数为0也列，注"本批
# 无此类")
RES_EFFS = {
    R_SWITCH: ["open", "short", "load", "bridge", "antenna", "none",
               "unres", "uncl"],
    R_LUTV: ["lut_o6", "lut_o5", "uncl"],
    R_LUTM: ["ctrl"],
    R_MUX: ["mux_used", "mux_float", "mux_open", "mux_same", "mux_na"],
    R_SET: ["ctrl"],
    R_DSP: ["dsp"],
    R_UNCL: ["uncl", "compound"],
}


# ------------------------------------------------------------ helpers

def load_version(design, ver):
    """(shots, class_fn).  v2 = v2 file + §24.1 overlay; v31 = v3.1 file
    whose cls_v31 already carries §24.1."""
    if ver == "v31":
        bd = S.load_j(OUT / design / "bit_attrib_downstream_v31.json")
        return bd["shots"], lambda s: s.get("cls_v31")
    bd = S.load_j(OUT / design / "bit_attrib_downstream_v2.json")
    shots = bd["shots"]
    # join v3 evidence (post-flip decode/subtrees) into v2 hits, same as
    # wp6_s18 does -- structure is version-independent.
    v3p = OUT / design / "bit_attrib_downstream_v3.json"
    if v3p.is_file():
        v3idx = {s["index"]: s for s in S.load_j(v3p)["shots"]}
        for s in shots:
            s3 = v3idx.get(s["index"])
            if not s3:
                continue
            ebf = {h.get("feature"): h.get("v3_evidence")
                   for h in s3.get("hits_detail", [])}
            for h in s.get("hits_detail", []):
                e2 = ebf.get(h.get("feature"))
                if e2:
                    h["v3_evidence"] = e2

    def cls24(s):
        base = T.pos_class_main(s)
        c, _q = T.pos_class_main_v24(s, "v2")
        return c or base
    return shots, cls24


def dom_attrib(attribs):
    """Domains per §27.5.1 as (stage, axis, idx): only lane/replica-
    bearing cells form domains.  Lane-less cells (shared control incl.
    clock/reset, twiddle ROM, decoder, voter, kernel input, P0 stage
    memory whose lane can't be derived) do NOT count."""
    out = set()
    for a in attribs:
        if not a or a[0] == "unmapped" or a[1] is None or a[2] is None:
            continue
        axis = "lane" if a[0] == "protected_datapath" else "rep"
        out.add((a[1], axis, a[2]))
    return out


ENC_RE = re.compile(r"/u_encode/")


def dom_cells(cells):
    """Same domain set from cell NAMES; encoder cells appearing as
    loads/affected count as check lanes 4,5 of their stage (§27.5.1)."""
    out = set()
    for c in cells:
        if not c:
            continue
        a = attrib_of_cell(c)
        if a[0] == "unmapped" or a[1] is None:
            continue
        if a[2] is not None:
            axis = "lane" if a[0] == "protected_datapath" else "rep"
            out.add((a[1], axis, a[2]))
        elif ENC_RE.search(c):
            out |= {(a[1], "lane", 4), (a[1], "lane", 5)}
    return out


def crosses(doms):
    """">=2 lanes within one stage" or ">=2 replicas within one stage"
    (different stages never combine -- §27.5.1)."""
    per = collections.defaultdict(set)
    for st, ax, i in doms:
        per[(st, ax)].add(i)
    return any(len(v) >= 2 for v in per.values())


def domains_of(attribs):
    """Route-through keeps the §27.3 wording ('不同的域或模块'): every
    distinct (stage, lane-or-module) position is its own domain --
    unchanged by §27.5.1."""
    return {(a[1], a[2]) for a in attribs
            if a and a[0] != "unmapped"}


def cells_attrib(cells):
    return [attrib_of_cell(c) for c in cells]


def dom_zh(doms):
    """(stage,axis,idx) set -> ['k级i路'/'k级r副本'...]"""
    out = []
    for st, ax, i in sorted(doms):
        out.append("%s级%s%s" % (st, i, "路" if ax == "lane" else "副本"))
    return out


def cells_at_bel(ctx, site, bel):
    return [c for c, _r, p in ctx.xd.site_cells.get(site, [])
            if p == bel]


def site_of(ctx, tile, ix):
    sites = ctx.xd.sites_of_tile.get(tile, [])
    return sites[ix] if ix < len(sites) else None


def slice_cells(ctx, site):
    """Every cell physically in the slice (FFs, LUTs, storage, carry)."""
    return [c for c, _r, _p in ctx.xd.site_cells.get(site, [])]


def slice_domains(ctx, site):
    return dom_cells(slice_cells(ctx, site))


def hit_attrib(h):
    """Best-known hit-level attrib: v31/v3 evidence lists first, else v2."""
    for k in ("attrib_v31", "attrib_v3"):
        if h.get(k) is not None:
            return h[k]
    return h.get("attrib", [])


def hit_gained_attrib(h):
    ev = h.get("v3_evidence") or {}
    out = []
    for g in ev.get("gained_nets", []):
        out += g.get("attrib") or []
    return out


def out_pin_nets(wn, tile, let):
    """Nets on the OUTMUX output pin ({let}MUX first, then {let})."""
    fam = "CLBLM" if "CLBLM" in tile else "CLBLL"
    for pin in (let + "MUX", let):
        for side in ("L", "M", "LL", "LM"):
            w = "%s/%s_%s_%s" % (tile, fam, side, pin)
            n = wn.nets(w)
            if n:
                return w, n
    return None, []


def bel_used(ctx, site, bels):
    return any(p in bels for _c, _r, p in ctx.xd.site_cells.get(site, []))


def pin_driven(wn, tile, pin):
    """Site input pin carries a routed net?"""
    fam = "CLBLM" if "CLBLM" in tile else "CLBLL"
    for side in ("L", "M", "LL", "LM"):
        w = "%s/%s_%s_%s" % (tile, fam, side, pin)
        if wn.nets(w):
            return True
    return False


def sel_src_inuse(ctx, wn, tile, site, site_tag, let, sig):
    """Whether a mux's newly selected source `sig` is an in-use producer.
    Internal producers -> the producing BEL/cell occupied; pin sources ->
    the pin wire carries a net; otherwise not in use."""
    if sig in ("O6",):
        return bool(cells_at_bel(ctx, site, let + "6LUT"))
    if sig in ("O5",):
        return bool(cells_at_bel(ctx, site, let + "6LUT")
                    or cells_at_bel(ctx, site, let + "5LUT"))
    if sig in ("CY", "CY0"):
        return bel_used(ctx, site, {"CARRY4"})
    if sig in ("F7", "F8"):
        return bel_used(ctx, site, {"F7AMUX", "F7BMUX", "F8MUX"})
    if re.match(r"^[A-D]5?Q$", sig):            # B5Q/A5Q etc: FF output
        return bel_used(ctx, site, {sig[0] + ("5" if "5" in sig else "")
                                    + "FF"})
    if re.match(r"^[A-D]X$", sig):              # AX/BX/CX/DX input pins
        return pin_driven(wn, tile, sig)
    if sig in ("IN_A", "IN_B"):
        return True                              # tie/logic selects
    if sig in ("SR", "CE", "CLK"):
        return pin_driven(wn, tile, sig)
    return False


# ------------------------------------------------------- 27.2 per hit

def hit_res_eff(ctx, wn, s, h):
    """(resource bucket, effect key, detail dict) for one hit."""
    res, feat = h.get("resource"), h.get("feature", "")
    if res == "开关" and h.get("pip"):
        ev = h.get("v3_evidence") or {}
        dec = ev.get("decode")
        det = {"decode": dec, "net": h.get("net"),
               "gained": [g.get("net") for g in ev.get("gained_nets", [])]}
        if dec == "A_断开当前输入":
            return R_SWITCH, "open", det
        if dec == "B_接上了在用的线":
            return R_SWITCH, "short", det
        if dec == "C_接上了空线":
            return R_SWITCH, "load", det
        if dec == "D_换成另一根":
            drv = any((g.get("drivers") or [])
                      for g in ev.get("gained_nets", []))
            return R_SWITCH, "bridge" if drv else "antenna", det
        if dec == "状态不变":
            return R_SWITCH, "none", det
        if dec is None:
            return R_SWITCH, "unres", det       # §27.5.4: no decode
        return R_SWITCH, "uncl", det
    if res == "查找表内容":
        m = INIT_RE.match(feat)
        if m:
            k = int(m.group("k"))
            return R_LUTV, "lut_o5" if k < 32 else "lut_o6", \
                {"k": k, "let": m.group("let")}
        return R_LUTV, "uncl", {}
    if res == "触发器与slice设置":
        m = SET_RE.match(feat)
        rest = m.group("rest") if m else feat
        mm = LUT_MODE_RE.match(rest)
        if mm:
            return R_LUTM, "ctrl", {"mode": mm.group(2)}
        m2 = MUX_SEL_RE.match(rest)
        if m2:
            let = m2.group(1) or m2.group(3)
            kind = m2.group(2) or "CY0"
            grp = S22.grp_of(ctx, s["tile"],
                             "%s.%s.%s%s."
                             % (s["tile"].split("_X")[0].upper(),
                                m.group("st"), let, kind))
            cur, aft, _f = S22.decode_group(ctx, s["tile"], grp,
                                            (s["la"], s["wd"], s["lsb"]))
            new = [x for x in aft if x not in cur]
            site = site_of(ctx, s["tile"], int(m.group("ix")))
            inuse = [sel_src_inuse(ctx, wn, s["tile"], site,
                                   m.group("st"), let, sig)
                     for sig in new]
            det = {"kind": kind, "cur": cur, "after": aft, "new": new}
            if not grp:
                return R_MUX, "mux_na", det    # decode failed only
            if cur == aft:
                return R_MUX, "mux_same", det  # 接法不变
            if cur and not aft:
                return R_MUX, "mux_open", det  # 原选断开(悬空)->开路
            if all(inuse) and new:
                return R_MUX, "mux_used", det
            return R_MUX, "mux_float", det
        # FF/slice settings (CLKINV/LATCH/SRUSEDMUX/CEUSEDMUX/FFSYNC/
        # ZINI/ZRST/WA*USED/WEMUX/...) -> control bit change
        return R_SET, "ctrl", {"set": rest.split(".")[0]}
    if res == "DSP 设置":
        return R_DSP, "dsp", {}
    if res:
        return R_UNCL, "uncl", {"resource": res}
    return R_UNCL, "uncl", {}



def o5_dests(ctx, wn, tile, site_tag, let):
    """Local O5-party enumeration == S22.o5_destinations but wire->net
    via the in-memory WireNets index and the {let}MUX-first pin fix
    (§25.2/§26.2).  Returns [(mux_name, [attrib cells], [nets])]."""
    site = site_of(ctx, tile, int(site_tag.split("_X")[-1]))
    out = []
    for name, bel in ((let + "FFMUX", let + "FF"),
                      (let + "5FFMUX", let + "5FF")):
        want = "O5" if name.endswith("FFMUX") and "5" not in name \
            else "IN_A"
        grp = S22.grp_of(ctx, tile,
                         S22.mux_prefix(tile, site_tag, name) + ".")
        cur, _n, _f = S22.decode_group(ctx, tile, grp)
        if want in cur:
            cells = [c for c, _r, p in ctx.xd.site_cells.get(site, [])
                     if p == bel] if site else []
            out.append((name, [attrib_of_cell(c) for c in cells], []))
    grp = S22.grp_of(ctx, tile,
                     S22.mux_prefix(tile, site_tag, let + "OUTMUX") + ".")
    cur, _n, _f = S22.decode_group(ctx, tile, grp)
    if "O5" in cur:
        _w, on = out_pin_nets(wn, tile, let)
        ats = []
        for n in on:
            _t, _d, loads = ctx.xd.net_info.get(n, ("?", [], []))
            ats += [attrib_of_cell(S.strip_pin(x)) for x in loads]
        out.append((let + "OUTMUX", ats, on))
    cy = S22.grp_of(ctx, tile,
                    S22.mux_prefix(tile, site_tag,
                                   "CARRY4." + let + "CY0"))
    cyset = False
    for _f, bits in cy.items():
        vals = [(ctx.ebc.bit(*loc), pos) for fr, b, pos in bits
                for loc in [ctx.cidx.resolve(tile, fr, b)] if loc]
        if vals and all(v == (1 if pos else 0) for v, pos in vals):
            cyset = True
    if cyset:
        cy4 = cells_at_bel(ctx, site, "CARRY4") if site else []
        out.append(("CARRY4." + let + "CY0",
                    [attrib_of_cell(c) for c in cy4], []))
    return out

# ------------------------------------------------------- 27.3 rules

def _a_zh(cell):
    """cell -> [stage, lane-or-replica label] for check listings."""
    a = attrib_of_cell(cell)
    if a[0] == "unmapped" or a[1] is None:
        return [a[1], "-"]
    if a[2] is None:
        return [a[1], "校验路4,5" if ENC_RE.search(cell) else "-"]
    return [a[1], "%s%s" % (a[2], "路" if a[0] == "protected_datapath"
                            else "副本")]


def q_site_cmf(ctx, s):
    """Slice-level bits (slice mode/setting or LUT-mode) whose slice's
    cells span >=2 domains of one stage (§27.5.1)."""
    for h in s.get("hits_detail", []):
        if h.get("resource") != "触发器与slice设置":
            continue
        m = SET_RE.match(h.get("feature", ""))
        if not m:
            continue
        rest = m.group("rest")
        is_slice = (rest.split(".")[0] in
                    {"CLKINV", "NOCLKINV", "FFSYNC", "LATCH",
                     "CEUSEDMUX", "SRUSEDMUX"}
                    or LUT_MODE_RE.match(rest) or WA_RE.match(rest))
        if not is_slice:
            continue
        site = site_of(ctx, s["tile"], int(m.group("ix")))
        if site:
            cells = slice_cells(ctx, site)
            doms = dom_cells(cells)
            if crosses(doms):
                return True, {"site": site,
                              "domains": dom_zh(doms),
                              "cells": [[c, _a_zh(c)]
                                        for c in cells]}
    return False, None


def q_clock_cmf(ctx, trees, s):
    """Clock-pin selector / clock-net switch bit; the perturbed clock
    branch feeds slices whose cells span >=2 domains.  The subtree's IN
    pins resolve to their SLICEs and every cell in those slices counts
    (a CLK pin serves the whole slice, not one lp-mapped cell)."""
    for h in s.get("hits_detail", []):
        if h.get("resource") != "开关" or not h.get("pip"):
            continue
        ev = h.get("v3_evidence") or {}
        nets = {h.get("net")} | {g.get("net") for g in
                                 ev.get("gained_nets", [])}
        pt, pip = h["pip"].split(" ", 1)
        dst_wire = pip.split("->", 1)[-1].lstrip(">").strip()
        is_clk = bool(nets & CLK_NETS) or bool(CLK_WIRE_RE.match(dst_wire))
        if not is_clk:
            continue
        tr = trees.get(h.get("net")) if h.get("net") else None
        dec = ev.get("decode")
        if tr is not None:
            # §27.5.3: OPEN perturbs only the disconnected clock pin's
            # slice = the dst wire's own subtree.  short/load/swap
            # perturb the originally selected source branch (its tile-
            # local subtree feeds every slice on it, §24.3) plus the
            # newly gained net's loads.
            if dec == "A_断开当前输入":
                branches = {dst_wire}
            else:
                branches = set(ev.get("cur") or ()) \
                    | set(ev.get("after") or ()) | {dst_wire}
            doms = set()
            site_cells_map = collections.defaultdict(list)
            for br in branches:
                st = tr.subtree("%s/%s" % (pt, br))
                if st is None:
                    continue
                sites = {sp for sp, _p in st[0]}
                for site in sites:
                    cs = slice_cells(ctx, site)
                    site_cells_map[site] = cs
                    doms |= dom_cells(cs)
            if dec != "A_断开当前输入":
                for g in ev.get("gained_nets", []):
                    gn = g.get("net")
                    if gn:
                        _t, _d, ld = ctx.xd.net_info.get(gn,
                                                       (None, [], []))
                        doms |= dom_cells([S.strip_pin(x) for x in ld])
            if crosses(doms):
                return True, {"branches": sorted(branches),
                              "domains": dom_zh(doms),
                              "slices": {sp: [[c, _a_zh(c)] for c in cs]
                                         for sp, cs in
                                         site_cells_map.items()}}
            continue
        loads = list(ev.get("dst_attrib") or [])
        for g in ev.get("gained_nets", []):
            loads += g.get("attrib") or []
        doms = dom_attrib(loads)
        if crosses(doms):
            return True, {"domains": dom_zh(doms)}
    return False, None


def q_route_through(ctx, wn, s):
    """LUT content (lower half) or mode bit: the O5-side party lands in a
    domain different from the O6 (LUT) cell's domain."""
    for h in s.get("hits_detail", []):
        res = h.get("resource")
        m = SET_RE.match(h.get("feature", ""))
        if not m:
            continue
        let = None
        if res == "查找表内容":
            mi = INIT_RE.match(h["feature"])
            if not mi or int(mi.group("k")) >= 32:
                continue              # O6 half doesn't touch the O5 side
            let = mi.group("let")
        elif res == "触发器与slice设置":
            mm = LUT_MODE_RE.match(m.group("rest"))
            if not mm:
                continue
            let = mm.group(1)
        else:
            continue
        stag, ix = m.group("st"), int(m.group("ix"))
        site = site_of(ctx, s["tile"], ix)
        if site is None:
            continue
        six = cells_at_bel(ctx, site, let + "6LUT")
        if not six:
            continue
        o6_dom = domains_of([attrib_of_cell(six[0])])
        o5_at = []
        o5_cells = []
        for _name, ats, _nets in o5_dests(ctx, wn, s["tile"], stag, let):
            o5_at += ats
        o5_dom = domains_of(o5_at)
        if o5_dom and not o5_dom <= o6_dom:
            return True, {"o6": [six[0], _a_zh(six[0])],
                          "o5_domains": sorted(
                              "%s级%s" % (a[1],
                                          a[2] if a[2] is not None
                                          else "模块")
                              for a in o5_at if a[1] is not None)}
    return False, None


RECOV_DRV_RE = re.compile(r"u_(data|valid)_vote|u_decode|/dec_out")


def q_recovery_sp(ctx, wn, s):
    """Switch or output-select bit on a voter/decoder-driven net, loads
    spanning >=2 domains of one stage (§27.5.1 domains)."""
    for h in s.get("hits_detail", []):
        res = h.get("resource")
        ev = h.get("v3_evidence") or {}
        drv = list(h.get("drivers") or [])
        loads = list(ev.get("dst_attrib") or [])
        if res == "开关" and h.get("pip"):
            for g in ev.get("gained_nets", []):
                drv += g.get("drivers") or []
                loads += g.get("attrib") or []
        elif res == "触发器与slice设置":
            m = SET_RE.match(h.get("feature", ""))
            rest = m.group("rest") if m else ""
            m2 = re.match(r"^([A-D])OUTMUX\.", rest)
            if not m2:
                continue
            _w, on = out_pin_nets(wn, s["tile"], m2.group(1))
            for n in on:
                _t, drvs, loads_l = ctx.xd.net_info.get(n, ("?", [], []))
                drv += drvs
                loads += [attrib_of_cell(S.strip_pin(x))
                          for x in loads_l]
        else:
            continue
        if any(RECOV_DRV_RE.search(d or "") for d in drv):
            doms = dom_attrib(loads)
            # encoder cells among the involved nets' loads count as the
            # stage's check lanes 4,5 (§27.5.1) -- net-level detection;
            # the exact subtree subset isn't stored per hit.
            enc_cells = []
            for n in ({h.get("net")} | {g.get("net") for g in
                                        ev.get("gained_nets", [])}):
                if not n:
                    continue
                _t, _d, ld = ctx.xd.net_info.get(n, (None, [], []))
                enc_cells += [S.strip_pin(x) for x in ld
                              if ENC_RE.search(x)]
            doms |= dom_cells(enc_cells)
            if crosses(doms):
                return True, {"drivers": drv, "domains": dom_zh(doms)}
    return False, None


def _drv_kind(name):
    """Driver class per §27.5.2: lane/replica cell | voter-decoder |
    other lane-less (shared control incl. clk/rst, twiddle ROM, encoder
    as DRIVER, kernel input)."""
    a = attrib_of_cell(S.strip_pin(name))
    if a[0] in ("protected_datapath", "tmr_stage") and a[2] is not None:
        return "lane", a
    if RECOV_DRV_RE.search(name or "") or a[0] == "decoder_voter":
        return "voter", a
    return "other", a


# ------------------------------------------------------------ §27.5.7

# P0 stage-level SECDED memory (u_memory subtree, incl. its own codec):
# excluded from the listed units -- the storage has its own protection.
P0MEM_RE = re.compile(r"(?:^|[./])u_memory(?:/|$)")
# name-level listed units that sit outside the kernel hierarchy and are
# therefore unmapped to any position class: kernel-input registers,
# clock/reset distribution, twiddle ROM, encoder/decoder/voter.
OUTSIDE_NAME_RES = [
    re.compile(r"^u_core/u_dut/(d_in|in_buf)"),         # 核输入寄存器
    re.compile(r"u_clk|kernel_rst|/u_rst/|/u_rst\b"),   # 时钟、复位 -> 共享控制
    re.compile(r"u_rom|g_rom|/coeff"),                  # 旋转因子表
    re.compile(r"u_encode|u_decode|u_(data|valid|last)_vote|/u_flag"),
]
OUTSIDE_POS = {"shared_control", "decoder_voter", "kinput"}


def is_outside_unit(name):
    """§27.5.7 listed unit?  shared control (incl. clock/reset), twiddle
    ROM, encoder, decoder, voter, kernel-input register u_dut/d_in_*.
    P0 stage memory (u_memory subtree) and its SECDED are NOT listed --
    the storage carries its own protection."""
    if not name:
        return False
    cell = S.strip_pin(name)
    if P0MEM_RE.search(cell):
        return False
    if attrib_of_cell(cell)[0] in OUTSIDE_POS:
        return True
    return any(r.search(cell) for r in OUTSIDE_NAME_RES)


def _mux_src_cells(ctx, site, sig):
    """Producer cells an OUTMUX selection connects to the output wire:
    {X}Q -> that FF's Q, O5/O6 -> the LUT, XOR/CY -> CARRY4, F7/F8 ->
    the F-mux BELs; pin-like sources have no in-slice producer."""
    m = re.match(r"^([A-D])(5?)Q$", sig)
    if m:
        return cells_at_bel(ctx, site, m.group(1) + m.group(2) + "FF")
    if sig in ("O5", "O6"):
        return []
    if sig in ("XOR", "CY"):
        return cells_at_bel(ctx, site, "CARRY4")
    if sig in ("F7", "F8"):
        return [c for c, _r, p in ctx.xd.site_cells.get(site, [])
                if p in ("F7AMUX", "F7BMUX", "F8MUX")]
    return []


def _net_drivers(ctx, n):
    _t, drvs, _l = ctx.xd.net_info.get(n, (None, [], []))
    return list(drvs)


def q_outside_struct(ctx, wn, s):
    """§27.5.7 structural outside-protection test, evaluated ONLY when
    none of the five mechanisms matched.  What the bit directly changes
    depends on its kind:
      * routing-switch bits & slice output-select (OUTMUX) bits: drivers
        of the changed nets (original net + newly connected nets);
      * LUT content/mode bits, FF & slice setting bits (incl. FFMUX,
        5FFMUX, CY0 input selects): the directly affected cells.
    Any listed unit touched -> outside protection by structure."""
    hit = []
    for h in s.get("hits_detail", []):
        res = h.get("resource")
        feat = h.get("feature", "")
        ev = h.get("v3_evidence") or {}
        if res == "开关" and h.get("pip"):
            nets = {h.get("net")} | {g.get("net") for g in
                                     ev.get("gained_nets", [])}
            pairs = [(h.get("net"), d) for d in (h.get("drivers") or [])]
            for g in ev.get("gained_nets", []):
                pairs += [(g.get("net"), d)
                          for d in (g.get("drivers") or [])]
            for n in sorted(x for x in nets if x):
                pairs += [(n, d) for d in _net_drivers(ctx, n)]
            for n, d in sorted(set(pairs)):
                if is_outside_unit(d):
                    hit.append({"net": n, "unit": d})
        elif res == "触发器与slice设置":
            m = SET_RE.match(feat)
            if not m:
                continue
            rest = m.group("rest")
            site = site_of(ctx, s["tile"], int(m.group("ix")))
            mo = re.match(r"^([A-D])OUTMUX\.", rest)
            if mo:
                # slice output select: changed nets = output-wire net
                # (原网) + nets driven by the newly selected sources
                # (新接上的网); drivers of any of them decide.
                let = mo.group(1)
                _w, nets = out_pin_nets(wn, s["tile"], let)
                nets = list(nets)
                tk = s["tile"].split("_X")[0].upper()
                grp = S22.grp_of(
                    ctx, s["tile"], "%s.%s.%sOUTMUX." % (tk, m.group("st"),
                                                        let))
                cur, aft, _f = S22.decode_group(
                    ctx, s["tile"], grp, (s["la"], s["wd"], s["lsb"]))
                for sig in [x for x in aft if x not in cur]:
                    # newly connected source: only an FF output brings
                    # its own (separate) net onto the output wire; the
                    # LUT/carry/F-mux sources feed the same wire.
                    if not re.match(r"^[A-D]5?Q$", sig):
                        continue
                    for c in _mux_src_cells(ctx, site, sig):
                        nets += [n for n, (_t, dr, _l)
                                 in ctx.xd.net_info.items()
                                 if c in dr]
                pairs = [(n, d) for n in set(nets)
                         for d in _net_drivers(ctx, n)]
                pairs += [(n, d) for n in set(nets)
                          for d in (h.get("drivers") or [])]
                for n, d in sorted(set(pairs)):
                    if is_outside_unit(d):
                        hit.append({"net": n, "unit": d})
                continue
            cells = _affected_cells(ctx, site, rest)
            for c in cells:
                if is_outside_unit(c):
                    hit.append({"cell": c, "unit": c})
        elif res == "查找表内容":
            mi = INIT_RE.match(feat)
            if not mi:
                continue
            site = site_of(ctx, s["tile"], int(mi.group("ix")))
            cells = cells_at_bel(ctx, site, mi.group("let") + "6LUT") + \
                cells_at_bel(ctx, site, mi.group("let") + "5LUT")
            for c in cells:
                if is_outside_unit(c):
                    hit.append({"cell": c, "unit": c})
    uniq = []
    seen = set()
    for x in hit:
        k = tuple(sorted(x.items()))
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return bool(uniq), {"units": uniq[:40]} if uniq else None


_SLICE_SET = {"CLKINV", "NOCLKINV", "FFSYNC", "LATCH", "CEUSEDMUX",
              "SRUSEDMUX"}
_LUT_BELS = tuple("%s%sLUT" % (let, k) for let in "ABCD"
                  for k in ("6", "5"))


def _affected_cells(ctx, site, rest):
    """Cells a LUT-mode / FF / slice-setting bit directly controls."""
    if site is None:
        return []
    mm = LUT_MODE_RE.match(rest)
    if mm:
        let = mm.group(1)
        return (cells_at_bel(ctx, site, let + "6LUT") +
                cells_at_bel(ctx, site, let + "5LUT"))
    m2 = MUX_SEL_RE.match(rest)          # FFMUX / 5FFMUX / CY0 selects
    if m2:
        let = m2.group(1) or m2.group(3)
        kind = m2.group(2) or "CY0"
        if kind == "CY0":
            return cells_at_bel(ctx, site, "CARRY4")
        return cells_at_bel(ctx, site,
                            let + ("FF" if kind == "FFMUX" else "5FF"))
    fm = FFSET_RE.match(rest)            # {A-D}FF.ZINI/ZRST/INIT
    if fm:
        return cells_at_bel(ctx, site, fm.group(1) + fm.group(2) + "FF")
    first = rest.split(".")[0]
    if first in _SLICE_SET:
        return slice_cells(ctx, site)
    if WA_RE.match(rest):                # WA*USED / WEMUX: LUT-RAM write
        return [c for c, _r, p in ctx.xd.site_cells.get(site, [])
                if p in _LUT_BELS]
    return slice_cells(ctx, site)


def q_two_domain_route(ctx, wn, s):
    """Per §27.5.2 the judge is the DRIVER of each modified net (hit net
    + newly gained nets):
      * voter/decoder-driven -> (handled by recovery_sp; skipped here)
      * lane/replica-driven  -> drivers+loads of THESE nets spanning
        >=2 domains of one stage -> two-domain shared routing
      * all lane-less        -> neither rule fires; position rule takes
        it (shared_control/kinput -> outside protection)."""
    for h in s.get("hits_detail", []):
        if h.get("resource") != "开关" or not h.get("pip"):
            continue
        ev = h.get("v3_evidence") or {}
        nets = {h.get("net")} | {g.get("net") for g in
                                 ev.get("gained_nets", [])}
        lane_doms, lane_cells, lane_nets = set(), set(), 0
        for n in nets:
            if not n:
                continue
            _t, drvs, loads = ctx.xd.net_info.get(n, (None, [], []))
            kinds = [_drv_kind(d)[0] for d in drvs]
            if not any(k == "lane" for k in kinds):
                continue
            lane_nets += 1
            cells = [S.strip_pin(x) for x in drvs + loads]
            lane_cells |= set(cells)
            lane_doms |= dom_cells(cells)
        # dst subtree attribs of lane-driven hits are already inside the
        # net's load set; nothing else to add.
        if lane_nets and crosses(lane_doms):
            return True, {"nets": sorted(x for x in nets if x),
                          "domains": dom_zh(lane_doms)}
    return False, None


OUTSIDE_CLASSES = {"shared_control", "kinput", "decoder_voter"}


def mechanism(ctx, wn, clk_trees, s, cls):
    """(primary mechanism, [all matched], info) -- structure only."""
    matched, info = [], {}
    ok, i2 = q_site_cmf(ctx, s)
    if ok:
        matched.append("site_cmf")
        info["site_cmf"] = i2
    ok, i2 = q_clock_cmf(ctx, clk_trees, s)
    if ok:
        matched.append("clock_cmf")
        info["clock_cmf"] = i2
    ok, i2 = q_route_through(ctx, wn, s)
    if ok:
        matched.append("route_through")
        info["route_through"] = i2
    ok, i2 = q_recovery_sp(ctx, wn, s)
    if ok:
        matched.append("recovery_sp")
        info["recovery_sp"] = i2
    ok, i2 = q_two_domain_route(ctx, wn, s)
    if ok:
        matched.append("two_domain_route")
        info["two_domain_route"] = i2
    if not matched:
        # §27.5.7: structural outside-protection -- the bit directly
        # changes a listed unit (drivers of changed nets for switch /
        # output-select bits, directly affected cells otherwise).
        ok, i2 = q_outside_struct(ctx, wn, s)
        if ok:
            matched.append("outside_protection")
            info["outside_protection"] = i2
    if cls in OUTSIDE_CLASSES and "outside_protection" not in matched:
        matched.append("outside_protection")
    if not matched:
        matched.append("single_domain")
    return matched[0], matched, info


# -------------------------------------------------------------- main

def s27(design, ver):
    ctx = S.Ctx(design)
    wn = S25.WireNets(ctx.dd)
    t0 = time.time()
    shots, cls_of = load_version(design, ver)

    # routed trees for clock-net hits only (subtree -> fed slices)
    clk_nets = set()
    for s in shots:
        for h in s.get("hits_detail", []):
            if h.get("resource") != "开关" or not h.get("pip"):
                continue
            ev = h.get("v3_evidence") or {}
            nets = {h.get("net")} | {g.get("net") for g in
                                     ev.get("gained_nets", [])}
            dst = h["pip"].split("->", 1)[-1].lstrip(">").strip()
            if nets & CLK_NETS or CLK_WIRE_RE.match(dst):
                clk_nets |= {n for n in nets if n}
    clk_trees, _cn2n = A3.load_net_trees(ctx, clk_nets)
    print("[%s] clk trees: %d" % (design, len(clk_trees)), flush=True)

    res_tab = collections.Counter()      # (resource, effect, cat)
    mech_tab = collections.Counter()     # (mech, cat)
    failed_rows = []                     # failed shots: resource/effect
    single_dom_failed = []               # failed + single_domain
    detail = {}                          # per-shot detail (kept for json)
    for s in shots:
        cat = s.get("category")
        cls = cls_of(s)
        pairs = set()
        hit_rows = []
        for h in s.get("hits_detail", []):
            r, e, det = hit_res_eff(ctx, wn, s, h)
            pairs.add((r, e))
            hit_rows.append({"feature": h.get("feature"),
                             "resource": r, "effect": e, "detail": det})
        if not pairs:
            pairs = {(R_UNCL, "uncl")}
        if len(pairs) == 1:
            r, e = next(iter(pairs))
        else:
            r, e = R_UNCL, "compound"
        res_tab[(r, e, cat)] += 1
        mech, mech_all, minfo = mechanism(ctx, wn, clk_trees, s, cls)
        mech_tab[(mech, cat)] += 1
        # §27.5.5: failed shots whose decode shows no change at all (every
        # hit '接法不变', or no hit features) get flagged in the list;
        # category/mechanism unchanged.
        nochg = (not hit_rows) or all(
            e in ("none", "mux_same") for _r, e in pairs)
        rec = {"index": s["index"], "category": cat, "cls": cls,
               "pairs": sorted(map(list, pairs)),
               "hits": hit_rows, "mech": mech, "mech_all": mech_all,
               "mech_info": minfo, "decode_unchanged": nochg}
        detail[s["index"]] = rec
        if cat in FAILED_CATS:
            failed_rows.append({"index": s["index"], "category": cat,
                                "cls": cls, "pairs": rec["pairs"],
                                "mech": mech, "mech_all": mech_all,
                                "decode_unchanged": nochg})
            if mech == "single_domain":
                single_dom_failed.append(rec)

    ev = {"design": design, "version": ver,
          "date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
          "res_x_eff_x_cat": {"%s|%s|%s" % k: v
                              for k, v in res_tab.items()},
          "mech_x_cat": {"%s|%s" % k: v for k, v in mech_tab.items()},
          "failed_shots": failed_rows,
          "single_domain_failed": single_dom_failed,
          "no_change_failed": [r["index"] for r in failed_rows
                               if r["decode_unchanged"]],
          "detail": detail,
          "runtime_seconds": round(time.time() - t0, 1)}
    raw = json.dumps(ev, ensure_ascii=False)
    (OUT / design / "S27_tns.json").write_text(raw, encoding="utf-8")
    (OUT / design / ("S27_tns_%s.json" % ver)).write_text(
        raw, encoding="utf-8")
    print("[%s] %s shots=%d failed=%d single_dom_failed=%d (%.0fs)"
          % (design, ver, len(shots), len(failed_rows),
             len(single_dom_failed), time.time() - t0), flush=True)
    return ev


# ----------------------------------------------------------- markdown

def md(design, ev):
    ver = ev["version"]
    L = ["## %s（%s）\n" % (design, ver)]

    # 27.2 resource x effect x category
    L.append("### 27.2 资源 × 效应 × 结果（每发按位计；一位含多种资源/"
             "效应时记“复合”）\n")
    L.append("| 资源 | 效应 |" +
             "".join(" %s |" % CAT_ZH[c] for c in CATS) + " 合计 |")
    L.append("| --- | --- |" + " ---: |" * (len(CATS) + 1))
    tab = collections.defaultdict(
        lambda: collections.Counter())
    tot = collections.Counter()
    for k, v in ev["res_x_eff_x_cat"].items():
        r, e, c = k.split("|")
        tab[(r, e)][c] += v
        tot[c] += v
    seen_pairs = set(tab)
    ordered = []
    for r in (R_SWITCH, R_LUTV, R_LUTM, R_MUX, R_SET, R_DSP, R_UNCL):
        for e in RES_EFFS.get(r, []):
            ordered.append((r, e))
    ordered += sorted(seen_pairs - set(ordered))
    for (r, e) in ordered:
        row = tab.get((r, e), {})
        lbl = EFF_ZH.get(e, e) + ("" if row else "（本批无此类）")
        L.append("| %s | %s |%s %d |"
                 % (r, lbl,
                    "".join(" %d |" % row.get(c, 0) for c in CATS),
                    sum(row.values())))
    L.append("| 合计 |  |%s %d |"
             % ("".join(" %d |" % tot.get(c, 0) for c in CATS),
                sum(tot.values())))
    L.append("")

    # failed shots' resource x effect listing
    L.append("### 失效单发的资源 × 效应（逐发）\n")
    if ev["failed_shots"]:
        L.append("| 发 | 结果 | 位置类别 | 资源×效应 | 解码有无改动 |")
        L.append("| ---: | --- | --- | --- | --- |")
        for r in ev["failed_shots"]:
            pe = "；".join("%s/%s" % (a, EFF_ZH.get(b, b))
                           for a, b in r["pairs"])
            note = ("解码未见改动，机理未定"
                    if r.get("decode_unchanged") else "—")
            L.append("| %d | %s | %s | %s | %s |"
                     % (r["index"], CAT_ZH.get(r["category"],
                                               r["category"]),
                        POS_ZH.get(r["cls"], r["cls"]), pe, note))
    else:
        L.append("（无）")
    L.append("")

    # 27.3 mechanism x category
    L.append("### 27.3 机理 × 结果（按结构判定，优先级取首条；"
             "明细见 S27_tns.json）\n")
    L.append("| 机理 |" + "".join(" %s |" % CAT_ZH[c] for c in CATS)
             + " 合计 |")
    L.append("| --- |" + " ---: |" * (len(CATS) + 1))
    mtab = collections.defaultdict(collections.Counter)
    mtot = collections.Counter()
    for k, v in ev["mech_x_cat"].items():
        m, c = k.split("|")
        mtab[m][c] += v
        mtot[c] += v
    for m in MECH_ORDER:
        row = mtab.get(m, {})
        L.append("| %s |%s %d |"
                 % (MECH_ZH[m],
                    "".join(" %d |" % row.get(c, 0) for c in CATS),
                    sum(row.values())))
    L.append("| 合计 |%s %d |"
             % ("".join(" %d |" % mtot.get(c, 0) for c in CATS),
                sum(mtot.values())))
    L.append("")
    L.append("注：P0 级存储单元没有定出路号，不算域——存储阵列为按位"
             "切片、按字排布（`u_memory` 下 `mem_reg_*` 位片各覆盖全部"
             "码字的同一字段位），单个存储位不固定属于哪一路。\n")
    nc = [r for r in ev["failed_shots"] if r.get("decode_unchanged")]
    if nc:
        by_cat = collections.defaultdict(list)
        for r in nc:
            by_cat[r["category"]].append(r["index"])
        L.append("注：解码未见改动、机理未定的失效发（类别不变，"
                 "逐发列号）：%s。"
                 % "；".join("%s %d 发：%s"
                             % (CAT_ZH.get(c, c), len(v),
                                "、".join(str(i) for i in sorted(v)))
                             for c, v in sorted(by_cat.items())))

    # §27.5.6: cells involved in the verified shots of this design
    my_checks = [(i, e) for d, i, e in CHECK_SHOTS if d == design]
    det2 = ev.get("detail", {})
    have = [(i, e, det2[str(i)] if str(i) in det2 else det2.get(i))
            for i, e in my_checks]
    have = [(i, e, r) for i, e, r in have if r]
    if have:
        L.append("### §§21–25 核对发所涉单元（级，路/副本）\n")
        for i, e, r in have:
            L.append("- **%d**（期望 %s → 实际 %s）："
                     % (i, MECH_ZH[e], MECH_ZH[r["mech"]]))
            info = r.get("mech_info") or {}
            for m in r["mech_all"]:
                ii = info.get(m) or {}
                if m == "clock_cmf" and ii.get("slices"):
                    for sp, cells in sorted(ii["slices"].items()):
                        cellstr = "、".join(
                            "%s(%s级,%s)" % (c.split("/")[-1][:26],
                                            a[0] if a[0] is not None
                                            else "?", a[1])
                            for c, a in cells)
                        L.append("  - %s：%s 单元：%s"
                                 % (MECH_ZH[m], sp, cellstr))
                elif ii.get("cells"):
                    L.append("  - %s：%s"
                             % (MECH_ZH[m], "、".join(
                                 "%s(%s级,%s)" % (c.split("/")[-1][:26],
                                                 a[0], a[1])
                                 for c, a in ii["cells"])))
                elif ii:
                    bits = []
                    for kk in ("domains", "drivers", "nets", "o6",
                               "o5_domains", "branches", "site"):
                        if ii.get(kk):
                            v = ii[kk]
                            bits.append("%s=%s" % (
                                kk, "、".join(map(str, v)) if isinstance(
                                    v, (list, set)) else str(v)))
                    L.append("  - %s：%s" % (MECH_ZH[m], "；".join(bits)))
        L.append("")

    # failed + single-domain list
    L.append("### 失效且机理为“只在一个域内”的单发——记“其余共模翻转"
             "（位置未定）”\n")
    if ev["single_domain_failed"]:
        L.append("| 发 | 结果 | 位置类别 | 命中特征 | 符合的机理 |")
        L.append("| ---: | --- | --- | --- | --- |")
        for r in ev["single_domain_failed"]:
            feats = "；".join((h["feature"] or "")[:40]
                              for h in r["hits"][:4])
            L.append("| %d | %s | %s | %s | %s |"
                     % (r["index"], CAT_ZH.get(r["category"],
                                               r["category"]),
                        POS_ZH.get(r["cls"], r["cls"]), feats,
                        "、".join(MECH_ZH[m] for m in r["mech_all"])))
    else:
        L.append("（无）")
    L.append("")
    return "\n".join(L)


def combine(ver):
    versions = [v for v in ("v2", "v31")
                if all((OUT / d / ("S27_tns_%s.json" % v)).is_file() or
                       (v == "v2" and
                        (OUT / d / "S27_tns.json").is_file())
                       for d in DESIGNS)]
    hdr = ("# WP6 §27 TNS 式单发分类表（版本：%s）\n"
           % "、".join("v2 + §24.1" if v == "v2" else "v3.1"
                       for v in versions))
    L = [hdr, "",
         "27.1 结果类别通用叫法：静默 = SDC（静默数据损坏）；标出 = 检测"
         "到、不可纠（报警）；卡住 = SEFI（功能中断）；已纠正、无影响"
         "照旧（Quinn 等，IEEE TNS 62(6), 2015）。",
         "",
         "机理结构判定（§27.3 优先级序）：site 共模 > 时钟布线共模 > "
         "直通 > 恢复电路输出单点 > 两域共用布线 > 保护之外 > 只在一个"
         "域内；一位符合多条取最前，全部符合项见各设计 S27_tns.json "
         "detail。失效且机理为“只在一个域内”者按作者裁定记“其余共模"
         "翻转（位置未定）”。", ""]
    # verification shots table
    L += ["### §§21–25 已查明发的落点核对（只核对，不改规则）", "",
          "| 设计 | 发 | 期望机理 | 实际机理 | 全部符合 | 判定 |",
          "| --- | ---: | --- | --- | --- | --- |"]
    det = {}
    for d in DESIGNS:
        f = (OUT / d / ("S27_tns_%s.json" % ver)
             if (OUT / d / ("S27_tns_%s.json" % ver)).is_file()
             else OUT / d / "S27_tns.json")
        if f.is_file():
            det[d] = json.loads(f.read_text())["detail"]
    for d, idx, expect in CHECK_SHOTS:
        r = (det.get(d) or {}).get(str(idx)) or \
            (det.get(d) or {}).get(idx)
        if not r:
            L.append("| %s | %d | %s | （无记录） | — | 缺发 |"
                     % (d, idx, MECH_ZH[expect]))
            continue
        ok = "✓" if r["mech"] == expect else "不符"
        L.append("| %s | %d | %s | %s | %s | %s |"
                 % (d, idx, MECH_ZH[expect], MECH_ZH[r["mech"]],
                    "、".join(MECH_ZH[m] for m in r["mech_all"]), ok))
    L.append("")
    for v in versions:
        for d in DESIGNS:
            f = (OUT / d / ("S27_tns_%s.md" % v)
                 if (OUT / d / ("S27_tns_%s.md" % v)).is_file()
                 else OUT / d / "S27_tns.md")
            L.append(f.read_text(encoding="utf-8") if f.is_file()
                     else "## %s（%s）\n\n（缺）\n" % (d, v))
    ct = compare_text()
    if ct:
        L.append("")
        L += ct
    p = OUT / "S27_tns_tables.md"
    p.write_text("\n".join(L), encoding="utf-8")
    agg = {"designs": DESIGNS, "versions": versions,
           "check_shots": [{"design": d, "index": i, "expect": e}
                           for d, i, e in CHECK_SHOTS],
           "per_design": {}}
    for d in DESIGNS:
        for v in versions:
            f = (OUT / d / ("S27_tns_%s.json" % v)
                 if (OUT / d / ("S27_tns_%s.json" % v)).is_file()
                 else OUT / d / "S27_tns.json")
            if f.is_file():
                j = json.loads(f.read_text())
                j.pop("detail", None)
                agg["per_design"].setdefault(d, {})[v] = j
    (OUT / "S27_tns_tables.json").write_text(
        json.dumps(agg, ensure_ascii=False), encoding="utf-8")
    print("wrote", p, "+ S27_tns_tables.json")


def _git_blob(pin, rel):
    import subprocess
    try:
        return json.loads(subprocess.check_output(
            ["git", "-C", str(OUT.parent.parent), "show",
             "%s:%s" % (pin, rel)]))
    except Exception:
        return None


def compare_text(pin="0950602", pin2="e953855"):
    """§27.5.6/27.5.7 three-column comparison: per design 'mechanism x
    failed count' of the pinned v1 pass (`pin`), the §27.5-fixed rerun
    (`pin2`), and the §27.5.7 rerun (current S27_tns.json), plus the
    list of failed shots whose mechanism changed.  Returns [] when the
    pinned v1 blob is unavailable."""
    L = ["## §27.5 修正前后机理对照（v2 + §24.1）", "",
         "前 = %s 冻结的 v2 首版；27.5 后 = %s（§27.5.1–27.5.6）；"
         "27.5.7 后 = 本轮重出。机理有变化的失效发逐发列出。"
         % (pin, pin2), ""]
    cols = ["前·失效数", "27.5 后·失效数", "27.5.7 后·失效数"]
    for d in DESIGNS:
        rel = "k7/results/wp6/%s/S27_tns_v2.json" % d
        b0 = _git_blob(pin, rel)
        if b0 is None:
            return []
        b1 = _git_blob(pin2, rel)
        after = json.loads((OUT / d / "S27_tns.json").read_text())
        maps = [{r["index"]: r for r in (x or {}).get("failed_shots", [])}
                for x in (b0, b1, after)]
        cnts = [collections.Counter(r["mech"] for r in m.values())
                for m in maps]
        L.append("## %s" % d)
        L.append("| 机理 | %s |" % " | ".join(cols))
        L.append("| --- |" + " ---: |" * len(cols))
        for m in MECH_ORDER:
            if any(c.get(m) for c in cnts):
                L.append("| %s |%s"
                         % (MECH_ZH[m],
                            "".join(" %d |" % c.get(m, 0)
                                    for c in cnts)))
        L.append("")
        ch = []
        for i in sorted(set(maps[0]) | set(maps[1]) | set(maps[2])):
            mm = [m.get(i) for m in maps]
            mechs = [r["mech"] if r else "（不在失效清单）" for r in mm]
            if len(set(mechs)) > 1:
                cat = next((r["category"] for r in mm if r), "?")
                ch.append((i, cat, mechs))
        if ch:
            L.append("机理变化的失效发（前 → 27.5 后 → 27.5.7 后）：")
            for i, c, mechs in ch:
                L.append("- %d（%s）：%s"
                         % (i, CAT_ZH.get(c, c),
                            " → ".join(MECH_ZH.get(m, m)
                                       for m in mechs)))
        else:
            L.append("机理变化的失效发：无。")
        L.append("")
    return L


def compare(pin="0950602", pin2="e953855"):
    L = ["# WP6 §27.5 修正前后机理对照（v2 + §24.1）", ""]
    body = compare_text(pin, pin2)
    if not body:
        print("compare: pinned v1 blob not found, skipped")
        return
    L += body[2:]
    p = OUT / "S27_compare.md"
    p.write_text("\n".join(L), encoding="utf-8")
    print("wrote", p)


def main():
    ver = "v31" if "v31" in sys.argv else "v2"
    args = [a for a in sys.argv[1:] if a not in ("v2", "v31")]
    if args and args[0] == "--combine":
        combine(ver)
        return
    if args and args[0] == "--compare":
        compare()
        return
    for d in (args or DESIGNS):
        ev = s27(d, ver)
        txt = md(d, ev)
        (OUT / d / "S27_tns.md").write_text(txt, encoding="utf-8")
        (OUT / d / ("S27_tns_%s.md" % ver)).write_text(
            txt, encoding="utf-8")


if __name__ == "__main__":
    main()
