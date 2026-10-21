"""WP6 batch-3 analysis: markdown tables per WP6 sections 4, 5, 6.

Reads results/wp6/<design>/campaign.jsonl + L_freeze.json + prereg.json +
tile tables (absence tolerated: position category / replica resolve to
未定 at analysis time).  Emits the section tables to stdout (or --out):

  section 4: singles five-class counts by position category and by stage
             (special positions as separate rows: tile_x49 column,
             X0Y4 tile_x23, P1's INT_R_X51Y156 / INT_R_X53Y152); the first
             affected shot of each category listed; repeatability table;
             L composition + f_bar (from L_freeze.json).
  section 5: natural pairs conditional failure rate F with Wilson 95% CI
             next to f_bar; breakdown by landing (different stage /
             same-stage same-lane / same-stage different-lane /
             same-stage replica-未定) with counts, failures and the 95%
             CI upper bound of the different-stage failure count;
             flagged-vs-silent ratio among failures.
  section 6: stress pairs four outcome classes by stage (with the
             miscorrection sub-row: cor > 0 among silent), control group
             side-by-side.

Usage:
    python scripts/k7_mod/wp6_analyze.py P0 [P1 S0 S1] [--out TABLES.md]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))
import wp6_sem_campaign as camp

OUT = Path(ROOT / "results" / "wp6")

DESIGN_ORDER = ["P0", "P1", "S0", "S1"]
DESIGN_DIR = camp.DESIGN_DIR
STAGE_KIND = {
    "P0": (set(range(1, 8)), {8, 9, 10}),
    "P1": (set(), set(range(1, 11))),
    "S0": (set(range(1, 8)), {8, 9, 10}),
    "S1": (set(), set(range(1, 11))),
}
CATEGORY_ZH = {
    "stuck": "卡住", "noimpact": "无影响", "corrected": "已纠正",
    "flagged": "标出的错", "silent": "静默的错",
    "flagged_but_clean": "标出但输出完好", "invalid": "作废",
}
CATS5 = ["stuck", "noimpact", "corrected", "flagged", "silent",
         "flagged_but_clean"]
AFFECTED = {"stuck", "flagged", "silent", "flagged_but_clean"}
FAIL = {"stuck", "flagged", "silent"}

SPECIAL_TILES = {
    "tile_x49": lambda t: t.get("tile_x") == 49,
    "X0Y4_tile_x23": lambda t: t.get("clock_region") == "X0Y4"
                             and t.get("tile_x") == 23,
    "INT_R_X51Y156": lambda t: t.get("tile") == "INT_R_X51Y156",
    "INT_R_X53Y152": lambda t: t.get("tile") == "INT_R_X53Y152",
}
P1_SHELL_TILES = ("INT_R_X51Y156", "INT_R_X53Y152")


# ------------------------------------------------------------------ loading

def read_jsonl(path):
    if not Path(path).is_file():
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_all(design, out=OUT):
    base = Path(out) / DESIGN_DIR[design]
    prereg = json.loads((Path(out) / "prereg.json").read_text(
        encoding="utf-8"))
    recs = read_jsonl(base / "campaign.jsonl")
    freeze = None
    if (base / "L_freeze.json").is_file():
        freeze = json.loads((base / "L_freeze.json").read_text(
            encoding="utf-8"))
    tile_attr = {}
    tt_path = base / "tile_table.json"
    if tt_path.is_file():
        try:
            tile_attr = json.loads(tt_path.read_text(
                encoding="utf-8")).get("tiles") or {}
        except json.JSONDecodeError:
            tile_attr = {}
    return prereg, recs, freeze, tile_attr


def position_category(design, rec, tile_attr):
    """Position category by tile attribution (WP6 2.4); 未定/混合 fallbacks;
    special positions (3.3) get their own rows."""
    bit = (rec.get("target") or [{}])[0]
    attr = camp.bit_attribution(tile_attr, bit)
    if attr:
        if attr.get("special"):
            return "外壳相关(%s)" % ",".join(attr["special"])
        return attr["category"] or "未定"
    t = bit or {}
    if t.get("tile_x") == 49:
        return "外壳相关(tile_x49)"
    if design == "P1" and t.get("tile") in P1_SHELL_TILES:
        return "外壳相关(%s)" % t["tile"]
    if t.get("clock_region") == "X0Y4" and t.get("tile_x") == 23:
        return "外壳相关(X0Y4_tile_x23)"
    return "未定(无瓦片归属)"


def stage_of(rec, tile_attr=None):
    """Stage attribution per prereg: telemetry last_stage+1 when an event
    was recorded; otherwise the tile-attribution stage of the shot target
    (noimpact shots have no telemetry event).  Mixed tiles resolve to a
    single stage only when every attribution agrees."""
    tel = rec.get("telemetry") or {}
    had_event = (tel.get("det_cnt") or tel.get("cor_cnt")
                 or tel.get("unc_cnt"))
    if had_event and "last_stage" in tel:
        return tel["last_stage"] + 1
    tgt = rec.get("target")
    if isinstance(tgt, dict) and tgt.get("stage"):
        return tgt["stage"]
    if isinstance(tgt, list) and tgt and isinstance(tgt[0], dict) \
            and tgt[0].get("stage"):
        return tgt[0]["stage"]
    if tile_attr:
        bit = (rec.get("target") or [{}])[0]
        t = tile_attr.get(bit.get("tile")) or {}
        stages = {a[1] for a in (t.get("attrib") or []) if a and a[1]}
        if len(stages) == 1:
            return stages.pop()
        if len(stages) > 1:
            return "混合"
        return None
    if "last_stage" in tel:
        return tel["last_stage"] + 1
    return None


def is_tmr(design, stage):
    if stage is None:
        return False
    _ecc, tmr = STAGE_KIND[design]
    return stage in tmr


def kind_recs(recs, kind):
    return [r for r in recs if r.get("kind") == kind and not r.get("void")]


# ------------------------------------------------------------------- Wilson

def wilson(k, n, z=1.959964):
    """Wilson score interval, 95% by default. Returns (lo, hi); (0,0) if
    n == 0."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    half = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, c - half), min(1.0, c + half)


def pct(x):
    return "%.1f%%" % (100 * x)


def fmt_ci(k, n):
    lo, hi = wilson(k, n)
    return "%d/%d = %s [95%% CI %s, %s]" % (k, n, pct(k / n) if n else "-",
                                           pct(lo), pct(hi))


# ------------------------------------------------------------------ section 4

def singles_table(design, recs, tile_attr):
    singles = kind_recs(recs, "single")
    rows_cat, rows_stage = {}, {}
    special_rows = {name: {c: 0 for c in CATS5} for name in SPECIAL_TILES}
    first_affected = []
    seen_cat = set()
    for r in singles:
        cat = r.get("category")
        if cat not in CATS5:
            continue
        bit = (r.get("target") or [{}])[0]
        cat_pos = position_category(design, r, tile_attr)
        stage = stage_of(r, tile_attr)
        special = None
        for name, fn in SPECIAL_TILES.items():
            if name.startswith("INT_R") and design != "P1":
                continue
            if fn(bit):
                special = name
                break
        if special:
            special_rows[special][cat] += 1
        else:
            rows_cat.setdefault(cat_pos, {c: 0 for c in CATS5})
            rows_cat[cat_pos][cat] += 1
        if stage == "混合":
            skey = "级混合"
        else:
            skey = ("级%d%s" % (stage, "(三模)" if is_tmr(design, stage) else "")
                    if stage is not None else "级未知(卡住无遥测)")
        rows_stage.setdefault(skey, {c: 0 for c in CATS5})
        rows_stage[skey][cat] += 1
        if cat in AFFECTED and cat not in seen_cat:
            seen_cat.add(cat)
            first_affected.append((cat, r.get("index"), bit, stage,
                                   r.get("tmr_override")))
    return singles, rows_cat, rows_stage, special_rows, first_affected


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def render_singles(design, recs, tile_attr):
    singles, rows_cat, rows_stage, special_rows, first_affected = \
        singles_table(design, recs, tile_attr)
    lines = ["## 第 4 节 单发预筛 — %s" % design, "",
             "样本: %d 发（不含作废/备用）。" % len(singles), "",
             "### 按位置类别（瓦片归属）", ""]
    rows = []
    for pos in sorted(rows_cat):
        r = rows_cat[pos]
        rows.append([pos] + [r[c] or "-" for c in CATS5]
                    + [sum(r.values())])
    for name in SPECIAL_TILES:
        if name.startswith("INT_R") and design != "P1":
            continue
        r = special_rows[name]
        if sum(r.values()):
            rows.append(["特殊位置 " + name] + [r[c] or "-" for c in CATS5]
                        + [sum(r.values())])
    lines += [md_table(["位置类别"] + [CATEGORY_ZH[c] for c in CATS5] + ["计"],
                       rows), "", "### 按级", ""]
    rows = []
    for s in sorted(rows_stage, key=str):
        r = rows_stage[s]
        rows.append([s] + [r[c] or "-" for c in CATS5] + [sum(r.values())])
    lines += [md_table(["级"] + [CATEGORY_ZH[c] for c in CATS5] + ["计"],
                       rows), "", "### 有影响的第一发（按类别）", ""]
    rows = [[CATEGORY_ZH[c], idx, (bit or {}).get("lfa_hex"),
             "级%s" % stage if stage is not None else "-",
             "是" if ov else "-"]
            for c, idx, bit, stage, ov in first_affected]
    lines += [md_table(["类别", "index", "lfa_hex", "级", "三模改判静默"],
                       rows) if rows else "（无）", ""]
    return lines


def render_repeat(design, out=OUT):
    path = Path(out) / DESIGN_DIR[design] / "repeat_report.json"
    lines = ["### 可重复性抽检（第 4.4 节）", ""]
    if not path.is_file():
        return lines + ["（未运行）", ""]
    rep = json.loads(path.read_text(encoding="utf-8"))
    lines.append("L 重打 %d 位、其余有影响类别重打 %d 位；不一致 %d 发。"
                 % (rep["n_L"], rep["n_affected"], len(rep["mismatches"])))
    lines.append("")
    if rep["mismatches"]:
        lines += [md_table(["lfa_hex", "首次类别", "重打类别"],
                           [[m["target"]["lfa_hex"],
                             CATEGORY_ZH.get(m["first"], m["first"]),
                             CATEGORY_ZH.get(m["repeat"], m["repeat"])]
                            for m in rep["mismatches"]]), ""]
    else:
        lines.append("全部一致。")
        lines.append("")
    return lines


def render_L(freeze):
    lines = ["### 库 L 组成与 f̄（第 2.5 节，冻结值）", ""]
    if not freeze:
        return lines + ["（L_freeze.json 不存在）", ""]
    lines.append("L 共 %d 位；f̄ = %.6f。" % (freeze["n"], freeze["f_bar"]))
    lines.append("")
    rows = [[s, v["n_s"], v["kind"], "%.6f" % v["term"]]
            for s, v in sorted(freeze["by_stage"].items(), key=lambda kv: int(kv[0]))]
    lines += [md_table(["级", "位数 n_s", "类别", "对 f̄ 的贡献"], rows),
              ""]
    if freeze.get("stage_conflict_count"):
        lines.append("级冲突位（遥测级 ≠ 瓦片归属级，保留并标出）: %d 位。"
                     % freeze["stage_conflict_count"])
        lines.append("")
    return lines


# ------------------------------------------------------------------ section 5

def pair_landing(design, ra, rb):
    """Landing class of a natural pair (WP6 5 report)."""
    ta = (ra.get("telemetry") or {})
    tb = (rb.get("telemetry") or {})
    sa = ta.get("last_stage", 0) + 1 if ta else None
    sb = tb.get("last_stage", 0) + 1 if tb else None
    if sa is None or sb is None:
        return "级未知"
    if sa != sb:
        return "不同级"
    if is_tmr(design, sa):
        # replica from target dicts if present (frozen L carries it)
        pa = (ra.get("target") or [{}])[0].get("replica")
        pb = (rb.get("target") or [{}])[0].get("replica")
        if pa in (None, "未定") or pb in (None, "未定"):
            return "同级副本未定"
        return "同级同路" if pa == pb else "同级不同路"
    la, lb = ta.get("last_loc"), tb.get("last_loc")
    if la is None or lb is None:
        return "同级路未知"
    return "同级同路" if la == lb else "同级不同路"


LANDING_ZH = {"不同级": "不同级", "同级同路": "同级同路（同副本）",
              "同级不同路": "同级不同路（不同副本）", "同级同副本": "同级同路（同副本）",
              "同级不同副本": "同级不同路（不同副本）", "同级副本未定": "同级副本未定",
              "级未知": "级未知"}


def render_natural(design, recs, freeze):
    lines = ["## 第 5 节 自然配对 — %s" % design, ""]
    pairs = kind_recs(recs, "pair_natural")
    n = len(pairs)
    fails = [r for r in pairs if r.get("category") in FAIL]
    k = len(fails)
    f_bar = freeze["f_bar"] if freeze else None
    lines += ["条件失效率 F =（标出 + 静默 + 卡住）/ %d:" % n,
              "", "F: " + fmt_ci(k, n),
              ""]
    lines.append("f̄（第 2.5 节上界）: %s" % ("%.6f" % f_bar if f_bar is not None
                                            else "（未冻结）"))
    lines.append("")
    # by landing: match each pair record against its two bits' first singles
    singles = {((r.get("target") or [{}])[0]).get("lfa_hex"): r
               for r in kind_recs(recs, "single")}
    by_land = {}
    for r in pairs:
        bits = r.get("target") or []
        la = pair_landing(
            design,
            {"telemetry": bit_telem(bits[0], singles), "target": [bits[0]]},
            {"telemetry": bit_telem(bits[1], singles), "target": [bits[1]]})
        by_land.setdefault(la, []).append(r)
    rows = []
    for land in ["不同级", "同级同路", "同级不同路", "同级副本未定", "级未知"]:
        rs = by_land.get(land, [])
        fk = sum(1 for r in rs if r.get("category") in FAIL)
        row = [LANDING_ZH.get(land, land), len(rs), fk,
               fmt_ci(fk, len(rs)) if rs else "-"]
        if land == "不同级":
            _lo, hi = wilson(fk, len(rs))
            row.append(pct(hi) if rs else "-")
        else:
            row.append("-")
        rows.append(row)
    rows.append(["合计", n, k, fmt_ci(k, n), "-"])
    lines += [md_table(["落点", "对数", "失效数", "失效率 [95% CI]",
                        "不同级失效数 CI 上限"], rows), ""]
    n_flag = sum(1 for r in fails if r.get("category") == "flagged")
    n_sil = sum(1 for r in fails if r.get("category") == "silent")
    n_stuck = k - n_flag - n_sil
    lines += ["失效中标出/静默比例: 标出 %d、静默 %d、卡住 %d（共 %d）。"
              % (n_flag, n_sil, n_stuck, k), ""]
    rep = sum(1 for r in kind_recs(recs, "pair_natural_repeat"))
    lines.append("失效对复打记录: %d 条（每失效对 1 次复打 + 2 次单独复打）。"
                 % rep)
    lines.append("")
    return lines


def bit_telem(bit, singles):
    """Telemetry of the single-shot record for this bit (for stage/lane)."""
    r = singles.get(bit.get("lfa_hex"))
    return (r or {}).get("telemetry") or {}


# ------------------------------------------------------------------ section 6

def render_stress(design, recs, tile_attr):
    lines = ["## 第 6 节 加压配对与对照 — %s" % design, ""]
    stress = kind_recs(recs, "pair_stress")
    control = kind_recs(recs, "pair_control")

    def stage_key(r):
        st = stage_of(r)
        if st is None:
            return "级未知"
        return "级%d%s" % (st, "(三模)" if is_tmr(design, st) else "")

    def outcome(r):
        cat = r.get("category")
        if cat == "corrected":
            return "仍被纠正或屏蔽"
        if cat == "noimpact":
            return "仍被纠正或屏蔽"          # masked, counted with corrected
        return CATEGORY_ZH.get(cat, cat)

    def rows_for(rs):
        by_stage = {}
        for r in rs:
            by_stage.setdefault(stage_key(r), []).append(r)
        rows = []
        for s in sorted(by_stage, key=str):
            prs = by_stage[s]
            counts = {}
            miscorr = 0
            for r in prs:
                o = outcome(r)
                counts[o] = counts.get(o, 0) + 1
                tel = r.get("telemetry") or {}
                if r.get("category") == "silent" and \
                        (tel.get("cor_cnt", 0) or 0) > 0:
                    miscorr += 1
            rows.append([s, len(prs),
                         counts.get("标出的错", 0) or "-",
                         counts.get("静默的错", 0) or "-",
                         miscorr or "-",
                         counts.get("仍被纠正或屏蔽", 0) or "-",
                         counts.get("卡住", 0) or "-"])
        return rows

    header = ["级", "对数", "标出", "静默", "(其中误纠 cor>0)",
              "仍被纠正/屏蔽", "卡住"]
    lines += ["### 加压配对（同一级不同路 / 不同副本）", "",
              md_table(header, rows_for(stress) or [["（无）"] + ["-"] * 6]),
              "", "### 对照组（同一级同路 / 同副本）", "",
              md_table(header, rows_for(control) or [["（无）"] + ["-"] * 6]),
              ""]
    deficit_path = Path(OUT) / DESIGN_DIR[design] / "pair_deficit.json"
    if deficit_path.is_file():
        deficit = json.loads(deficit_path.read_text(encoding="utf-8"))
        lines.append("缺额/跳过记录: %s" % json.dumps(
            deficit, ensure_ascii=False))
        lines.append("")
    return lines


# ---------------------------------------------------------------------- main

def analyze_design(design, out=OUT):
    prereg, recs, freeze, tile_attr = load_all(design, out)
    lines = ["# WP6 批次 3 分析表 — %s" % design, ""]
    lines += ["位流 SHA-256: `%s`；帧表版本 v2；池版本 v2。"
              % prereg["designs"][design]["bit_sha"], ""]
    lines += render_singles(design, recs, tile_attr)
    lines += render_repeat(design, out)
    lines += render_L(freeze)
    lines += render_natural(design, recs, freeze)
    lines += render_stress(design, recs, tile_attr)
    voids = [r for r in recs if r.get("void")]
    repro = [r for r in recs if r.get("recovery_path") == "reprogram"]
    resets = sum(len(r.get("shell_resets") or []) for r in recs)
    lines += ["## 恢复与作废记录", "",
              "作废 %d 发；重编程 %d 发；外壳复位 %d 次。"
              % (len(voids), len(repro), resets), ""]
    return "\n".join(lines)


def main(argv=None):
    global OUT
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("designs", nargs="+", choices=DESIGN_ORDER)
    ap.add_argument("--out", type=Path, default=None,
                    help="write tables here instead of stdout")
    ap.add_argument("--dir", type=Path, default=OUT,
                    help="wp6 results dir (default results/wp6)")
    args = ap.parse_args(argv)
    OUT = Path(args.dir)
    text = "\n\n".join(analyze_design(d, OUT) for d in args.designs)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print("wrote %s" % args.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
