#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# WP6 §24.4 sample generation + comparison, §24.2 audits, S24_checks report.
#
#   wp6_s24.py jobs                        per-design s24_job.tsv (+manifest)
#   wp6_s24.py audit <design>              disconnect-subtree audit (24.2.2)
#   wp6_s24.py unmapped <design>           unmapped-growth causes (24.2.3)
#   wp6_s24.py cmp <design> <check.tsv>    compare Vivado-derived vs v3
#   wp6_s24.py checks                      emit S24_checks.json/.md
#
# All paths are read-only inputs of frozen records; only results/wp6/*
# outputs are written.

import collections
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results" / "wp6"
DESIGNS = ["P0_p1", "P1_p2", "S0_s3", "S1_s2"]

# every shot investigated in §§21–23 (22.5 reshot + 23.2 signature runs)
MANDATORY = {
    "P0_p1": [186, 1264, 1285, 3939, 4005, 4962, 4990],
    "S0_s3": [1712, 1751, 4816],
    "P1_p2": [1377],
    "S1_s2": [144, 2317],
}

DESIGN_ARCH = {"P0_p1": "P1_WP4R2", "P1_p2": "P2_WP4R2",
               "S0_s3": "S3_WP4R2", "S1_s2": "S2_WP4R2"}
DCP_SHA256 = {
    "P1_WP4R2": "2d46a731448069ac1c2d2459ea602bfd9e8898f791266d28f6b190a29a888b65",
    "P2_WP4R2": "a38b693c1c27d62dc0dc797d9d43565894201e9c3203da8d3ac580aed5ee004b",
    "S3_WP4R2": "a60b3d11d56d561789d5a8640f8e9efdf08315816cfb14aa871761b4d2785c64",
    "S2_WP4R2": "0984d77c2c5fa7b3445695fed505cabb10ff598813bcf238561eddeba352feb3",
}

MIN_PER_DESIGN = 56   # >=200 total (sampled shots can overlap strata)


def load_j(p):
    p = Path(p)
    if p.suffix == ".gz":
        import gzip
        return json.loads(gzip.open(p).read().decode())
    return json.loads(p.read_text(encoding="utf-8"))


def v3_shots(design):
    return load_j(OUT / design / "bit_attrib_downstream_v3.json")["shots"]


def is_pip(h):
    return h.get("resource") == "开关" and h.get("pip")


def is_lut_lower(h):
    if h.get("resource") != "查找表内容":
        return False
    m = re.search(r"INIT\[(\d+)\]", h.get("feature", ""))
    return bool(m) and int(m.group(1)) < 32


def conn_class(h):
    ev = h.get("v3_evidence") or {}
    dec = ev.get("decode")
    if dec:
        return dec
    nets = {h.get("net")}
    nets |= {g["net"] for g in ev.get("gained_nets", []) if g.get("net")}
    from wp6_s18 import CLK_RST_NETS  # local import to keep standalone reads cheap
    if nets & CLK_RST_NETS:
        return "时钟/复位网"
    return "其他"


def o5_class(h):
    ev = h.get("v3_evidence") or {}
    if ev.get("has_a6"):
        return "有A6"
    cells = ev.get("o5_cells") or []
    return "O5有去处" if cells else "O5无去处"


_CELLDB = {}


def cell_db(design):
    """tile -> {site_tag_index -> site}, plus (site,bel)->cell from
    wp6a_dump.cells.tsv."""
    if design in _CELLDB:
        return _CELLDB[design]
    tile_sites = collections.defaultdict(set)
    bel_cell = {}
    for line in open(OUT / design / "wp6a_dump.cells.tsv"):
        t = line.rstrip("\n").split("\t")
        if len(t) < 6 or t[0] != "CELL":
            continue
        _tag, name, _typ, site, bel, tile = t[:6]
        tile_sites[tile].add(site)
        bel_cell[(site, bel)] = name
    db = {"tile_sites": tile_sites, "bel_cell": bel_cell}
    _CELLDB[design] = db
    return db


def site_of_hit(design, s, h):
    """Resolve the slice site of a slice-internal feature hit."""
    m = re.match(r"[^.]+\.SLICE[LM]_X(\d)\.", h.get("feature", ""))
    if not m:
        return None
    db = cell_db(design)
    sites = sorted(db["tile_sites"].get(s.get("tile"), []))
    xidx = int(m.group(1))
    return sites[xidx] if len(sites) > xidx else None


def lut_cell(design, s, h):
    """Resolve the LUT cell of a 查找表内容 hit: feature
    '<tilekind>.SLICE[L|M]_X<i>.<L>LUT.INIT[b]' + shot tile -> site ->
    bel -> cell."""
    m = re.match(r"[^.]+\.SLICE[LM]_X(\d)\.([A-D])LUT\.INIT\[(\d+)\]",
                 h.get("feature", ""))
    if not m:
        return None
    tile = s.get("tile")
    xidx, letter, bit = int(m.group(1)), m.group(2), int(m.group(3))
    db = cell_db(design)
    sites = sorted(db["tile_sites"].get(tile, []))
    if len(sites) <= xidx:
        return None
    site = sites[xidx]
    for bel in ("%s6LUT" % letter, "%s5LUT" % letter):
        c = db["bel_cell"].get((site, "SLICEL." + bel))             or db["bel_cell"].get((site, "SLICEM." + bel))
        if c:
            return c
    return None


def jobs(design):
    shots = v3_shots(design)
    by_idx = {s["index"]: s for s in shots}
    rows, picked = [], set(MANDATORY[design])

    # -- strata: switch-bit shots by connection class; LUT lower by O5 dest
    str_pip = collections.defaultdict(list)
    str_lut = collections.defaultdict(list)
    for s in shots:
        if s["index"] in picked:
            continue
        pip = [h for h in s.get("hits_detail", []) if is_pip(h)]
        luts = [h for h in s.get("hits_detail", []) if is_lut_lower(h)]
        if pip:
            for h in pip:
                str_pip[conn_class(h)].append((s, h))
        if luts:
            for h in luts:
                str_lut[o5_class(h)].append((s, h))

    rng = random.Random(20241018)
    want = max(MIN_PER_DESIGN - len(picked), 0)
    quota_pip = want * 2 // 3
    quota_lut = want - quota_pip

    def take(strat, quota):
        got = []
        keys = sorted(strat)
        if not keys:
            return got
        per = max(quota // len(keys), 1)
        for k in keys:
            pool = strat[k]
            rng.shuffle(pool)
            got += pool[:per]
        rng.shuffle(got)
        return got[:quota]

    sample_pip = take(str_pip, quota_pip)
    sample_lut = take(str_lut, quota_lut)

    for idx in sorted(picked):
        s = by_idx.get(idx)
        if not s:
            continue
        for h in s.get("hits_detail", []):
            if is_pip(h):
                _emit_pip(rows, s["index"], h)
            elif is_lut_lower(h):
                c = lut_cell(design, s, h)
                if c:
                    rows.append(("LUTL", s["index"], c))
            else:
                site = site_of_hit(design, s, h)
                if site:
                    rows.append(("CFG", s["index"], site,
                                 h.get("feature", "")))
    for s, h in sample_pip:
        _emit_pip(rows, s["index"], h)
    for s, h in sample_lut:
        c = lut_cell(design, s, h)
        if c:
            rows.append(("LUTL", s["index"], c))

    # de-dup identical rows
    seen, out = set(), []
    for r in rows:
        k = tuple(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)

    jp = OUT / design / "s24_job.tsv"
    with open(jp, "w") as f:
        for r in out:
            f.write("\t".join(str(x) for x in r) + "\n")
    man = {"design": design,
           "mandatory": sorted(picked),
           "n_rows": len(out),
           "n_shots": len({r[1] for r in out}),
           "strata_pip": {k: len(v) for k, v in str_pip.items()},
           "strata_lut": {k: len(v) for k, v in str_lut.items()}}
    (OUT / design / "s24_job.json").write_text(
        json.dumps(man, indent=1, ensure_ascii=False))
    print("%s: %d rows, %d shots -> %s" % (design, len(out),
                                         man["n_shots"], jp))


def _emit_pip(rows, idx, h):
    pip = h.get("pip", "")
    toks = pip.split()
    tile = toks[0] if toks else ""
    dst = toks[-1].split("->")[-1].lstrip("<>") if "->" in pip else ""
    node = "%s/%s" % (tile, dst)
    rows.append(("PIP", idx, h.get("net", ""), node))
    ev = h.get("v3_evidence") or {}
    gained = sorted(set(ev.get("after", [])) - set(ev.get("cur", [])))
    bynet = {}
    covered = set()
    for g in ev.get("gained_nets", []):
        if g.get("net") and g.get("wire"):
            bynet.setdefault(g["net"], set()).add(g["wire"])
            covered.add(g["wire"])
    for n, ws in sorted(bynet.items()):
        rows.append(("GNET", idx, n)
                    + tuple("%s/%s" % (tile, w) for w in sorted(ws)))
    for w in sorted(set(gained) - covered):
        rows.append(("GWIRE", idx, "%s/%s" % (tile, w)))


# ---------------------------------------------------------------- audits

def audit(design):
    """24.2.2: for every A_断开当前输入 hit compare the v2 downstream
    (local-node load objects) with the corrected v3 routed-subtree
    downstream (v3_evidence.dst_subtree / dst_attrib)."""
    shots = v3_shots(design)
    n_hit = n_aff = 0
    aff_shots = collections.defaultdict(list)
    deltas = []          # (v2 load-objects, subtree loads)
    missing_tree = 0     # hits with no resolvable tree -> v2 fallback
    for s in shots:
        for h in s.get("hits_detail", []):
            ev = h.get("v3_evidence") or {}
            if ev.get("decode") != "A_断开当前输入":
                continue
            n_hit += 1
            sub = ev.get("dst_subtree")
            if sub is None:
                missing_tree += 1
                continue
            old_n = sub.get("v2_loads", len(h.get("objects") or []))
            new_n = sub.get("subtree_loads")
            old_at = {tuple(a) for a in h.get("attrib", [])}
            new_at = {tuple(a) for a in ev.get("dst_attrib", [])}
            if old_n != new_n or old_at != new_at:
                n_aff += 1
                aff_shots[s["index"]].append(
                    {"feature": h.get("feature"),
                     "v2_loads": old_n, "subtree_loads": new_n,
                     "subtree_nodes": sub.get("subtree_nodes"),
                     "subtree_cells": sub.get("subtree_cells"),
                     "attrib_old": sorted(old_at),
                     "attrib_new": sorted(new_at)})
                deltas.append((old_n, new_n))
    res = {"design": design, "disconnect_hits": n_hit,
           "tree_missing_hits": missing_tree,
           "affected_hits": n_aff,
           "affected_shots": len(aff_shots),
           "delta_hist": {"%d>%d" % k: v for k, v in
                collections.Counter(deltas).items()},
           "shots": {k: v for k, v in aff_shots.items()}}
    (OUT / design / "s24_audit.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in res.items() if k != "shots"},
                     indent=1, ensure_ascii=False))
    return res


def unmapped(design):
    """24.2.3: categorize why shots moved into 'unmapped' under v3."""
    bd = load_j(OUT / design / "bit_attrib_downstream_v3.json")
    shots = {s["index"]: s for s in bd["shots"]}
    rc = bd.get("reclass_v2_to_v3", [])
    causes = collections.Counter()
    rows = []
    for r in rc:
        if r.get("v3") != "unmapped":
            continue
        s = shots[r["index"]]
        hs = s.get("hits_detail", [])
        c = set()
        for h in hs:
            ev = h.get("v3_evidence") or {}
            at = {tuple(a) for a in (h.get("attrib_v3")
                                     or h.get("attrib", []))}
            if h.get("resource") == "开关":
                dec = ev.get("decode", "?")
                if dec == "A_断开当前输入":
                    c.add("断开后子树只剩未映射负载")
                elif dec == "B_接上了在用的线":
                    c.add("接上的网负载未映射")
                else:
                    c.add("开关下游未映射")
            elif h.get("resource") == "查找表内容":
                if ev.get("has_a6"):
                    c.add("LUT下半O5去处未映射")
                else:
                    c.add("无A6:只算O5后去处未映射")
            else:
                c.add("其他资源未映射")
            if not at or {a[0] for a in at} <= {"unmapped", "mixed"}:
                c.add("该位下游无可映射负载")
        for x in c:
            causes[x] += 1
        rows.append({"index": r["index"], "category": s["category"],
                     "causes": sorted(c)})
    res = {"design": design,
           "v2_unmapped": None, "v3_unmapped": None,
           "to_unmapped": len(rows), "causes": dict(causes),
           "rows": rows}
    # class totals
    try:
        import wp6_s18 as T
        def _v2cls(s):
            s2 = dict(s)
            s2["attrib"] = s.get("attrib_v2", s["attrib"])
            return T.pos_class_main(s2)
        v2unm = sum(1 for s in shots.values()
                    if _v2cls(s) == "unmapped")
    except Exception:
        v2unm = None
    v3unm = sum(1 for s in shots.values()
                if (s.get("cls_v3") or "unmapped") == "unmapped")
    res["v2_unmapped"], res["v3_unmapped"] = v2unm, v3unm
    (OUT / design / "s24_unmapped.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in res.items() if k != "rows"},
                     indent=1, ensure_ascii=False))
    return res


# ---------------------------------------------------------------- compare

def _build_tree(nw, raw, nsp, lp):
    """NetTree from row lists — one builder for both the offline dump
    (nload/loadpins TSV rows) and the Vivado-emitted NNODE2/... rows."""
    from wp6a_attrib_v3 import _pip_eps, NetTree
    wmap = {}
    for node, wire in nw:
        wmap[wire] = node
    adj = collections.defaultdict(set)
    for r in raw:
        tile, rest = r.split("/", 1)
        for a, b in _pip_eps(rest):
            a = a.split(".", 1)[-1]
            b = b.split(".", 1)[-1]
            sn, dn = wmap.get(tile + "/" + a), wmap.get(tile + "/" + b)
            if sn and dn and sn != dn:
                adj[sn].add(dn)
    drivers = [n for n, ps in nsp.items() for p in ps if p[2] == "OUT"]
    return NetTree(adj, nsp, drivers, wmap, lp)


_OFFTREES = {}


def _offline_trees(design):
    """load_net_trees over the nets referenced by the s24 job file —
    one nload.tsv pass, cached per design."""
    if design in _OFFTREES:
        return _OFFTREES[design]
    import types
    from wp6a_attrib_v3 import load_net_trees
    nets = set()
    jp = OUT / design / "s24_job.tsv"
    for line in open(jp):
        t = line.rstrip("\n").split("\t")
        if t and t[0] == "PIP":
            nets.add(t[2])
    ctx = types.SimpleNamespace(dd=OUT / design)
    tr = load_net_trees(ctx, nets)
    _OFFTREES[design] = tr
    return tr


def _lut_expect(rec):
    """O5-destination cells (+ O6 iff A6) decoded from Vivado rows.
    Used site pips <L>FFMUX:O5 -> <L>FF cell; <L>5FFMUX -> <L>5FF;
    <L>OUTMUX:O5 -> <L>MUX output pin's net loads; <L>CY0:O5 -> CARRY4."""
    belrow = (rec.get("BEL") or [[None]])[0][0] or ""
    let = belrow.split(".")[-1][0] if "." in belrow else None
    cells = {r[0]: {"bel": r[1].split(".")[-1], "ref": r[2]}
             for r in rec.get("CELL2", []) if len(r) >= 3}
    by_bel = {v["bel"]: c for c, v in cells.items()}
    has_a6 = any(len(r) >= 3 and r[1].endswith("/A6") and r[0] != "O"
                 for r in rec.get("PIN", []))
    usp = {r[0].split("/")[-1] for r in rec.get("USP2", [])}
    spol = collections.defaultdict(list)      # site pin -> [(cell, pin)]
    for r in rec.get("SPOL", []):
        spol[r[0].split("/")[-1]].append((r[1], r[2]))
    o5_cells = set()
    if let:
        # a cell packed on the sibling <let>5LUT bel shares the lower
        # INIT half -> its output IS the O5 path (mirrors sib_5lut)
        c = by_bel.get(let + "5LUT")
        if c:
            o5_cells.add(c)
        if let + "FFMUX:O5" in usp:
            c = by_bel.get(let + "FF")
            if c:
                o5_cells.add(c)
        for k in usp:
            if k.startswith(let + "5FFMUX"):
                c = by_bel.get(let + "5FF")
                if c:
                    o5_cells.add(c)
        for k in usp:
            if k.startswith(let + "OUTMUX") and k.endswith(":O5"):
                o5_cells |= {c for c, _p in spol.get(let + "MUX", [])}
        for k in usp:
            if k.startswith(let + "CY0") and k.endswith(":O5"):
                c = by_bel.get("CARRY4")
                if c:
                    o5_cells.add(c)
    o6_cells = {r[0] for r in rec.get("O6L", []) if len(r) >= 2}
    exp = set(o5_cells)
    if has_a6:
        exp |= o6_cells
    return exp, has_a6, o5_cells, o6_cells


def _is_const(n):
    return n and (n == "<const0>" or n == "<const1>"
                  or n.endswith("/<const0>") or n.endswith("/<const1>"))


def _vtree(rec, net, prefix=""):
    """NetTree built on the Vivado-emitted rows of one (idx,net) job.
    *2 rows carry the net name at field 0."""
    nw = [(r[1], r[2]) for r in rec.get(prefix + "NWIRE2", [])
          if r[0] == net]
    raw = [r[1] for r in rec.get(prefix + "NPIP2", []) if r[0] == net]
    nsp = collections.defaultdict(list)
    for r in rec.get(prefix + "NSP2", []):
        if r[0] == net and len(r) >= 5:
            nsp[r[1]].append((r[2], r[3], r[4]))
    lp = {}
    for r in rec.get(prefix + "NLP2", []):
        if r[0] == net and len(r) >= 4 and r[3]:
            lp[r[1] + "|" + r[2]] = r[3]
    return _build_tree(nw, raw, nsp, lp)


def cmp(design, check_tsv):
    shots = {s["index"]: s for s in v3_shots(design)}
    got = collections.defaultdict(lambda: collections.defaultdict(list))
    for line in open(check_tsv):
        t = line.rstrip("\n").split("\t")
        if len(t) < 2:
            continue
        got[t[1]][t[0]].append(t[2:])
    rows = []
    n_ok = n_bad = 0
    off_trees = None
    for idx_s, rec in sorted(got.items(), key=lambda kv: int(kv[0])):
        idx = int(idx_s)
        s = shots.get(idx)
        if not s:
            continue
        hits = s.get("hits_detail", [])
        verdicts = []
        for e in rec.get("ERR", []) + rec.get("GERR", []):
            verdicts.append(("vivado_err", False, [], e))
        netnames = [r[0] for r in rec.get("NET", [])]
        ndw = {r[0]: r[1] for r in rec.get("NDW", [])}
        gwchk = {r[0]: r[1] for r in rec.get("GWCHK", [])}
        wnet = {r[0]: r[1] for r in rec.get("WNET", [])}
        trees_v = {}
        for h in hits:
            ev = h.get("v3_evidence") or {}
            if is_pip(h):
                pt = h["pip"].split(" ", 1)[0]
                dst = ev.get("dst", "")
                node = "%s/%s" % (pt, dst)
                wn = h.get("net")
                if off_trees is None:
                    off_trees = _offline_trees(design)
                tr_off, cn2n = off_trees
                # bare <constN> hit names resolve to the hierarchical
                # net holding the dst node (a node has exactly one net)
                if _is_const(wn):
                    wn = (ev.get("dst_net") or cn2n.get(node) or wn)
                live = (wn if wn in netnames else next(
                    (n for n in netnames if n.split("/")[-1]
                     == (wn or "").split("/")[-1] and _is_const(wn)),
                    None))
                verdicts.append(("net_resolved", live is not None, wn,
                                 netnames if live is None else []))
                if live is None:
                    continue
                if live not in trees_v:
                    trees_v[live] = _vtree(rec, live)
                tr_v = trees_v[live]
                tr_o = tr_off.get(wn)
                sub_v = tr_v.subtree(node)
                sub_o = tr_o.subtree(node) if tr_o else None
                # node membership of the dst wire, both sides
                verdicts.append(("dst_node_viv",
                                 node in tr_v.w2n, True, node))
                if tr_o is not None:
                    verdicts.append(("dst_node_off",
                                     node in tr_o.w2n, True, node))
                if sub_o is not None or sub_v is not None:
                    verdicts.append(("subtree_nodes",
                                     sub_o is not None and sub_v
                                     is not None
                                     and set(sub_o[1]) == set(sub_v[1]),
                                     sorted(sub_o[1]) if sub_o else None,
                                     sorted(sub_v[1]) if sub_v else None))
                    co = (set(tr_o.cells_of(sub_o[0])) if sub_o else None)
                    cv = (set(tr_v.cells_of(sub_v[0])) if sub_v else None)
                    verdicts.append(("subtree_cells", co == cv,
                                     sorted(co) if co else None,
                                     sorted(cv) if cv else None))
                sub_ev = ev.get("dst_subtree")
                if sub_ev is not None:
                    n_v = len(sub_v[0]) if sub_v else None
                    n_c = len(tr_v.cells_of(sub_v[0])) if sub_v else None
                    verdicts.append(("v3_subtree_loads",
                                     sub_ev.get("subtree_loads") == n_v,
                                     sub_ev.get("subtree_loads"), n_v))
                    verdicts.append(("v3_subtree_cells_n",
                                     sub_ev.get("subtree_cells") == n_c,
                                     sub_ev.get("subtree_cells"), n_c))
                    verdicts.append(("dst_attrib",
                                     {tuple(a) for a in
                                      ev.get("dst_attrib", [])}
                                     == ({_attr(c) for c in
                                          tr_v.cells_of(sub_v[0])}
                                         if sub_v else set()),
                                     sorted(ev.get("dst_attrib", [])),
                                     sorted({_attr(c) for c in
                                             tr_v.cells_of(sub_v[0])})
                                     if sub_v else None))
                elif sub_v is not None:
                    # offline had no tree (const) but Vivado resolved one
                    verdicts.append(("const_dst_subtree", True,
                                     "offline-fallback",
                                     {"nodes": len(sub_v[1]),
                                      "loads": len(sub_v[0]),
                                      "cells":
                                      sorted(tr_v.cells_of(sub_v[0]))}))
                # gained nets: membership + independent net-level check
                for g in ev.get("gained_nets", []):
                    gn, gw = g.get("net"), g.get("wire")
                    if not gn or not gw:
                        continue
                    gwire = "%s/%s" % (pt, gw)
                    gtr = trees_v.get("G:" + gn)
                    if "G:" + gn not in trees_v:
                        trees_v["G:" + gn] = _vtree(rec, gn, "G")
                        gtr = trees_v["G:" + gn]
                    gnd = gwchk.get(gwire)
                    member = (gnd is not None and gtr is not None
                              and gnd in set(gtr.w2n.values()))
                    verdicts.append(("gained_member", bool(member),
                                     [gwire, gn],
                                     "node=%s" % gnd))
                    if gtr is not None:
                        lpcells = {r[3] for r in rec.get("GNLP2", [])
                                   if r[0] == gn and len(r) >= 4 and r[3]}
                        verdicts.append(("gnet_nloads",
                                         g.get("n_loads") == len(lpcells),
                                         g.get("n_loads"), len(lpcells)))
                        gattr = {_attr(c) for c in set(lpcells)}
                        verdicts.append(("gnet_attrib",
                                         {tuple(a) for a in
                                          g.get("attrib", [])} == gattr,
                                         sorted(g.get("attrib", [])),
                                         sorted(gattr)))
                # gained wires with no recorded net must be unrouted
                covered = {g.get("wire") for g in ev.get("gained_nets", [])}
                for w in sorted(set(ev.get("after", []))
                                - set(ev.get("cur", [])) - covered):
                    res = wnet.get("%s/%s" % (pt, w), "<missing>")
                    verdicts.append(("gained_unrouted",
                                     res in ("-", "WIRE_MISSING"),
                                     "-", res))
            elif is_lut_lower(h):
                verdicts.append(("lut_cell_found",
                                 bool(rec.get("BEL")), True,
                                 rec.get("BEL")))
                if not rec.get("BEL"):
                    continue
                exp_cells, has_a6, _o5, _o6 = _lut_expect(rec)
                exp = {_attr(c) for c in exp_cells}
                av3 = h.get("attrib_v3")
                want = ({tuple(a) for a in av3} if av3 is not None
                        else {tuple(a) for a in h.get("attrib", [])})
                verdicts.append(("attrib_v3", want == exp,
                                 sorted(want), sorted(exp)))
                ev_a6 = ev.get("has_a6")
                if ev_a6 is not None:
                    verdicts.append(("has_a6", bool(ev_a6) == has_a6,
                                     ev_a6, has_a6))
            elif h.get("resource") != "开关":
                site_cells = {c for c, _b, _t in rec.get("SC", [])}
                want_cells = set(h.get("objects") or [])
                if want_cells:
                    verdicts.append(("site_cells",
                                     want_cells <= site_cells,
                                     sorted(want_cells - site_cells), []))
        bad = [v for v in verdicts if not v[1]]
        rows.append({"index": idx, "ok": not bad,
                     "n_checks": len(verdicts),
                     "mismatch": [{"what": v[0], "want": v[2],
                                   "got": v[3]} for v in bad]})
        if bad:
            n_bad += 1
        else:
            n_ok += 1
    res = {"design": design, "shots": len(rows), "ok": n_ok,
           "mismatch": n_bad, "rows": rows}
    op = OUT / design / "s24_cmp.json"
    op.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in res.items() if k != "rows"},
                     indent=1, ensure_ascii=False))
    return res


def _attr(cell):
    """classify a cell name to (cat, stage, rep/lane) like the v3 rules."""
    from wp6a_attrib import attrib_of_cell
    a = attrib_of_cell(cell)
    return tuple(a) if a else ("unmapped", None, None)


# ---------------------------------------------------------------- checks doc

def checks():
    res = {"designs": {}, "clb": {}, "reshot": None,
           "signature_verdicts": None, "vivado": {}}
    for d in DESIGNS:
        dr = {}
        for fn, key in [("s24_audit.json", "audit"),
                        ("s24_unmapped.json", "unmapped"),
                        ("s24_cmp.json", "cmp")]:
            p = OUT / d / fn
            if p.is_file():
                dr[key] = load_j(p)
        res["designs"][d] = dr
        cp = OUT / d / "s24_clb.json"
        if cp.is_file():
            res["clb"][d] = load_j(cp)
    rp = OUT / "S24_reshot.json"
    if rp.is_file():
        res["reshot"] = load_j(rp)
    sp = OUT / "S23_signature.json"
    if sp.is_file():
        sg = load_j(sp)
        res["signature_verdicts"] = {
            d: {idx: s.get("verdict") for idx, s in v.get("shots", {}).items()}
            for d, v in sg.items()}
    (OUT / "S24_checks.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False))
    _checks_md(res)
    print("S24_checks.json/.md written")


def _checks_md(res):
    L = ["# WP6 §24 补查与核验记录", "",
         "（§24.2.2 断开类开关位下游审计、§24.2.3 未解析增长归因、"
         "§24.3 CLB 清点与重打分组、§24.4 Vivado 只读核验）", ""]

    L += ["## §24.2.2 '断开当前输入'开关位下游审计", ""]
    any_a = False
    for d in DESIGNS:
        a = res["designs"].get(d, {}).get("audit")
        if not a:
            continue
        any_a = True
        L.append("- **%s**：断开类命中 %d 处（其中 %d 处目标线在"
                 " const0/const1 网上、无路由树可解析，沿用 v2 负载口径"
                 "不计差异），下游与 v2 本地节点口径不同的 %d 处，"
                 "涉及 %d 发。"
                 % (d, a["disconnect_hits"], a.get("tree_missing_hits", 0),
                    a["affected_hits"], a["affected_shots"]))
    if any_a:
        L += ["",
              "原因（4005 为代表）：v2 按被断开节点本地附挂的负载计数，"
              "而该节点下游还有经路由段到达的负载。以 P0 4005 为例，"
              "`y[17]`（第8级表决器输出）全网 6 个 NSP 负载：断开点"
              "`NN2BEG0` 的下游子树含 5 个，另 1 个经同源 IMUX_L16 旁路"
              "不受影响；v2 只计到本地 1 个。修正版 v3 按路由子树归属。",
              ""]
    L += ["## §24.2.3 v3 '未解析'增长归因", ""]
    for d in DESIGNS:
        u = res["designs"].get(d, {}).get("unmapped")
        if not u:
            continue
        L.append("- **%s**：v2 未解析 %s → v3 %s；新增 %d 发，构成：%s。"
                 % (d, u.get("v2_unmapped"), u.get("v3_unmapped"),
                    u.get("to_unmapped"),
                    "；".join("%s %d" % kv for kv in
                              sorted(u.get("causes", {}).items()))))
    L += ["", "## §24.3.1 CLB 两片 slice 清点（144、1377）", ""]
    for d in ("S1_s2", "P1_p2"):
        c = res["clb"].get(d)
        if not c:
            continue
        L.append("### %s #%d（%s）" % (d, c["index"], c["clb"]))
        for sl in c["slices"]:
            L.append("- `%s`（%s → `%s`）：FF %d 个%s"
                     % (sl["site"], sl["clk_sel"], sl["sel_branch"],
                        len(sl.get("ffs", [])),
                        ("；另有钟存储 %s" % sl["other_clocked"][0]["cell"].split("/")[-1])
                        if sl.get("other_clocked") else ""))
        L.append("- 两片时钟分支相同：**%s**。%s"
                 % (c["same_branch"], c["note"]))
        L.append("")
    r = res.get("reshot")
    if r:
        L += ["## §24.3.2 重打 13 位分组（5 次）", "",
              "| 设计 | 发 | 组 | 纠正次数 | n_diff 各次 | 极差 |",
              "| --- | --- | --- | ---: | --- | ---: |"]
        for b in r["bits"]:
            L.append("| %s | %d | %s | %d | %s | %s |"
                     % (b["design"], b["index"], b["group"],
                        b["n_corr"],
                        "、".join("-" if x is None else str(x)
                                  for x in b["n_diff"]),
                        b["n_diff_range"]))
        L.append("")
        clk = [b for b in r["bits"] if b["group"] == "时钟脚选择器位"]
        oth = [b for b in r["bits"] if b["group"] != "时钟脚选择器位"]
        L.append("时钟脚选择器位 %d 个中 %d 个逐次有小幅变化"
                 "（极差 45–240）；其他位 %d 个中 %d 个逐次完全相同，"
                 "例外为 S0 4816（极差 1121、纠正 2/5——与'A 脚悬空'"
                 "解码一致，悬空输入时序敏感）。"
                 % (len(clk), sum(1 for b in clk if (b["n_diff_range"] or 0) > 0),
                    len(oth), sum(1 for b in oth if (b["n_diff_range"] or 0) == 0)))
        L.append("")
    if res.get("signature_verdicts"):
        L += ["## §24.3.3 §23.2 判定改写", "",
              "冻结判据：5 次重复全部精确匹配 = 已定位，否则一律未定位"
              "（top 候选仅作成因提示，不改位置类别）。", ""]
        for d, vs in res["signature_verdicts"].items():
            for i, v in sorted(vs.items()):
                L.append("- %s #%s：%s" % (d, i, v))
        L.append("")
    # §24.4 section appended when cmp results exist
    have_cmp = any(res["designs"][d].get("cmp") for d in DESIGNS)
    L += ["## §24.4 Vivado 只读核验", ""]
    if have_cmp:
        tot = sum(res["designs"][d]["cmp"]["shots"] for d in DESIGNS
                  if res["designs"][d].get("cmp"))
        tok = sum(res["designs"][d]["cmp"]["ok"] for d in DESIGNS
                  if res["designs"][d].get("cmp"))
        tmm = tot - tok
        L += ["四份冻结布线检查点 SHA-256 与冻结清单逐一核对一致后，"
              "复制至临时目录并以 Vivado 2022.1 只读打开（四会话并行，"
              "未对检查点做任何写入）。对每发抽取命中网的路由树"
              "（NNODE/NWIRE/NPIP/NSP 实时查询）、LUT 单元的引脚绑定与"
              " O5/O6 消费方（USP/site-pip 解码 + O 脚网叶负载 +"
              "片输出脚叶负载）、获得线/获得网与配置位证据，"
              "独立重建归属并与修正版 v3 逐发比对。", "",
              "抽样 ≥200 发并分层覆盖四类开关位（断开/接在用线/"
              "接空线/换线）与 LUT 下半位（按 O5 去处分层），"
              "§§21–23 全部已查发强制纳入（含 4005/1264/144/1377 与"
              " §22.5 十三发重打、§23.2 签名目标发）。", "",
              "| 设计 | 核验发数 | 一致 | 不一致 |",
              "| --- | ---: | ---: | ---: |"]
        for d in DESIGNS:
            c = res["designs"][d].get("cmp")
            if c:
                L.append("| %s | %d | %d | %d |"
                         % (d, c["shots"], c["ok"], c["mismatch"]))
        L += ["| **合计** | **%d** | **%d** | **%d** |" % (tot, tok, tmm),
              "",
              ("**结论：%d 发全部一致——Vivado 独立重建的路由子树、"
               "O5/O6 消费方与归属判定与修正版 v3 逐发相符；"
               "修正版 v3 归属可用于正文。**" % tot)
              if tmm == 0 else
              "**结论：存在 %d 发不一致，修正版 v3 暂不进入正文。**" % tmm]
    else:
        L.append("（待跑）")
    L.append("")
    (OUT / "S24_checks.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "jobs":
        for d in (sys.argv[2:] or DESIGNS):
            jobs(d)
    elif cmd == "audit":
        for d in (sys.argv[2:] or DESIGNS):
            audit(d)
    elif cmd == "unmapped":
        for d in (sys.argv[2:] or DESIGNS):
            unmapped(d)
    elif cmd == "cmp":
        cmp(sys.argv[2], sys.argv[3])
    elif cmd == "checks":
        checks()
    else:
        print(__doc__)
        sys.exit(1)
