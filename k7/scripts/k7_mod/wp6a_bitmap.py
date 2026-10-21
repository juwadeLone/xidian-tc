"""WP6-A bit map: configuration bit (la, wd, bit) -> tile and Project X-Ray
feature, for bit-level attribution of SEM injections.

Inputs
  - frame table v2 (results/k7_mod_fft/frame_table_k7_v2.json), with two
    merged columns split here (see split_merged_columns);
  - Project X-Ray kintex7 tile-type bit tables in
    third_party/prjxray-db/kintex7 (commit in third_party/prjxray-db/README.md).

Bit numbering
  prjxray, the .ll file and results/wp1*/ll_check.py number the bits of a
  32-bit frame word from the LSB (bit 0 = value & 1).  The WP6 pool v2
  (wp6_sem_campaign.build_pool) stores bt counted from the MSB
  ((w >> (31 - bt)) & 1), so the essential bit sits at LSB position 31 - bt.
  Which position the SEM flips for "N ... bt" is what gate G0 decides.

Address rules (row_check PASS x4): row = wd//2 (wd<50), (wd-1)//2 (wd>50),
word 50 = frame ECC; tile Y = 50*region_y + row.  A 1-row tile (CLB, INT)
owns words off..off+1 with off = 2*row (+1 if row >= 25); a 5-row tile (DSP,
BRAM) owns 10 words from its base row r0 = row - row % 5.  In a CLB / DSP /
BRAM major column the paired INT tile (same X and Y, side L/R as the column
tile) shares the frames: both tile types are looked up.

    python wp6a_bitmap.py coverage <campaign.jsonl> [--pos lsb|msb]
    python wp6a_bitmap.py g0-select <campaign.jsonl> <out.json>
"""
import argparse
import bisect
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FTAB = ROOT / "results" / "k7_mod_fft" / "frame_table_k7_v2.json"
DB_DIR = ROOT / "third_party" / "prjxray-db" / "kintex7"
TILE_TYPES = ["clbll_l", "clbll_r", "clblm_l", "clblm_r", "int_l", "int_r",
              "dsp_l", "dsp_r", "bram_l", "bram_r"]
ECC_WORD = 50


# ------------------------------------------------------------ frame table

def split_merged_columns(cols):
    """Frame table v2 has two 64-frame columns in the core regions
    (X1Y4 tile_x 64, X1Y5 tile_x 61): a 36-frame CLB column merged with the
    following 28-frame DSP / BRAM column, whose tile_x is missing.  Split at
    frame 36 and take the kind of tile_x+1 from the other regions of the
    same half (columns are uniform vertically)."""
    kind_of = defaultdict(Counter)
    for c in cols:
        if c["n_minors"] in (28, 36):
            half = c["clock_region"][:2]
            kind_of[(half, c["tile_x"])][(c["tile_kind"], c["n_minors"])] += 1
    out = []
    for c in cols:
        if c["n_minors"] == 64:
            nxt = kind_of[(c["clock_region"][:2], c["tile_x"] + 1)]
            if nxt and c["tile_kind"].startswith("CLB"):
                (kind2, n2), _ = nxt.most_common(1)[0]
                if n2 == 28:
                    a = dict(c, n_minors=36, frame_hi=c["frame_lo"] + 35,
                             split_from=64)
                    b = dict(c, tile_x=c["tile_x"] + 1, tile_kind=kind2,
                             n_minors=28, frame_lo=c["frame_lo"] + 36,
                             split_from=64)
                    out += [a, b]
                    continue
        out.append(c)
    return sorted(out, key=lambda x: x["frame_lo"])


def load_columns(path=FTAB):
    cols = json.loads(Path(path).read_text(encoding="utf-8"))["columns"]
    cols = split_merged_columns(cols)
    return cols, [c["frame_lo"] for c in cols]


# ------------------------------------------------------------- bit tables

def load_db(db_dir=DB_DIR):
    """{tile_type: {(frame, bit): [(feature, positive)]}}"""
    db = {}
    for tt in TILE_TYPES:
        idx = defaultdict(list)
        for line in (Path(db_dir) / f"segbits_{tt}.db").open():
            f = line.split()
            for tok in f[1:]:
                a, b = tok.lstrip("!").split("_")
                idx[(int(a), int(b))].append((f[0], not tok.startswith("!")))
        db[tt] = idx
    return db


def feature_class(tile_type, feature):
    if tile_type.startswith("int"):
        return "routing"
    if re.search(r"\.[A-D]LUT\.INIT\[", feature):
        return "lut_init"
    if tile_type.startswith("clb"):
        return "clb_ff_mux"
    return tile_type.split("_")[0] + "_config"


def _word_off(row):
    return 2 * row + (1 if row >= 25 else 0)


def map_bit(cols, lo, db, la, wd, bit_lsb):
    """Return dict(column, minor, row, y, status, hits[(tile, tile_type,
    feature, positive, class)]).  bit_lsb is the LSB-numbered position."""
    c = cols[bisect.bisect_right(lo, la) - 1]
    m = la - c["frame_lo"]
    res = {"tile_kind": c["tile_kind"], "tile_x": c["tile_x"],
           "clock_region": c["clock_region"], "minor": m, "hits": []}
    if wd == ECC_WORD or not (0 <= m < c["n_minors"]):
        res["status"] = "ecc_or_outside"
        return res
    row = wd // 2 if wd < ECC_WORD else (wd - 1) // 2
    y = 50 * int(c["clock_region"][-1]) + row
    res.update(row=row, y=y)
    kind, x = c["tile_kind"], c["tile_x"]
    side = "L" if kind.endswith("_L") else "R"
    looks = []
    if kind.startswith(("CLBLL", "CLBLM")) and c["n_minors"] == 36:
        b1 = (wd - _word_off(row)) * 32 + bit_lsb
        looks = [(f"{kind}_X{x}Y{y}", kind.lower(), b1),
                 (f"INT_{side}_X{x}Y{y}", f"int_{side.lower()}", b1)]
    elif kind.startswith(("DSP", "BRAM")) and c["n_minors"] == 28:
        r0 = row - row % 5
        b5 = (wd - _word_off(r0)) * 32 + bit_lsb
        b1 = (wd - _word_off(row)) * 32 + bit_lsb
        looks = [(f"{kind}_X{x}Y{50 * int(c['clock_region'][-1]) + r0}",
                  kind.lower(), b5),
                 (f"INT_{side}_X{x}Y{y}", f"int_{side.lower()}", b1)]
    else:
        res["status"] = "other_column"
        return res
    for tile, tt, b in looks:
        for feat, pos in db[tt].get((m, b), []):
            res["hits"].append((tile, tt, feat, pos, feature_class(tt, feat)))
    res["status"] = "hit" if res["hits"] else "unknown"
    return res


def pool_bt_to_lsb(bt):
    """WP6 pool v2 bt (MSB-numbered) -> LSB-numbered position."""
    return 31 - int(bt)


# ------------------------------------------------------------------- CLI

def _singles(path):
    recs = [json.loads(l) for l in open(path, encoding="utf-8")]
    return [r for r in recs if r["kind"] == "single" and not r["void"]]


def cmd_coverage(args):
    cols, lo = load_columns()
    db = load_db()
    st, cl = Counter(), Counter()
    for r in _singles(args.campaign):
        t = r["target"][0]
        pos = pool_bt_to_lsb(t["bt"]) if args.pos == "msb" else int(t["bt"])
        m = map_bit(cols, lo, db, t["la"], t["wd"], pos)
        st[m["status"]] += 1
        if m["hits"]:
            cl["+".join(sorted({h[4] for h in m["hits"]}))] += 1
    print(json.dumps({"position": args.pos, "status": st,
                      "classes": cl.most_common()}, indent=1))


def cmd_g0_select(args):
    """Bits of a finished single-shot campaign for which exactly one of
    the two candidate positions (31-bt, bt) has a known feature."""
    cols, lo = load_columns()
    db = load_db()
    out = []
    for r in _singles(args.campaign):
        t = r["target"][0]
        ess = map_bit(cols, lo, db, t["la"], t["wd"], pool_bt_to_lsb(t["bt"]))
        mir = map_bit(cols, lo, db, t["la"], t["wd"], int(t["bt"]))
        if ess["status"] not in ("hit", "unknown"):
            continue
        ek, mk = ess["status"] == "hit", mir["status"] == "hit"
        if ek == mk:
            continue
        bt_inj = 31 - int(t["bt"])
        lfa_hex = "%010X" % ((0xC0 << 32) | ((int(t["la"]) & 0xFFFFF) << 12)
                             | ((int(t["wd"]) & 0x7F) << 5) | (bt_inj & 0x1F))
        out.append({
            "group": "A" if ek else "B",
            "la": t["la"], "wd": t["wd"], "bt_pool": t["bt"],
            "bt_inject": bt_inj, "lfa_hex_inject": lfa_hex,
            "tile": t.get("tile"), "p0_index": r["index"],
            "p0_category": r["category"],
            "features_at_31_minus_bt": sorted({h[2] for h in ess["hits"]}),
            "features_at_bt": sorted({h[2] for h in mir["hits"]}),
        })
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("A", sum(o["group"] == "A" for o in out),
          "B", sum(o["group"] == "B" for o in out),
          "categories", Counter(o["p0_category"] for o in out))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("coverage")
    p.add_argument("campaign")
    p.add_argument("--pos", choices=["lsb", "msb"], default="msb",
                   help="msb: test LSB position 31-bt (the .ebd essential "
                        "bit); lsb: test LSB position bt")
    p.set_defaults(fn=cmd_coverage)
    p = sub.add_parser("g0-select")
    p.add_argument("campaign")
    p.add_argument("out")
    p.set_defaults(fn=cmd_g0_select)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
