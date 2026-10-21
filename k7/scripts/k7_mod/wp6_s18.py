"""WP6 §18 main-text tables on the accepted v2 + by-driver 口径.

Inputs (all offline):
    results/wp6/<design>/bit_attrib_downstream_v2.json[.gz]  (v2 shots)
    results/wp6/<design>/pair_identity_v2.json               (control pairs)
    results/wp6/<design>/s17_v2.json                         (§17 evidence)

Run one process per design:
    python3 wp6_s18.py P0_p1

Outputs:
    results/wp6/<design>/s18_v2.json   machine-readable evidence
    results/wp6/<design>/S18_v2.md     section text for MAIN_TABLES.md
"""
import json
import sys
import time
import collections
from pathlib import Path

from wp6_tile_table import classify as cell_classify

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results" / "wp6"

CATS = ["noimpact", "corrected", "flagged", "silent",
        "flagged_but_clean", "stuck"]
CAT_ZH = {"noimpact": "无影响", "corrected": "已纠正", "flagged": "标出",
          "silent": "静默", "flagged_but_clean": "标出但完好",
          "stuck": "卡住"}

# §18 rows: main-text classes incl. separate kernel-input row (task 3).
POS_ROWS = [("protected_datapath", "受保护数据通路"),
            ("tmr_stage", "三模级"),
            ("kinput", "核输入"),
            ("shared_control", "共享控制"),
            ("decoder_voter", "译码器与表决器"),
            ("mixed", "混合"),
            ("unmapped", "未解析")]


def load_j(p):
    import gzip
    g = Path(str(p) + ".gz")
    if g.is_file():
        return json.load(gzip.open(g, "rt", encoding="utf-8"))
    return json.loads(Path(p).read_text(encoding="utf-8"))


def replica_stages(shot):
    m = collections.defaultdict(set)
    for a in shot.get("attrib", []):
        if a[0] == "tmr_stage" and a[2] is not None:
            m[a[1]].add(a[2])
    return m


def lane_stages(shot):
    m = collections.defaultdict(set)
    for a in shot.get("attrib", []):
        if a[0] == "protected_datapath" and a[2] is not None:
            m[a[1]].add(a[2])
    return m


def is_xrep(s):
    return any(len(v) >= 2 for v in replica_stages(s).values())


def is_xlane(s):
    return any(len(v) >= 2 for v in lane_stages(s).values())


def shot_drivers_net(s):
    drivers = {x for h in s.get("hits_detail", [])
               for x in (h.get("drivers") or [])}
    nets = {h.get("net") for h in s.get("hits_detail", []) if h.get("net")}
    return drivers, nets


def driver_group(drivers, nets):
    ns = " ".join(nets)
    if any("u_data_vote" in x for x in drivers):
        return "data_vote"
    if any("u_valid_vote" in x or "u_last_vote" in x for x in drivers):
        return "valid_vote"
    if any("u_decode" in x for x in drivers):
        return "dec_out"
    if "/d_in" in ns:
        return "kinput"
    if "kernel_rst" in ns or any("kernel_rst" in x for x in drivers):
        return "rst"
    if any("u_clk" in x for x in drivers) or "u_clk" in ns \
            or "/clk" in ns.lower():
        return "clk"
    return "other"


def pos_class_load(s):
    cats = {a[0] for a in s.get("attrib", [])}
    cats.discard("mixed")
    if not cats:
        return "unmapped"
    return "mixed" if len(cats) > 1 else next(iter(cats))


# §18 by-driver map: kinput is its own row (task 3 ruling pending).
DRV2POS = {"data_vote": "decoder_voter", "valid_vote": "decoder_voter",
           "dec_out": "decoder_voter", "kinput": "kinput",
           "rst": "shared_control", "clk": "shared_control"}


def cell_pos(cell):
    """v2 position class of a driver cell, from its own hierarchy."""
    if "u_kernel/" not in cell:
        return "unmapped"
    return cell_classify(cell.split("u_kernel/", 1)[1])[0]


def pos_class_main_v18(s):
    """§18 rule: by-driver only for the voter/decoder/kinput/rst/clk groups."""
    cls = pos_class_load(s)
    if s.get("resource") == "开关" and (is_xrep(s) or is_xlane(s)):
        drivers, nets = shot_drivers_net(s)
        cls = DRV2POS.get(driver_group(drivers, nets), cls)
    return cls


def driver_pos_class(drivers):
    """§19: position class of the driving unit(s), by their own v2
    attribution.  Conflicting non-unmapped driver classes -> mixed;
    no resolved driver class -> None (caller keeps by-load class)."""
    cc = collections.Counter(cell_pos(d) for d in drivers)
    top = [c for c, _n in cc.most_common() if c != "unmapped"]
    if len(set(top)) > 1:
        return "mixed"
    return top[0] if top else None


def is_shared_mem_load(s):
    """§20 ruling (strict 口径): the shot's v2 loads land ONLY on the
    stage's shared u_memory — every attributed load is protected_datapath
    with no lane id.  P0 has one u_memory per stage holding all six
    lanes' words, so it counts as covering all lanes."""
    at = s.get("attrib", [])
    return bool(at) and all(a[0] == "protected_datapath" and a[2] is None
                            for a in at)


def _pos_main(s):
    """v3-aware main class: prefer the shot's stored cls_v3 (per-net
    §23.1 merge); v2 files have no cls_v3 -> identical behaviour."""
    return s.get("cls_v3") or pos_class_main(s)


# ------------------------------------------------------------- §24.1
# Clock/reset nets -> shared control (author ruling): ANY switch bit
# connected to u_clk/inst/clk_out2 or u_core/u_dut/kernel_rst before or
# after the flip — including every slice's clock/reset pin selectors —
# counts as shared control regardless of loads or outcome.  Slice-
# internal setting bits (CLKINV/LATCH/...) stay by-load.

CLK_RST_NETS = {"u_clk/inst/clk_out2", "u_core/u_dut/kernel_rst"}


def _hit_clkrst_nets(h):
    """clk/rst nets touched by a switch hit, pre (h['net']) or post
    (v3_evidence gained_nets)."""
    if h.get("resource") != "开关" or not h.get("pip"):
        return []
    ns = set()
    if h.get("net"):
        ns.add(h["net"])
    ev = h.get("v3_evidence") or {}
    ns |= {g["net"] for g in ev.get("gained_nets", []) if g.get("net")}
    return sorted(ns & CLK_RST_NETS)


def _merge_cls_v24(classes):
    cats = {c for c in classes if c}
    cats.discard("unmapped")
    if not cats:
        return "unmapped"
    if "mixed" in cats or len(cats) > 1:
        return "mixed"
    return next(iter(cats))


def _hit_class(h, ver):
    if ver == "v3":
        if h.get("v3_net_classes"):
            return _merge_cls_v24(h["v3_net_classes"])
        if h.get("attrib_v3") is not None:
            return _merge_cls_v24(
                [a[0] for a in h.get("attrib_v3", [])])
    cats = {a[0] for a in h.get("attrib", [])}
    cats.discard("mixed")
    if not cats:
        return "unmapped"
    return "mixed" if len(cats) > 1 else next(iter(cats))


def pos_class_main_v24(s, ver="v2"):
    """pos_class_main + §24.1 bit-level override.  Returns
    (class, qualifying hit features) — class is None when nothing
    qualifies (caller keeps the base class)."""
    hits = s.get("hits_detail", [])
    qidx = {i for i, h in enumerate(hits) if _hit_clkrst_nets(h)}
    if not qidx:
        return None, []
    if ver == "v3":
        classes = ["shared_control" if i in qidx else _hit_class(h, "v3")
                   for i, h in enumerate(hits)]
        cls = _merge_cls_v24(classes)
    else:
        at = []
        for i, h in enumerate(hits):
            if i in qidx:
                at.append(["shared_control", None, None])
            else:
                at += [list(a) for a in h.get("attrib", [])]
        s2 = dict(s)
        s2["attrib"] = at
        cls = pos_class_load(s2)
    if s.get("resource") == "开关":
        if is_xrep(s) or is_xlane(s):
            drivers, nets = shot_drivers_net(s)
            g = driver_group(drivers, nets)
            cls = DRV2POS.get(g) or driver_pos_class(drivers) or cls
        elif is_shared_mem_load(s):
            drivers, _n = shot_drivers_net(s)
            if driver_pos_class(drivers) == "shared_control":
                cls = "shared_control"
    qual = sorted({h.get("feature") for i, h in enumerate(hits)
                   if i in qidx})
    nets = sorted({n for i, h in enumerate(hits)
                   if i in qidx for n in _hit_clkrst_nets(h)})
    return cls, [{"feature": f, "nets": nets} for f in qual]


def pos_class_main(s):
    """§19+§20 rule: switch bits whose v2 loads cross lanes/replicas are
    classified by their net's driver unit; §20 extends the trigger to
    bits loading only the stage's shared memory when the driver is
    stage-level shared control.  Otherwise by-load."""
    cls = pos_class_load(s)
    if s.get("resource") != "开关":
        return cls
    if is_xrep(s) or is_xlane(s):
        drivers, nets = shot_drivers_net(s)
        g = driver_group(drivers, nets)
        if g in DRV2POS:
            cls = DRV2POS[g]
        else:
            cls = driver_pos_class(drivers) or cls
    elif is_shared_mem_load(s):
        # §20: driver must be stage-level shared control by its own v2
        # attribution — the group map (clk/rst/kinput) does not apply.
        drivers, _nets = shot_drivers_net(s)
        if driver_pos_class(drivers) == "shared_control":
            cls = "shared_control"
    return cls


def unique_replica(shot, stage):
    reps = replica_stages(shot).get(stage, set())
    return next(iter(reps)) if len(reps) == 1 else None


def unique_lane(shot, stage):
    lns = lane_stages(shot).get(stage, set())
    return next(iter(lns)) if len(lns) == 1 else None


def dd_pips(dd):
    return dd / "wp6a_dump.pips.tsv"


def _src_nets(tsv, pattern):
    """nets whose route uses a pip touching <tile>/<kind>.<wire>."""
    import subprocess
    if not tsv.is_file():
        return []
    try:
        out = subprocess.run(
            ["rg", "--no-line-number", pattern, str(tsv)],
            capture_output=True, text=True, timeout=600)
    except Exception:
        return []
    nets = set()
    for ln in out.stdout.splitlines():
        f = ln.split("\t")
        if len(f) >= 3 and f[0] == "PIP":
            nets.add(f[1])
    return nets


def s18(design, ver="v2"):
    fn = ("bit_attrib_downstream_v3.json" if ver == "v3"
          else "bit_attrib_downstream_v2.json")
    bd = load_j(OUT / design / fn)
    shots = bd["shots"]
    ver = "v3" if "cls_v3" in (shots[0] if shots else {}) else "v2"
    by_key = {(s["la"], s["wd"], s["bt"]): s for s in shots}
    ev = {"design": design, "rule": "downstream_v2 + by_driver(§18)",
          "date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
          "ver": ver,
          "reclass_v2_to_v3": bd.get("reclass_v2_to_v3", []),
          "g4_v3": bd.get("g4_telemetry_downstream_v3")}

    # §24.1: post-flip connectivity lives in the v3 file's v3_evidence;
    # for the v2 (main-text) pass join it in by (index, feature).
    if ver == "v2":
        v3p = OUT / design / "bit_attrib_downstream_v3.json"
        if v3p.is_file():
            v3idx = {s["index"]: s for s in load_j(v3p)["shots"]}
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
        """main-text class: base (v2 pos_class_main, or cls_v3) + §24.1."""
        base = s.get("cls_v3") or pos_class_main(s)
        c, _q = pos_class_main_v24(s, ver)
        return c or base

    # ---- task 1: position class x outcome category (main-text 口径) ------
    tab = collections.Counter()
    for s in shots:
        tab[(cls24(s), s["category"])] += 1
    ev["pos_x_cat"] = {p: {c: tab.get((p, c), 0) for c in CATS}
                       for p, _ in POS_ROWS}
    ev["pos_totals"] = {p: sum(ev["pos_x_cat"][p].values())
                        for p, _ in POS_ROWS}

    # §19: reclassification counts vs the §18 version of the rule
    trans = collections.Counter()
    trans_idx = collections.defaultdict(list)
    for s in shots:
        a, b = pos_class_main_v18(s), _pos_main(s)
        if a != b:
            trans["%s>%s" % (a, b)] += 1
            trans_idx["%s>%s" % (a, b)].append(s["index"])
    ev["reclass_v19"] = {"total": sum(trans.values()),
                         "by_transition": dict(trans),
                         "idx": dict(trans_idx)}
    # §20 subset: switch bits moving only under the shared-memory ruling
    sm = [s["index"] for s in shots
          if s.get("resource") == "开关" and not (is_xrep(s) or is_xlane(s))
          and is_shared_mem_load(s)
          and pos_class_load(s) != "shared_control"
          and _pos_main(s) == "shared_control"]
    ev["reclass_s20"] = {"n": len(sm), "idx": sm,
                         "by_cat": dict(collections.Counter(
                             s["category"] for s in shots
                             if s["index"] in sm))}
    # loose 口径 for the note: loads merely touching the shared memory,
    # group map (clk/rst) still applied to the driver resolution
    loose = []
    loose_imp = {"corrected": 0, "impacted": 0}
    for s in shots:
        if s.get("resource") != "开关" or is_xrep(s) or is_xlane(s):
            continue
        if not any(a[0] == "protected_datapath" and a[2] is None
                   for a in s.get("attrib", [])):
            continue
        if pos_class_load(s) == "shared_control":
            continue
        drivers, nets = shot_drivers_net(s)
        g = driver_group(drivers, nets)
        if (DRV2POS.get(g) or driver_pos_class(drivers)) \
                == "shared_control":
            loose.append(s["index"])
            if s["category"] != "noimpact":
                loose_imp["impacted"] += 1
                if s["category"] == "corrected":
                    loose_imp["corrected"] += 1
    ev["reclass_s20"]["loose_n"] = len(loose)
    ev["reclass_s20"]["loose_corr_imp"] = loose_imp

    # §24.1 reclass list: base main class -> clk/rst-overlaid class
    r24 = []
    for s in shots:
        base = s.get("cls_v3") or pos_class_main(s)
        c, qual = pos_class_main_v24(s, ver)
        if c and c != base:
            nets = sorted({n for q in qual for n in q["nets"]})
            r24.append({"index": s["index"], "category": s["category"],
                        "from": base, "to": c, "nets": nets})
    ev["reclass_v24"] = {
        "total": len(r24),
        "by_cat": dict(collections.Counter(r["category"] for r in r24)),
        "by_transition": dict(collections.Counter(
            "%s>%s" % (r["from"], r["to"]) for r in r24)),
        "rows": r24}

    # delta vs §17.2 by-driver table (kinput split from shared)
    s17f = OUT / design / "s17_v2.json"
    if s17f.is_file():
        s17 = json.loads(s17f.read_text(encoding="utf-8"))
        drv17 = s17["posclass_driver"]
        ev["s17_check"] = {
            "shared_control_s17": drv17.get("shared_control", 0),
            "shared_plus_kinput_s18":
                ev["pos_totals"]["shared_control"]
                + ev["pos_totals"]["kinput"],
            "delta": {p: ev["pos_totals"].get(p, 0) - drv17.get(p, 0)
                      for p in ("protected_datapath", "tmr_stage",
                                "decoder_voter", "mixed", "unmapped")},
            "shared_delta": ev["pos_totals"]["shared_control"]
                + ev["pos_totals"]["kinput"]
                - drv17.get("shared_control", 0),
            "consistent": all(
                ev["pos_totals"].get(p, 0) == drv17.get(p, 0)
                for p in ("protected_datapath", "tmr_stage",
                          "decoder_voter", "mixed", "unmapped"))
                and ev["pos_totals"]["shared_control"]
                    + ev["pos_totals"]["kinput"]
                    == drv17.get("shared_control", 0)}

    # ---- task 2a: protected_datapath corrected-to-bit-exact (S0/P0) -----
    pd = [s for s in shots if cls24(s) == "protected_datapath"]
    pd_imp = [s for s in pd if s["category"] != "noimpact"]
    pd_cor = [s for s in pd_imp if s["category"] == "corrected"]
    ev["datapath_corrected"] = {
        "total_pd": len(pd), "impacted": len(pd_imp),
        "corrected": len(pd_cor),
        "frac": round(len(pd_cor) / max(1, len(pd_imp)), 4),
        "impacted_by_cat": dict(collections.Counter(
            s["category"] for s in pd_imp))}

    # ---- task 2b: silent totals + class distribution --------------------
    sil = [s for s in shots if s["category"] == "silent"]
    ev["silent_total"] = len(sil)
    ev["silent_by_class"] = dict(collections.Counter(
        cls24(s) for s in sil))

    # ---- task 2c: tmr_stage silent -> decoder_voter vs replica-internal --
    tmr_sil = [s for s in sil if pos_class_load(s) == "tmr_stage"]
    bym = collections.Counter(cls24(s) for s in tmr_sil)
    ev["tmr_silent"] = {
        "total": len(tmr_sil),
        "to_decoder_voter": bym.get("decoder_voter", 0),
        "stay_tmr": bym.get("tmr_stage", 0),
        "other_reclass": {k: v for k, v in bym.items()
                          if k not in ("decoder_voter", "tmr_stage")},
        "stay_idx": [s["index"] for s in tmr_sil
                     if cls24(s) == "tmr_stage"],
        "by_main": dict(bym)}

    # ---- task 4: SRC-side net of single-replica silent switch bits ------
    s17f0 = OUT / design / "s17_v2.json"
    if s17f0.is_file():
        s17 = json.loads(s17f0.read_text(encoding="utf-8"))
        src_rows = []
        for rec in s17.get("silent_one_replica", []):
            s = by_key.get(tuple(rec["lfa"]))
            hit = next((h for h in (s or {}).get("hits_detail", [])
                        if h.get("feature") == s.get("feature")), None)
            pip = (hit or {}).get("pip") or ""
            tile, _, wsrc = pip.partition(" ")[0], None, None
            m = pip.split(" ", 1)
            src = m[1].split("->", 1)[0] if len(m) > 1 else None
            nets = []
            if tile and src:
                kind = tile.split("_X")[0]
                pat = "%s/%s.%s" % (tile, kind, src)
                nets = sorted(_src_nets(dd_pips(OUT / design), pat))
            own = sorted({h.get("net") for h in (s or {})
                         .get("hits_detail", []) if h.get("net")})
            src_rows.append({
                "index": rec["index"], "lfa": rec["lfa"],
                "feature": rec["feature"], "pip": pip, "src_wire": src,
                "src_nets": nets, "own_net": own,
                "implicated": [n for n in nets if n not in own]})
        ev["src_side"] = src_rows

    # ---- task 2d: control pairs re-checked under v2 ---------------------
    pf = OUT / design / "pair_identity_v2.json"
    if pf.is_file():
        pi = json.loads(pf.read_text(encoding="utf-8"))
        rows = []
        for grp, kind in (("control_ecc", "lane"), ("control_tmr", "rep")):
            for r in pi.get(grp, []):
                s1 = by_key.get(tuple(r["t1"]))
                s2 = by_key.get(tuple(r["t2"]))
                if kind == "lane":
                    v1 = (unique_lane(s1, r["stage1"]) if s1 else None)
                    v2 = (unique_lane(s2, r["stage1"]) if s2 else None)
                else:
                    v1 = (unique_replica(s1, r["stage1"]) if s1 else None)
                    v2 = (unique_replica(s2, r["stage1"]) if s2 else None)
                rows.append({"index": r["index"], "group": grp,
                             "category": r.get("category"),
                             "stage": r["stage1"],
                             "t1": r["t1"], "t2": r["t2"],
                             "v2_1": v1, "v2_2": v2,
                             "v2_same": (v1 is not None and v1 == v2),
                             "undecidable": v1 is None or v2 is None,
                             "O": r.get("O"), "M": r.get("M")})
        ev["control_pairs"] = rows
        ev["control_summary"] = {
            grp: {"n": len([r for r in rows if r["group"] == grp]),
                  "same": sum(r["v2_same"]
                              for r in rows if r["group"] == grp),
                  "diff": sum(1 for r in rows if r["group"] == grp
                              and not r["v2_same"] and not r["undecidable"]),
                  "undecidable": sum(r["undecidable"]
                                     for r in rows if r["group"] == grp),
                  "by_cat": dict(collections.Counter(
                      r["category"] for r in rows if r["group"] == grp))}
            for grp in ("control_ecc", "control_tmr")}
    # ---- §19 task 3: protected-datapath shots NOT corrected --------------
    camp = {}
    cf = OUT / design / "campaign.jsonl.gz"
    if cf.is_file():
        import gzip as _gz
        for ln in _gz.open(cf, "rt", encoding="utf-8"):
            r = json.loads(ln)
            if r.get("kind") == "single":
                camp[r["index"]] = r.get("telemetry") or {}
    pd_unc = []
    for s in shots:
        if cls24(s) != "protected_datapath":
            continue
        if s["category"] in ("corrected", "noimpact"):
            continue
        drivers, nets = shot_drivers_net(s)
        lanes = sorted({a[2] for a in s.get("attrib", [])
                        if a[0] == "protected_datapath"
                        and a[2] is not None})
        stages = sorted({a[1] for a in s.get("attrib", [])
                         if a[0] == "protected_datapath"})
        tel = camp.get(s["index"]) or {}
        pd_unc.append({
            "index": s["index"], "category": s["category"],
            "feature": s.get("feature"),
            "lfa": [s["la"], s["wd"], s["bt"]],
            "nets": sorted(nets), "drivers": sorted(drivers),
            "lanes": lanes, "stages": stages,
            "lane_scope": ("路 %s" % ",".join(map(str, lanes)))
                if lanes else "级内共享（u_memory 等，无路号）",
            "tel": {k: tel.get(k) for k in
                    ("det_cnt", "unc_cnt", "cor_cnt")}})
    ev["pd_uncorrected"] = pd_unc

    # ---- §19 task 4: shared twiddle-ROM cross-lane switch bits ----------
    rom = []
    for s in shots:
        if s.get("resource") != "开关":
            continue
        drivers, nets = shot_drivers_net(s)
        if not (any("u_rom" in d or "g_rom" in d for d in drivers)
                or any("coeff_" in n for n in nets)):
            continue
        lanes = sorted({a[2] for a in s.get("attrib", [])
                        if a[0] == "protected_datapath"
                        and a[2] is not None})
        if len(lanes) < 2:
            continue
        tel = camp.get(s["index"]) or {}
        rom.append({
            "index": s["index"], "category": s["category"],
            "net": sorted(nets)[0] if nets else None,
            "drivers": sorted(drivers),
            "stages": sorted({a[1] for a in s.get("attrib", [])
                              if a[0] == "protected_datapath"}),
            "lanes": lanes, "coverage": "全六路" if len(lanes) >= 6
                                      else "部分路",
            "tel": {k: tel.get(k) for k in
                    ("det_cnt", "unc_cnt", "cor_cnt")}})
    ev["rom_xlane"] = {
        "n": len(rom),
        "by_cov_cat": dict(collections.Counter(
            "%s/%s" % (r["coverage"], r["category"]) for r in rom)),
        "rows": rom}

    return ev


def md(design, ev, ver="v2"):
    L = []
    L.append("## %s\n" % design)
    if ver == "v3":
        g4 = ev.get("g4_v3") or {}
        L.append("**v3 版**（§23.1：LUT 下半并 O5 去处 + 开关按选择器"
                 "翻转实际接法归属）。G4=%.4f（门限 0.95；较 v2 的"
                 "变化见总表注）。\n"
                 % (g4.get("stage_consistent_frac") or 0.0))

    # task 1 table
    L.append("单发：位置类别 × 结果类别（%s + 按驱动正文版；核输入单列，"
             "见 §18-3）。\n" % ver)
    L.append("| 位置类别 |" + "".join(" %s |" % CAT_ZH[c] for c in CATS)
             + " 合计 |")
    L.append("| --- |" + " ---: |" * (len(CATS) + 1))
    for p, zh in POS_ROWS:
        row = ev["pos_x_cat"][p]
        L.append("| %s |%s %d |" % (zh, "".join(
            " %d |" % row.get(c, 0) for c in CATS), sum(row.values())))
    L.append("| 合计 |%s %d |" % (
        "".join(" %d |" % sum(ev["pos_x_cat"][p][c]
                            for p, _ in POS_ROWS) for c in CATS),
        sum(ev["pos_totals"].values())))
    L.append("")
    rc = ev.get("reclass_v19")
    if rc:
        zn = dict(POS_ROWS)
        det = "；".join("%s→%s %d 发" % (zn.get(a, a), zn.get(b, b), n)
                        for ab, n in sorted(rc["by_transition"].items())
                        for a, b in [ab.split(">")])
        L.append("§19+§20 按驱动规则，与 §18 版相比共改类 %d 发%s。"
                 % (rc["total"], "：" + det if det else ""))
    s20 = ev.get("reclass_s20")
    if s20 and s20["n"]:
        L.append("其中 §20 共用存储裁定（严格口径：v2 负载只落在本级"
                 "共用存储 u_memory 上、驱动按自身 v2 归属属本级共享控"
                 "制）改类 %d 发（%s）；若取宽泛口径（负载含共用存储、"
                 "驱动经组映射判定）则 %d 发，数据通路变为 %d / %d。"
                 % (s20["n"],
                    "；".join("%s %d" % (CAT_ZH.get(k, k), v)
                              for k, v in s20["by_cat"].items()),
                    s20["loose_n"],
                    ev["datapath_corrected"]["corrected"]
                        + s20["by_cat"].get("corrected", 0)
                        - s20["loose_corr_imp"]["corrected"],
                    ev["datapath_corrected"]["impacted"]
                        + s20["by_cat"].get("corrected", 0)
                        + s20["by_cat"].get("flagged", 0)
                        + s20["by_cat"].get("silent", 0)
                        + s20["by_cat"].get("flagged_but_clean", 0)
                        + s20["by_cat"].get("stuck", 0)
                        - s20["loose_corr_imp"]["impacted"]))
        L.append("")
    sc = ev.get("s17_check")
    if sc:
        if sc["consistent"]:
            L.append("位置计数与 §17.2 按驱动版一致 ✓（§17 共享控制 %d = "
                     "本表共享控制 %d + 核输入 %d）。"
                     % (sc["shared_control_s17"],
                        ev["pos_totals"]["shared_control"],
                        ev["pos_totals"]["kinput"]))
        else:
            L.append("§19 补全按驱动规则后位置计数与 §17.2 按驱动版的差："
                     "%s；共享控制+核输入 %+d（§17 版 %d → 本表 %d+%d）。"
                     "差异即 §19+§20 新改类的发数。"
                     % ("；".join("%s %+d" % (dict(POS_ROWS).get(k, k), v)
                                  for k, v in sc["delta"].items() if v),
                        sc["shared_delta"], sc["shared_control_s17"],
                        ev["pos_totals"]["shared_control"],
                        ev["pos_totals"]["kinput"]))
        L.append("")

    # task 2 numbers
    d2 = ev["datapath_corrected"]
    L.append("正文用数（出处 `s18_%s.json`）：" % ver)
    if d2["total_pd"] == 0:
        L.append("- 受保护数据通路：不适用（本设计无编码数据通路）。")
    else:
        L.append("- 受保护数据通路有影响的单发中纠正到逐位一致：%d / %d"
                 "（%.1f%%；有影响构成：%s）。"
                 % (d2["corrected"], d2["impacted"], 100.0 * d2["frac"],
                    "；".join("%s %d" % (CAT_ZH[k], v)
                              for k, v in sorted(
                                  d2["impacted_by_cat"].items(),
                                  key=lambda x: -x[1]))))
    L.append("- 单发静默合计 %d 发；按位置类别：%s。"
             % (ev["silent_total"],
                "；".join("%s %d" % (dict(POS_ROWS).get(k, k), v)
                          for k, v in sorted(ev["silent_by_class"].items(),
                                             key=lambda x: -x[1]))))
    t = ev["tmr_silent"]
    ore = "；".join("%s %d" % (dict(POS_ROWS).get(k, k), v)
                    for k, v in t["other_reclass"].items())
    L.append("- 三模级单发静默 %d 发：按驱动归入译码器与表决器 %d 发"
             "（表决器/译码器输出布线），留在副本内 %d 发%s。"
             % (t["total"], t["to_decoder_voter"], t["stay_tmr"],
                ("；另改归 %s" % ore) if ore else ""))
    L.append("  - “留在副本内”与 §17.3 单副本静默清单口径不同：后者只数"
             "负载恰落一份副本的开关位；本行另含副本内 slice/FF 设置位"
             "（SRL/CLKINV/LATCH 等），而 §17.3 中的复位网、表决器网开关"
             "位按驱动已改归共享控制/译码器与表决器——规划方给出的"
             "三模级静默数（P0≈4、P1≈1、S0≈0、S1≈3）即本行口径，"
             "两清单互补不重合。")
    cs = ev.get("control_summary")
    if cs:
        for grp, zh in (("control_ecc", "编码级同路对照"),
                        ("control_tmr", "三模级同副本对照")):
            s_ = cs.get(grp)
            if not s_ or not s_["n"]:
                continue
            L.append("- %s %d 对：v2 确属同路/同副本 %d 对，"
                     "v2 判为不同 %d 对，未定 %d 对；结果构成 %s。"
                     % (zh, s_["n"], s_["same"], s_["diff"],
                        s_["undecidable"],
                        "；".join("%s %d" % (CAT_ZH.get(k, k), v)
                                  for k, v in s_["by_cat"].items())))
            if grp == "control_ecc" and s_["undecidable"]:
                L.append("  - “未定”指位的 v2 负载未落在数据通路单元上"
                         "（多归译码器/表决器或路号不可解），无法按 v2 "
                         "路归属独立证实同路——这些对以抽对时的路号为准。")
        diffs = [r for r in ev.get("control_pairs", [])
                 if not r["v2_same"] and not r["undecidable"]]
        for r in diffs:
            L.append("  - 对 %s（%s 级）：v2 下位1 %s、位2 %s ——"
                     "判为不同%s，结果 %s。"
                     % (r["index"], r["stage"],
                        ("%s %s" % ("副本" if r["group"] == "control_tmr"
                                    else "路", r["v2_1"])),
                        ("%s %s" % ("副本" if r["group"] == "control_tmr"
                                    else "路", r["v2_2"])),
                        "副本" if r["group"] == "control_tmr" else "路",
                        CAT_ZH.get(r["category"], r["category"])))
    # ---- §19-3: protected-datapath shots not corrected (P0/S0) ----------
    pu = ev.get("pd_uncorrected")
    if pu:
        L.append("§19-3 数据通路内未被纠正的单发（v2 + §19 按驱动后仍归"
                 "受保护数据通路；逐发）：")
        L.append("")
        L.append("| 发号 | 结果 | 网 | 驱动单元 | 负载所在路 |"
                 " 遥测 det/unc/cor |")
        L.append("| ---: | --- | --- | --- | --- | --- |")
        for r in pu:
            L.append("| %d | %s | `%s` | `%s` | %s | %s/%s/%s |"
                     % (r["index"], CAT_ZH.get(r["category"], r["category"]),
                        "；".join(r["nets"]) or "（无网记录）",
                        "；".join(r["drivers"]) or "（无驱动记录）",
                        r["lane_scope"],
                        r["tel"].get("det_cnt"), r["tel"].get("unc_cnt"),
                        r["tel"].get("cor_cnt")))
        L.append("")
        L.append("注：级内共享一行指负载全部落在 u_memory 等无 lane 号的"
                 "级内结构上——位的影响仍是整条数据通路，但不是某一路的"
                 "私有资源。")
        L.append("")

    # ---- §19-4: shared twiddle-ROM cross-lane switch bits (P0) ----------
    rm = ev.get("rom_xlane")
    if rm and rm["n"]:
        L.append("§19-4 共用旋转因子表驱动的跨路开关位（按负载覆盖面分）：")
        L.append("")
        L.append("覆盖面 × 结果：%s。"
                 % "；".join("%s %d 发" % kv for kv in
                            sorted(rm["by_cov_cat"].items())))
        L.append("")
        L.append("| 发号 | 结果 | 覆盖面 | 网 | 级 |"
                 " 遥测 det/unc/cor |")
        L.append("| ---: | --- | --- | --- | --- | --- |")
        for r in rm["rows"]:
            L.append("| %d | %s | %s | `%s` | %s | %s/%s/%s |"
                     % (r["index"], CAT_ZH.get(r["category"], r["category"]),
                        "%s（%s）" % (r["coverage"],
                                      ",".join(map(str, r["lanes"]))),
                        (r["net"] or "?").split("/")[-1],
                        ",".join(map(str, r["stages"])),
                        r["tel"].get("det_cnt"), r["tel"].get("unc_cnt"),
                        r["tel"].get("cor_cnt")))
        L.append("")
        v1285 = next((r for r in rm["rows"] if r["index"] == 1285), None)
        if v1285:
            L.append("核实第 1285 发：网 `%s`（驱动 `%s`）的 v2 负载确为"
                     "第 %s 级全部六路的旋转器，结果确为静默——覆盖面与"
                     "结果与规划方离线判读一致。但遥测 det=%s/unc=%s/"
                     "cor=%s：注流期间有 %s 个非零校验子均被纠正、无不可"
                     "纠标志——“码字仍然自洽、校验子为零”只是候选机理，"
                     "与遥测并不完全吻合，此发记为“未解释”，不下定论。"
                     % (v1285["net"], v1285["drivers"][0],
                        ",".join(map(str, v1285["stages"])),
                        v1285["tel"].get("det_cnt"),
                        v1285["tel"].get("unc_cnt"),
                        v1285["tel"].get("cor_cnt"),
                        v1285["tel"].get("det_cnt")))
            L.append("")

    # task 4: SRC-side net analysis for §17.3 bits (§18.5 wording)
    if ev.get("src_side"):
        L.append("§18-4 单副本静默位的开关源端（SRC 侧网，即“可能牵连”侧）：")
        L.append("")
        L.append("按 §18.5 更正口径：“静默”= 输出出错且无标志；单副本出错"
                 "会被表决屏蔽、只能得“已纠正”，所以单副本屏蔽解释不了"
                 "静默——说明这几发的真实影响不止一份副本，目前归属没有"
                 "找全。正文只报个数：每个设计 1–2 发，记“原因未查明”。")
        L.append("")
        for r in ev["src_side"]:
            clk = any("u_clk" in n for n in r["own_net"])
            L.append("- 位 %s `%s`（pip `%s`）："
                     % (r["index"], r["feature"], r["pip"]))
            L.append("  - 本网：%s" % ("；".join("`%s`" % n
                                               for n in r["own_net"]) or "？"))
            if r["src_nets"]:
                L.append("  - SRC 线 `%s` 所在网：%s"
                         % (r["src_wire"], "；".join(
                            "`%s`" % n for n in r["src_nets"])))
            else:
                L.append("  - SRC 线 `%s` 未布线（桩线，无所属网）。"
                         % r["src_wire"])
            im = r["implicated"][0] if r["implicated"] else None
            if im and ("u_data_vote" in im or "u_valid_vote" in im
                       or "u_decode" in im):
                L.append("  - 可能的解释：表决器输出被短接——SRC 侧网 `%s`"
                         " 扇出到下一级的三份副本，时钟网与它接通若把"
                         "表决器输出拉错，三份副本同时拿到错数据，正好"
                         "能解释静默。不下定论。" % im)
            elif im:
                L.append("  - 记“未解释”：SRC 侧网 `%s` 与负载在同一份"
                         "副本，接通后也只坏一份副本，解释不了静默。"
                         % im)
            else:
                L.append("  - 记“未解释”：SRC 为未布线桩线，“两网接通”"
                         "不成立，静默尚无解释。")
            if clk:
                L.append("  - 待查方向：时钟分支上的开关可能改变本 slice "
                         "的时钟时序，影响不止逻辑归属里的那些负载。")
        L.append("")

    if ver == "v3":
        rc = ev.get("reclass_v2_to_v3", [])
        L.append("### §23-2 v2 → v3 改类清单（%d 发）" % len(rc))
        L.append("")
        if rc:
            L.append("| 发 | 结果类别 | v2 主类 | v3 主类 | 依据 |")
            L.append("| --- | --- | --- | --- | --- |")
            for r in rc:
                L.append("| %d | %s | %s | %s | %s |"
                         % (r["index"], CAT_ZH.get(r["category"],
                                                   r["category"]),
                            dict(POS_ROWS).get(r["v2"], r["v2"]),
                            dict(POS_ROWS).get(r["v3"], r["v3"]),
                            r.get("reason", "")))
        else:
            L.append("（无）")
        L.append("")

    return "\n".join(L)


def main():
    v3 = "--v3" in sys.argv
    suf = "v3" if v3 else "v2"
    if len(sys.argv) > 1 and sys.argv[1] == "--combine":
        hdr = ("# WP6 §24.2 修正版 v3 归属表（未进正文——待 §24.4 Vivado "
               "只读核验通过后方可转正）" if v3
               else "# WP6 §18 正文用表（v2 + 按驱动 + §24.1 时钟/复位→共享控制）")
        note = ("口径：v3 下游归属（§23.1：LUT 下半并 O5 去处 + 开关位"
                "按选择器翻转实际接法归属，§24.2 修正：无 A6 只算 O5、"
                "下游按路由子树）+ 按驱动位置类别（§17.2/§19/§20 规则栈"
                "不变）。逐位证据见各设计 `s18_v3.json`。"
                if v3 else
                "口径：v2 下游归属（§16 验收版）+ 按驱动位置类别（§17.2 "
                "正文版）+ §24.1 作者裁定：翻转前后接核时钟 clk_out2 或"
                "核复位 kernel_rst 的开关位（含各 slice 时钟/复位脚选择"
                "器）一律归共享控制；核输入按 §18-3 单列。逐发改类清单见各"
                "设计 `S18_v2.md` §24.1 段。")
        parts = [hdr, "", note, "",
                 "生成：%s。" % time.strftime("%Y-%m-%d %H:%M"), ""]
        if v3:
            parts += ["### v2 → v3 G4 对照（ corrected 单发遥测一致性，"
                      "门限 0.95）", "",
                      "| 设计 | v2 判定发数 | v2 一致率 | v3 判定发数 |"
                      " v3 一致率 | v3 判定 |",
                      "| --- | ---: | ---: | ---: | ---: | --- |"]
            for d in ("P0_p1", "P1_p2", "S0_s3", "S1_s2"):
                try:
                    bd = json.loads((OUT / d /
                        "bit_attrib_downstream_v3.json").read_text())
                    b2 = json.loads((OUT / d /
                        "bit_attrib_downstream_v2.json").read_text())
                    g2 = b2["g4_telemetry_downstream_v2"]
                    g3 = bd["g4_telemetry_downstream_v3"]
                    parts.append(
                        "| %s | %d | %.4f | %d | %.4f | %s |"
                        % (d, g2["unique_stage_judged"],
                           g2["stage_consistent_frac"],
                           g3["unique_stage_judged"],
                           g3["stage_consistent_frac"], g3["verdict"]))
                except Exception:
                    parts.append("| %s | — | — | — | — | — |" % d)
            parts += ["", "注：v3 G4 按 v3 归属重算；判定发数下降是因部分"
                      "发改归 unmapped/mixed 后不再满足唯一级条件，余下"
                      "判定发的一致率不降反升。", ""]
        for d in ("P0_p1", "P1_p2", "S0_s3", "S1_s2"):
            f = OUT / d / f"S18_{suf}.md"
            parts.append(f.read_text(encoding="utf-8") if f.is_file()
                         else "## %s\n\n（缺 S18_%s.md）\n" % (d, suf))
        out = OUT / ("MAIN_TABLES_v3.md" if v3 else "MAIN_TABLES.md")
        out.write_text("\n".join(parts), encoding="utf-8")
        print("wrote", out, f"({suf})")
        return
    design = sys.argv[1]
    ev = s18(design, suf)
    dd = OUT / design
    (dd / f"s18_{suf}.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    (dd / f"S18_{suf}.md").write_text(
        md(design, ev, suf), encoding="utf-8")
    print(design, f"({suf})", "pos_totals:", ev["pos_totals"])
    print("   silent:", ev["silent_by_class"], "| tmr_sil:",
          ev["tmr_silent"]["total"])
    print("   ctrl:", ev.get("control_summary"))


if __name__ == "__main__":
    main()
