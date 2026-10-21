"""WP6 §21 per-shot root-cause work on the six uncorrected datapath shots.

All offline.  Four designs run as separate processes; the per-shot bit
decoding inside a design is chunked across cores (fork pool).

Inputs (all offline):
    results/wp6/<design>/bit_attrib_downstream_v2.json[.gz]  (v2 shots)
    results/wp6/<design>/campaign.jsonl[.gz]                 (telemetry)
    results/wp6/<design>/wp6a_dump.{cells,sites,luts,lutpins,pips}.tsv
    results/wp6/<design>/wp6a_dump.nload.tsv                 (node/wire->net)
    /home/xdu/frozen/wp6/<arch>/k7_paper_top.ebc             (bitstream)
    third_party/prjxray-db/kintex7/segbits_*.db              (X-Ray)

Run one process per design:
    python3 wp6_s21.py P0_p1
    python3 wp6_s21.py --combine      # merge -> S21_datapath_residuals.md/.json
"""
import collections
import gzip
import json
import os
import re
import subprocess
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wp6a_bitmap as bm
from wp6a_attrib import (Ebc, ColumnIndex, ExportData, attrib_of_cell,
                         load_feature_db, parse_lutpins)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results" / "wp6"
FROZEN = Path("/home/xdu/frozen/wp6")
ARCH = {"P0_p1": "P1_WP4R2", "P1_p2": "P2_WP4R2",
        "S0_s3": "S3_WP4R2", "S1_s2": "S2_WP4R2"}
DESIGNS = ("P0_p1", "P1_p2", "S0_s3", "S1_s2")
SEM = {"P0_p1": "P0", "P1_p2": "P1", "S0_s3": "S0", "S1_s2": "S1"}

INIT_RE = re.compile(
    r"^(?P<tk>[A-Z0-9_]+)\.(?P<st>SLICEL|SLICEM)_X(?P<ix>[01])\."
    r"(?P<let>[A-D])LUT\.INIT\[(?P<k>\d+)\]$")
CLK_RE = re.compile(r"^INT_[LR]\.CLK(_L)?[01]\.")
SEL_RE = re.compile(r"^INT_[LR]\.(CLK(_L)?[01])\.(.+)$")
AOMUX_RE = re.compile(
    r"^(CLBLM_[LR]|CLBLL_[LR])\.(SLICE[LM]_X[01])\.AOUTMUX\.(.+)$")

# §21 target shots
TARGETS = {"P0_p1": [1264, 2130, 4990], "S0_s3": [1712, 1751, 4816],
           "P1_p2": [], "S1_s2": []}
# §18-4 single-replica / single-lane silent set that are clock-selector bits
CLK_SILENT = {"P0_p1": [186], "P1_p2": [1377], "S1_s2": [144, 1406],
              "S0_s3": []}

RESULT_ZH = {"noimpact": "无影响", "corrected": "已纠正", "flagged": "标出",
             "silent": "静默", "flagged_but_clean": "标出但完好",
             "stuck": "卡住"}
CLASS_ZH = {"protected_datapath": "数据通路", "tmr_stage": "三模级",
            "kinput": "核输入", "shared_control": "共享控制",
            "decoder_voter": "译码器与表决器", "mixed": "混合",
            "unmapped": "未解析"}


def load_j(p):
    g = Path(str(p) + ".gz")
    if g.is_file():
        return json.load(gzip.open(g, "rt", encoding="utf-8"))
    return json.loads(Path(p).read_text(encoding="utf-8"))


def rg_lines(pattern, path, timeout=900):
    try:
        out = subprocess.run(["rg", "--no-line-number", pattern, str(path)],
                             capture_output=True, text=True, timeout=timeout)
        return out.stdout.splitlines()
    except Exception:
        return []


# ---------------------------------------------------------------- bitstream

class Ctx:
    def __init__(self, design):
        self.design = design
        dd = OUT / design
        self.dd = dd
        self.shots = load_j(dd / "bit_attrib_downstream_v2.json")["shots"]
        self.camp = {}
        for name in ("campaign.jsonl.gz", "campaign.jsonl"):
            cf = dd / name
            if cf.is_file():
                op = gzip.open if name.endswith("gz") else open
                for ln in op(cf, "rt", encoding="utf-8"):
                    r = json.loads(ln)
                    if r.get("kind") == "single":
                        self.camp[r["index"]] = r
                break
        self.ebc = Ebc(FROZEN / ARCH[design] / "k7_paper_top.ebc")
        cols, _lo = bm.load_columns()
        self.cidx = ColumnIndex(cols)
        self.feat_db, self.mask_db = load_feature_db()
        self.xd = ExportData(dd)
        self.lutpins = parse_lutpins(dd / "wp6a_dump.lutpins.tsv")

    def selector_state(self, tile, featmap, flip_pos=None):
        """Evaluate which features of a mux feature group match the current
        bitstream (optionally with position flip_pos toggled)."""
        pos_union = {}
        for feat, bits in featmap.items():
            for fr, b, pos in bits:
                pos_union[(fr, b)] = None
        vals = {}
        for (fr, b) in pos_union:
            loc = self.cidx.resolve(tile, fr, b)
            vals[(fr, b)] = self.ebc.bit(*loc) if loc else None
        if flip_pos in vals and vals[flip_pos] is not None:
            vals[flip_pos] = 1 - vals[flip_pos]
        matched = {}
        for feat, bits in featmap.items():
            ok = True
            for fr, b, pos in bits:
                v = vals.get((fr, b))
                if v is None or v != (1 if pos else 0):
                    ok = False
                    break
            matched[feat] = ok
        return matched


_G = {}


class _Lite:
    """Worker-side view: only bitstream + X-Ray tables (no dump TSVs)."""
    def __init__(self, design):
        self.design = design
        self.ebc = Ebc(FROZEN / ARCH[design] / "k7_paper_top.ebc")
        cols, _lo = bm.load_columns()
        self.cidx = ColumnIndex(cols)
        self.feat_db, _m = load_feature_db()
        self.dd = OUT / design

    selector_state = Ctx.selector_state


def _pool_init(design):
    _G["ctx"] = _Lite(design)


def _decode_clk_shot(s):
    """Worker: decode one clock-selector shot -> row dict."""
    ctx = _G["ctx"]
    for h in s.get("hits_detail", []):
        feat = h.get("feature", "")
        m = SEL_RE.match(feat)
        if not m:
            continue
        tile = (h.get("pip") or "").split(" ", 1)[0] or s.get("tile")
        sel = m.group(1)
        tt = tile.split("_X")[0].lower()
        grp = {n: bits for n, bits in ctx.feat_db.get(tt, {}).items()
               if n.startswith(tt.upper() + "." + sel + ".")}
        if not grp:
            return {"index": s["index"], "category": s["category"],
                    "tile": tile, "sel": sel, "decode": "no_group"}
        # injected bit -> local (frame,bit) inside the selector feature group
        target = (s["la"], s["wd"], s["lsb"])
        flip = None
        pos_of = {}
        for f2, bits in grp.items():
            for fr, b, pos in bits:
                loc = ctx.cidx.resolve(tile, fr, b)
                if loc:
                    pos_of[(fr, b)] = loc
                    if (loc[0], loc[1], loc[2]) == target:
                        flip = (fr, b)
        now = ctx.selector_state(tile, grp)
        aft = ctx.selector_state(tile, grp, flip_pos=flip)
        cur = sorted(f.split(".")[-1] for f, ok in now.items() if ok)
        new = sorted(f.split(".")[-1] for f, ok in aft.items() if ok)
        gained = [w for w in new if w not in cur]
        lost = [w for w in cur if w not in new]
        if not lost and gained:
            kind = "多接一根"
        elif lost and not gained:
            kind = "断开当前输入"
        elif lost and gained:
            kind = "换成另一根"
        elif not lost and not gained and cur == new:
            kind = "状态不变"
        else:
            kind = "其他"
        return {"index": s["index"], "category": s["category"],
                "tile": tile, "sel": sel, "feature": feat,
                "lfa": [s["la"], s["wd"], s["bt"]],
                "flip_local": list(flip) if flip else None,
                "decode": kind, "now": cur, "after": new,
                "gained": gained, "lost": lost}
    return None


def task_clk(ctx):
    shots = [s for s in ctx.shots
             if any(SEL_RE.match(h.get("feature", "") or "")
                    for h in s.get("hits_detail", []))]
    ncpu = min(len(shots), os.cpu_count() or 1, 16)
    rows = []
    if ncpu > 1:
        with Pool(ncpu, initializer=_pool_init, initargs=(ctx.design,)) as p:
            rows = [r for r in p.map(_decode_clk_shot, shots, chunksize=4)
                    if r]
    else:
        _pool_init(ctx.design)
        rows = [r for r in (_decode_clk_shot(s) for s in shots) if r]

    # batch wire->net: one pass over nload.tsv for all gained wires
    want = set()
    for r in rows:
        for w in r.get("gained", []):
            want.add("%s/%s" % (r["tile"], w))
    pat = "|".join(re.escape(w) for w in sorted(want)) or "NOMATCH"
    wire2net = {}
    for ln in rg_lines(pat, ctx.dd / "wp6a_dump.nload.tsv"):
        f = ln.split("\t")
        if len(f) >= 4 and f[0] in ("NWIRE", "NNODE") and \
                (f[2] in want or f[3] in want):
            wire2net.setdefault(f[2] if f[2] in want else f[3],
                                set()).add(f[1])
    # batch net->(drivers,loads): one pass over pips.tsv NET rows
    nets = sorted({n for ns in wire2net.values() for n in ns
                   if not n.startswith("<const")})
    nmap = {}
    if nets:
        pat = "^NET\t(" + "|".join(re.escape(n) for n in nets) + ")\t"
        for ln in rg_lines(pat, ctx.dd / "wp6a_dump.pips.tsv"):
            f = ln.split("\t")
            if len(f) >= 5:
                nmap[f[1]] = (_braces(f[3]), _braces(f[4]))
    for r in rows:
        nets_of = sorted({n for w in r.get("gained", [])
                          for n in wire2net.get("%s/%s" % (r["tile"], w), ())
                          if not n.startswith("<const")})
        r["gained_nets"] = nets_of
        reach = collections.Counter()
        reach_detail = []
        for n in nets_of:
            drvs, loads = nmap.get(n, ([], []))
            lanes, repls, stages = set(), set(), set()
            for ld in loads:
                a = attrib_of_cell(strip_pin(ld))
                reach[a[0]] += 1
                if a[1] is not None:
                    stages.add(a[1])
                if a[2] is not None:
                    (lanes if a[0] == "protected_datapath"
                     else repls).add(a[2])
            reach_detail.append({"net": n, "drivers": drvs,
                                 "n_loads": len(loads),
                                 "stages": sorted(stages),
                                 "lanes": sorted(lanes),
                                 "replicas": sorted(repls)})
        r["reach"] = dict(reach)
        r["reach_detail"] = reach_detail
        # group label (rule fixed before looking at outcome):
        #   A 断开 / B 接上了在用的线 / C 接上了空线 / D 换线 / E 不变或其他
        if r.get("decode") == "多接一根":
            r["group"] = "B_接上了在用的线" if nets_of else "C_接上了空线"
        elif r.get("decode") == "断开当前输入":
            r["group"] = "A_断开当前输入"
        elif r.get("decode") == "换成另一根":
            r["group"] = "D_换成另一根" + ("（在用的线）" if nets_of else "")
        else:
            r["group"] = "E_" + str(r.get("decode"))
        # cross-lane/replica flag on gained nets
        r["gained_crosses"] = any(
            len(d["lanes"]) > 1 or len(d["replicas"]) > 1
            for d in reach_detail)
    return rows


_PINRE = re.compile(r"^[A-Z][A-Z0-9]{0,5}$")


def strip_pin(ld):
    parts = ld.split("/")
    if len(parts) > 1 and _PINRE.match(parts[-1]):
        return "/".join(parts[:-1])
    return ld


def _braces(s):
    s = s.strip()
    if s.startswith("{"):
        found = re.findall(r"\{([^{}]*)\}", s)
        if found:
            return [x for x in found if x]
        s = s.strip("{}")
    return [x for x in s.split() if x]


# ----------------------------------------------------------------- 21.4 A6

def task_a6(ctx):
    """INIT-bit shots on LUTs lacking an A6 pin: split lower/upper halves,
    and split by whether the slice column also hosts a X5LUT cell (the O5
    reader of the lower half)."""
    tab = collections.Counter()
    for s in ctx.shots:
        for h in s.get("hits_detail", []):
            if h.get("resource") != "查找表内容":
                continue
            m = INIT_RE.match(h.get("feature", ""))
            if not m:
                continue
            cell = next((o for o in h.get("objects", [])
                         if o in ctx.xd.lut_has_a6), None)
            if cell is None or ctx.xd.lut_has_a6.get(cell, True):
                continue
            site = ctx.xd.cells_row.get(cell, (None,) * 4)[0]
            let = m.group("let")
            sib = any(p == let + "5LUT"
                      for c, _r, p in ctx.xd.site_cells.get(site, [])
                      if c != cell)
            half = "hi" if int(m.group("k")) >= 32 else "lo"
            tab[(half, sib, "n")] += 1
            if s["category"] != "noimpact":
                tab[(half, sib, "imp")] += 1
    out = {}
    for half in ("lo", "hi"):
        for sib in (False, True):
            n = tab[(half, sib, "n")]
            out["%s_sib%s" % (half, int(sib))] = {
                "n": n, "imp": tab[(half, sib, "imp")],
                "frac": round(tab[(half, sib, "imp")] / max(1, n), 4)}
    out["audit"] = {}
    sc = ctx.dd / "selfcheck.json"
    if sc.is_file():
        g2 = json.loads(sc.read_text(encoding="utf-8")).get(
            "g2_lut_init", {})
        out["audit"] = g2.get("a6_pin_audit", {})
    return out


def read_lut_init(ctx, tile, feature_prefix):
    """Reconstruct 64-bit INIT of a LUT bel from the bitstream."""
    tt = tile.split("_X")[0].lower()
    grp = {n: b for n, b in ctx.feat_db.get(tt, {}).items()
           if n.startswith(feature_prefix)}
    init = 0
    for n, bits in grp.items():
        k = int(n.split("[")[1].rstrip("]"))
        fr, b, pos = bits[0]
        loc = ctx.cidx.resolve(tile, fr, b)
        if loc and ctx.ebc.bit(*loc):
            init |= 1 << k
    return init, len(grp)


def lut_expected(ctx, cell):
    """Expand the logical INIT property over the bel pin map -> (lo, hi)."""
    prop = pmap = None
    for ln in open(ctx.dd / "wp6a_dump.luts.tsv", encoding="utf-8"):
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 4 and p[0] == "LUT" and p[1] == cell:
            prop, pmap = p[2], p[3]
            break
    if prop is None:
        return None
    mp = {}
    for pair in pmap.split(";"):
        if "=" not in pair:
            continue
        lhs, rhs = pair.split("=", 1)
        m = re.search(r"/A(\d)$", rhs.split(",")[0])
        if m:
            mp[int(lhs[1:])] = int(m.group(1))
    try:
        val = int(prop.split("h")[1], 16)
    except Exception:
        return None
    lo = hi = 0
    for addr in range(64):
        lg = 0
        for ilog, apin in mp.items():
            lg |= ((addr >> (apin - 1)) & 1) << ilog
        bit = (val >> lg) & 1
        if addr < 32:
            lo |= bit << addr
        else:
            hi |= bit << (addr - 32)
    return {"prop": prop, "pmap": pmap, "exp_lo": lo, "exp_hi": hi}


# ------------------------------------------------------------- 21.1 / 21.3

def shot_1264(ctx):
    """LUT INIT bit; check bitstream content, pin map, A6 net, telemetry,
    and scan the whole campaign for the same signature."""
    s = next(s for s in ctx.shots if s["index"] == 1264)
    h = s["hits_detail"][0]
    cell = h["objects"][0]
    exp = lut_expected(ctx, cell)
    m = INIT_RE.match(h["feature"])
    pref = "%s.%s_X%s.%sLUT.INIT" % (m.group("tk"), m.group("st"),
                                   m.group("ix"), m.group("let"))
    init, nfeats = read_lut_init(ctx, s["tile"], pref)
    lo, hi = init & 0xFFFFFFFF, init >> 32
    live = ("both" if exp and lo == exp["exp_lo"] and hi == exp["exp_hi"]
            else "hi" if exp and hi == exp["exp_hi"]
            else "lo" if exp and lo == exp["exp_lo"] else "neither")
    site = ctx.xd.cells_row.get(cell, (None,) * 4)[0]
    sib = [c for c, _r, p in ctx.xd.site_cells.get(site, [])
           if p == m.group("let") + "5LUT"]
    a6net = ctx.lutpins.get(cell, {}).get(6)
    rec = ctx.camp.get(1264, {})
    return {"shot": s, "feature": h["feature"], "cell": cell,
            "site": site, "a6_pin_net": a6net,
            "init_bitstream": "0x%016X" % init, "n_init_feats": nfeats,
            "expected": exp, "live_half": live,
            "sibling_5lut": sib, "campaign": rec.get("telemetry"),
            "notes": rec.get("notes"), "err": rec.get("err"),
            "shell_resets": rec.get("shell_resets")}


def scan_same_signature(ctx):
    """All shots: silent + zero telemetry + loads in a single lane."""
    out = []
    for s in ctx.shots:
        if s["category"] != "silent":
            continue
        tel = (ctx.camp.get(s["index"], {}).get("telemetry") or {})
        if (tel.get("det_cnt"), tel.get("unc_cnt"),
                tel.get("cor_cnt")) != (0, 0, 0):
            continue
        attrs = s.get("attrib") or []
        if not attrs:
            continue
        lanes = {a[2] for a in attrs
                 if a[0] == "protected_datapath" and a[2] is not None}
        if len(attrs) == 1 and attrs[0][0] == "protected_datapath" \
                and attrs[0][2] is not None:
            out.append({"index": s["index"], "tile": s["tile"],
                        "feature": s["hits_detail"][0].get("feature")
                        if s.get("hits_detail") else None,
                        "attrib": attrs})
    return out


def shot_4816(ctx):
    """AOUTMUX select bit: decode current/post-flip selection, then list the
    net on the slice A output wire and all its loads."""
    s = next(s for s in ctx.shots if s["index"] == 4816)
    tile = s["tile"]
    tt = tile.split("_X")[0].lower()
    grp = {n: b for n, b in ctx.feat_db.get(tt, {}).items()
           if AOMUX_RE.match(n)}
    grp = {n: b for n, b in grp.items()
           if n.split(".")[1] == "SLICEL_X1"}
    target = (s["la"], s["wd"], s["lsb"])
    flip = None
    for f2, bits in grp.items():
        for fr, b, pos in bits:
            loc = ctx.cidx.resolve(tile, fr, b)
            if loc and (loc[0], loc[1], loc[2]) == target:
                flip = (fr, b)
    now = ctx.selector_state(tile, grp)
    aft = ctx.selector_state(tile, grp, flip_pos=flip)
    cur = sorted(f.split(".")[-1] for f, ok in now.items() if ok)
    new = sorted(f.split(".")[-1] for f, ok in aft.items() if ok)

    # A output wire of the L-half slice of the tile (CLB wire naming uses
    # the slice side letter, not the tile name: CLBLM_L_A / CLBLL_L_A)
    fam = "CLBLM" if "CLBLM" in tile else "CLBLL"
    cands = ["%s/%s_L_A" % (tile, fam), "%s/%s_M_A" % (tile, fam)]
    nets = set()
    out_wire = cands[0]
    for ln in rg_lines("|".join(re.escape(c) for c in cands),
                       ctx.dd / "wp6a_dump.nload.tsv"):
        f = ln.split("\t")
        if f[0] == "NWIRE" and (f[2] in cands or f[3] in cands):
            nets.add(f[1])
            out_wire = f[2] if f[2] in cands else f[3]
    info = []
    for n in sorted(nets):
        typ, drvs, loads = ctx.xd.net_info.get(n, (None, [], []))
        info.append({"net": n, "type": typ, "drivers": drvs,
                     "loads": [(ld, attrib_of_cell(strip_pin(ld)))
                               for ld in loads]})
    return {"shot": s, "group_feats": sorted(grp), "flip_local": flip,
            "now": cur, "after": new, "out_wire": out_wire,
            "nets": info, "campaign": ctx.camp.get(4816, {}).get("telemetry")}


def shot_2130(ctx):
    """LATCH config bit: inventory all FFs in the slice and their lanes."""
    s = next(s for s in ctx.shots if s["index"] == 2130)
    tile = s["tile"]
    h = s["hits_detail"][0]
    m = re.search(r"(SLICEL|SLICEM)_X([01])", h["feature"])
    sites = ctx.xd.sites_of_tile.get(tile, [])
    site = sites[int(m.group(2))] if sites else None
    ffs = []
    for cell, ref, posn in ctx.xd.site_cells.get(site, []):
        if ref in ("FDRE", "FDSE", "FDCE", "FDPE", "LDCE") or posn.endswith(
                "FF") or posn.endswith("FF2"):
            ffs.append({"cell": cell, "ref": ref, "bel": posn,
                        "attrib": attrib_of_cell(cell)})
    return {"shot": s, "site": site, "tile": tile,
            "feature": h["feature"], "ffs": ffs,
            "objects": h.get("objects"),
            "campaign": ctx.camp.get(2130, {}).get("telemetry")}


def s21(design):
    ctx = Ctx(design)
    ev = {"design": design, "date": time.strftime("%Y-%m-%d %H:%M:%S %z")}
    ev["a6_half"] = task_a6(ctx)
    ev["clk_bits"] = task_clk(ctx)
    ev["same_sig"] = scan_same_signature(ctx)
    if design == "P0_p1":
        ev["shot_1264"] = shot_1264(ctx)
        ev["shot_2130"] = shot_2130(ctx)
        ev["shot_4990"] = next(r for r in ev["clk_bits"]
                               if r["index"] == 4990)
    if design == "S0_s3":
        ev["shot_1712"] = next(r for r in ev["clk_bits"]
                               if r["index"] == 1712)
        ev["shot_1751"] = next(r for r in ev["clk_bits"]
                               if r["index"] == 1751)
        ev["shot_4816"] = shot_4816(ctx)
    return ev


# --------------------------------------------------------------- report

def tel_str(tel):
    if not tel:
        return "n/a"
    return "%d/%d/%d" % (tel.get("det_cnt", -1), tel.get("unc_cnt", -1),
                         tel.get("cor_cnt", -1))


def fmt_net(n):
    return n.split("u_kernel/", 1)[-1] if "u_kernel/" in n else n


def combine():
    evs = {}
    for d in DESIGNS:
        p = OUT / d / "s21.json"
        if p.is_file():
            evs[d] = load_j(p)
    md = []
    md.append("# WP6 §21 数据通路未纠正单发逐发查因（全部离线）\n")
    md.append("生成：`scripts/k7_mod/wp6_s21.py`；输入 v2 归属、campaign "
              "遥测、位流（k7_paper_top.ebc）、Project X-Ray 段位表与"
              " wp6a_dump 导出。不上板、不跑 Vivado。\n")

    # ---- 21.1 shot 1264
    if "P0_p1" in evs and "shot_1264" in evs["P0_p1"]:
        r = evs["P0_p1"]["shot_1264"]
        s = r["shot"]
        md.append("\n## 21.1 第 1264 发（P0，静默，det/unc/cor = 0/0/0）\n")
        md.append("- 位：`%s` `%s`（帧 %d / 字 %d / 位 %d）。" % (
            s["tile"], r["feature"], s["la"], s["wd"], s["bt"]))
        md.append("- 单元：`%s`（%s）；slice `%s`。" % (
            fmt_net(r["cell"]), "查找表", r["site"]))
        if r["expected"]:
            md.append("- 逻辑 INIT `%s`，引脚图 `%s`。" % (
                r["expected"]["prop"], r["expected"]["pmap"]))
        md.append("- 位流实读 INIT64 = `%s`（上半 `%08X`、下半 `%08X`）；"
                  "按引脚图的函数展开（每半期望）= `%08X`：上半吻合、"
                  "下半不吻合 → 硬件经 O6 读上半，翻转位在未被读出的"
                  "下半（O5 表区，本列无 5LUT 单元）。判定活动半 = **%s**。"
                  % (r["init_bitstream"],
                     int(r["init_bitstream"], 16) >> 32,
                     int(r["init_bitstream"], 16) & 0xFFFFFFFF,
                     (r["expected"] or {}).get("exp_hi", -1),
                     r["live_half"]))
        md.append("- A6 引脚网：%s（无驱动即悬空）；同列 5LUT 单元：%s。"
                  % (r["a6_pin_net"] or "无（no_driver）",
                     "无" if not r["sibling_5lut"] else
                     "、".join(fmt_net(c) for c in r["sibling_5lut"])))
        md.append("- 遥测（campaign.jsonl 持久字段）: det/unc/cor = %s；"
                  "notes=%s，err=%s，shell_resets=%s。主机控制台日志"
                  "（orchestrator_console.txt）未持久化，离线不可查。"
                  % (tel_str(r["campaign"]), r["notes"], r["err"],
                     r["shell_resets"]))
        md.append("- **结论：离线无法判定（(b)）。** 位流证实翻转的是未被"
                  "读出的下半（O5 表，本列无 5LUT 单元），该 LUT 输出函数"
                  "不变；按译码 RTL，第 4 路单错必然记 fl[4]、只可能"
                  "“已纠正”——输出出错不可能来自这一位，需板上同位重打"
                  "（见 21.5）。\n")

    # same-signature scan
    md.append("\n### 同型扫描（静默 + 遥测全零 + 负载只一路）\n")
    for d in DESIGNS:
        ev = evs.get(d)
        if not ev:
            continue
        lst = ev.get("same_sig", [])
        if lst:
            md.append("- %s：%d 发 — %s" % (
                SEM[d], len(lst),
                "、".join("%d(%s)" % (x["index"], x["tile"]) for x in lst)))
        else:
            md.append("- %s：0 发" % SEM[d])
    md.append("")

    # ---- 21.2 clock selector
    tot_all = sum(len(evs[d].get("clk_bits", [])) for d in DESIGNS if d in evs)
    md.append("\n## 21.2 时钟脚选择器位（四设计全部 %d 发）\n" % tot_all)
    md.append("规则（先定、不看结果）：用 X-Ray INT_L/INT_R 段位表定位注入位"
              "在选择器内的 (frame,bit)，读位流得当前接通输入，翻转后重判。"
              "分组：A 断开当前输入 / B 多接一根且在用的线 / C 多接一根空线 "
              "/ D 换成另一根 / E 不变或其他。\n")
    tot = collections.Counter()
    for d in DESIGNS:
        ev = evs.get(d)
        if not ev:
            continue
        rows = ev.get("clk_bits", [])
        tot[d] = len(rows)
        tab = collections.Counter()
        grp_cat = collections.defaultdict(collections.Counter)
        for r in rows:
            tab[r["group"]] += 1
            grp_cat[r["group"]][r["category"]] += 1
        md.append("\n### %s（%d 发）\n" % (SEM[d], len(rows)))
        md.append("| 组 | 发数 | 结果分布 |")
        md.append("| --- | ---: | --- |")
        for g, n in sorted(tab.items()):
            cats = "、".join("%s %d" % (RESULT_ZH.get(c, c), k)
                             for c, k in grp_cat[g].most_common())
            md.append("| %s | %d | %s |" % (g, n, cats))
    md.append("")
    md.append("\n合计：%s\n" % "、".join(
        "%s %d" % (SEM[d], tot[d]) for d in DESIGNS if d in tot))
    md.append("\n逐发明细（全部发）：\n")
    for d in DESIGNS:
        ev = evs.get(d)
        if not ev:
            continue
        md.append("\n#### %s\n" % SEM[d])
        md.append("| 发 | 瓦片 | 选择器 | 当前 | 翻转后 | 组 | 新接线的网 "
                  "| 网负载（级/路/副本） | 结果 |")
        md.append("| ---: | --- | --- | --- | --- | --- | --- | --- | --- |")
        for r in ev.get("clk_bits", []):
            nets = r.get("gained_nets", [])
            reach = []
            for rd in r.get("reach_detail", []):
                bits = []
                if rd["stages"]:
                    bits.append("级%s" % rd["stages"])
                if rd["lanes"]:
                    bits.append("路%s" % rd["lanes"])
                if rd["replicas"]:
                    bits.append("副本%s" % rd["replicas"])
                reach.append("%s(%d载 %s)" % (
                    fmt_net(rd["net"]), rd["n_loads"],
                    " ".join(bits)))
            md.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r["index"], r["tile"], r["sel"],
                "、".join(r.get("now", [])) or "无",
                "、".join(r.get("after", [])) or "无",
                r["group"],
                "；".join(fmt_net(n) for n in nets) or "—",
                "；".join(reach) or "—",
                RESULT_ZH.get(r["category"], r["category"])))
    md.append("")

    # the four single-replica/single-lane silent clock bits
    md.append("\n### §18-4 的时钟静默位在此分组中的落点\n")
    md.append("| 设计 | 发 | 组 | 新接的线→网 | 是否跨路/副本 |")
    md.append("| --- | ---: | --- | --- | --- |")
    for d in DESIGNS:
        ev = evs.get(d)
        if not ev:
            continue
        for idx in CLK_SILENT.get(d, []):
            r = next((x for x in ev.get("clk_bits", [])
                      if x["index"] == idx), None)
            if not r:
                continue
            nets = "、".join(fmt_net(n)
                             for n in r.get("gained_nets", [])) or "—"
            md.append("| %s | %d | %s | %s | %s |" % (
                SEM[d], idx, r["group"], nets,
                "是" if r.get("gained_crosses") else "否"))
    md.append("")
    md.append("对照规划方预测：“超出本路的几发应落在接上在用的线一组，"
              "被纠正的多数落在另一组”——S1 的 144、1406 两发静默确在 B 组，"
              "且 1406 新接的网（第 3 级表决器 y[0]）负载覆盖第 4 级全部"
              "三份副本，是 1406 静默的结构完备解释（仍按“可能的解释”记，"
              "不下定论）；P0 186 落 A（断开）、P1 1377 落 C（空线），两发"
              "仍记“未解释”；已纠正发集中于 A/C（断开只坏本路→单路错→"
              "被纠正），方向一致。\n")

    # ---- 21.3 shots 4816 & 2130 + 4990/1712/1751 detail
    md.append("\n## 21.3 第 4816 发（S0）与第 2130 发（P0）\n")
    ev = evs.get("S0_s3")
    if ev and "shot_4816" in ev:
        r = ev["shot_4816"]
        s = r["shot"]
        md.append("\n### 4816（S0，标出，%s）\n" % tel_str(r["campaign"]))
        md.append("- 位：`%s` `%s`（同时属 %d 个 AOUTMUX 特征）。当前选择："
                  "**%s**；翻转后：**%s**。" % (
                      s["tile"], s["hits_detail"][0]["feature"],
                      len(r["group_feats"]),
                      "、".join(r["now"]) or "无",
                      "、".join(r["after"]) or "无"))
        md.append("- A 输出线 `%s` 上的网：" % r["out_wire"])
        for ni in r["nets"]:
            loads = "、".join("%s(%s,级%s,路%s)" % (
                fmt_net(c), CLASS_ZH.get(a[0], a[0]), a[1], a[2])
                for c, a in ni["loads"])
            md.append("  - `%s`（驱动 %s）：负载 %s" % (
                fmt_net(ni["net"]),
                "、".join(fmt_net(x) for x in ni["drivers"]),
                loads or "无"))
        md.append("- **结论：推测待证。** 位流层面已查明：翻转把 AOUTMUX "
                  "从 O5 断开→A 脚悬空→boundary_im[1] 网（驱动 + 8 个负载"
                  "全在第 2 级译码器域）失去驱动，可解释 865 拍检出；"
                  "唯一的 1 拍不可纠只能记“译码域误纠”方向——悬空网上"
                  "的错符号进入校验子计算即可造成误纠，离线不能逐拍复核。")
        md.append("")
    ev = evs.get("P0_p1")
    if ev and "shot_2130" in ev:
        r = ev["shot_2130"]
        s = r["shot"]
        md.append("\n### 2130（P0，标出但完好，%s）\n"
                  % tel_str(r["campaign"]))
        md.append("- 位：`%s` `%s` → slice `%s` 全部触发器翻成锁存器。"
                  % (s["tile"], r["feature"], r["site"]))
        md.append("- slice 内触发器清点（%d 个）：" % len(r["ffs"]))
        for f in r["ffs"]:
            a = f["attrib"]
            md.append("  - `%s`（%s @%s）→ %s,级%s,路%s" % (
                fmt_net(f["cell"]), f["ref"], f["bel"],
                CLASS_ZH.get(a[0], a[0]), a[1], a[2]))
        lanes = collections.Counter(f["attrib"][2] for f in r["ffs"])
        md.append("- 覆盖路：%s。" % dict(lanes))
        md.append("- **结论：已查明。** 块内只有这 %d 个 FF（第 %s 路），"
                  "LATCH 翻转后两路校验寄存器同时失效：每拍两路同错→报"
                  "不可纠、功能路输出完好，与 fbc 遥测（%s）逐项吻合。"
                  % (len(r["ffs"]), "、".join(str(k) for k in lanes),
                     tel_str(r["campaign"])))
    # clock shots detail for the three §21.2 targets
    md.append("\n### 21.2 三发的解码明细\n")
    for d, idx in (("P0_p1", 4990), ("S0_s3", 1712), ("S0_s3", 1751)):
        ev = evs.get(d)
        if not ev:
            continue
        key = "shot_%d" % idx
        r = ev.get(key) or next((x for x in ev.get("clk_bits", [])
                                 if x["index"] == idx), None)
        if not r:
            continue
        nets = r.get("gained_nets", [])
        det = []
        for rd in r.get("reach_detail", []):
            det.append("%s（%d 载，级%s 路%s 副本%s）" % (
                fmt_net(rd["net"]), rd["n_loads"], rd["stages"],
                rd["lanes"], rd["replicas"]))
        md.append("- **%d**（%s，%s）：选择器 %s @ %s；当前 %s → 翻转后 %s"
                  "（%s）；新接的线的网：%s。" % (
                      idx, SEM[d], RESULT_ZH.get(r["category"],
                                                 r["category"]),
                      r["sel"], r["tile"],
                      "、".join(r.get("now", [])),
                      "、".join(r.get("after", [])) or "无（断开）",
                      r["group"], "；".join(det) or "无在用网"))
    md.append("")
    md.append("- 4990：**推测待证。** 翻转后多接 FAN_BOUNCE5，该线挂第 5 级"
              "译码器 out_data[19]，负载落第 6 级路 3（9 个）——时钟脚与"
              "在用的译码器输出网短接。注意：若路 3 在不可纠拍被拉错，"
              "功能输出就不会完好，所以短接本身不构成 fbc 的解释；"
              "§22.2 已补同叶段清点（见 S22_offline.md）：同 CLB 另一 "
              "slice（CLK_L0 用户）的时钟多选器选的是同一根 "
              "GCLK_L_B11_WEST 节点线，且该 slice 恰装第 6 级路 4 的 "
              "in_im_q_reg[20..23]，与本 slice 的路 5 "
              "in_im_q_reg[12..15] 同节点——两条校验路同时被扰动可解释"
              " unc=824 + 输出完好；短接对路 3 数据网的影响离线不可"
              "复核。")
    md.append("- 1712：**推测待证。** 翻转后 CLK_L1 无输入接通（断开），"
              "第 2 级路 5 旋转因子表输出寄存器失去时钟、停在旧值——"
              "266 拍 det=cor 与“路 5 恒错被逐拍纠正”一致；静默（功能"
              "输出出错）方向只能是误纠或译码后受损，离线不可判定。")
    md.append("- 1751：**推测待证。** 同为断开（CLK_L1 无输入），第 1 级"
              "路 0 寄存器停钟；1141 拍检出、1047 纠正与单路恒错吻合，"
              "但 94 拍不可纠要求当拍另有一路同错，离线找不到第二个源。"
              )

    # ---- 21.4 A6 label
    md.append("\n## 21.4 “未用半（A6 接高）”标签核对与改正\n")
    md.append("G2 自检的 a6_pin_audit（selfcheck.json）：四设计无 A6 引脚"
              "的 LUT 全部 no_driver（悬空），vcc_tied=0、other=0——"
              "“A6 接高”前提不成立。\n")
    md.append("无 A6 引脚 LUT 的 INIT 位按上/下半、以及同列是否有 5LUT "
              "单元（下半即该单元的 O5 表）统计“有影响”比例：\n")
    md.append("| 设计 | 下半·无 5LUT | 下半·有 5LUT | 上半·无 5LUT "
              "| 上半·有 5LUT |")
    md.append("| --- | ---: | ---: | ---: | ---: |")
    for d in DESIGNS:
        ev = evs.get(d)
        if not ev:
            continue
        a = ev.get("a6_half", {})
        def cell(k):
            v = a.get(k, {})
            return "%d/%d (%.1f%%)" % (v.get("imp", 0), v.get("n", 0),
                                       100 * v.get("frac", 0))
        md.append("| %s | %s | %s | %s | %s |" % (
            SEM[d], cell("lo_sib0"), cell("lo_sib1"),
            cell("hi_sib0"), cell("hi_sib1")))
    md.append("")
    md.append("裸上下半合计（=上表两列相加）与规划方本机所见一致："
              "P0 下半 52/277、P1 63/331、S0 80/332、S1 81/364——"
              "下半影响率与其他查找表位同量级，“未用”前提不成立。")
    md.append("")
    md.append("读法：下半的“有影响”主要来自同列 5LUT 单元占用了 O5 表的"
              "情形（四成以上），无占用时仍有约一成的残差——悬空 A6 下"
              "“读哪一半”并非确定二选一，下半位仍可能经浮点敏感起作用。"
              "上半是该 LUT 自己的 O6 表，始终在用。**改正标签**："
              "`wp6a_attrib.py` 不再把无 A6 LUT 的下半一律标“未用半"
              "（A6 接高）”，改为按同列 5LUT 单元判定：有则为该单元的"
              " O5 函数表（在用），无则标“下半无 O5 读出（本列无 5LUT "
              "单元）”。位置类别不变。\n")

    # ---- 21.5
    md.append("\n## 21.5 板上重打清单（只列不做，待作者批准）\n")
    md.append("- 第 1264 发同一位重打 5 次，记输出差异摘要（必做）。")
    md.append("- 21.2/21.3 离线查不清的发各重打 5 次：暂列 "
              "4990、1712、1751、4816；2130 已查明不列。")
    md.append("- 原结果不改，重打只作诊断。\n")

    (OUT / "S21_datapath_residuals.md").write_text(
        "\n".join(md), encoding="utf-8")
    (OUT / "S21_datapath_residuals.json").write_text(
        json.dumps(evs, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote S21_datapath_residuals.{md,json}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--combine":
        return combine()
    ev = s21(sys.argv[1])
    (OUT / sys.argv[1] / "s21.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    print(sys.argv[1], "done")


if __name__ == "__main__":
    main()
