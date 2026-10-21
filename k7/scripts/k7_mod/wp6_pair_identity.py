"""WP6 section 13: pair count-identity column + per-design REPORT.md.

For every same-stage natural pair the injected error beats must equal the
sum of the two bits' own single-shot error beats (same input vector, no
upstream error):

  ECC stage, different lanes: M = (a + b) - (c + 2u); C = u + M.
      M = 0 identity holds; M > 0 => M overlapping beats mis-corrected;
      M < 0 cannot be explained -> list the pair.
  ECC stage, same lane (control): expect u = 0; list a,b,c,u and a+b-c.
  TMR stage, same stage: O = a + b - c >= 0, and silent pairs O > 0.

a,b = each bit's own prescreen-single cor_cnt; c,u = the pair's cor_cnt /
unc_cnt.  Repeats (pair_natural_repeat, single_reprobe) are listed next to
their originals to test the deterministic-error-beats premise.

Usage: wp6_pair_identity.py <design> [--out results/wp6]
Writes results/wp6/<dir>/REPORT.md and pair_identity.json.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wp6_sem_campaign as camp                    # noqa: E402

CAT_ZH = camp.CATEGORY_ZH
ECC_LANES = set(range(6))

# WP6 §16 downstream-attribution version: "v1" = site-level expansion
# (over-counts bypass site pins and bidirectional pips); "v2" = pin-level
# load attribution following the actual routed direction.  Selects which
# bit_attrib_downstream*.json the downstream-口径 tables read; under v2
# the outputs are written as REPORT_v2.md / pair_identity_v2.json.
_DS_VER = "v1"
_DS_FILES = {"v1": "bit_attrib_downstream.json",
             "v2": "bit_attrib_downstream_v2.json"}
_DS_LABEL = {"v1": "v1（多算）", "v2": "v2"}


def ds_fname():
    return _DS_FILES[_DS_VER]


def read_jsonl(path):
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.open(encoding="utf-8")
            if l.strip()]


def key_of(t):
    return (t["la"], t["wd"], t["bt"])


def tel_of(r):
    return r.get("telemetry") or {}


def single_index(recs):
    """{(la,wd,bt): first non-void prescreen single record}."""
    idx = {}
    for r in recs:
        if r.get("kind") != "single" or r.get("void") or r.get("standby"):
            continue
        t = (r.get("target") or [None])[0]
        if t is None:
            continue
        idx.setdefault(key_of(t), r)
    return idx


def stage_of(t):
    return t.get("stage")


def lane_of(bit_single, t):
    tel = tel_of(bit_single)
    loc = tel.get("last_loc")
    if isinstance(loc, int) and tel.get("last_class") == 1:
        return loc
    return t.get("lane")


def replica_of(t):
    r = t.get("replica")
    return r if isinstance(r, int) else "未定"


def pair_identity(design, out):
    ddir = Path(out) / camp.DESIGN_DIR[design]
    recs = read_jsonl(ddir / "campaign.jsonl")
    singles = single_index(recs)
    ecc_stages = set(camp.DESIGNS[design]["ecc_stages"])
    tmr_stages = set(camp.DESIGNS[design]["tmr_stages"])

    ecc_diff, ecc_same, tmr = [], [], []
    stress_ecc, stress_tmr = [], []
    control_ecc, control_tmr = [], []
    repeats, reprobes = [], []
    missing_single = []
    for r in recs:
        if r.get("kind") not in ("pair_natural", "pair_natural_repeat",
                                 "pair_stress", "pair_control",
                                 "pair_control_repeat") \
                or r.get("void"):
            continue
        tgt = r.get("target") or []
        if len(tgt) != 2:
            continue
        s1 = singles.get(key_of(tgt[0]))
        s2 = singles.get(key_of(tgt[1]))
        if s1 is None or s2 is None:
            missing_single.append(r.get("index"))
            continue
        a = tel_of(s1).get("cor_cnt")
        b = tel_of(s2).get("cor_cnt")
        pt = tel_of(r)
        c, u = pt.get("cor_cnt"), pt.get("unc_cnt")
        row = {"index": r.get("index"), "kind": r["kind"],
               "subkind": r.get("subkind"),
               "category": r.get("category"),
               "stage1": stage_of(tgt[0]), "stage2": stage_of(tgt[1]),
               "rep1": replica_of(tgt[0]), "rep2": replica_of(tgt[1]),
               "a": a, "b": b, "c": c, "u": u,
               "key1": camp._bit_key(tgt[0]), "key2": camp._bit_key(tgt[1]),
               "t1": key_of(tgt[0]), "t2": key_of(tgt[1])}
        row["lane1"] = lane_of(s1, tgt[0])
        row["lane2"] = lane_of(s2, tgt[1])
        if r["kind"] in ("pair_natural_repeat", "pair_control_repeat"):
            repeats.append(row)
            continue
        if row["stage1"] is None or row["stage1"] != row["stage2"]:
            continue
        st = row["stage1"]
        if st in ecc_stages:
            if r["kind"] == "pair_natural":
                if row["lane1"] != row["lane2"]:
                    m = (a + b) - (c + 2 * (u or 0))
                    row["M"] = m
                    row["C_overlap"] = (u or 0) + m
                    ecc_diff.append(row)
                else:
                    row["overlap"] = a + b - c
                    ecc_same.append(row)
            else:
                row["M"] = (a + b) - (c + 2 * (u or 0))
                row["C_overlap"] = (u or 0) + row["M"]
                row["overlap"] = a + b - c
                row["pool_lane1"] = tgt[0].get("lane")
                row["pool_lane2"] = tgt[1].get("lane")
                if r["kind"] == "pair_stress":
                    if row["lane1"] == row["lane2"]:
                        row["_moved"] = True
                    stress_ecc.append(row)
                else:
                    control_ecc.append(row)
        elif st in tmr_stages:
            row["O"] = a + b - c
            (tmr if r["kind"] == "pair_natural"
             else stress_tmr if r["kind"] == "pair_stress"
             else control_tmr).append(row)

    for r in recs:
        if r.get("kind") != "single_reprobe" or r.get("void"):
            continue
        t = (r.get("target") or [None])[0]
        if t is None:
            continue
        s = singles.get(key_of(t))
        reprobes.append({"index": r.get("index"),
                         "subkind": r.get("subkind"),
                         "key": camp._bit_key(t),
                         "reprobe_cor": tel_of(r).get("cor_cnt"),
                         "reprobe_unc": tel_of(r).get("unc_cnt"),
                         "orig_cor": tel_of(s).get("cor_cnt") if s else None,
                         "category": r.get("category"),
                         "orig_category": s.get("category") if s else None})

    return {"design": design, "ecc_diff_lane": ecc_diff,
            "ecc_same_lane": ecc_same, "tmr_same_stage": tmr,
            "stress_ecc": stress_ecc, "stress_tmr": stress_tmr,
            "control_ecc": control_ecc, "control_tmr": control_tmr,
            "pair_repeats": repeats, "single_reprobes": reprobes,
            "missing_single": missing_single,
            "n_pair_natural": sum(1 for r in recs
                                  if r.get("kind") == "pair_natural")}


def arch_audit(recs, expected):
    bad = []
    n = 0
    for r in recs:
        tel = tel_of(r)
        htel = (r.get("health") or {}).get("telemetry") or {}
        for src in (tel, htel):
            if "arch" not in src:
                continue
            n += 1
            if src["arch"] != expected:
                bad.append({"index": r.get("index"), "kind": r.get("kind"),
                            "arch": src["arch"]})
    return n, bad


def cat_counts(recs):
    c = collections.Counter()
    for r in recs:
        if r.get("kind") == "single" and not r.get("standby"):
            c[r.get("category")] += 1
    return c


def fmt(v):
    return "–" if v is None else str(v)


def bit_attrib_shots(ddir, fname="bit_attrib.json"):
    ba = load_json_maybe_gz(Path(ddir) / fname)
    if not ba:
        return {}
    return {(s["la"], s["wd"], s["bt"]): s for s in ba.get("shots") or []}


def bit_replica_map(ddir, fname="bit_attrib.json"):
    """WP6-A per-bit replica: {(la,wd,bt): replica int} where the bit's
    attrib entries name exactly one tmr_stage replica; absent/ambiguous
    -> not in map."""
    out = {}
    for key, s in bit_attrib_shots(ddir, fname).items():
        reps = set()
        for h in s.get("hits_detail") or []:
            for a in h.get("attrib") or []:
                if len(a) >= 3 and a[0] == "tmr_stage":
                    reps.add(a[2])
        if len(reps) == 1:
            out[key] = next(iter(reps))
    return out


def attrib_lines(shot):
    if not shot:
        return "（无逐位归属记录）"
    parts = []
    for h in shot.get("hits_detail") or []:
        for a in h.get("attrib") or []:
            if len(a) >= 3:
                parts.append("%s 级%s #%s" % (a[0], a[1], a[2]))
    if not parts:
        return "（归属未定）"
    return "；".join(sorted(set(parts)))


# ------------------------------------------------------------- WP6 14.1
_XDS = {}
_BM = {}
_DSHOTS = {}          # (design, key) -> stored downstream shot


def dshots_by_key(design, ddir):
    if not any(k[0] == design for k in _DSHOTS):
        a = load_json_maybe_gz(Path(ddir) / ds_fname())
        if a:
            for s_ in a["shots"]:
                _DSHOTS.setdefault((design, (s_["la"], s_["wd"],
                                             s_["bt"])), s_)
    return _DSHOTS
_STAGE_TOK = re.compile(r"g_(?:p)?(?:stage|tmr)\[(\d+)\]")
_CHAN_TOK = re.compile(r"/g_(?:ch|lane)\[(\d+)\]")


def load_json_maybe_gz(path):
    """Prefer <path>.gz (§14.3 layout); fall back to plain .json."""
    path = Path(path)
    gz = Path(str(path) + ".gz")
    src = gz if gz.is_file() else path
    if not src.is_file():
        return None
    if src.suffix == ".gz":
        with gzip.open(src, "rt", encoding="utf-8") as f:
            return json.loads(f.read())
    return json.loads(src.read_text(encoding="utf-8"))


def export_data(design, ddir):
    """Lazy (ExportData, None).  The heavy DownstreamData index is not
    needed: downstream objects are a subset of whole-net net loads, and
    the stored downstream lists are unioned in by bit_objects()."""
    design = str(design)
    if design not in _XDS:
        import wp6a_attrib as A
        _XDS[design] = (A.ExportData(ddir), None)
    return _XDS[design]


def shots_by_key(ddir):
    out = {}
    a = load_json_maybe_gz(Path(ddir) / "bit_attrib.json")
    if a:
        for s_ in a["shots"]:
            out.setdefault((s_["la"], s_["wd"], s_["bt"]), s_)
    return out


def bit_objects(design, ddir, key, shots):
    """Union of attributed object paths across both口径 for one pool bit.
    Whole-net objects are recomputed in full (attribute_hit); downstream
    objects are a subset of net loads by construction, but the stored
    downstream lists are still unioned in for completeness."""
    import wp6a_attrib as A
    import wp6a_bitmap as bm
    s_ = shots.get(key)
    if not s_ or s_.get("lsb") is None:
        return []
    if not _BM:
        _BM["cols"], _BM["lo"] = bm.load_columns()
        _BM["db"] = bm.load_db()
    dshots_by_key(design, ddir)
    xd, _dd = export_data(design, ddir)
    r = bm.map_bit(_BM["cols"], _BM["lo"], _BM["db"],
                   key[0], key[1], s_["lsb"])
    objs = set()
    for h in r["hits"]:
        a = A.attribute_hit(h, xd)
        if a:
            objs.update(a.get("objects") or [])
    for s2 in (s_, _DSHOTS.get((design, key))):
        for h in (s2 or {}).get("hits_detail") or []:
            objs.update(h.get("objects") or [])
    return sorted(objs)


def decoder_member(design, ddir, key, stage, shots):
    """§14.1-2: any attributed object in the bit's own-stage decode logic
    (ECC: u_decode, excl. u_encode and u_memory/*/u_decode) or vote logic
    (TMR: u_valid_vote / u_last_vote / u_flag).  None = bit not found."""
    if stage is None or key not in shots:
        return None
    pat = re.compile(r"g_(?:p)?(?:stage|tmr)\[%d\]" % int(stage))
    for o in bit_objects(design, ddir, key, shots):
        m = pat.search(o)
        if not m:
            continue
        tail = o[m.end():]
        iu, im = tail.find("/u_decode/"), tail.find("/u_memory/")
        if iu >= 0 and (im < 0 or im > iu):
            return True
        if re.search(r"/(u_valid_vote|u_last_vote|u_flag)(/|$)", tail):
            return True
    return False


def decoder_member_loose(design, ddir, key, shots):
    """Loose variant: u_decode at ANY pipeline stage (still excluding
    u_memory paths and u_encode).  Cross-check aid for §14.1-2."""
    for o in bit_objects(design, ddir, key, shots):
        iu, im = o.find("/u_decode/"), o.find("/u_memory/")
        if iu >= 0 and (im < 0 or im > iu):
            return True
    return False


def channel_of(design, ddir, key, shots):
    """{k}: g_ch[k]/g_lane[k] tokens in the bit's attributed objects."""
    return {int(k) for o in bit_objects(design, ddir, key, shots)
            for k in _CHAN_TOK.findall(o)}


def bit_const_net(shots, key):
    """(is_const, net) — the bit sits on a constant net (POWER/GROUND or
    net_category 常量网) per its whole-net attribution hits."""
    s_ = shots.get(key)
    for h in (s_ or {}).get("hits_detail") or []:
        if h.get("net_category") == "常量网" \
                or h.get("net_type") in ("POWER", "GROUND"):
            return True, h.get("net")
    return False, None


def bit_net_name(shots, key):
    """First named net among the bit's whole-net hits (any type)."""
    s_ = shots.get(key)
    for h in (s_ or {}).get("hits_detail") or []:
        if h.get("net"):
            return h["net"]
    return None


def ds_tmr_replicas(design, ddir, key, stage):
    """{replica} — downstream-口径 tmr_stage labels at telemetry `stage`."""
    if stage is None:
        return set()
    dshots_by_key(design, ddir)
    s_ = _DSHOTS.get((design, key))
    reps = set()
    for h in (s_ or {}).get("hits_detail") or []:
        for a in h.get("attrib") or []:
            if len(a) >= 3 and a[0] == "tmr_stage" and a[1] == stage:
                reps.add(a[2])
    return reps


def ds_replicas_by_stage(design, ddir, key):
    """{stage: {replica,...}} — downstream tmr_stage map for one bit."""
    dshots_by_key(design, ddir)
    s_ = _DSHOTS.get((design, key))
    by = {}
    for h in (s_ or {}).get("hits_detail") or []:
        for a in h.get("attrib") or []:
            if len(a) >= 3 and a[0] == "tmr_stage":
                by.setdefault(a[1], set()).add(a[2])
    return by


def clean_check(design, ddir, key, stage, shots):
    """WP6 §15-1 clean-bit rule -> (clean, [reasons]).
    Clean iff the downstream 口径 attribution at the telemetry stage
    names exactly one replica AND the bit is not on a constant net."""
    reasons = []
    reps = ds_tmr_replicas(design, ddir, key, stage)
    if len(reps) > 1:
        reasons.append("下游多副本")
    elif not reps:
        reasons.append("下游在本级无副本")
    if bit_const_net(shots, key)[0]:
        reasons.append("常量网")
    return (not reasons), reasons


def ds_channels(design, ddir, key):
    """{ch} — g_ch[k]/g_lane[k] tokens in downstream-attributed objects."""
    dshots_by_key(design, ddir)
    s_ = _DSHOTS.get((design, key))
    chs = set()
    for h in (s_ or {}).get("hits_detail") or []:
        for o in h.get("objects") or []:
            chs.update(int(k) for k in _CHAN_TOK.findall(o))
    return chs


def bit_resource(shots, key):
    """Shot-level resource label of a stored shot ('未解析' if absent)."""
    s_ = shots.get(key)
    if not s_:
        return "未解析"
    return s_.get("resource") or "未解析"


def miscorr_cat(r):
    """Category cell: silent pair with u=0 and M>0 -> all overlapping
    beats mis-corrected (§15-4); a batch-4 stress pair whose report-time
    lanes coincide gets the §15-3 footnote marker."""
    if r["category"] == "silent" and (r["u"] or 0) == 0 \
            and (r.get("M") or 0) > 0:
        return "静默·误纠"
    txt = CAT_ZH.get(r["category"], r["category"])
    if r.get("_moved"):
        txt += " †"
    return txt


def drift_note(r, rep_pair_all, rep_single_all, shots):
    """§15-5: for a pair classified 计数漂移 with reshoot14 rounds, report
    the second bit's prescreen vs reshoot-consistent counts and resources."""
    if r.get("_class") != "计数漂移":
        return ""
    rnd = [x for x in reshoot_rounds(r, rep_pair_all, rep_single_all)
           if x[0].startswith("rep")]
    if not rnd:
        return ""
    bvals = "、".join("%s" % b_r for _t, _a, b_r, _c, _u in rnd)
    res = sorted({bit_resource(shots, tuple(r["t1"])),
                  bit_resource(shots, tuple(r["t2"]))})
    restxt = "两位均为%s位" % res[0] if len(res) == 1 \
        else "位资源 %s/%s" % (res[0], res[1])
    return ("；§15-5 复打结论：第二位预筛 b=%s，复打 %s；%s——"
            "归计数漂移") % (fmt(r["b"]), bvals, restxt)


def reshoot_rounds(row, rep_pair_all, rep_single_all):
    """[(tag, a_r, b_r, c_r, u_r)] — one entry per reshoot round that has
    the pair repeat plus both single reprobes.  tag: 'natural' (the §5
    same-round repeat) or 'repN' (§14 reshoot)."""
    def nat(r):
        return r.get("subkind") != "reshoot14"

    idx = row["index"]
    rounds = []
    ss = {r["key"]: r for r in rep_single_all
          if r["index"] == idx and nat(r)}
    for pr in rep_pair_all:
        if pr["index"] == idx and nat(pr):
            s1, s2 = ss.get(row["key1"]), ss.get(row["key2"])
            if s1 and s2:
                rounds.append(("natural", s1["reprobe_cor"],
                               s2["reprobe_cor"], pr["c"], pr["u"]))
    for j in (1, 2):
        pr = next((r for r in rep_pair_all
                   if r.get("subkind") == "reshoot14"
                   and r["index"] == idx * 100 + j), None)
        s1 = next((r for r in rep_single_all
                   if r.get("subkind") == "reshoot14"
                   and r["index"] == idx * 10 + j * 2), None)
        s2 = next((r for r in rep_single_all
                   if r.get("subkind") == "reshoot14"
                   and r["index"] == idx * 10 + j * 2 + 1), None)
        if pr is not None and s1 is not None and s2 is not None:
            rounds.append(("rep%d" % j, s1["reprobe_cor"],
                           s2["reprobe_cor"], pr["c"], pr["u"]))
    return rounds


def reshoot_col(rounds, ecc=True):
    """Render the reshoot-口径 column: M_r / O_r per round."""
    if not rounds:
        return "—"
    out = []
    for tag, a_r, b_r, c_r, u_r in rounds:
        v = (a_r + b_r) - (c_r + 2 * (u_r or 0)) if ecc \
            else (a_r + b_r) - c_r
        out.append("%s" % v if len(rounds) == 1 else "%s:%s" % (tag, v))
    return ",".join(out)


def classify_violation(rounds, dec1, dec2, ecc=True):
    """§14.1-2 four-way class for a pair whose count identity fails."""
    def val(a_r, b_r, c_r, u_r):
        return (a_r + b_r) - (c_r + 2 * (u_r or 0)) if ecc \
            else (a_r + b_r) - c_r

    def holds(v):
        return v == 0 if ecc else v >= 0

    if rounds:
        vals = [val(a_r, b_r, c_r, u_r) for _t, a_r, b_r, c_r, u_r in rounds]
        if all(holds(v) for v in vals):
            return "计数漂移"
        if not all(not holds(v) for v in vals):
            return "未定（复打口径不一）"
    if dec1 or dec2:
        return "含译码器位"
    if rounds:
        return "可复现误纠"
    return "未定"


def report(design, ident, recs, out):
    ddir = Path(out) / camp.DESIGN_DIR[design]
    exp_arch = camp.EXPECTED_TEL_ARCH[design]
    n_tel, bad_arch = arch_audit(recs, exp_arch)
    cats = cat_counts(recs)
    freeze = ddir / "L_freeze.json"
    freeze_txt = "未生成"
    if freeze.is_file():
        fz = json.loads(freeze.read_text(encoding="utf-8"))
        freeze_txt = "n=%d，f̄=%.6f" % (fz.get("n", -1), fz.get("f_bar", -1))
    rep_path = ddir / "repeat_report.json"
    rep_txt = "未生成"
    mism = []
    if rep_path.is_file():
        rp = json.loads(rep_path.read_text(encoding="utf-8"))
        mism = rp.get("mismatches") or []
        rep_txt = "L=%s，受影响=%s，不一致 %d 发" % (
            rp.get("n_L"), rp.get("n_affected"), len(mism))

    shots = shots_by_key(ddir)
    # WP6 §15-1: per-row clean-bit flags for the three TMR pair tables.
    clean_info = {}          # (key, stage) -> (clean, [reasons])
    unclean_bits = {}
    for _rows in (ident["tmr_same_stage"], ident["stress_tmr"],
                  ident["control_tmr"]):
        for r in _rows:
            st = r["stage1"]
            for _tk in ("t1", "t2"):
                key = tuple(r[_tk])
                if (key, st) not in clean_info:
                    _c, _why = clean_check(design, ddir, key, st, shots)
                    clean_info[(key, st)] = (_c, _why)
                    if not _c:
                        unclean_bits[(key, st)] = _why
            r["_clean"] = (clean_info[(tuple(r["t1"]), st)][0]
                           and clean_info[(tuple(r["t2"]), st)][0])
    rec_by_idx = {(r.get("kind"), r.get("subkind"), r.get("index")): r
                  for r in recs}
    rep_pair_all = ident["pair_repeats"]
    rep_single_all = ident["single_reprobes"]
    singles = single_index(recs)
    orig_all_pairs = {}
    for r in recs:
        if r.get("kind") in ("pair_natural", "pair_stress", "pair_control") \
                and not r.get("void"):
            orig_all_pairs.setdefault(r.get("index"), r)
    _dec_cache = {}

    def dec(key, stage):
        """§14.1-2 decoder/voter membership for one pair bit (cached)."""
        kk = (key, stage)
        if kk not in _dec_cache:
            _dec_cache[kk] = decoder_member(design, ddir, key, stage, shots)
        return _dec_cache[kk]

    L = []
    L.append("# %s SEM 注入报告（WP6）" % design)
    L.append("")
    L.append("> 下游口径版本：**%s**。%s" % (
        _DS_LABEL[_DS_VER],
        "本报告的下游口径派生表用 v1（多算）旧口径；"
        "v2 引脚级口径并列见 `REPORT_v2.md`。" if _DS_VER == "v1"
        else "本报告的下游口径派生表用 v2 引脚级口径"
        "（§16 修正：站点引脚级负载 + 按布线方向遍历）。"))
    L.append("")
    L.append("数据：`campaign.jsonl`（%d 条记录）。本报告由 "
             "`scripts/k7_mod/wp6_pair_identity.py` 生成。" % len(recs))
    L.append("")
    L.append("## 单发五类")
    L.append("")
    L.append("| 类别 | 发数 |")
    L.append("| --- | ---: |")
    for k in ("noimpact", "corrected", "silent", "flagged",
              "flagged_but_clean", "stuck", "invalid"):
        if cats.get(k):
            L.append("| %s（%s） | %d |" % (CAT_ZH.get(k, k), k, cats[k]))
    L.append("")
    L.append("## 可重复性抽检与冻结 L")
    L.append("")
    L.append("- 复打：%s" % rep_txt)
    L.append("- 冻结 L：%s" % freeze_txt)
    L.append("")

    # ---- count identity tables -------------------------------------------
    L.append("## 自然配对的计数等式（WP6 第 13 节）")
    L.append("")
    ed = ident["ecc_diff_lane"]
    L.append("编码级同级不同路 %d 对。" % len(ed))
    if ed:
        m0 = [r for r in ed if r["M"] == 0]
        mp = [r for r in ed if (r["M"] or 0) > 0]
        mn = [r for r in ed if (r["M"] or 0) < 0]
        L.append("M = (a+b) − (c+2u)：等式成立（M=0）%d 对；M>0 %d 对"
                 "（按误纠解释，重合拍 C=u+M）；M<0 %d 对。"
                 % (len(m0), len(mp), len(mn)))
        L.append("")
        L.append("| 对 | 级 | 路a | 路b | a | b | c | u | M | 复打口径 M_r | "
                 "译码器位 | 归类 | 类别 |")
        L.append("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
                 "---: | ---: | --- | --- | --- | --- |")
        for r in sorted(ed, key=lambda r: r["index"]):
            rnd = reshoot_rounds(r, rep_pair_all, rep_single_all)
            if (r["M"] or 0) != 0:
                d1 = dec(r["t1"], r["stage1"])
                d2 = dec(r["t2"], r["stage1"])
                dz = {"是" if v else "否" for v in (d1, d2)
                      if v is not None}
                dtxt = "是" if (d1 or d2) else (
                    "否" if dz else "未定")
                cls = classify_violation(rnd, d1, d2, ecc=True)
            else:
                d1 = d2 = dec(r["t1"], r["stage1"])
                d2 = dec(r["t2"], r["stage1"])
                dtxt = ("是" if (d1 or d2) else
                        "否" if (d1 is not None or d2 is not None) else "未定")
                cls = "—"
            r["_dec"] = (d1, d2)
            r["_class"] = cls
            L.append("| %d | %d | %s | %s | %s | %s | %s | %s | %s | %s | "
                     "%s | %s | %s |"
                     % (r["index"], r["stage1"], fmt(r["lane1"]),
                        fmt(r["lane2"]), fmt(r["a"]), fmt(r["b"]),
                        fmt(r["c"]), fmt(r["u"]), fmt(r["M"]),
                        reshoot_col(rnd, ecc=True), dtxt, cls,
                        miscorr_cat(r)))
        for r in mn:
            L.append("")
            L.append("M<0 逐对列出：对 %d（级 %d 路 %s+%s，a=%s b=%s "
                     "c=%s u=%s，M=%s%s）" % (
                         r["index"], r["stage1"], fmt(r["lane1"]),
                         fmt(r["lane2"]), fmt(r["a"]), fmt(r["b"]),
                         fmt(r["c"]), fmt(r["u"]), fmt(r["M"]),
                         drift_note(r, rep_pair_all, rep_single_all,
                                    shots)))
        if mp:
            cats_mp = collections.Counter(
                CAT_ZH.get(r["category"], r["category"]) for r in mp)
            L.append("")
            L.append("M>0 的对类别：%s" % json.dumps(
                dict(cats_mp), ensure_ascii=False))
        viol = [r for r in ed if (r["M"] or 0) != 0]
        if viol:
            cvc = collections.Counter(r["_class"] for r in viol)
            L.append("")
            L.append("M≠0 的对归类（§14.1-2）：%s；含译码器位 %d 对。"
                     % (json.dumps(dict(cvc), ensure_ascii=False),
                        sum(1 for r in viol
                            if r["_dec"][0] or r["_dec"][1])))
        loose_m0 = []
        for r in ed:
            if r["M"] == 0 and not (r["_dec"][0] or r["_dec"][1]):
                if decoder_member_loose(design, ddir, r["t1"], shots) or \
                        decoder_member_loose(design, ddir, r["t2"], shots):
                    loose_m0.append(r["index"])
        ndec0 = sum(1 for r in ed if r["M"] == 0
                    and (r["_dec"][0] or r["_dec"][1]))
        L.append("")
        L.append("M=0 且含译码器位（本级 u_decode 口径）：%d 对。"
                 "放宽到任意级 u_decode 再加 %d 对（%s）。"
                 % (ndec0, len(loose_m0),
                    "、".join("对 %d" % i for i in loose_m0) or "无"))
        drift0 = [r for r in ed if r["M"] == 0
                  and reshoot_rounds(r, rep_pair_all, rep_single_all)
                  and reshoot_col(
                      reshoot_rounds(r, rep_pair_all, rep_single_all),
                      ecc=True) not in ("0",)
                  and any(m != 0 for m in (
                      int(x.split(":")[-1]) for x in reshoot_col(
                          reshoot_rounds(r, rep_pair_all,
                                         rep_single_all)).split(",")))]
        if drift0:
            L.append("")
            L.append("M=0 但复打口径有小偏差的对：%s。" % "、".join(
                "对 %d（M_r=%s）" % (r["index"], reshoot_col(
                    reshoot_rounds(r, rep_pair_all, rep_single_all),
                    ecc=True)) for r in drift0))
    L.append("")

    es = ident["ecc_same_lane"]
    L.append("编码级同级同路（对照组口径）%d 对。" % len(es))
    if es:
        bad = [r for r in es if (r["u"] or 0) != 0]
        L.append("预期 u=0：%s。重合拍 a+b−c 见表。" %
                 ("全部满足" if not bad else "%d 对不符" % len(bad)))
        L.append("")
        L.append("| 对 | 级 | 路 | a | b | c | u | a+b−c | 复打口径 | 类别 |")
        L.append("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
                 "---: | --- | --- |")
        for r in sorted(es, key=lambda r: r["index"]):
            rnd = reshoot_rounds(r, rep_pair_all, rep_single_all)
            rr = "、".join("%s" % (a_r + b_r - c_r)
                           for _t, a_r, b_r, c_r, _u in rnd) or "—"
            L.append("| %d | %d | %s | %s | %s | %s | %s | %s | %s | %s |"
                     % (r["index"], r["stage1"], fmt(r["lane1"]),
                        fmt(r["a"]), fmt(r["b"]), fmt(r["c"]), fmt(r["u"]),
                        fmt(r["overlap"]), rr,
                        CAT_ZH.get(r["category"], r["category"])))
        for r in bad:
            L.append("")
            L.append("u≠0 逐对列出：对 %d（u=%s）" % (r["index"], fmt(r["u"])))
    L.append("")

    tm = ident["tmr_same_stage"]
    L.append("三模级同级 %d 对。" % len(tm))
    if tm:
        grp = collections.Counter()
        for r in tm:
            if r["rep1"] == "未定" or r["rep2"] == "未定":
                grp["未定"] += 1
            elif r["rep1"] == r["rep2"]:
                grp["同副本"] += 1
            else:
                grp["不同副本"] += 1
        bad_o = [r for r in tm if (r["O"] or 0) < 0]
        bad_sil = [r for r in tm if r["category"] == "silent"
                   and not (r["O"] or 0) > 0]
        L.append("拆分：不同副本 %d、同副本 %d、未定 %d（瓦片级口径）。"
                 % (grp["不同副本"], grp["同副本"], grp["未定"]))
        L.append("O = a+b−c ≥ 0：%s；静默的对 O>0：%s。" % (
            "全部满足" if not bad_o else "%d 对不符" % len(bad_o),
            "全部满足" if not bad_sil else "%d 对不符" % len(bad_sil)))
        L.append("")
        L.append("| 对 | 级 | 副本a | 副本b | a | b | c | O | "
                 "复打口径 O_r | 表决器位 | 归类 | 类别 |")
        L.append("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
                 "---: | --- | --- | --- | --- |")
        for r in sorted(tm, key=lambda r: r["index"]):
            rnd = reshoot_rounds(r, rep_pair_all, rep_single_all)
            viol = (r["O"] or 0) < 0 or (r["category"] == "silent"
                                         and not (r["O"] or 0) > 0)
            if viol:
                d1 = dec(r["t1"], r["stage1"])
                d2 = dec(r["t2"], r["stage1"])
                dtxt = "是" if (d1 or d2) else (
                    "否" if (d1 is not None or d2 is not None) else "未定")
                cls = classify_violation(rnd, d1, d2, ecc=False)
            else:
                d1 = dec(r["t1"], r["stage1"])
                d2 = dec(r["t2"], r["stage1"])
                dtxt = ("是" if (d1 or d2) else
                        "否" if (d1 is not None or d2 is not None)
                        else "未定")
                cls = "—"
            r["_dec"] = (d1, d2)
            r["_class"] = cls
            L.append("| %d | %d | %s | %s | %s | %s | %s | %s | %s | %s | "
                     "%s | %s |"
                     % (r["index"], r["stage1"], fmt(r["rep1"]),
                        fmt(r["rep2"]), fmt(r["a"]), fmt(r["b"]),
                        fmt(r["c"]), fmt(r["O"]),
                        reshoot_col(rnd, ecc=False), dtxt, cls,
                        CAT_ZH.get(r["category"], r["category"])))
        for r in bad_o + bad_sil:
            L.append("")
            L.append("不符逐对列出：对 %d（O=%s，归类 %s，类别 %s%s）" % (
                r["index"], fmt(r["O"]), r["_class"],
                CAT_ZH.get(r["category"], r["category"]),
                drift_note(r, rep_pair_all, rep_single_all, shots)))
        cl = [r for r in tm if r.get("_clean")]
        L.append("")
        L.append("**干净子集**（§15-1：两位都干净 %d/%d 对；全表为上表，"
                 "原样保留）：" % (len(cl), len(tm)))
        if cl:
            L.append("")
            L.append("| 对 | 级 | 副本a | 副本b | a | b | c | O | "
                     "复打口径 O_r | 类别 |")
            L.append("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
                     "---: | --- | --- |")
            for r in sorted(cl, key=lambda r: r["index"]):
                rnd = reshoot_rounds(r, rep_pair_all, rep_single_all)
                L.append("| %d | %d | %s | %s | %s | %s | %s | %s | %s | "
                         "%s |"
                         % (r["index"], r["stage1"], fmt(r["rep1"]),
                            fmt(r["rep2"]), fmt(r["a"]), fmt(r["b"]),
                            fmt(r["c"]), fmt(r["O"]),
                            reshoot_col(rnd, ecc=False),
                            CAT_ZH.get(r["category"], r["category"])))
    L.append("")

    # ---- P1 section-13 split recompute with bit-level replicas ------------
    bmap = bit_replica_map(ddir)
    if tm and bmap:
        grp2 = collections.Counter()
        same_rep_rows = []
        L.append("### 三模级同级对：逐位副本号重算（WP6-A 归属）")
        L.append("")
        L.append("规划方早先\"同级同副本 20 对、其中 2 对静默\"的拆分作废"
                 "（把两个\"未定\"当成同一副本）。下表按 WP6-A 逐位归属"
                 "的副本号重算；\"未定\"=逐位归属给不出唯一副本。")
        L.append("")
        recomputed = []
        for r in tm:
            rp1 = bmap.get(tuple(r["t1"]))
            rp2 = bmap.get(tuple(r["t2"]))
            if rp1 is None or rp2 is None:
                grp2["未定"] += 1
                g = "未定"
            elif rp1 == rp2:
                grp2["同副本"] += 1
                g = "同副本"
            else:
                grp2["不同副本"] += 1
                g = "不同副本"
            recomputed.append((r, rp1, rp2, g))
            if g == "同副本" and r["category"] != "corrected":
                same_rep_rows.append((r, rp1))
        L.append("重算结果：不同副本 %d、同副本 %d、未定 %d（逐位口径，"
                 "整网负载版）。"
                 % (grp2["不同副本"], grp2["同副本"], grp2["未定"]))
        if same_rep_rows:
            L.append("")
            L.append("同副本且非\"已纠正\"的对，逐位列出归属：")
            for r, rp in same_rep_rows:
                L.append("- 对 %d（级 %d 副本 %d，类别 %s，O=%s）："
                         "位 %s、位 %s 的逐位归属均为副本 %d" % (
                             r["index"], r["stage1"], rp,
                             CAT_ZH.get(r["category"], r["category"]),
                             fmt(r["O"]), r["key1"], r["key2"], rp))
        # ---- downstream cross-check (WP6-A section 8) --------------------
        comb = {}
        dmap = bit_replica_map(ddir, ds_fname())
        if dmap:
            conflicts = []
            grp3 = collections.Counter()
            for r in tm:
                for ti in ("t1", "t2"):
                    key = tuple(r[ti])
                    w, d = bmap.get(key), dmap.get(key)
                    if w is not None and d is not None and w != d:
                        conflicts.append((key, w, d))
                        continue          # 14.1-1: conflicting bits unused
                    comb[key] = d if d is not None else w
            same3 = []
            for r in tm:
                rp1, rp2 = comb.get(tuple(r["t1"])), comb.get(tuple(r["t2"]))
                if rp1 is None or rp2 is None:
                    grp3["未定"] += 1
                elif rp1 == rp2:
                    grp3["同副本"] += 1
                    if r["category"] != "corrected":
                        same3.append((r, rp1))
                else:
                    grp3["不同副本"] += 1
            L.append("")
            L.append("下游口径复核（WP6-A 第 8 节）：下游归属唯一即用下游，"
                     "否则用整网；两者唯一副本不一致的位弃用并逐个列出。"
                     "结果：不同副本 %d、同副本 %d、未定 %d。"
                     % (grp3["不同副本"], grp3["同副本"], grp3["未定"]))
            if conflicts:
                L.append("整网/下游唯一副本不一致的位（弃用）：")
                for key, w, d in sorted(conflicts):
                    L.append("- 位 (la=%d, wd=%d, bt=%d)：整网 %d vs 下游 %d"
                             % (key[0], key[1], key[2], w, d))
            for r, rp in same3:
                L.append("- 下游口径下同副本且非\"已纠正\"：对 %d（级 %d "
                         "副本 %d，类别 %s）" % (
                             r["index"], r["stage1"], rp,
                             CAT_ZH.get(r["category"], r["category"])))
        L.append("")

        # ---- 14.1-4 channel split for different-replica pairs ------------
        repmap = comb if comb else bmap
        diff_rows = []
        for r in tm:
            rp1 = repmap.get(tuple(r["t1"])) if repmap else None
            rp2 = repmap.get(tuple(r["t2"])) if repmap else None
            if rp1 is None or rp2 is None:
                rp1, rp2 = r["rep1"], r["rep2"]
            if isinstance(rp1, int) and isinstance(rp2, int) \
                    and rp1 != rp2:
                diff_rows.append(r)
        if diff_rows:
            grp_ch = {"同通道": [], "异通道": [], "含副本内共用": []}
            for r in diff_rows:
                c1 = channel_of(design, ddir, tuple(r["t1"]), shots)
                c2 = channel_of(design, ddir, tuple(r["t2"]), shots)
                r["_ch"] = (sorted(c1), sorted(c2))
                if len(c1) == 1 and len(c2) == 1:
                    grp_ch["同通道" if c1 == c2 else "异通道"].append(r)
                else:
                    grp_ch["含副本内共用"].append(r)
            L.append("### 不同副本对按通道拆分（§14.1-4）")
            L.append("")
            L.append("两位逐位归属对象名里的通道号（`g_ch[k]`/"
                     "`g_lane[k]`；无通道号的记\"副本内共用\"）。")
            L.append("")
            L.append("| 组 | 对数 | 类别分布 |")
            L.append("| --- | ---: | --- |")
            for g in ("同通道", "异通道", "含副本内共用"):
                rows = grp_ch[g]
                cc = collections.Counter(
                    CAT_ZH.get(r["category"], r["category"]) for r in rows)
                L.append("| %s | %d | %s |" % (g, len(rows),
                         json.dumps(dict(cc), ensure_ascii=False)))
            for r in diff_rows:
                if r["category"] == "silent":
                    c1, c2 = r["_ch"]
                    L.append("- 静默对 %d（级 %d 副本 %s+%s，通道 %s vs %s）"
                             % (r["index"], r["stage1"], fmt(r["rep1"]),
                                fmt(r["rep2"]), c1 or "副本内共用",
                                c2 or "副本内共用"))
            L.append("")

    # ================= 批次 4：加压配对与对照（WP6 §14.2-3）=================
    se, st2 = ident["stress_ecc"], ident["stress_tmr"]
    ce, ct = ident["control_ecc"], ident["control_tmr"]
    if se or st2 or ce or ct:
        L.append("## 加压配对与对照（批次 4，WP6 §14.2）")
        L.append("")
        if se:
            gpath, gdec = [], []
            for r in sorted(se, key=lambda r: r["index"]):
                d1 = dec(r["t1"], r["stage1"])
                d2 = dec(r["t2"], r["stage1"])
                r["_dec"] = (d1, d2)
                (gdec if (d1 or d2) else gpath).append(r)
            n_mis = sum(1 for r in se
                        if r["category"] == "silent" and (r["u"] or 0) == 0
                        and (r.get("M") or 0) > 0)
            L.append("### 编码级加压对 %d 对：两位都在路内 %d、"
                     "含译码器位 %d%s。"
                     % (len(se), len(gpath), len(gdec),
                        ("；其中误纠 %d 对（类别列记『静默·误纠』）"
                         % n_mis) if n_mis else ""))
            for gname, rows in (("两位都在路内", gpath),
                                ("含译码器位", gdec)):
                if not rows:
                    continue
                L.append("")
                L.append("**%s**（%d 对）：" % (gname, len(rows)))
                L.append("")
                L.append("| 对 | 级 | 路a | 路b | a | b | c | u | M | "
                         "复打口径 M_r | 类别 |")
                L.append("| ---: | ---: | ---: | ---: | ---: | ---: | "
                         "---: | ---: | ---: | --- | --- |")
                for r in rows:
                    rnd = reshoot_rounds(r, rep_pair_all, rep_single_all)
                    L.append("| %d | %d | %s | %s | %s | %s | %s | %s | %s "
                             "| %s | %s |"
                             % (r["index"], r["stage1"], fmt(r["lane1"]),
                                fmt(r["lane2"]), fmt(r["a"]), fmt(r["b"]),
                                fmt(r["c"]), fmt(r["u"]), fmt(r["M"]),
                                reshoot_col(rnd, ecc=True),
                                miscorr_cat(r)))
            L.append("")
        if st2:
            grp_ch = {"同通道": [], "异通道": [], "含副本内共用": []}
            for r in sorted(st2, key=lambda r: r["index"]):
                c1 = channel_of(design, ddir, tuple(r["t1"]), shots)
                c2 = channel_of(design, ddir, tuple(r["t2"]), shots)
                r["_ch"] = (sorted(c1), sorted(c2))
                if len(c1) == 1 and len(c2) == 1:
                    grp_ch["同通道" if c1 == c2 else "异通道"].append(r)
                else:
                    grp_ch["含副本内共用"].append(r)
            L.append("### 三模级加压对 %d 对，按通道分组（§14.1-4）："
                     "同通道 %d、异通道 %d、含副本内共用 %d。"
                     % (len(st2), len(grp_ch["同通道"]),
                        len(grp_ch["异通道"]), len(grp_ch["含副本内共用"])))
            for gname, rows in grp_ch.items():
                if not rows:
                    continue
                L.append("")
                L.append("**%s**（%d 对）：" % (gname, len(rows)))
                L.append("")
                L.append("| 对 | 级 | 副本a | 副本b | 通道a | 通道b | "
                         "a | b | c | O | 复打口径 O_r | 类别 |")
                L.append("| ---: | ---: | ---: | ---: | --- | --- | "
                         "---: | ---: | ---: | ---: | --- | --- |")
                for r in rows:
                    rnd = reshoot_rounds(r, rep_pair_all, rep_single_all)
                    c1, c2 = r["_ch"]
                    L.append("| %d | %d | %s | %s | %s | %s | %s | %s | "
                             "%s | %s | %s | %s |"
                             % (r["index"], r["stage1"], fmt(r["rep1"]),
                                fmt(r["rep2"]), c1 or "共用",
                                c2 or "共用", fmt(r["a"]), fmt(r["b"]),
                                fmt(r["c"]), fmt(r["O"]),
                                reshoot_col(rnd, ecc=False),
                                CAT_ZH.get(r["category"], r["category"])))
            L.append("")
            cl = [r for r in st2 if r.get("_clean")]
            nsil = sum(1 for r in cl if r["category"] == "silent")
            L.append("**干净子集**（§15-1：两位都干净 %d/%d 对，其中静默 "
                     "%d 对；全表为上表，原样保留）："
                     % (len(cl), len(st2), nsil))
            if cl:
                L.append("")
                L.append("| 对 | 级 | 副本a | 副本b | 通道a | 通道b | "
                         "a | b | c | O | 复打口径 O_r | 类别 |")
                L.append("| ---: | ---: | ---: | ---: | --- | --- | "
                         "---: | ---: | ---: | ---: | --- | --- |")
                for r in sorted(cl, key=lambda r: r["index"]):
                    rnd = reshoot_rounds(r, rep_pair_all, rep_single_all)
                    c1, c2 = r["_ch"]
                    L.append("| %d | %d | %s | %s | %s | %s | %s | %s | "
                             "%s | %s | %s | %s |"
                             % (r["index"], r["stage1"], fmt(r["rep1"]),
                                fmt(r["rep2"]), c1 or "共用",
                                c2 or "共用", fmt(r["a"]), fmt(r["b"]),
                                fmt(r["c"]), fmt(r["O"]),
                                reshoot_col(rnd, ecc=False),
                                CAT_ZH.get(r["category"], r["category"])))
            L.append("")
        if ce or ct:
            L.append("### 对照组（编码级同路 %d 对、三模级同副本 %d 对）"
                     % (len(ce), len(ct)))
            L.append("")
            if ce:
                L.append("| 对 | 级 | 路 | a | b | c | u | a+b−c | "
                         "复打口径 | 类别 |")
                L.append("| ---: | ---: | ---: | ---: | ---: | ---: | "
                         "---: | ---: | --- | --- |")
                for r in sorted(ce, key=lambda r: r["index"]):
                    rnd = reshoot_rounds(r, rep_pair_all, rep_single_all)
                    rr = "、".join("%s" % (a_r + b_r - c_r)
                                   for _t, a_r, b_r, c_r, _u in rnd) or "—"
                    L.append("| %d | %d | %s | %s | %s | %s | %s | %s | "
                             "%s | %s |"
                             % (r["index"], r["stage1"], fmt(r["lane1"]),
                                fmt(r["a"]), fmt(r["b"]), fmt(r["c"]),
                                fmt(r["u"]), fmt(r["overlap"]), rr,
                                CAT_ZH.get(r["category"], r["category"])))
            if ct:
                L.append("")
                L.append("| 对 | 级 | 副本 | a | b | c | O | "
                         "复打口径 O_r | 类别 |")
                L.append("| ---: | ---: | ---: | ---: | ---: | ---: | "
                         "---: | --- | --- |")
                for r in sorted(ct, key=lambda r: r["index"]):
                    rnd = reshoot_rounds(r, rep_pair_all, rep_single_all)
                    L.append("| %d | %d | %s | %s | %s | %s | %s | %s | "
                             "%s |"
                             % (r["index"], r["stage1"], fmt(r["rep1"]),
                                fmt(r["a"]), fmt(r["b"]), fmt(r["c"]),
                                fmt(r["O"]), reshoot_col(rnd, ecc=False),
                                CAT_ZH.get(r["category"], r["category"])))
                cl = [r for r in ct if r.get("_clean")]
                cl_sr = [r for r in cl
                         if r["rep1"] != "未定" and r["rep1"] == r["rep2"]]
                nsil = sum(1 for r in cl if r["category"] == "silent")
                L.append("")
                L.append("**干净子集**（§15-1：两位都干净 %d/%d 对，其中"
                         "池口径同副本 %d 对、静默 %d 对；全表为上表，"
                         "原样保留）："
                         % (len(cl), len(ct), len(cl_sr), nsil))
                if cl:
                    L.append("")
                    L.append("| 对 | 级 | 副本 | a | b | c | O | "
                             "复打口径 O_r | 类别 |")
                    L.append("| ---: | ---: | ---: | ---: | ---: | ---: | "
                             "---: | --- | --- |")
                    for r in sorted(cl, key=lambda r: r["index"]):
                        rnd = reshoot_rounds(r, rep_pair_all,
                                             rep_single_all)
                        L.append("| %d | %d | %s | %s | %s | %s | %s | %s "
                                 "| %s |"
                                 % (r["index"], r["stage1"], fmt(r["rep1"]),
                                    fmt(r["a"]), fmt(r["b"]), fmt(r["c"]),
                                    fmt(r["O"]),
                                    reshoot_col(rnd, ecc=False),
                                    CAT_ZH.get(r["category"],
                                               r["category"])))
            L.append("")
        rs14 = [r for r in rep_pair_all if r.get("subkind") == "reshoot14"]
        rb14 = [r for r in rep_single_all if r.get("subkind") == "reshoot14"]
        if rs14 or rb14:
            L.append("### §14.2-2 复打记录（对 115、212）：配对 %d 条、"
                     "单发 %d 条。" % (len(rs14), len(rb14)))
            L.append("")
            L.append("| 记录 | 序号 | a_r | b_r | c_r | u_r | 类别 |")
            L.append("| --- | ---: | ---: | ---: | ---: | ---: | --- |")
            for r in sorted(rs14, key=lambda r: r["index"]):
                L.append("| pair_natural_repeat | %d | %s | %s | %s | %s "
                         "| %s |"
                         % (r["index"], fmt(r["a"]), fmt(r["b"]),
                            fmt(r["c"]), fmt(r["u"]),
                            CAT_ZH.get(r["category"], r["category"])))
            for r in sorted(rb14, key=lambda r: r["index"]):
                L.append("| single_reprobe | %d | %s | – | – | – | %s |"
                         % (r["index"], fmt(r["reprobe_cor"]),
                            CAT_ZH.get(r["category"], r["category"])))
            L.append("")
        L.append("")

    # ================= WP6 §15 离线补充 =================
    if unclean_bits or ident["stress_tmr"] or ident["control_tmr"]:
        L.append("## 三模副本号『干净』子集与常量网耦合（WP6 §15，离线）")
        L.append("")
        L.append("判定（§15-1）：一位算『干净』要求其下游口径归属在遥测"
                 "给出的那一级上恰好一份副本，且不在常量网上。上方三张"
                 "三模级表（自然配对、加压对、对照组）各附『两位都干净』"
                 "子集；全量表均按抽样/预登记口径原样保留。")
        L.append("")
        # -- §15-1 unclean-bit list -------------------------------------
        pool_nat, pool_st, pool_ct = set(), set(), set()
        for r in ident["tmr_same_stage"]:
            for _tk in ("t1", "t2"):
                pool_nat.add((tuple(r[_tk]), r["stage1"]))
        for r in ident["stress_tmr"]:
            for _tk in ("t1", "t2"):
                pool_st.add((tuple(r[_tk]), r["stage1"]))
        for r in ident["control_tmr"]:
            for _tk in ("t1", "t2"):
                pool_ct.add((tuple(r[_tk]), r["stage1"]))
        pool_sc = pool_st | pool_ct
        n_unc_sc = sum(1 for ks in pool_sc if ks in unclean_bits)
        rcnt = collections.Counter(
            w for ks in pool_sc if ks in unclean_bits
            for w in unclean_bits[ks])

        def _pools(ks):
            return "、".join(lbl for lbl, p in (
                ("自然", pool_nat), ("加压", pool_st), ("对照", pool_ct))
                if ks in p)
        L.append("### §15-1 不干净位清单")
        L.append("")
        L.append("三模加压对与对照组用到的位 %d 个（(位,级) 唯一键口径）："
                 "不干净 %d 个，占 %.1f%%。原因拆分（一位可有多因）：%s。"
                 % (len(pool_sc), n_unc_sc,
                    100.0 * n_unc_sc / max(1, len(pool_sc)),
                    "、".join("%s %d" % kv for kv in sorted(rcnt.items()))
                    or "无"))
        if unclean_bits:
            L.append("")
            L.append("| 位 (la,wd,bt) | 级 | 原因 | 所在表 |")
            L.append("| --- | ---: | --- | --- |")
            for (key, st), why in sorted(unclean_bits.items(),
                                         key=lambda kv: (kv[0][1],
                                                         kv[0][0])):
                L.append("| %s | %s | %s | %s |"
                         % (key, st, " + ".join(why),
                            _pools((key, st))))
        L.append("")
        # -- §15-2 constant-net cross-replica coupling in pairs ----------
        coupling = []
        _K2LBL = {"pair_control": "对照组", "pair_natural": "自然配对",
                  "pair_stress": "加压对"}
        for r in (ident["control_tmr"] + ident["tmr_same_stage"]
                  + ident["stress_tmr"]):
            if r["rep1"] == "未定" or r["rep1"] != r["rep2"]:
                continue
            repkind = r["kind"] + "_repeat"
            for tk in ("t1", "t2"):
                key = tuple(r[tk]); st = r["stage1"]
                by = ds_replicas_by_stage(design, ddir, key)
                if len(by.get(st) or ()) >= 2:
                    isc, _netc = bit_const_net(shots, key)
                    dsh_ = _DSHOTS.get((design, key)) or {}
                    coupling.append({
                        "index": r["index"], "key": key, "stage": st,
                        "pool_rep": r["rep1"],
                        "table": _K2LBL.get(r["kind"], r["kind"]),
                        "reps": sorted(by[st]),
                        "chs": sorted(ds_channels(design, ddir, key)),
                        "net": bit_net_name(shots, key),
                        "wn": (shots.get(key) or {}).get("attrib"),
                        "ds": dsh_.get("attrib"), "const": isc,
                        "cat": r["category"],
                        "rep_cats": sorted({
                            rr["category"] for rr in rep_pair_all
                            if rr["index"] == r["index"]
                            and rr["kind"] == repkind})})
        if coupling:
            L.append("### §15-2 池口径记同副本、下游口径跨副本的对")
            L.append("")
            L.append("耦合机制逐条标注（常量网/普通信号网）；这些对按"
                     "池口径记作同副本，实为跨副本对。%s" % (
                         "**注意：本节基于 v1（多算）下游口径，其中"
                         "『信号网·非常量』各条在 v2 复核完成前不作结"
                         "论；v2 口径见 REPORT_v2.md。**"
                         if _DS_VER == "v1" else
                         "本节基于 v2 引脚级下游口径（§16 修正后）。"))
            L.append("")
            for cp in coupling:
                wn_txt = "、".join("%s[%s]副本%s" % tuple(a)
                                  for a in (cp["wn"] or [])) or "未定"
                ch_txt = ("同通道 %s" % cp["chs"] if cp["chs"]
                          else "通道未定")
                L.append("- %s对 %d 位 %s（遥测级 %d）：网 `%s`（%s）；"
                         "整网归属 %s；下游归属级 %d 副本 %s、通道 %s"
                         "——该对实为『副本 %s %s』跨副本耦合，非真同"
                         "副本（池口径记同副本 %s）。类别 %s（复打 %s）。"
                         % (cp["table"], cp["index"], cp["key"],
                            cp["stage"], cp["net"] or "?",
                            "常量网" if cp["const"] else "信号网·非常量",
                            wn_txt, cp["stage"], cp["reps"], cp["chs"],
                            cp["reps"], ch_txt, fmt(cp["pool_rep"]),
                            CAT_ZH.get(cp["cat"], cp["cat"]),
                            "、".join(CAT_ZH.get(x, x)
                                      for x in cp["rep_cats"]) or "无"))
            L.append("")
        # -- §15-2 singles on constant nets reaching >=2 replicas --------
        const_xrep = []
        for r in recs:
            if r.get("kind") != "single" or r.get("void"):
                continue
            t = r["target"]
            t = t[0] if isinstance(t, list) else t
            key = (t["la"], t["wd"], t["bt"])
            s_ = shots.get(key)
            if not s_ or s_.get("resource") != "开关":
                continue
            isc, net = bit_const_net(shots, key)
            if not isc:
                continue
            by = ds_replicas_by_stage(design, ddir, key)
            multi = {s2: sorted(v) for s2, v in by.items() if len(v) >= 2}
            if multi:
                const_xrep.append({"key": key, "by": multi, "net": net,
                                   "cat": r.get("category")})
        L.append("### §15-2 常量网上且下游同级跨 ≥2 副本的单发开关位"
                 "（%d 个）" % len(const_xrep))
        L.append("")
        if const_xrep:
            L.append("| 位 (la,wd,bt) | 下游级→副本 | 单发类别 | 网 |")
            L.append("| --- | --- | --- | --- |")
            for cp in sorted(const_xrep, key=lambda x: x["key"]):
                bytxt = "、".join("级%s→副本%s" % (st, v)
                                  for st, v in sorted(cp["by"].items()))
                L.append("| %s | %s | %s | `%s` |"
                         % (cp["key"], bytxt,
                            CAT_ZH.get(cp["cat"], cp["cat"]),
                            cp["net"] or "?"))
        else:
            L.append("无。")
        L.append("")
        # -- §15-3 moved-pair note ---------------------------------------
        moved = [r for r in ident["stress_ecc"] if r.get("_moved")]
        if moved:
            L.append("### §15-3 抽样归类与报告路号口径")
            L.append("")
            for r in moved:
                L.append("- 对 %d（表中类别列带 †）：抽样按冻结 L 集池"
                         "路号 %s≠%s 归『加压对』；报告路号取自两位单发遥测"
                         " last_loc（均为 %s），曾将其放入对照表。按 "
                         "§15-3 两表均按抽样归类，该对列于加压表"
                         "（a+b−c=%s）。"
                         % (r["index"], fmt(r.get("pool_lane1")),
                            fmt(r.get("pool_lane2")), fmt(r["lane1"]),
                            fmt(r["overlap"])))
            L.append("")
        # -- §15-4 silent-miscorrected pair detail -----------------------
        miscorr = [r for r in ident["stress_ecc"]
                   if r["category"] == "silent" and (r["u"] or 0) == 0
                   and (r.get("M") or 0) > 0]
        if miscorr:
            L.append("### §15-4 静默误纠对明细")
            L.append("")
            for r in miscorr:
                rec = rec_by_idx.get((r["kind"], r["subkind"],
                                      r["index"])) or {}
                ds_ = rec.get("output_diff_summary") or {}
                ds_txt = ("n_diff=%s、tail_zero=%s、n_beats=%s、"
                          "n_fields=%s、pow2_fields=%s、frames_hit=%s"
                          % (rec.get("n_diff"), rec.get("tail_zero"),
                             ds_.get("n_beats"), ds_.get("n_fields"),
                             ds_.get("pow2_fields"),
                             ds_.get("frames_hit"))) if ds_ else "未记录"
                s1r = singles.get(tuple(r["t1"]))
                s2r = singles.get(tuple(r["t2"]))
                L.append("- 对 %d（级 %d 路 %s+%s，a=%s、b=%s、c=%s、"
                         "u=0、M=%s）：重合拍 %s 拍全部误纠，整次运行"
                         "无不可纠报出；类别行记『静默·误纠』。"
                         % (r["index"], r["stage1"], fmt(r["lane1"]),
                            fmt(r["lane2"]), fmt(r["a"]), fmt(r["b"]),
                            fmt(r["c"]), fmt(r["M"]), fmt(r["M"])))
                L.append("  - 位 %s：%s；单发类别 %s（a=%s）。"
                         % (tuple(r["t1"]),
                            attrib_lines(shots.get(tuple(r["t1"]))),
                            CAT_ZH.get((s1r or {}).get("category"), "?"),
                            fmt(r["a"])))
                L.append("  - 位 %s：%s；单发类别 %s（b=%s）。"
                         % (tuple(r["t2"]),
                            attrib_lines(shots.get(tuple(r["t2"]))),
                            CAT_ZH.get((s2r or {}).get("category"), "?"),
                            fmt(r["b"])))
                L.append("  - 逐拍差异摘要：%s。" % ds_txt)
            L.append("")

    # ============ 14.1-3 计数层面可重复性（repeat / reprobe 逐发比 cor_cnt）
    rep_rows = []
    for r in recs:
        k = r.get("kind")
        if r.get("void") or r.get("standby"):
            continue
        if k in ("repeat", "single_reprobe"):
            t = (r.get("target") or [None])[0]
            if t is None:
                continue
            orig = singles.get(key_of(t))
            oc = tel_of(orig).get("cor_cnt") if orig else None
            rep_rows.append({"kind": k, "index": r.get("index"),
                             "key": camp._bit_key(t),
                             "tkey": key_of(t),
                             "res": bit_resource(shots, key_of(t)),
                             "new": tel_of(r).get("cor_cnt"), "orig": oc,
                             "same": tel_of(r).get("cor_cnt") == oc})
        elif k in ("pair_natural_repeat", "pair_control_repeat"):
            tgt = r.get("target") or []
            if len(tgt) != 2:
                continue
            orig = orig_all_pairs.get(r["index"])
            if not orig:
                continue
            oc, ou = tel_of(orig).get("cor_cnt"), tel_of(orig).get("unc_cnt")
            rep_rows.append({"kind": k, "index": r.get("index"),
                             "key": camp._bit_key(tgt[0]) + "+" + camp._bit_key(tgt[1]),
                             "tkey": key_of(tgt[0]),
                             "res": bit_resource(shots, key_of(tgt[0])),
                             "new": tel_of(r).get("cor_cnt"),
                             "new_u": tel_of(r).get("unc_cnt"),
                             "orig": oc, "orig_u": ou,
                             "same": (tel_of(r).get("cor_cnt") == oc
                                      and tel_of(r).get("unc_cnt") == ou)})
    if rep_rows:
        L.append("## 计数层面可重复性（§14.1-3）")
        L.append("")
        tot = collections.Counter()
        okc = collections.Counter()
        diffs = []
        for r in rep_rows:
            tot[r["res"]] += 1
            if r["same"]:
                okc[r["res"]] += 1
            else:
                diffs.append(r)
        L.append("repeat / single_reprobe / 配对复打记录共 %d 条，与预筛"
                 "同位逐发比 cor_cnt（配对复打同核 unc_cnt）；计数完全相同"
                 "的比例按资源类别：" % len(rep_rows))
        L.append("")
        L.append("| 资源类别 | 记录数 | 计数相同 | 比例 |")
        L.append("| --- | ---: | ---: | ---: |")
        for resn, v in tot.most_common():
            L.append("| %s | %d | %d | %.2f%% |"
                     % (resn, v, okc.get(resn, 0), 100.0 * okc.get(resn, 0) / v))
        if diffs:
            L.append("")
            L.append("计数不同的记录逐条列出：")
            for r in diffs:
                feat = ""
                objs = ""
                sh = shots.get(r.get("tkey"))
                if sh and sh.get("hits_detail"):
                    feat = sh["hits_detail"][0].get("feature") or ""
                    objs = "、".join((sh["hits_detail"][0].get("objects")
                                     or [])[:3])
                if r["kind"] == "single_reprobe" or r["kind"] == "repeat":
                    L.append("- %s #%d（%s，%s）：预筛 %s vs 复打 %s；%s %s"
                             % (r["kind"], r["index"], r["key"], r["res"],
                                fmt(r["orig"]), fmt(r["new"]), feat, objs))
                else:
                    L.append("- %s #%d（%s，%s）：原发 c=%s,u=%s vs 复打 "
                             "c=%s,u=%s；%s %s"
                             % (r["kind"], r["index"], r["key"], r["res"],
                                fmt(r["orig"]), fmt(r.get("orig_u")),
                                fmt(r["new"]), fmt(r.get("new_u")),
                                feat, objs))
        else:
            L.append("")
            L.append("全部记录计数相同。")
        L.append("")

    rp = ident["pair_repeats"]
    sb = ident["single_reprobes"]
    L.append("失效对的复打与单发重探（确定性核对）：pair_natural_repeat "
             "%d 条、single_reprobe %d 条。" % (len(rp), len(sb)))
    if rp or sb:
        L.append("")
        L.append("| 记录 | 对/发 | a | b | c | u | 原发类别 | 本次类别 |")
        L.append("| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |")
        for r in sorted(rp, key=lambda r: r["index"]):
            base = (r["index"] // 100 if r.get("subkind") == "reshoot14"
                    else r["index"])
            orig = orig_all_pairs.get(base)
            oc, ou = (tel_of(orig).get("cor_cnt"),
                      tel_of(orig).get("unc_cnt")) if orig else (None, None)
            flag = "" if (r["c"] == oc and r["u"] == ou) else " **不符**"
            oc_cat = CAT_ZH.get(orig.get("category")) if orig else "—"
            tag = r["kind"] + ("[%s]" % r["subkind"] if r.get("subkind")
                               else "")
            L.append("| %s | %d | %s | %s | %s（原 %s）| "
                     "%s（原 %s）| %s | %s%s |"
                     % (tag, r["index"], fmt(r["a"]), fmt(r["b"]), fmt(r["c"]),
                        fmt(oc), fmt(r["u"]), fmt(ou), oc_cat,
                        CAT_ZH.get(r["category"], r["category"]), flag))
        for r in sorted(sb, key=lambda r: r["index"]):
            flag = "" if r["reprobe_cor"] == r["orig_cor"] else " **不符**"
            L.append("| single_reprobe | %d | %s（原 %s）| – | – | – | %s "
                     "| %s%s |"
                     % (r["index"], fmt(r["reprobe_cor"]),
                        fmt(r["orig_cor"]),
                        CAT_ZH.get(r.get("orig_category"),
                                   r.get("orig_category") or "—"),
                        CAT_ZH.get(r["category"], r["category"]), flag))
    L.append("")

    # ---- sharded export provenance + downstream section (WP6-A §8) -------
    prov_path = Path(out) / "shard_provenance.json"
    if prov_path.is_file():
        pv = json.loads(prov_path.read_text(encoding="utf-8"))
        g = pv.get("gate_P0") or {}
        mp = (pv.get("main_pips") or {}).get(camp.DESIGN_DIR[design]) or {}
        nl = (pv.get("nload_downstream") or {}).get(
            camp.DESIGN_DIR[design]) or {}
        L.append("## 分片导出与下游口径（WP6-A 第 8 节）")
        L.append("")
        if g:
            L.append("- **P0 分片门槛**：冻结检查点 `P1_WP4R2` 按 n=%d 分片"
                     "重导，各片耗时 %s s；合并 %s 行 vs 既有完整导出 %s 行，"
                     "逐行排序后 SHA-256 一致（%s…）。判定 **%s**。单进程 "
                     "Vivado 峰值 RSS %s kB（约 %.1f GB）；最多 %d 进程并行，"
                     "总量远在 100 GB 上限内。"
                     % (g.get("n_shards"),
                        "%s-%s" % (min(g.get("per_shard_seconds") or [0]),
                                   max(g.get("per_shard_seconds") or [0])),
                        "{:,}".format(g.get("merged_lines") or 0),
                        "{:,}".format(g.get("reference_lines") or 0),
                        (g.get("merged_sha256") or "")[:16],
                        g.get("verdict"),
                        "{:,}".format(g.get("vivado_peak_rss_kb") or 0),
                        (g.get("vivado_peak_rss_kb") or 0) / 1e6,
                        (pv.get("memory") or {}).get(
                            "max_concurrent_vivado", "?")))
        if mp:
            if mp.get("n_shards"):
                L.append("- **本设计 pips 导出**：n=%d 分片，各片 %s s，"
                         "合并 %s 行；峰值 RSS %s kB。"
                         % (mp["n_shards"],
                            "%s-%s" % (min(mp["per_shard_seconds"]),
                                       max(mp["per_shard_seconds"])),
                            "{:,}".format(mp.get("merged_lines") or 0),
                            "{:,}".format(mp.get("vivado_peak_rss_kb") or 0)))
            else:
                L.append("- **本设计 pips 导出**：%s。"
                         % mp.get("mode", "单进程"))
        if nl:
            L.append("- **本设计下游导出**（`downstream_dump.tcl`，"
                     "节点/线/PIP/站点引脚）：n=%d 分片，各片 %s s，"
                     "合并 %s 行。" % (
                         nl.get("n_shards"),
                         "%s-%s" % (min(nl.get("per_shard_seconds") or [0]),
                                    max(nl.get("per_shard_seconds") or [0])),
                         "{:,}".format(nl.get("merged_lines") or 0)))
        ba_ds = ddir / ds_fname()
        if ba_ds.is_file():
            ds = json.loads(ba_ds.read_text(encoding="utf-8"))
            g4key = ("g4_telemetry_downstream_v2" if _DS_VER == "v2"
                     else "g4_telemetry_downstream")
            dvkey = "v1_v2_diff" if _DS_VER == "v2" else "diff_vs_wholenet"
            g4 = ds.get(g4key) or {}
            dv = ds.get(dvkey) or {}
            lane = g4.get("lane_consistent_frac")
            L.append("- **下游归属结果（%s）**：已纠正 %d 发中可判 %d 发，"
                     "级一致率 %.4f、路一致率 %s（门 0.95，%s）；"
                     "下游归属与%s不同的发 %d 发（详见 "
                     "`%s`）。整网口径保留在 "
                     "`bit_attrib.json`，两口径并列。" % (
                         _DS_LABEL[_DS_VER],
                         g4.get("corrected_singles", -1),
                         g4.get("unique_stage_judged", -1),
                         g4.get("stage_consistent_frac", -1),
                         "不适用" if lane is None else "%.4f" % lane,
                         g4.get("verdict", "?"),
                         "整网归属" if _DS_VER == "v1" else "v1 口径",
                         dv.get("attrib_changed_shots", -1),
                         ds_fname()))
        if _DS_VER == "v2":
            cmpf = Path(out) / "vld50_cmp.json"
            if cmpf.is_file():
                cm = json.loads(cmpf.read_text(encoding="utf-8"))
                rd = (cm.get("results") or {}).get(
                    camp.DESIGN_DIR[design]) or {}
                rs = (ds.get("resolve_stats") or {})
                L.append("- **独立校验（§16）**：冻结检查点只读 Vivado 会话"
                         "独立复算（ROUTE 树线序定向 + 引脚级负载，"
                         "实现与 python 无共享代码）：本设计 %d/%d 位开关"
                         "负载集合一致，miss_pip=%d；四设计合计 %d/%d。"
                         "路由叶站点引脚经 BEL 规则解析（%d 引脚→%d 单元），"
                         "未映射站点引脚 %d。脚本 `results/wp6/wp6_vld50.tcl`，"
                         "原始输出 `vld50.log`。" % (
                             rd.get("match", -1), rd.get("total", -1),
                             cm.get("miss_pip", -1),
                             cm.get("total_match", -1),
                             sum((v or {}).get("total", 0)
                                 for v in (cm.get("results") or {}).values()),
                             rs.get("bel_rule_pins", 0),
                             rs.get("bel_rule_cells", 0),
                             sum(len(s.get("unresolved_sitepins") or [])
                                 for s in ds.get("shots", []))))
        L.append("")

    # ---- integrity notes --------------------------------------------------
    L.append("## 完整性核对")
    L.append("")
    L.append("- 遥测 arch 逐条核对（WP6 第 13 节，本设计应为 %d）："
             "检查遥测字段 %d 处，不符 %d 处。%s" % (
                 exp_arch, n_tel, len(bad_arch),
                 "" if not bad_arch else "明细：" + json.dumps(
                     bad_arch[:20], ensure_ascii=False)))
    diff_err = sum(1 for r in recs
                   if isinstance(r.get("output_diff_summary"), dict)
                   and "error" in r["output_diff_summary"])
    diff_ok = sum(1 for r in recs
                  if isinstance(r.get("output_diff_summary"), dict)
                  and "n_beats" in r["output_diff_summary"])
    diff_pos = sum(1 for r in recs
                   if (r.get("n_diff") or 0) > 0
                   or (isinstance(r.get("output_diff_summary"), dict)
                       and (r["output_diff_summary"].get("n_diff") or 0) > 0))
    L.append("- 输出差异摘要：带摘要字段 %d 发（其中实际有差异 "
             "n_diff>0 共 %d 发；逐拍差异见 output_diffs.jsonl.gz）；"
             "修复前产出、摘要为 {\"error\": \"'data'\"} 的发 %d 条，"
             "按作者裁定记\"未记录\"，不补造。"
             % (diff_ok, diff_pos, diff_err))
    if mism:
        L.append("- 可重复性不一致单列：")
        shots = bit_attrib_shots(ddir)
        for m in mism:
            t = m["target"]
            s = shots.get((t["la"], t["wd"], t["bt"]))
            L.append("  - `%s`：首次 %s，复打 %s。逐位归属：%s" % (
                t["lfa_hex"], m.get("first"), m.get("repeat"),
                attrib_lines(s)))
    L.append("")

    # ---- board-order note (WP6-G0 section 5) ------------------------------
    ts = [r.get("ts") or r.get("time") for r in recs if r.get("ts") or r.get("time")]
    if design == "S1":
        L.append("## 板上注入顺序（对 WP6-G0 第 5 节）")
        L.append("")
        L.append("- S0 的板上注入在 S1 开始前**已全部完成**：S0 末条记录 "
                 "2026-10-17T01:34:31Z，S1 首条记录 01:34:56Z。**S0→S1 "
                 "顺序无偏差。**")
        L.append("- 已记录的偏差是 P1 干净重跑穿插在 S0 单发段内（S0 暂停 "
                 "21:32:29–22:14:10 间注入 P1 共 894 条）——见 "
                 "`results/wp6/prereg_addendum_1.md` 第 5 节。")
        L.append("- 单发段内 SEM 忙标志两次卡死（每发等满 60 s 超时），"
                 "看门狗按 SHA 校验后重编程同一份冻结位流恢复"
                 "（约 14:40 与 15:31–15:32 CST），零数据损失。")
        L.append("")
        L.append("## 输出差异摘要覆盖（WP6 第 13 节\"未记录\"口径）")
        L.append("")
        L.append("- 受影响单发 1,877 发：带摘要字段 59 发（其中实际有"
                 "差异 n_diff>0 共 3 发）；修复前 `\"error\"` 摘要的 "
                 "57 发及其余缺字段者记 **\"未记录\"**，不作废、不重跑、"
                 "不补造（与上方完整性核对同一口径）。")
        L.append("- 逐拍差异：`output_diffs.jsonl.gz` 按发序号索引。")
        L.append("")
    elif design in ("S0", "P1"):
        L.append("## 板上注入顺序（对 WP6-G0 第 5 节）")
        L.append("")
        L.append("- 顺序偏差已记录：P1 干净重跑穿插在 S0 单发段内"
                 "（S0 暂停 21:32:29–22:14:10 间注入 P1 共 894 条），"
                 "见 `results/wp6/prereg_addendum_1.md` 第 5 节。")
        L.append("")
    return "\n".join(L)


def main(argv=None):
    global _DS_VER
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("design", choices=list(camp.DESIGNS))
    ap.add_argument("--out", type=Path, default=camp.OUT)
    ap.add_argument("--downstream-ver", choices=["v1", "v2"], default="v1",
                    help="WP6 §16 downstream-口径 version: v1 site-level "
                         "(over-counted) or v2 pin-level routed-direction")
    args = ap.parse_args(argv)
    _DS_VER = args.downstream_ver
    suf = "_v2" if _DS_VER == "v2" else ""
    ddir = Path(args.out) / camp.DESIGN_DIR[args.design]
    recs = read_jsonl(ddir / "campaign.jsonl")
    ident = pair_identity(args.design, args.out)
    text = report(args.design, ident, recs, args.out)
    for sec in (ident["ecc_diff_lane"], ident["ecc_same_lane"],
                ident["tmr_same_stage"], ident["stress_ecc"],
                ident["stress_tmr"], ident["control_ecc"],
                ident["control_tmr"]):
        for r in sec:
            r["decoder_bit"] = r.pop("_dec", None)
            r["class"] = r.pop("_class", None)
            r["channels"] = r.pop("_ch", None)
            r["clean"] = r.pop("_clean", None)
            r.pop("_clean_t1", None)
            r.pop("_clean_t2", None)
            if r.pop("_moved", None):
                r["moved_table"] = True
    (ddir / ("pair_identity%s.json" % suf)).write_text(
        json.dumps(ident, ensure_ascii=False, indent=1), encoding="utf-8")
    (ddir / ("REPORT%s.md" % suf)).write_text(text, encoding="utf-8")
    print("wrote %s/REPORT%s.md + pair_identity%s.json (downstream=%s)"
          % (ddir, suf, suf, _DS_VER))
    # console summary for cross-check with the author's numbers
    ed, tm = ident["ecc_diff_lane"], ident["tmr_same_stage"]
    print("ecc diff-lane %d pairs: M=0 %d, M>0 %d, M<0 %d" % (
        len(ed), sum(1 for r in ed if r["M"] == 0),
        sum(1 for r in ed if (r["M"] or 0) > 0),
        sum(1 for r in ed if (r["M"] or 0) < 0)))
    print("  M>0: %s" % [(r["index"], r["stage1"], r["lane1"], r["lane2"],
                          r["M"]) for r in ed if (r["M"] or 0) > 0])
    print("tmr same-stage %d pairs: O<0 %d, silent O<=0 %d" % (
        len(tm), sum(1 for r in tm if (r["O"] or 0) < 0),
        sum(1 for r in tm if r["category"] == "silent"
            and not (r["O"] or 0) > 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
