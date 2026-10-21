"""WP6 §23.1 attribution v3 (offline; one process per design),
with the §24.2 corrections:

  * LUT INIT lower half (k<32): loads = all O5 destinations -- same-
    column 5LUT sibling, or decoded mux consumers ({X}OUTMUX=O5 ->
    loads of the slice-output net; {X}FFMUX=O5 / {X}5FFMUX=IN_A ->
    that FF; CARRY4.{X}CY0 -> the carry chain cell).  §24.2: for LUTs
    without an A6 pin the O6 unit is NOT merged (O5 destinations only);
    O6 consumers are unioned only when the cell really has an A6 pin.
    A re-attributed hit REPLACES its v2 by-load attrib (the LUT cell
    itself is the resource, not a load).
  * switch bits: post-flip selector decode per the 21.2 method.
    §24.2: the downstream is the routed subtree under the mux-output
    node (NNODE/NPIP tree in nload.tsv), not the wire-local load; gains
    on an in-use wire union that net's loads.  Each involved net is
    classified by the current rules (v2 by-load, section-19 by-driver,
    section-20 shared memory); differing net classes -> "mixed".

Outputs results/wp6/<design>/bit_attrib_downstream_v3.json (same schema
as v2, hits carry 'attrib_v3' + v3_evidence; shot 'attrib' = v3 union,
'attrib_v2' = v2 for reference) and s23_v3.json (G4 + reclass list).
"""
import collections
import gzip
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wp6_s21 as S
import wp6_s22 as S22
import wp6_s18 as T
from wp6a_attrib import attrib_of_cell, check_g4

OUT = S.OUT
DESIGNS = S.DESIGNS

LUT_RE = re.compile(r"(SLICE[LM])_X([01])\.([A-D])LUT\.INIT\[(\d+)\]")


# ------------------------------------------------------------- net class

def _pseudo(attrib):
    return {"attrib": [list(a) for a in attrib], "resource": "开关"}


def class_of_loads(attrib):
    cats = {a[0] for a in attrib}
    cats.discard("mixed")
    if not cats:
        return "unmapped"
    return "mixed" if len(cats) > 1 else next(iter(cats))


def class_of_net(loads_attrib, drivers, netnames):
    """One net under the current rules (v2 loads, §19 driver, §20
    shared memory), scoped to the net itself."""
    cls = class_of_loads(loads_attrib)
    sh = _pseudo(loads_attrib)
    if T.is_xrep(sh) or T.is_xlane(sh):
        g = T.driver_group(set(drivers), set(netnames))
        if g in T.DRV2POS:
            cls = T.DRV2POS[g]
        else:
            cls = T.driver_pos_class(set(drivers)) or cls
    elif T.is_shared_mem_load(sh):
        if T.driver_pos_class(set(drivers)) == "shared_control":
            cls = "shared_control"
    return cls


def hit_stages_lanes(h):
    at = h.get("attrib_v3", h.get("attrib", []))
    return {a[1] for a in at if a[1] is not None}, \
        {a[2] for a in at
         if a[0] == "protected_datapath" and a[2] is not None}


def check_g4_v3(shot_results, singles):
    """G4 on v3 per-hit attribution (attrib_v3 || attrib)."""
    corr = [(s, r) for s, r in zip(singles, shot_results)
            if s.get("category") == "corrected" and not s.get("void")]
    n = consistent = lane_n = lane_ok = 0
    inc = []
    for s, r in corr:
        stages, lanes = set(), set()
        for h in r.get("hits_detail", []):
            st, ln = hit_stages_lanes(h)
            stages |= st
            lanes |= ln
        tel = s.get("telemetry") or {}
        ts = tel.get("last_stage")
        if ts is None or len(stages) != 1:
            continue
        n += 1
        ok = (ts + 1) in stages
        consistent += ok
        lm = None
        if lanes:
            lane_n += 1
            lm = tel.get("last_loc") in lanes
            lane_ok += lm
        if not ok or lm is False:
            inc.append({"index": s.get("index"),
                        "expected_stage": ts + 1,
                        "attributed_stages": sorted(stages),
                        "last_loc": tel.get("last_loc"),
                        "lane_match": lm})
    frac = consistent / n if n else None
    lf = lane_ok / lane_n if lane_n else None
    return {"corrected_singles": len(corr), "unique_stage_judged": n,
            "stage_consistent": consistent,
            "stage_consistent_frac": round(frac, 4)
            if frac is not None else None,
            "lane_judged": lane_n, "lane_consistent": lane_ok,
            "lane_consistent_frac": round(lf, 4)
            if lf is not None else None,
            "gate": 0.95,
            "verdict": "pass" if (frac or 0) >= 0.95 else "fail",
            "inconsistencies": inc[:200]}


def srt(attribs):
    """attrib tuples with None stage/lane sort safely."""
    return sorted(attribs, key=lambda a: (
        a[0], -1 if a[1] is None else a[1],
        -1 if a[2] is None else a[2]))


def merge_classes(classes):
    cats = {c for c in classes if c != "unmapped"}
    if not cats:
        return "unmapped"
    if "mixed" in cats or len(cats) > 1:
        return "mixed"
    return next(iter(cats))


# ------------------------------------------------------------- wire->net

class NetResolver:
    def __init__(self, ctx):
        self.ctx = ctx
        self.cache = {}

    def nets_of_wire(self, wire):
        """tile/wire -> in-use nets (dst_net first, nload fallback)."""
        if wire in self.cache:
            return self.cache[wire]
        tile, w = wire.split("/", 1)
        nets = []
        n = self.ctx.xd.dst_net.get((tile, w))
        if n:
            nets = [n]
        else:
            nets = S22.wire_net(self.ctx, wire)
        nets = [x for x in nets if not x.startswith("<const")]
        self.cache[wire] = nets
        return nets


# ------------------------------------------------------------- net trees

def _pip_eps(rest):
    """NPIP endpoints after stripping 'TILE/SITE.'; yields (src,dst)
    pairs honouring ->>, ->, and bidirectional <<->> links."""
    if "<<->>" in rest:
        a, b = rest.split("<<->>", 1)
        return [(a, b), (b, a)]
    if "->>" in rest:
        return [tuple(rest.split("->>", 1))]
    if "->" in rest:
        return [tuple(rest.split("->", 1))]
    return []


class NetTree:
    """One net's routed tree: nodes (NNODE/NWIRE), pip edges (NPIP),
    site pins (NSP -> loadpins cell).  subtree_loads(node) = IN-load
    cells that are only reachable from the driver through `node`."""

    def __init__(self, adj, nsp, drivers, w2n, lp):
        self.adj = adj            # node -> {node}
        self.nsp = nsp            # node -> [(site, pin, dir)]
        self.drivers = drivers    # [node] with OUT pins
        self.w2n = w2n            # tile/wire -> node
        self.lp = lp              # (net, site, pin) -> cell

    def _reach(self, banned=None):
        seen, st = set(), list(self.drivers)
        while st:
            n = st.pop()
            if n in seen or n == banned:
                continue
            seen.add(n)
            st += list(self.adj[n])
        return seen

    def subtree(self, wire):
        """tile/wire -> (cells, nodes) of the IN loads downstream of the
        node's routed subtree.  None if wire/net unresolvable."""
        node = self.w2n.get(wire)
        if node is None or not self.drivers:
            return None
        without = self._reach(banned=node)
        allr = self._reach()
        sub = allr - without - {node}
        sub.add(node)
        cells = []
        for n in sub:
            for site, pin, d in self.nsp.get(n, ()):
                if d == "IN":
                    cells.append((site, pin))
        return cells, sorted(sub)

    def cells_of(self, site_pins):
        out = []
        for site, pin in site_pins:
            c = self.lp.get(site + "|" + pin)
            if c:
                out.append(c)
        return out


def _is_const(net):
    """Constant nets: bare '<const0/1>' hit names or hierarchical
    '<scope>/<constN>' dump names."""
    return (net is not None and
            (net in ("<const0>", "<const1>")
             or net.endswith("/<const0>") or net.endswith("/<const1>")))


def load_net_trees(ctx, nets, const_dst=None):
    """One pass over nload.tsv + loadpins.tsv for `nets`; returns
    (trees, cn2n): {net: NetTree} plus {node: hierarchical const net}.

    §24.4: constant nets carry real routed trees in nload.tsv under
    hierarchical names (u_core/.../u/<const1>); hit records store the
    bare '<const1>'.  A pre-pass maps each hit's dst node to its const
    net (a node belongs to exactly one net) so those nets join `want`.
    """
    # §24.4: const nets are routed trees too — nload/pips keep them
    # under the same bare "<constN>" names the hit records use.
    want = {n for n in nets if n}
    cn2n = {}
    for node in const_dst or ():
        pt, dst = node.split("/", 1)
        n = getattr(ctx, "xd", None) and ctx.xd.dst_net.get((pt, dst))
        if n:
            cn2n[node] = n
    want |= set(cn2n.values())
    nw = collections.defaultdict(list)      # net -> [(node, wire)]
    nsp = collections.defaultdict(lambda: collections.defaultdict(list))
    raw = collections.defaultdict(list)
    with open(ctx.dd / "wp6a_dump.nload.tsv", encoding="utf-8") as f:
        for line in f:
            t = line.split("\t")
            if len(t) < 3 or t[1] not in want:
                continue
            net = t[1]
            if t[0] == "NWIRE":
                nw[net].append((t[2], t[3].rstrip("\n")))
            elif t[0] == "NSP":
                nsp[net][t[2]].append((t[3], t[4], t[5].rstrip("\n")))
            elif t[0] == "NPIP":
                raw[net].append(t[2].rstrip("\n"))
    lp = {}
    with open(ctx.dd / "wp6a_dump.loadpins.tsv", encoding="utf-8") as f:
        for line in f:
            t = line.split("\t")
            if len(t) >= 5 and t[0] == "NLP" and t[1] in want:
                lp.setdefault(t[1], {})[t[2] + "|" + t[3]] = t[4]
    trees = {}
    for net in want:
        # NWIRE rows: field2 = node, field3 = wire  -> wmap: wire->node
        wmap = {}
        for node, wire in nw.get(net, ()):
            wmap[wire] = node
        adj = collections.defaultdict(set)
        for r in raw.get(net, ()):
            tile, rest = r.split("/", 1)
            for a, b in _pip_eps(rest):
                # strip the "SITE." prefix: site names may themselves
                # contain underscores (INT_INTERFACE_R., BRAM_L.,
                # LIOI3., ...), while wire names never contain "."
                a = a.split(".", 1)[-1]
                b = b.split(".", 1)[-1]
                sn, dn = wmap.get(tile + "/" + a), wmap.get(tile + "/" + b)
                if sn and dn and sn != dn:
                    adj[sn].add(dn)
        drivers = [n for n, ps in nsp.get(net, {}).items()
                   for p in ps if p[2] == "OUT"]
        trees[net] = NetTree(adj, nsp.get(net, {}), drivers,
                             wmap, lp.get(net, {}))
    return trees, cn2n


def net_reach(ctx, net):
    typ, drvs, loads = ctx.xd.net_info.get(net, (None, [], []))
    at = [attrib_of_cell(S.strip_pin(ld)) for ld in loads]
    return {"net": net, "type": typ, "drivers": drvs, "n_loads": len(loads),
            "attrib": [list(a) for a in srt(set(at))]}


# ------------------------------------------------------------- per-hit v3

def carry_s_driven(ctx):
    """{(site, s_index)} — CARRY4/S pins at a site that have a *routed*
    driver recorded in loadpins.tsv.  A used CARRY4 S pin with no such
    row is driven intra-site by the co-column LUT's O6 (S[A]=0..S[D]=3).
    """
    key = "_carry_s_driven"
    got = getattr(ctx, key, None)
    if got is None:
        got = set()
        with open(ctx.dd / "wp6a_dump.loadpins.tsv",
                  encoding="utf-8") as f:
            for line in f:
                t = line.split("\t")
                if (len(t) >= 6 and t[0] == "NLP"):
                    m = re.search(r"CARRY4/S(\d)$", t[5].rstrip("\n"))
                    if m:
                        got.add((t[2], int(m.group(1))))
        setattr(ctx, key, got)
    return got


def o6_dests(ctx, tile, site_tag, let, site, cell):
    """Bel-level O6 consumers of the LUT at column `let`, `site`.

    The LUT's O-pin net drives CARRY4.S and mux-fed FFs *inside* the
    slice; those intra-site nets have no routing pips, so the frozen
    dumps never enumerate them (verified: loadpin_dump.tcl only emits
    nets touching a core-tile pip).  Destinations are recovered from
    decoded mux states (same source as o5_destinations) plus the
    co-column CARRY4 S-pin rule.

    Returns (cells, nets): cell consumers + out-of-slice net names.
    """
    cells, nets = set(), []
    sites_cells = ctx.xd.site_cells.get(site, [])
    # decode the four columns' FFMUX/OUTMUX select states
    sel = {}
    for X in "ABCD":
        for kind in ("FFMUX", "OUTMUX"):
            grp = S22.grp_of(ctx, tile, S22.mux_prefix(
                tile, site_tag, X + kind) + ".")
            cur, _n, _f = S22.decode_group(ctx, tile, grp)
            sel[X + kind] = set(cur)

    def _ff_cell(X):
        return [c for c, _r, p in sites_cells if p == X + "FF"]

    def _out_nets(X):
        fam = "CLBLM" if "CLBLM" in tile else "CLBLL"
        for side in ("L", "M", "LL", "LM"):
            cand = "%s/%s_%s_%s" % (tile, fam, side, X)
            got = S22.wire_net(ctx, cand)
            if got:
                return got
        return []

    for X in "ABCD":
        for kind in ("FFMUX", "OUTMUX"):
            s_ = sel.get(X + kind, set())
            hit = False
            if X == let and "O6" in s_:
                hit = True
            elif "F7" in s_ and ((X == "A" and let in "AB")
                                 or (X == "C" and let in "CD")):
                hit = True        # F7<A/C>MUX combines the pair's O6s
            elif "F8" in s_:
                hit = True        # F8MUX combines all four O6s
            if not hit:
                continue
            if kind == "FFMUX":
                cells.update(_ff_cell(X))
            else:
                nets += _out_nets(X)
    # co-column CARRY4 S input: driven by this LUT's O6 unless a routed
    # net already binds CARRY4/S<col> at the site
    cy4 = [c for c, _r, p in sites_cells if p == "CARRY4"]
    if cy4 and (site, "ABCD".index(let)) not in carry_s_driven(ctx):
        cells.update(cy4)
    cells.discard(cell)
    return cells, nets


def lut_lower_v3(ctx, s, h, m, resolver, drv2nets):
    """INIT[k], k<32 -> O5 destinations (+ O6 consumers if A6)."""
    stag, ix, let, k = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
    cell = next((o for o in h.get("objects", [])
                 if o in ctx.xd.lut_has_a6), None)
    site_tag = "%s_X%d" % (stag, ix)
    muxes, dests = S22.o5_destinations(ctx, s["tile"], site_tag, let)
    sites = ctx.xd.sites_of_tile.get(s["tile"], [])
    site = sites[ix] if ix < len(sites) else None
    sib = [c for c, _r, p in ctx.xd.site_cells.get(site, [])
           if p == let + "5LUT" and c != cell]
    cells = set(sib)
    outnets = []
    for d in dests:
        if d["mux"].endswith("OUTMUX"):
            for n in d.get("nets", []):
                outnets.append(n)
        for c in d.get("cells", []):
            cells.add(c)
    attrib = {attrib_of_cell(c) for c in cells}
    for n in outnets:
        for a in net_reach(ctx, n)["attrib"]:
            attrib.add(tuple(a))
    # §24.2: merge O6 only when the cell really has an A6 pin; an
    # unidentified cell must NOT default to A6 (that wrongly pulled the
    # LUT's O6-side loads into lower-half bits).
    has_a6 = bool(cell) and ctx.xd.lut_has_a6.get(cell, False)
    o6 = set()
    o6_cells, o6_nets = set(), []
    if has_a6:
        # routed O-pin nets (leave the slice -> present in the dumps)
        for n in drv2nets.get(cell, []):
            o6_nets.append(n)
            for a in net_reach(ctx, n)["attrib"]:
                o6.add(tuple(a))
        # intra-site O6 consumers (FFMUX:O6 / OUTMUX:O6 / F7 / F8 /
        # co-column CARRY4.S) — invisible to the routed dumps
        oc, on = o6_dests(ctx, s["tile"], site_tag, let, site, cell)
        o6_cells |= oc
        o6_nets += [n for n in on if n not in o6_nets]
        for c in oc:
            a = attrib_of_cell(c)
            if a:
                o6.add(tuple(a))
        for n in on:
            for a in net_reach(ctx, n)["attrib"]:
                o6.add(tuple(a))
    attrib |= o6
    ev = {"muxes": muxes, "sib_5lut": sib, "o5_cells": sorted(cells),
          "o5_nets": outnets, "has_a6": has_a6,
          "o6_cells": sorted(o6_cells), "o6_nets": o6_nets,
          "o6_attrib": [list(a) for a in srt(o6)]}
    return [list(a) for a in srt(attrib)], ev


def pip_v3(ctx, s, h, resolver, trees, cn2n=None):
    """Decode post-flip connection; downstream = routed subtree under
    the mux-output node; union in-use gained-net loads."""
    pt, pip = h["pip"].split(" ", 1)
    dst = pip.split("->", 1)[1]
    grp = S22.grp_of(ctx, pt, "%s.%s." % (pt.split("_X")[0].upper(), dst))
    cur, aft, flip = S22.decode_group(ctx, pt, grp,
                                      (s["la"], s["wd"], s["lsb"]))
    gained = [w for w in aft if w not in cur]
    lost = [w for w in cur if w not in aft]
    # §24.2: v2 counted wire-local loads (e.g. 4005 -> 1); the true
    # downstream is the routed subtree under the dst node (y[17] ->
    # 5 of the 6 net loads; the sibling IMUX_L16 branch keeps its
    # driver).  Fall back to v2 loads when the tree is unresolvable.
    sub_attrib = set()
    sub_ev = None
    net = h.get("net")
    dst_node = "%s/%s" % (pt, dst)
    if net and _is_const(net):
        net = ((cn2n or {}).get(dst_node)
               or ctx.xd.dst_net.get((pt, dst))
               or net)
    tr = trees.get(net) if net else None
    if tr is not None:
        st = tr.subtree(dst_node)
        if st is not None:
            site_pins, nodes = st
            cells = tr.cells_of(site_pins)
            sub_attrib = {attrib_of_cell(c) for c in cells}
            sub_ev = {"subtree_loads": len(site_pins),
                      "subtree_cells": len(cells),
                      "subtree_nodes": len(nodes),
                      "v2_loads": len(h.get("objects") or [])}
    if sub_ev is None:
        sub_attrib = {tuple(a) for a in h.get("attrib", [])}
    nets = []
    new_attrib = set(sub_attrib)
    for w in gained:
        for n in resolver.nets_of_wire("%s/%s" % (pt, w)):
            ri = net_reach(ctx, n)
            for a in ri["attrib"]:
                new_attrib.add(tuple(a))
            nets.append({"wire": w, "net": n, "type": ri["type"],
                         "drivers": ri["drivers"], "n_loads": ri["n_loads"],
                         "attrib": ri["attrib"],
                         "cls": class_of_net(
                             ri["attrib"], ri["drivers"], {n})})
    if not lost and not gained:
        kind = "状态不变"
    elif lost and not gained:
        kind = "A_断开当前输入"
    elif not lost and gained:
        kind = "B_接上了在用的线" if nets else "C_接上了空线"
    elif lost and gained:
        kind = "D_换成另一根"
    else:
        kind = "其他"
    ev = {"dst": dst, "dst_net": net, "cur": cur, "after": aft,
          "decode": kind,
          "gained_nets": nets,
          "dst_attrib": [list(a) for a in srt(sub_attrib)]}
    if sub_ev:
        ev["dst_subtree"] = sub_ev
    return [list(a) for a in srt(new_attrib)], ev


# ------------------------------------------------------------- shot level

def process_shot(ctx, s, resolver, drv2nets, trees, cn2n):
    hits = s.get("hits_detail", [])
    v3_attrib = set() if hits else set(
        tuple(a) for a in s.get("attrib", []))
    hit_evs = []
    classes = []
    for h in hits:
        h2 = dict(h)
        ev = None
        if h.get("resource") == "查找表内容":
            m = LUT_RE.search(h.get("feature", ""))
            if m and int(m.group(4)) < 32:
                at, ev = lut_lower_v3(ctx, s, h, m, resolver, drv2nets)
                h2["attrib_v3"] = at
                h2["v3_reason"] = "O5 去处%s" % (
                    "+O6(有A6)" if (ev or {}).get("has_a6") else "")
                classes.append(class_of_loads(at))
            else:
                classes.append(class_of_loads(h.get("attrib", [])))
        elif h.get("resource") == "开关" and h.get("pip"):
            at, ev = pip_v3(ctx, s, h, resolver, trees, cn2n)
            h2["attrib_v3"] = at
            h2["v3_evidence"] = ev
            # per-net classification: dst-side net (subtree scope) +
            # each gained net
            nc = [class_of_net(ev["dst_attrib"],
                               h.get("drivers") or [],
                               {h.get("net")} if h.get("net") else set())]
            nc += [n["cls"] for n in ev["gained_nets"]]
            h2["v3_net_classes"] = nc
            classes.append(merge_classes(nc))
        else:
            classes.append(class_of_loads(h.get("attrib", [])))
        # a re-attributed hit REPLACES its v2 by-load attrib -- the
        # attributed resource cell is not itself a downstream load.
        v3_attrib |= {tuple(a) for a in h2.get("attrib_v3",
                                              h.get("attrib", []))}
        hit_evs.append(h2)
    return {"attrib": [list(a) for a in srt(v3_attrib)],
            "hits": hit_evs,
            # shots with no classifiable hits keep the v2 main class —
            # nothing was re-decoded, so nothing can be reclassified.
            "cls_v3": (merge_classes(classes) if classes
                       else T.pos_class_main(s))}


def drv2nets_map(ctx, cells):
    wanted = set(cells)
    out = collections.defaultdict(list)
    for net, (_t, drvs, _l) in ctx.xd.net_info.items():
        for d in drvs:
            if d in wanted:
                out[d].append(net)
    return out


def s23(design):
    ctx = S.Ctx(design)
    resolver = NetResolver(ctx)
    a6_cells = {o for s in ctx.shots for h in s.get("hits_detail", [])
                for o in h.get("objects", [])
                if ctx.xd.lut_has_a6.get(o)}
    d2n = drv2nets_map(ctx, a6_cells)
    t0 = time.time()
    v2j = S.load_j(OUT / design / "bit_attrib_downstream_v2.json")
    # §24.2: routed-subtree downstream for switch hits needs each net's
    # node/pip/pin tree — one pass over nload.tsv + loadpins.tsv.
    pnets = {h.get("net") for s in v2j["shots"]
             for h in s.get("hits_detail", [])
             if h.get("resource") == "开关" and h.get("pip")}
    # §24.4: const nets are routed trees too — resolve each const-net
    # hit's dst node to its hierarchical net name so its subtree is
    # verified like any other.
    const_dst = set()
    for s in v2j["shots"]:
        for h in s.get("hits_detail", []):
            if (h.get("resource") == "开关" and h.get("pip")
                    and _is_const(h.get("net"))):
                pt, pip = h["pip"].split(" ", 1)
                const_dst.add("%s/%s" % (pt, pip.split("->", 1)[1]))
    trees, cn2n = load_net_trees(ctx, pnets, const_dst)
    print("[%s] net trees: %d (%.0fs)" % (design, len(trees),
                                         time.time() - t0), flush=True)
    shots_v3 = []
    reclass = []
    for i, s in enumerate(v2j["shots"]):
        r = process_shot(ctx, s, resolver, d2n, trees, cn2n)
        s3 = dict(s)
        s3["attrib_v2"] = s.get("attrib", [])
        s3["attrib"] = r["attrib"]
        s3["hits_detail"] = r["hits"]
        s3["cls_v3"] = r["cls_v3"]
        shots_v3.append(s3)
        c2 = T.pos_class_main(s)
        if r["cls_v3"] != c2:
            reasons = sorted({h.get("v3_reason") for h in r["hits"]
                              if h.get("v3_reason")}
                             | {h["v3_evidence"]["decode"]
                                for h in r["hits"]
                                if h.get("v3_evidence")})
            reclass.append({"index": s["index"], "v2": c2, "v3": r["cls_v3"],
                            "category": s["category"],
                            "reason": "；".join(reasons)})
        if (i + 1) % 1000 == 0:
            print("[%s] %d/%d (%.0fs)" % (design, i + 1, len(v2j["shots"]),
                                          time.time() - t0), flush=True)
    singles = list(ctx.camp.values())
    singles.sort(key=lambda s: s.get("index", 0))
    by_idx = {s["index"]: s for s in shots_v3}
    # check_g4 pairs zip(singles, shot_results) positionally — align by
    # index explicitly (P1's v2 file is not index-ordered).  The v3
    # metric reads attrib_v3 per hit, falling back to the v2 attrib.
    g4 = check_g4_v3([by_idx.get(s.get("index"), {}) for s in singles],
                     singles)
    out = {"design": design, "rule": "v3 = v2 + O5 destinations + "
                                     "decoded mux effect (§23.1) + "
                                     "§24.2 fixes (no-A6 O5-only, "
                                     "routed-subtree downstream, const-net trees, "
                                     "intra-site O6 (§24.4))",
           "date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
           "g4_telemetry_downstream_v3": g4,
           "shots": shots_v3,
           "reclass_v2_to_v3": reclass,
           "runtime_seconds": round(time.time() - t0, 1)}
    p = OUT / design / "bit_attrib_downstream_v3.json"
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("[%s] wrote %s shots=%d reclass=%d g4=%.4f/%s (%.0fs)"
          % (design, p, len(shots_v3), len(reclass),
             g4["stage_consistent_frac"] or 0, g4["verdict"],
             time.time() - t0), flush=True)


def s24_patch(design):
    """Re-run only shots whose SLICEL-in-CLBLM lower-half LUT hits were
    mis-decoded by the tile-type site_tag guess (fixed: tag now comes
    from the feature name).  Such shots never carry switch hits
    (verified), so an empty tree set is safe."""
    ctx = S.Ctx(design)
    resolver = NetResolver(ctx)
    a6_cells = {o for s in ctx.shots for h in s.get("hits_detail", [])
                for o in h.get("objects", [])
                if ctx.xd.lut_has_a6.get(o)}
    d2n = drv2nets_map(ctx, a6_cells)
    t0 = time.time()
    p = OUT / design / "bit_attrib_downstream_v3.json"
    v3j = json.loads(p.read_text(encoding="utf-8"))
    v2j = S.load_j(OUT / design / "bit_attrib_downstream_v2.json")
    v2by = {s["index"]: s for s in v2j["shots"]}

    def _affected(s):
        return any(
            h.get("resource") == "\u67e5\u627e\u8868\u5185\u5bb9"
            and (lambda m: m and int(m.group(4)) < 32
                 and m.group(1) == "SLICEL" and "CLBLM" in s["tile"])(
                LUT_RE.search(h.get("feature", "")))
            for h in s["hits_detail"])

    patched = 0
    for i, s in enumerate(v3j["shots"]):
        if not _affected(s):
            continue
        s2 = v2by[s["index"]]
        r = process_shot(ctx, s2, resolver, d2n, {}, {})
        s["attrib"] = r["attrib"]
        s["hits_detail"] = r["hits"]
        s["cls_v3"] = r["cls_v3"]
        patched += 1
    # rebuild the reclass list for the patched shots
    rec = {r["index"]: r for r in v3j["reclass_v2_to_v3"]}
    for s in v3j["shots"]:
        if not _affected(s):
            continue
        c2 = T.pos_class_main(v2by[s["index"]])
        if s["cls_v3"] != c2:
            reasons = sorted({h.get("v3_reason") for h in s["hits_detail"]
                              if h.get("v3_reason")}
                             | {h["v3_evidence"]["decode"]
                                for h in s["hits_detail"]
                                if h.get("v3_evidence")})
            rec[s["index"]] = {"index": s["index"], "v2": c2,
                               "v3": s["cls_v3"], "category": s["category"],
                               "reason": "\uff1b".join(reasons)}
        else:
            rec.pop(s["index"], None)
    v3j["reclass_v2_to_v3"] = sorted(rec.values(), key=lambda r: r["index"])
    singles = list(ctx.camp.values())
    singles.sort(key=lambda s: s.get("index", 0))
    by_idx = {s["index"]: s for s in v3j["shots"]}
    v3j["g4_telemetry_downstream_v3"] = check_g4_v3(
        [by_idx.get(s.get("index"), {}) for s in singles], singles)
    v3j["date"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
    v3j["rule"] += " + site-tag L/M fix (SLICEL-in-CLBLM)"
    p.write_text(json.dumps(v3j, ensure_ascii=False), encoding="utf-8")
    print("[%s] patched=%d reclass=%d g4=%.4f/%s (%.0fs)"
          % (design, patched, len(v3j["reclass_v2_to_v3"]),
             v3j["g4_telemetry_downstream_v3"]["stage_consistent_frac"] or 0,
             v3j["g4_telemetry_downstream_v3"]["verdict"],
             time.time() - t0), flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--patch":
        s24_patch(sys.argv[2])
    else:
        s23(sys.argv[1])
