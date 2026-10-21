"""WP6-A steps 3-4: bit-level self-checks (G1/G2/G3) and attribution (G4).

selfcheck (per design, offline) writes results/wp6/<design>/selfcheck.json:
  G1  pips      every exported pip INT_x_X..Y../INT_x.SRC->>DST must match
                bitmap feature INT_x.DST.SRC; ppips always/default/hint are
                skipped (counted); the rest are checked against the frozen
                .ebc: positive segbits = 1, inverted (!) segbits = 0.
                Gate: >= 99.5% of pips with a bitmap feature agree.
  G2  LUT INIT  LUTs on a 6LUT whose co-located 5LUT is empty (author ruling
                2026-10-16, recorded in tasks/WP6A_bit_attribution.md): with
                an A6 pin compare all 64 INIT bits; without an A6 pin compare
                ONLY INIT[32..63] (INIT[0..31] reported, not judged - O5
                programming stores the <=5-input function in the upper half
                and a minimized don't-care fill below).  Gate: 100% of
                comparable agree on the judged bits.  Part of G2: every
                comparable no-A6 LUT must have A6 tied to VCC (<const1>) or
                no driver at all (wp6a_dump.lutpins.tsv); any other case is
                an exception -> fail and stop (no G3/G4).
  G3  coverage  every pool bit (regenerated via wp6_sem_campaign.build_pool)
                mapped with wp6a_bitmap.map_bit; tabulated by column type.
                No gate (offline P0 reference: 99.8% known function).

attrib (step 4, per WP6A section 4) writes results/wp6/<design>/bit_attrib.json:
  per-bit attribution of every single shot of the design's campaign.jsonl
  (pool_version v2.1 -> physical LSB = bt; v2 -> 31 - bt) and G4 telemetry
  consistency for "corrected" singles (gate >= 95% on unique-stage shots).

Bit numbering: G0 verdict M (results/wp6/g0/REPORT.md, commit 8d1f8b0): SEM
addresses bits LSB-first.  Pool v2 bt is MSB-based, pool v2.1 bt is LSB-based.

Frame/word conventions follow scripts/k7_mod/wp6a_bitmap.py (map_bit):
row = wd//2 (wd<50), (wd-1)//2 (wd>50), word 50 = frame ECC, tile Y =
50*region_y + row; a pip/INIT segbits token <frame>_<bit> maps to
la = column.frame_lo + frame, wd = word_off(row) + bit//32 (word_off =
2*row + (row>=25)), lsb = bit%32; the paired INT tile of a CLB / DSP / BRAM
column shares the frames (same X and Y, side per the column kind).

    python wp6a_attrib.py selfcheck P0_p1 [--bit-mode auto|m|M|N] [--input-dir DIR]
    python wp6a_attrib.py attrib P0_p1 [--input-dir DIR]
"""
import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))

import wp6a_bitmap as bm
import wp6_sem_campaign as sc
from wp6_tile_table import classify as tile_classify

OUT = ROOT / "results" / "wp6"
DESIGNS = {"P0_p1": "P0", "P1_p2": "P1", "S0_s3": "S0", "S1_s2": "S1"}
G1_GATE = 0.995
G4_GATE = 0.95
PIP_TILE_RE = re.compile(r"^(CLBLL|CLBLM|INT|DSP|BRAM)_([LR])_X(\d+)Y(\d+)$")
INIT_RE = re.compile(r"^(?P<tk>[A-Z0-9_]+)\.(?P<st>SLICEL|SLICEM)_X(?P<ix>[01])\."
                     r"(?P<let>[A-D])LUT\.INIT\[(?P<k>\d+)\]$")


# ------------------------------------------------------------ shared loads

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def retry_read(path, mode="r", tries=8, pause=2.0):
    """The results tree occasionally flickers out; retry briefly."""
    last = None
    for _ in range(tries):
        try:
            if "b" in mode:
                return Path(path).read_bytes()
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            last = e
            time.sleep(pause)
    raise last


def load_json(path):
    """Prefer <path>.gz (section-14.3 layout); fall back to plain JSON."""
    import gzip
    gz = Path(str(path) + ".gz")
    if gz.is_file():
        with gzip.open(gz, "rt", encoding="utf-8") as f:
            return json.loads(f.read())
    return json.loads(retry_read(path))


class Ebc:
    """Frozen .ebc (same format as .ebd): one pad frame first, WPW words per
    frame, 32-char binary lines after a small text header."""

    def __init__(self, path):
        self.path = Path(path)
        words = []
        with open(self.path, "r", encoding="ascii", errors="replace") as f:
            for line in f:
                s = line.strip()
                if len(s) == 32 and s.isdigit() and set(s) <= {"0", "1"}:
                    words.append(int(s, 2))
        if len(words) % sc.WPW:
            raise RuntimeError("%s: %d words not a multiple of %d"
                               % (self.path, len(words), sc.WPW))
        self.words = words
        self.n_frames = len(words) // sc.WPW

    def bit(self, la, wd, lsb):
        fr = la + 1                              # .ebc leading pad frame
        if not (0 <= fr < self.n_frames and 0 <= wd < sc.WPW and 0 <= lsb < 32):
            raise IndexError("ebc bit out of range la=%s wd=%s lsb=%s"
                             % (la, wd, lsb))
        return (self.words[fr * sc.WPW + wd] >> lsb) & 1


def load_feature_db():
    """{tile_type: {feature: [(frame, bit, positive), ...]}} from segbits_*.db
    and {tile_type: set((frame, bit))} from mask_*.db."""
    feat, mask = {}, {}
    for tt in bm.TILE_TYPES:
        idx = defaultdict(list)
        for line in (bm.DB_DIR / f"segbits_{tt}.db").open():
            f = line.split()
            for tok in f[1:]:
                a, b = tok.lstrip("!").split("_")
                idx[f[0]].append((int(a), int(b), not tok.startswith("!")))
        feat[tt] = dict(idx)
        mask[tt] = set()
        mp = bm.DB_DIR / f"mask_{tt}.db"
        if mp.is_file():
            for line in mp.open():
                f = line.split()
                if len(f) >= 2 and f[0] == "bit":
                    a, b = f[1].split("_")
                    mask[tt].add((int(a), int(b)))
    return feat, mask


def load_ppips():
    """{tile_type: {pip feature TYPE.DST.SRC: class}} (prjxray ppips use the
    same TYPE.DST.SRC naming as segbits)."""
    out = {}
    for tt in bm.TILE_TYPES:
        idx = {}
        pp = bm.DB_DIR / f"ppips_{tt}.db"
        if pp.is_file():
            for line in pp.open():
                f = line.split()
                if len(f) >= 2:
                    idx[f[0]] = f[1]
        out[tt] = idx
    return out


def word_off(row):
    return 2 * row + (1 if row >= 25 else 0)


class ColumnIndex:
    """tile -> (la, wd, lsb) resolver following wp6a_bitmap.map_bit."""

    def __init__(self, cols):
        self.by_key = defaultdict(list)
        for c in cols:
            kind = c["tile_kind"]
            if not kind.startswith(("CLBLL", "CLBLM", "DSP", "BRAM")):
                continue
            if c["n_minors"] not in (28, 36) or c.get("frame_lo") is None:
                continue
            side = "L" if kind.endswith("_L") else "R"
            ry = int(c["clock_region"][-1])
            self.by_key[(ry, int(c["tile_x"]), side)].append(c)

    def resolve(self, tile, frame, bit):
        """-> (la, wd, lsb) or None when no column owns this tile."""
        m = PIP_TILE_RE.match(tile)
        if not m:
            return None
        kind, side, x, y = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
        ry, row = y // 50, y % 50
        cands = self.by_key.get((ry, x, side)) or []
        for c in cands:
            if frame >= c["n_minors"]:
                continue
            if c["tile_kind"].startswith(("DSP", "BRAM")) and \
                    kind.startswith(("DSP", "BRAM")):
                r0 = row - row % 5
                return (c["frame_lo"] + frame, word_off(r0) + bit // 32, bit % 32)
            if c["tile_kind"].startswith(("CLBLL", "CLBLM")) and \
                    kind.startswith(("INT", "CLBLL", "CLBLM")):
                return (c["frame_lo"] + frame, word_off(row) + bit // 32, bit % 32)
            if c["tile_kind"].startswith(("DSP", "BRAM")) and \
                    kind.startswith("INT"):
                return (c["frame_lo"] + frame, word_off(row) + bit // 32, bit % 32)
        return None


def lsb_of(bt, pool_version):
    """G0=M: physical LSB position of a pool/campaign record's bit."""
    return int(bt) if str(pool_version) == "v2.1" else 31 - int(bt)


# --------------------------------------------------------------------- G1

def check_g1(design_dir, ebc, feat_db, mask_db, ppips, cidx):
    t0 = time.time()
    skipped, no_feature, unmapped = Counter(), 0, 0
    with_feature = agree = disagree = 0
    outside_mask = 0
    by_type = Counter()
    disag_by_switch = Counter()
    agree_by_switch = Counter()
    examples = []
    pip_re = re.compile(r"^PIP\t([^\t]+)\t([^/]+)/([A-Z0-9_]+)\.(.+?)(->>|->)(.+)$")
    with open(design_dir / "wp6a_dump.pips.tsv", encoding="utf-8") as f:
        for line in f:
            m = pip_re.match(line.rstrip("\n"))
            if not m:
                continue
            net, tile, tt_tok, src, _arrow, dst = m.groups()
            tt = tt_tok.lower()
            by_type[tt_tok] += 1
            pp = ppips.get(tt, {})
            fname = f"{tt_tok}.{dst}.{src}"
            if fname in pp:
                skipped[pp[fname]] += 1
                continue
            bits = feat_db.get(tt, {}).get(fname)
            if not bits:
                no_feature += 1
                continue
            if not all((fr, b) in mask_db.get(tt, set()) for fr, b, _ in bits):
                outside_mask += 1
            locs = []
            loc = None
            for fr, b, pos in bits:
                loc = cidx.resolve(tile, fr, b)
                if loc is None:
                    break
                locs.append((loc, pos))
            if loc is None:
                unmapped += 1
                continue
            with_feature += 1
            bad = [(la, wd, lsb, pos, ebc.bit(la, wd, lsb))
                   for (la, wd, lsb), pos in locs
                   if ebc.bit(la, wd, lsb) != (1 if pos else 0)]
            switch = re.sub(r"\d+$", "", dst)
            if bad:
                disagree += 1
                disag_by_switch[f"{tt_tok}.{switch}"] += 1
                if len(examples) < 50:
                    examples.append({"net": net, "pip": f"{tile}/{tt_tok}.{src}->{dst}",
                                     "feature": fname,
                                     "mismatch_bits": bad[:8]})
            else:
                agree += 1
                agree_by_switch[f"{tt_tok}.{switch}"] += 1
    tot = agree + disagree
    frac = agree / tot if tot else 0.0
    return {
        "pips_total": sum(by_type.values()),
        "pips_by_tile_type": dict(by_type),
        "skipped_ppips": dict(skipped),
        "no_feature": no_feature,
        "feature_bits_outside_mask": outside_mask,
        "unmapped_tile": unmapped,
        "with_feature": with_feature,
        "agree": agree,
        "disagree": disagree,
        "agree_frac": round(frac, 6),
        "gate": G1_GATE,
        "verdict": "pass" if frac >= G1_GATE else "fail",
        "disagree_by_switch": disag_by_switch.most_common(40),
        "agree_by_switch_top": agree_by_switch.most_common(10),
        "disagree_examples": examples,
        "seconds": round(time.time() - t0, 1),
    }


# --------------------------------------------------------------------- G2

def parse_lutpins(path):
    """{cell: {bel_pin_suffix: net_or_None}} e.g. {'A6': 'netname'}; a pin
    with no net -> ''.  Only entries with a non-empty bel pin are keyed."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 3 or p[0] != "LUTPIN":
                continue
            pins = {}
            for pair in p[2].split(";"):
                if "=" not in pair:
                    continue
                _lg, rest = pair.split("=", 1)
                if "@" in rest:
                    bp, net = rest.rsplit("@", 1)
                else:
                    bp, net = rest, ""
                bp = bp.split(",")[0]
                m = re.search(r"/A(\d)$", bp)
                if m:
                    pins[int(m.group(1))] = net
            out[p[1]] = pins
    return out


def check_g2(design_dir, ebc, feat_db, cidx):
    """Author ruling 2026-10-16: with-A6 LUTs judge 64 bits; no-A6 LUTs judge
    INIT[32..63] only; every comparable no-A6 LUT must have A6 VCC-tied or
    undriven (lutpins.tsv)."""
    t0 = time.time()
    site_tile = {}
    occ = set()             # (site, bel_position) e.g. (SLICE_X65Y229, "B6LUT")
    lut_cells = {}          # cell -> (site, bel_position, site_type, tile)
    with open(design_dir / "wp6a_dump.cells.tsv", encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 6 or p[0] != "CELL":
                continue
            _tag, cell, ref, site, bel, tile = p[:6]
            b = bel.split(".")
            if len(b) != 2:
                continue
            site_tile.setdefault(site, (tile, b[0]))
            occ.add((site, b[1]))
            if ref.startswith("LUT"):
                lut_cells[cell] = (site, b[1], b[0], tile)
    n_luts_total = len(lut_cells)

    site_idx = {}           # (tile, site) -> X0/X1 index, ascending site X
    with open(design_dir / "wp6a_dump.sites.tsv", encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 3 or p[0] != "SITES":
                continue
            sites = sorted(p[2:], key=lambda s: int(re.search(r"X(\d+)Y", s).group(1)))
            for i, s in enumerate(sites):
                site_idx[(p[1], s)] = i

    lutpins = parse_lutpins(design_dir / "wp6a_dump.lutpins.tsv")

    comparable = agree = disagree = 0
    agree_with_a6 = agree_no_a6 = 0
    low_half_divergent_unjudged = 0
    excluded_not6lut = excluded_5lut_busy = feature_missing = loc_missing = 0
    mismatch_bits_total = 0
    disagree_cells = []
    a6_audit = {"vcc_tied": 0, "no_driver": 0, "other": 0, "other_cells": []}
    n_a6_judged_bits_bad = 0
    for line in open(design_dir / "wp6a_dump.luts.tsv", encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) < 4 or p[0] != "LUT":
            continue
        _tag, cell, init_s, pinmap_s = p[:4]
        info = lut_cells.get(cell)
        if info is None:
            continue
        site, pos, stype, tile = info
        if not pos.endswith("6LUT"):
            excluded_not6lut += 1
            continue
        letter = pos[0]
        if (site, letter + "5LUT") in occ:
            excluded_5lut_busy += 1
            continue
        idx = site_idx.get((tile, site))
        if idx is None:
            loc_missing += 1
            continue
        tile_kind = tile.rsplit("_X", 1)[0]    # e.g. CLBLL_R
        tt = tile_kind.lower()                 # e.g. clbll_r
        init_m = re.match(r"(\d+)'h([0-9a-fA-F]+)$", init_s)
        if not init_m:
            feature_missing += 1
            continue
        init = int(init_m.group(2), 16)
        p_of = {}
        for pair in pinmap_s.split(";"):
            if "=" not in pair:
                continue
            lg, bp = pair.split("=", 1)
            pins = bp.split(",")
            am = re.search(r"/[A-D][56]LUT/A(\d)$", pins[0])
            if not am:
                continue
            p_of[int(lg.lstrip("I"))] = int(am.group(1)) - 1
        js = sorted(p_of)
        has_a6 = any(p_of[j] == 5 for j in js)
        li = [0] * 64
        for k in range(64):
            v = 0
            for j in js:
                v |= ((k >> p_of[j]) & 1) << j
            li[k] = v
        phys = [(init >> li[k]) & 1 for k in range(64)]
        dbits = []
        ok_lut = True
        for k in range(64):
            feat = f"{tile_kind}.{stype}_X{idx}.{letter}LUT.INIT[{k:02d}]"
            toks = feat_db.get(tt, {}).get(feat)
            if not toks:
                ok_lut = False
                break
            fr, b, pos_bit = toks[0]
            if not pos_bit:
                ok_lut = False        # INIT bits are never inverted
                break
            loc = cidx.resolve(tile, fr, b)
            if loc is None:
                ok_lut = False
                break
            dbits.append(loc)
        if not ok_lut:
            feature_missing += 1
            continue
        comparable += 1
        judged = range(64) if has_a6 else range(32, 64)
        bad = [(k, phys[k], ebc.bit(*dbits[k])) for k in judged
               if ebc.bit(*dbits[k]) != phys[k]]
        low_bad = sum(1 for k in range(32)
                      if ebc.bit(*dbits[k]) != phys[k])
        if has_a6 and bad:
            n_a6_judged_bits_bad += len(bad)
        if not has_a6:
            # A6 tie audit (ruling): VCC-tied or undriven only
            net6 = lutpins.get(cell, {}).get(6)
            if net6 == "<const1>":
                a6_audit["vcc_tied"] += 1
            elif net6 in ("", None):
                a6_audit["no_driver"] += 1
            else:
                a6_audit["other"] += 1
                if len(a6_audit["other_cells"]) < 100:
                    a6_audit["other_cells"].append(
                        {"cell": cell, "site": site, "bel": pos,
                         "a6_net": net6})
            if low_bad:
                low_half_divergent_unjudged += 1
        if bad:
            disagree += 1
            mismatch_bits_total += len(bad)
            if len(disagree_cells) < 50:
                disagree_cells.append({
                    "cell": cell, "site": site, "bel": pos, "tile": tile,
                    "init": init_s, "has_a6_pin": has_a6,
                    "n_bad_judged_bits": len(bad),
                    "bad_bits": [[k, exp, got] for k, exp, got in bad[:16]],
                    "pinmap": pinmap_s})
        else:
            agree += 1
            if has_a6:
                agree_with_a6 += 1
            else:
                agree_no_a6 += 1
    frac = agree / comparable if comparable else 0.0
    exceptions = a6_audit["other"]
    verdict = ("pass" if comparable and frac == 1.0 and exceptions == 0
               else "fail")
    return {
        "criterion": "author ruling 2026-10-16: A6-pinned LUTs judge INIT[0..63]"
                     "; no-A6 LUTs judge INIT[32..63] only; every no-A6 LUT"
                     " must have A6 VCC-tied or undriven",
        "luts_total": n_luts_total,
        "comparable": comparable,
        "comparable_frac_of_total": round(comparable / n_luts_total, 6)
        if n_luts_total else None,
        "excluded_not_on_6lut": excluded_not6lut,
        "excluded_5lut_occupied": excluded_5lut_busy,
        "excluded_no_site_index": loc_missing,
        "excluded_feature_missing": feature_missing,
        "agree": agree,
        "disagree": disagree,
        "agree_frac": round(frac, 6),
        "agree_with_a6_pin": agree_with_a6,
        "agree_without_a6_pin": agree_no_a6,
        "no_a6_low_half_divergent_unjudged": low_half_divergent_unjudged,
        "a6_pin_audit": a6_audit,
        "mismatch_bits_total": mismatch_bits_total,
        "gate": "100% of comparable agree on judged bits; A6 audit clean",
        "verdict": verdict,
        "disagree_cells": disagree_cells,
        "seconds": round(time.time() - t0, 1),
    }


# --------------------------------------------------------------------- G3

def g0_verdict_from_report(path):
    """Parse results/wp6/g0/REPORT.md verdict line -> 'M' / 'N' / None."""
    p = Path(path)
    if not p.is_file():
        return None
    for line in retry_read(p).splitlines():
        if re.search(r"(结论|verdict)", line, re.I):
            mm = re.search(r"\b([MN])\b", line)
            if mm:
                return mm.group(1)
    return None


def check_g3(design, sem_design, bit_modes):
    t0 = time.time()
    prereg = sc.load_prereg(OUT)
    bits = sc.build_pool(sem_design, prereg, out=OUT)
    pool_version = getattr(sc, "POOL_VERSION", "v2.1")
    cols, lo = bm.load_columns()
    db = bm.load_db()
    modes = {}
    for mode in bit_modes:
        status_by_col = defaultdict(Counter)
        cls_by_col = defaultdict(Counter)
        n_hit = n_tot = 0
        for b in bits:
            lsb = lsb_of(b["bt"], pool_version) if mode == "auto" else \
                (31 - int(b["bt"]) if mode == "M" else int(b["bt"]))
            r = bm.map_bit(cols, lo, db, b["la"], b["wd"], lsb)
            col = r.get("tile_kind", "?")
            status_by_col[col][r["status"]] += 1
            n_tot += 1
            if r["hits"]:
                n_hit += 1
                cls_by_col[col]["+".join(sorted({h[4] for h in r["hits"]}))] += 1
        modes[mode] = {
            "pool_version": pool_version,
            "pool_bits": n_tot,
            "hit": n_hit,
            "hit_frac": round(n_hit / n_tot, 6) if n_tot else None,
            "status_by_column": {c: dict(s) for c, s in sorted(status_by_col.items())},
            "classes_by_column": {c: dict(s) for c, s in sorted(cls_by_col.items())},
        }
    return {
        "bit_modes": modes,
        "seconds": round(time.time() - t0, 1),
    }


# ----------------------------------------------------------------- exports
# per-design data needed for attribution (WP6A section 4)

class ExportData:
    def __init__(self, design_dir):
        t0 = time.time()
        self.dir = Path(design_dir)
        self.site_cells = defaultdict(list)   # site -> [(cell, ref, pos)]
        self.tile_of_site = {}
        self.cells_row = {}
        with open(self.dir / "wp6a_dump.cells.tsv", encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) < 6 or p[0] != "CELL":
                    continue
                _t, cell, ref, site, bel, tile = p[:6]
                b = bel.split(".")
                pos = b[1] if len(b) == 2 else bel
                self.site_cells[site].append((cell, ref, pos))
                self.tile_of_site[site] = tile
                self.cells_row[cell] = (site, ref, pos, tile)
        self.sites_of_tile = defaultdict(list)
        self.site_idx = {}
        with open(self.dir / "wp6a_dump.sites.tsv", encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) < 3 or p[0] != "SITES":
                    continue
                ss = sorted(p[2:], key=lambda s: int(re.search(r"X(\d+)Y", s).group(1)))
                self.sites_of_tile[p[1]] = ss
                for i, s in enumerate(ss):
                    self.site_idx[(p[1], s)] = i
        self.lut_has_a6 = {}
        self.lut_pmap = {}
        with open(self.dir / "wp6a_dump.luts.tsv", encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) < 4 or p[0] != "LUT":
                    continue
                p_of = set()
                for pair in p[3].split(";"):
                    if "=" not in pair:
                        continue
                    m = re.search(r"/[A-D][56]LUT/A(\d)$", pair.split("=", 1)[1].split(",")[0])
                    if m:
                        p_of.add(int(m.group(1)))
                self.lut_has_a6[p[1]] = 6 in p_of
                self.lut_pmap[p[1]] = p[3]
        # routing: (tile, dst) -> net; net -> (type, drivers, loads)
        self.dst_net = {}
        self.net_info = {}
        pip_re = re.compile(r"^PIP\t([^\t]+)\t([^/]+)/([A-Z0-9_]+)\.(.+?)(->>|->)(.+)$")
        with open(self.dir / "wp6a_dump.pips.tsv", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("PIP\t"):
                    m = pip_re.match(line)
                    if m:
                        net, tile, _tt, _src, _a, dst = m.groups()
                        self.dst_net.setdefault((tile, dst), net)
                elif line.startswith("NET\t"):
                    p = line.split("\t")
                    if len(p) >= 5:
                        self.net_info[p[1]] = (
                            p[2], _braces(p[3]), _braces(p[4]))
        self.tile_table = load_json(
            OUT / self.dir.name / "tile_table.json").get("tiles", {})
        self.seconds = round(time.time() - t0, 1)


def _braces(s):
    """Tcl-brace field: '{a} {b}' or 'a b' -> [a, b]; '{}' -> []."""
    s = s.strip()
    if s.startswith("{"):
        found = re.findall(r"\{([^{}]*)\}", s)
        if found:
            return [x for x in found if x]
        s = s.strip("{}")
    return [x for x in s.split() if x]


def rel_of(cell):
    return cell.split("u_kernel/", 1)[1] if "u_kernel/" in cell else None


def attrib_of_cell(cell):
    rel = rel_of(cell)
    if rel is None:
        return ("unmapped", None, None)
    return tile_classify(rel)


# ------------------------------------------------------- per-bit attribution

FFSET_RE = re.compile(r"^([A-D])(5?FF)\.(ZINI|ZRST)$")
MUX_RE = re.compile(r"^([A-D])(5?FFMUX|OUTMUX)\.")
CARRY_RE = re.compile(r"^CARRY4\.")
SHARED_SLICE = {"CLKINV", "NOCLKINV", "FFSYNC", "LATCH", "CEUSEDMUX",
                "SRUSEDMUX"}
SHARED_RE = re.compile(r"^PRECYINIT\.")


def attribute_hit(hit, xd):
    """One map_bit hit (tile, tile_type, feature, positive, class) ->
    dict(resource category, feature, objects, attrib set, tags, primary)."""
    tile, tt, feature, pos, cls = hit
    if cls == "lut_init":
        m = INIT_RE.match(feature)
        if not m:
            return None
        let, k = m.group("let"), int(m.group("k"))
        sites = xd.sites_of_tile.get(tile, [])
        ix = int(m.group("ix"))
        if ix >= len(sites):
            return None
        site = sites[ix]
        objs = []
        for cell, ref, posn in xd.site_cells.get(site, []):
            if posn == let + "6LUT":
                objs.append(cell)
        if not objs:
            return {"resource": "查找表内容", "feature": feature, "objects": [],
                    "attrib": [], "tags": ["未用资源"], "primary": []}
        tags = []
        has_sib5 = False
        if k < 32:
            for cell, ref, posn in xd.site_cells.get(site, []):
                if posn == let + "5LUT" and cell not in objs:
                    objs.append(cell)
                    has_sib5 = True
        if k < 32 and not xd.lut_has_a6.get(objs[0], True):
            if has_sib5:
                tags.append("下半为同列 5LUT 单元的 O5 表")
            else:
                # §22.1: O5 can still be consumed via AOUTMUX/AFFMUX/
                # A5FFMUX(IN_A)/CARRY4.XCY0 even with no 5LUT sibling --
                # "no reader" was wrong (nosib_nodest imp = 0 for all
                # four designs; every residual sits in the has-dest group)
                tags.append("下半为 O5 表（本列无 5LUT；读出与否取决于 "
                            "O5 去向多选器）")
        prim = [(o, attrib_of_cell(o)) for o in objs]
        return {"resource": "查找表内容", "feature": feature, "objects": objs,
                "attrib": [a for _o, a in prim], "tags": tags,
                "primary": prim}

    if cls == "routing":
        parts = feature.split(".")
        if len(parts) != 3:
            return None
        dst, src = parts[1], parts[2]
        net = xd.dst_net.get((tile, dst))
        out = {"resource": "开关", "feature": feature, "pip": f"{tile} {src}->{dst}"}
        if net is None:
            out.update({"objects": [], "attrib": [], "primary": [],
                        "tags": ["未用开关"], "net": None})
            return out
        ty, drv, lds = xd.net_info.get(net, ("?", [], []))
        attribs = [attrib_of_cell(c) for c in lds]
        kern = [a for a in attribs if a[0] != "unmapped"]
        if net in ("<const0>", "<const1>") or ty in ("GROUND", "POWER"):
            netcat = "常量网"
        elif len({a[0] for a in kern}) > 1:
            netcat = "混合（网）"
        elif kern:
            netcat = kern[0][0]
        else:
            netcat = "unmapped"
        src_net = xd.dst_net.get((tile, src))
        tags = []
        if src_net is not None and src_net != net:
            tags.append("可能牵连:" + src_net)
        out.update({"net": net, "net_type": ty, "net_category": netcat,
                    "drivers": drv, "loads": lds,
                    "objects": lds,
                    "attrib": kern,
                    "primary": [(c, attrib_of_cell(c)) for c in lds
                                if attrib_of_cell(c)[0] != "unmapped"],
                    "tags": tags})
        return out

    if cls == "clb_ff_mux":
        m2 = re.match(r"^[A-Z0-9_]+\.(?:SLICEL|SLICEM)_X(\d)\.", feature)
        ix = int(m2.group(1)) if m2 else 0
        sites = xd.sites_of_tile.get(tile, [])
        site = sites[ix] if ix < len(sites) else None
        rest = feature.split(".", 2)[2] if len(feature.split(".")) >= 3 else feature
        m = FFSET_RE.match(rest)
        if m:
            let, ff5, _attr = m.groups()
            objs = [c for c, _r, p in xd.site_cells.get(site, [])
                    if p == let + ff5] if site else []
            prim = [(o, attrib_of_cell(o)) for o in objs]
            return {"resource": "触发器与slice设置", "feature": feature,
                    "objects": objs, "attrib": [a for _o, a in prim],
                    "primary": prim, "tags": []}
        m = MUX_RE.match(rest)
        if m:
            let = m.group(1)
            objs = [c for c, _r, p in xd.site_cells.get(site, [])
                    if p.startswith(let)] if site else []
            prim = [(o, attrib_of_cell(o)) for o in objs]
            return {"resource": "触发器与slice设置", "feature": feature,
                    "objects": objs, "attrib": [a for _o, a in prim],
                    "primary": prim, "tags": ["MUX/CARRY 相关BEL"]}
        # SLICEM distributed-memory features: LUT mode / data-in mux /
        # write-address / write-enable -> the memory cells of that position
        mm = re.match(r"^([A-D])(?:LUT\.(SRL|RAM|SMALL|DI1MUX\..*))", rest)
        if mm:
            let = mm.group(1)
            objs = [c for c, _r, p in xd.site_cells.get(site, [])
                    if p.startswith(let)] if site else []
            prim = [(o, attrib_of_cell(o)) for o in objs]
            return {"resource": "触发器与slice设置", "feature": feature,
                    "objects": objs, "attrib": [a for _o, a in prim],
                    "primary": prim, "tags": ["分布式存储设置"]}
        if re.match(r"^(WA[0-9]+USED|WEMUX\.)", rest):
            objs = [c for c, _r, _p in xd.site_cells.get(site, [])] \
                if site else []
            prim = [(o, attrib_of_cell(o)) for o in objs]
            return {"resource": "触发器与slice设置", "feature": feature,
                    "objects": objs, "attrib": [a for _o, a in prim],
                    "primary": prim, "tags": ["分布式存储设置(slice级)"]}
        if CARRY_RE.match(rest):
            objs = [c for c, _r, p in xd.site_cells.get(site, [])
                    if p == "CARRY4"] if site else []
            prim = [(o, attrib_of_cell(o)) for o in objs]
            return {"resource": "触发器与slice设置", "feature": feature,
                    "objects": objs, "attrib": [a for _o, a in prim],
                    "primary": prim, "tags": ["MUX/CARRY 相关BEL"]}
        # shared slice setting -> all FFs of the slice
        name = rest
        if name in SHARED_SLICE or SHARED_RE.match(name):
            objs = [c for c, _r, p in xd.site_cells.get(site, [])
                    if re.match(r"^[A-D]5?FF$", p)] if site else []
            prim = [(o, attrib_of_cell(o)) for o in objs]
            return {"resource": "触发器与slice设置", "feature": feature,
                    "objects": objs, "attrib": [a for _o, a in prim],
                    "primary": prim, "tags": ["slice共用设置"]}
        return None

    if cls == "dsp_config":
        parts = feature.split(".")
        which = parts[2] if len(parts) >= 3 else ""   # DSP_0 / DSP_1
        sites = xd.sites_of_tile.get(tile, [])
        if not sites:
            return {"resource": "DSP 设置", "feature": feature, "objects": [],
                    "attrib": [], "primary": [], "tags": ["无DSP单元"]}
        sites_s = sorted(sites, key=lambda s: int(re.search(r"X(\d+)Y", s).group(1)))
        site = sites_s[0] if which == "DSP_0" else sites_s[-1]
        objs = [c for c, r, _p in xd.site_cells.get(site, [])
                if r.startswith("DSP48")]
        prim = [(o, attrib_of_cell(o)) for o in objs]
        return {"resource": "DSP 设置", "feature": feature, "objects": objs,
                "attrib": [a for _o, a in prim], "primary": prim, "tags": []}

    if cls == "bram_config":
        return {"resource": "BRAM", "feature": feature, "objects": [],
                "attrib": [], "primary": [], "tags": ["类别未定义-瓦片级退回"]}
    return None


# ------------------------------------------------- section 8: downstream rule
#
# Parallel attribution for routing (switch) bits: instead of whole-net loads,
# walk the net's routing tree from the hit pip's DST wire node downstream and
# collect only load cells reached on downstream nodes.  Original section-4
# results are preserved untouched; this produces a parallel
# bit_attrib_downstream.json.

PIP_TOKEN_RE = re.compile(r"([^/]+)/([A-Z0-9_]+)\.(.+?)(->>|->)(.+)$")


class DownstreamData:
    """wp6a_dump.nload.tsv: per net wire->node map, node->load cells and the
    net's pip list (lazy node-level adjacency, built per queried net)."""

    def __init__(self, design_dir):
        t0 = time.time()
        self.dir = Path(design_dir)
        self.wire2node = defaultdict(dict)          # net -> {wire -> node}
        self.node_sp = defaultdict(list)            # (net, node) -> [(site,pin,dir)]
        self.net_pips = defaultdict(list)           # net -> [(tile,src,dst,arrow)]
        self._adj = {}                              # net -> {snode -> {dnode}}
        self._adj2 = {}                             # v2: oriented {u -> {v}}
        self._adj2_root = {}                        # v2: chosen roots per net
        self.net_unreachable = Counter()            # v2: nodes not reached
        self.out_nodes = defaultdict(set)           # v2: net -> nodes w/ OUT sp
        self.missing_wire = Counter()
        self.nlp = defaultdict(list)                # (net,site) -> [(spn,cell,pin,bel)]
        self.driver = {}                            # net -> (dnode, dsite, rtok0)
        self.usp = defaultdict(set)                 # site -> {SITEPIP:BELPIN}
        self._spmap = None                          # (net,site,spn) -> {cell}
        self._spstats = Counter()
        n = {"NNODE": 0, "NWIRE": 0, "NPIP": 0, "NSP": 0}
        with open(self.dir / "wp6a_dump.nload.tsv", encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) < 3:
                    continue
                tag, net = p[0], p[1]
                if tag == "NWIRE" and len(p) >= 4:
                    self.wire2node[net][p[3]] = p[2]
                    n[tag] += 1
                elif tag == "NSP" and len(p) >= 6:
                    self.node_sp[(net, p[2])].append((p[3], p[4], p[5]))
                    if p[5] == "OUT":
                        self.out_nodes[net].add(p[2])
                    n[tag] += 1
                elif tag == "NPIP" and len(p) >= 3:
                    m = PIP_TOKEN_RE.match(p[2])
                    if m:
                        tile, _tt, src, arrow, dst = m.groups()
                        self.net_pips[net].append((tile, src, dst, arrow))
                        n[tag] += 1
                elif tag == "NNODE":
                    n[tag] += 1
        self.rows = n
        self.seconds = round(time.time() - t0, 1)

    def _adjacency(self, net):
        adj = self._adj.get(net)
        if adj is None:
            adj = defaultdict(set)
            w2n = self.wire2node.get(net, {})
            for tile, src, dst, arrow in self.net_pips.get(net, []):
                sn = w2n.get("%s/%s" % (tile, src))
                dn = w2n.get("%s/%s" % (tile, dst))
                if sn is None or dn is None:
                    self.missing_wire[net] += 1
                    continue
                adj[sn].add(dn)
                if arrow == "->>":
                    adj[dn].add(sn)
            self._adj[net] = adj
        return adj

    def downstream_sitepins(self, net, tile, src, dst):
        """Site pins reachable from the target pip's DST node downstream.
        Returns (sitepins, tags): sitepins = [(node, site, pin, dir)]."""
        tags = []
        w2n = self.wire2node.get(net)
        if w2n is None:
            return [], ["下游口径:网无节点导出"]
        start = w2n.get("%s/%s" % (tile, dst))
        if start is None:
            return [], ["下游口径:目标开关DST线缺节点"]
        if self.missing_wire.get(net):
            tags.append("下游口径:网内存在缺线开关")
        adj = self._adjacency(net)
        seen = set()
        stack = [start]
        sps = []
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            for site, pin, d in self.node_sp.get((net, node), []):
                sps.append((node, site, pin, d))
            stack.extend(adj.get(node, ()))
        return sps, tags


_LUTPIN_RE = re.compile(r"^([ABCD])([1-6])$")
_LUTPOS_RE = re.compile(r"^([ABCD])([56])LUT$")
_FFPOS_RE = re.compile(r"^[A-H]5?FF$")


def sitepin_cells_v2(xd, site, pin):
    """v2 fallback for IN site pins reached by the route that carry no leaf
    pin on the net (constant-net taps and the like): resolve to the cells
    behind the pin's consuming BELs, pin-level only.

      SLICE  Xk (X=A-D, k=1-6) -> LUT cells at X6LUT (and X5LUT when k<=5)
      SLICE  CIN/COUT          -> carry-chain cells
      SLICE  SR/CE/CLK/WE      -> FF cells (the control-input consumers)
      other sites / other pins -> all cells in the site.
    """
    cells = xd.site_cells.get(site)
    if not cells:
        return [], False
    m = _LUTPIN_RE.match(pin)
    if m:
        col, k = m.group(1), int(m.group(2))
        # any ref placed on the column's LUT bels consumes the pin --
        # including RAM32M/RAM64M cells that occupy SLICEM LUT bels
        hit = [c for c, ref, pos in cells
               if _LUTPOS_RE.match(pos)
               and pos[0] == col and (pos[1] == "6" or k <= 5)]
        if not hit:
            # SLICEM multi-column RAM macros (RAM32M/RAM64M/RAM128X1S)
            # physically span RAMA..RAMD bels sharing the column pin
            hit = [c for c, ref, pos in cells
                   if ref.startswith(("RAM32M", "RAM64M", "RAM128",
                                      "RAM256"))]
        return sorted(hit), True
    if pin in ("CIN", "COUT"):
        hit = [c for c, ref, pos in cells
               if "CARRY" in ref or "CARRY" in pos]
        return sorted(hit), True
    if pin in ("SR", "CE", "CLK", "WE"):
        hit = [c for c, ref, pos in cells if _FFPOS_RE.match(pos)]
        return sorted(hit), True
    return sorted(c for c, _r, _p in cells), bool(cells)


def sitepin_cells(xd, site, pin):
    """(site, site-pin name) -> ([cells], resolved).

    IN site pins map to cells placed in the site that consume that pin:
      SLICE  Xk (X=A-D, k=1-6) -> LUT cells at X5LUT (k<=5) / X6LUT
      SLICE  CIN/COUT          -> carry-chain cells
      SLICE  CLK/CE/SR/WE      -> all cells in the site
      other sites (DSP48, BRAM, IOB...) -> all cells in the site.
    """
    cells = xd.site_cells.get(site)
    if cells is None:
        return [], False
    m = _LUTPIN_RE.match(pin)
    if m:
        col, k = m.group(1), int(m.group(2))
        hit = [c for c, ref, pos in cells
               if ref.startswith("LUT") and _LUTPOS_RE.match(pos)
               and pos[0] == col and (pos[1] == "6" or k <= 5)]
        return sorted(hit), True
    if pin in ("CIN", "COUT"):
        hit = [c for c, ref, pos in cells
               if "CARRY" in ref or "CARRY" in pos]
        return sorted(hit), True
    return sorted(c for c, _r, _p in cells), bool(cells)

def attribute_hit_downstream(hit, xd, dd):
    """Section-8 downstream rule for routing bits; other classes delegate."""
    tile, tt, feature, pos, cls = hit
    if cls != "routing":
        return attribute_hit(hit, xd)
    base = attribute_hit(hit, xd)
    if base is None or base.get("net") is None:
        return base
    parts = feature.split(".")
    if len(parts) != 3:
        return base
    dst, src = parts[1], parts[2]
    net = base["net"]
    sps, dtags = dd.downstream_sitepins(net, tile, src, dst)
    lds = []
    unresolved = []
    for _node, site, pin, d in sps:
        if d != "IN":
            continue
        cs, ok = sitepin_cells(xd, site, pin)
        if ok:
            lds.extend(cs)
        else:
            unresolved.append("%s/%s" % (site, pin))
    if unresolved:
        dtags.append("\u4e0b\u6e38\u53e3\u5f84:\u7ad9\u70b9\u5f15\u811a\u672a\u6620\u5c04[%d]" % len(unresolved))
    lds = sorted(set(lds))
    attribs = [attrib_of_cell(c) for c in lds]
    kern = [a for a in attribs if a[0] != "unmapped"]
    ty = base.get("net_type")
    if net in ("<const0>", "<const1>") or ty in ("GROUND", "POWER"):
        netcat = "\u5e38\u91cf\u7f51"
    elif len({a[0] for a in kern}) > 1:
        netcat = "\u6df7\u5408\uff08\u7f51\uff09"
    elif kern:
        netcat = kern[0][0]
    else:
        netcat = "unmapped"
    out = dict(base)
    out["rule"] = "downstream"
    out["loads_wholenet"] = base.get("loads", [])
    out["loads"] = lds
    out["objects"] = lds
    out["attrib"] = kern
    out["net_category_downstream"] = netcat
    out["primary"] = [(c, attrib_of_cell(c)) for c in lds
                      if attrib_of_cell(c)[0] != "unmapped"]
    out["tags"] = list(base.get("tags", [])) + dtags
    if unresolved:
        out["unresolved_sitepins"] = unresolved[:20]
    if set(lds) != set(base.get("loads", [])):
        out["tags"].append("\u4e0b\u6e38\u53e3\u5f84:\u8d1f\u8f7d\u96c6\u4e0d\u540c")
    return out


def corrected_tile(tile_kind, tile_x, row, clock_region):
    """WP6A section 2 corrections: DSP/BRAM tiles named by 5-row rounding;
    merged columns are already split by wp6a_bitmap.load_columns."""
    ry = int(str(clock_region)[-1])
    if str(tile_kind).startswith(("DSP", "BRAM")):
        row = row - row % 5
    return "%s_X%dY%d" % (tile_kind, tile_x, 50 * ry + row)


# --------------------------------------------------------------------- G4

def check_g4(shot_results, singles):
    """Telemetry consistency for 'corrected' singles."""
    corr = [(s, r) for s, r in zip(singles, shot_results)
            if s.get("category") == "corrected" and not s.get("void")]
    n = consistent = 0
    lane_n = lane_ok = 0
    inconsistencies = []
    for s, r in corr:
        stages = {a[1] for h in r.get("hits_detail", []) for a in h["attrib"]
                  if a[1] is not None}
        tel = s.get("telemetry") or {}
        tel_stage = tel.get("last_stage")
        if tel_stage is None:
            continue
        expected = tel_stage + 1
        if len(stages) != 1:
            continue                # judged only when attribution is unique
        n += 1
        ok = expected in stages
        consistent += ok
        lanes = {a[2] for h in r.get("hits_detail", []) for a in h["attrib"]
                 if a[0] == "protected_datapath" and a[2] is not None}
        lane_stat = None
        if lanes:
            lane_n += 1
            lane_stat = tel.get("last_loc") in lanes
            lane_ok += 1 if lane_stat else 0
        if not ok or lane_stat is False:
            inconsistencies.append({
                "index": s.get("index"), "expected_stage": expected,
                "attributed_stages": sorted(stages),
                "last_loc": tel.get("last_loc"),
                "lane_match": lane_stat,
                "resource": r.get("resource"),
                "feature": r.get("feature"),
                "tile": r.get("tile")})
    frac = consistent / n if n else None
    lane_frac = lane_ok / lane_n if lane_n else None
    return {
        "corrected_singles": len(corr),
        "unique_stage_judged": n,
        "stage_consistent": consistent,
        "stage_consistent_frac": round(frac, 4) if frac is not None else None,
        "lane_judged": lane_n,
        "lane_consistent": lane_ok,
        "lane_consistent_frac": round(lane_frac, 4) if lane_frac is not None else None,
        "gate": G4_GATE,
        "verdict": ("pass" if frac is not None and frac >= G4_GATE
                    else "fail" if frac is not None else "not-judged"),
        "inconsistencies": inconsistencies[:200],
    }


def cmd_attrib(args):
    dname = args.design
    if dname not in DESIGNS:
        raise SystemExit("design must be one of %s" % sorted(DESIGNS))
    design_dir = Path(args.input_dir) if args.input_dir else OUT / dname
    sc_path = OUT / dname / "selfcheck.json"
    if not sc_path.is_file():
        raise SystemExit("selfcheck.json missing; run selfcheck first")
    sc = load_json(sc_path)
    if sc["g1_pips"]["verdict"] != "pass" or sc["g2_lut_init"]["verdict"] != "pass":
        raise SystemExit("selfcheck gates not passed (G1=%s G2=%s); stop"
                         % (sc["g1_pips"]["verdict"], sc["g2_lut_init"]["verdict"]))
    for k in ("cells", "luts", "sites", "pips"):
        f = design_dir / f"wp6a_dump.{k}.tsv"
        if not f.is_file() or f.stat().st_size == 0:
            raise SystemExit("missing or empty export: %s" % f)

    t_all = time.time()
    print("[%s] loading exports ..." % dname)
    xd = ExportData(design_dir)
    print("[%s] exports loaded (%.1f s)" % (dname, xd.seconds))
    cols, lo = bm.load_columns()
    db = bm.load_db()

    singles = []
    for line in retry_read(OUT / dname / "campaign.jsonl").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue                        # partially flushed last line
        if r.get("kind") == "single" and not r.get("void") and r.get("target"):
            singles.append(r)
    print("[%s] %d single shots" % (dname, len(singles)))

    pool_versions = Counter(s.get("pool_version") for s in singles)
    results = []
    st = Counter()
    for s in singles:
        t = s["target"][0]
        lsb = lsb_of(t["bt"], s.get("pool_version"))
        r = bm.map_bit(cols, lo, db, t["la"], t["wd"], lsb)
        det = []
        for hit in r["hits"]:
            a = attribute_hit(hit, xd)
            if a:
                det.append(a)
        # compact per-hit record (full object lists would make this GB-scale)
        det_c = []
        for h in det:
            c = {"resource": h["resource"], "feature": h["feature"],
                 "tags": h["tags"],
                 "attrib": [list(x) for x in sorted(
                     {tuple(a) for a in h["attrib"]},
                     key=lambda a: (a[0], a[1] if a[1] is not None else -1,
                                    a[2] if a[2] is not None else -1))],
                 "n_objects": len(h["objects"]),
                 "objects": h["objects"][:8]}
            for key in ("net", "net_type", "net_category", "pip", "drivers"):
                if h.get(key):
                    v = h[key]
                    c[key] = v[:8] if isinstance(v, list) else v
            det_c.append(c)
        entry = {
            "index": s.get("index"),
            "category": s.get("category"),
            "la": t["la"], "wd": t["wd"], "bt": t["bt"], "lsb": lsb,
            "pool_version": s.get("pool_version"),
            "map_status": r["status"],
            "column_tile_kind": r.get("tile_kind"),
            "tile": corrected_tile(r.get("tile_kind"), r.get("tile_x"),
                                   r.get("row", 0), r.get("clock_region"))
            if r.get("tile_kind") else t.get("tile"),
            "hits_detail": det_c,
        }
        if det:
            cats = {h["resource"] for h in det}
            entry["resource"] = "+".join(sorted(cats))
            entry["feature"] = det[0]["feature"]
            att = {tuple(a) for h in det for a in h["attrib"]}
            entry["attrib"] = [list(a) for a in sorted(
                att, key=lambda a: (a[0], a[1] or -1, a[2] if a[2] is not None else -1))]
            entry["tags"] = sorted({tg for h in det for tg in h["tags"]})
        else:
            entry["resource"] = "未解析"
            tt = entry["tile"]
            ta = xd.tile_table.get(tt, {})
            entry["attrib"] = ta.get("attrib", [])
            entry["feature"] = None
            entry["tags"] = ["未解析"] + (["瓦片级mixed"] if any(
                a[0] == "mixed" for a in entry["attrib"]) else [])
        entry["unique"] = len({a[1] for a in entry["attrib"]
                               if a[1] is not None}) == 1
        # replica uniqueness for tmr_stage
        repl = {a[2] for a in entry["attrib"]
                if a[0] == "tmr_stage" and a[2] is not None}
        entry["tmr_replica_unique"] = len(repl) == 1 if repl else None
        if entry["tmr_replica_unique"]:
            entry["tmr_replica"] = repl.pop()
        results.append(entry)
        st[entry["resource"]] += 1

    g4 = check_g4(results, singles)
    print("[%s] G4: corrected=%d judged=%d stage_consistent=%.4f (%s)"
          % (dname, g4["corrected_singles"], g4["unique_stage_judged"],
             g4["stage_consistent_frac"] or 0, g4["verdict"]))

    out = {
        "design": dname,
        "step": "WP6-A step 4 per-bit attribution + G4",
        "date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "campaign_shots": len(singles),
        "pool_versions": dict(pool_versions),
        "map_status": dict(Counter(r["map_status"] for r in results)),
        "resource_categories": dict(st.most_common()),
        "g4_telemetry": g4,
        "shots": results,
        "runtime_seconds": round(time.time() - t_all, 1),
    }
    opath = OUT / dname / "bit_attrib.json"
    opath.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("[%s] wrote %s (%.1f s)" % (dname, opath, time.time() - t_all))
    return 0 if g4["verdict"] != "fail" else 2


def cmd_attrib_downstream(args):
    """Section-8 parallel attribution: routing hits re-attributed by the
    downstream-load rule; writes bit_attrib_downstream.json and a diff
    summary versus the section-4 whole-net bit_attrib.json."""
    dname = args.design
    if dname not in DESIGNS:
        raise SystemExit("design must be one of %s" % sorted(DESIGNS))
    design_dir = Path(args.input_dir) if args.input_dir else OUT / dname
    sc_path = OUT / dname / "selfcheck.json"
    if not sc_path.is_file():
        raise SystemExit("selfcheck.json missing; run selfcheck first")
    sc = load_json(sc_path)
    if sc["g1_pips"]["verdict"] != "pass" or sc["g2_lut_init"]["verdict"] != "pass":
        raise SystemExit("selfcheck gates not passed; stop")
    nl = design_dir / "wp6a_dump.nload.tsv"
    if not nl.is_file() or nl.stat().st_size == 0:
        raise SystemExit("missing or empty export: %s" % nl)

    t_all = time.time()
    print("[%s] loading exports (downstream) ..." % dname)
    xd = ExportData(design_dir)
    dd = DownstreamData(design_dir)
    print("[%s] exports loaded (xd %.1f s, dd %.1f s, rows=%s)"
          % (dname, xd.seconds, dd.seconds, dd.rows))
    cols, lo = bm.load_columns()
    db = bm.load_db()

    singles = []
    for line in retry_read(OUT / dname / "campaign.jsonl").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("kind") == "single" and not r.get("void") and r.get("target"):
            singles.append(r)
    print("[%s] %d single shots" % (dname, len(singles)))

    pool_versions = Counter(s.get("pool_version") for s in singles)
    results = []
    st = Counter()
    changed = 0
    replica_conflicts = []
    for s in singles:
        t = s["target"][0]
        lsb = lsb_of(t["bt"], s.get("pool_version"))
        r = bm.map_bit(cols, lo, db, t["la"], t["wd"], lsb)
        det = []
        for hit in r["hits"]:
            a = attribute_hit_downstream(hit, xd, dd)
            if a:
                det.append(a)
        det_c = []
        for h in det:
            c = {"resource": h["resource"], "feature": h["feature"],
                 "tags": h["tags"],
                 "attrib": [list(x) for x in sorted(
                     {tuple(a) for a in h["attrib"]},
                     key=lambda a: (a[0], a[1] if a[1] is not None else -1,
                                    a[2] if a[2] is not None else -1))],
                 "n_objects": len(h["objects"]),
                 "objects": h["objects"][:8]}
            for key in ("net", "net_type", "net_category", "pip", "drivers",
                        "net_category_downstream"):
                if h.get(key):
                    v = h[key]
                    c[key] = v[:8] if isinstance(v, list) else v
            det_c.append(c)
        entry = {
            "index": s.get("index"),
            "category": s.get("category"),
            "la": t["la"], "wd": t["wd"], "bt": t["bt"], "lsb": lsb,
            "pool_version": s.get("pool_version"),
            "map_status": r["status"],
            "column_tile_kind": r.get("tile_kind"),
            "tile": corrected_tile(r.get("tile_kind"), r.get("tile_x"),
                                   r.get("row", 0), r.get("clock_region"))
            if r.get("tile_kind") else t.get("tile"),
            "hits_detail": det_c,
        }
        if det:
            cats = {h["resource"] for h in det}
            entry["resource"] = "+".join(sorted(cats))
            entry["feature"] = det[0]["feature"]
            att = {tuple(a) for h in det for a in h["attrib"]}
            entry["attrib"] = [list(a) for a in sorted(
                att, key=lambda a: (a[0], a[1] or -1, a[2] if a[2] is not None else -1))]
            entry["tags"] = sorted({tg for h in det for tg in h["tags"]})
        else:
            entry["resource"] = "未解析"
            ta = xd.tile_table.get(entry["tile"], {})
            entry["attrib"] = ta.get("attrib", [])
            entry["feature"] = None
            entry["tags"] = ["未解析"]
        entry["unique"] = len({a[1] for a in entry["attrib"]
                               if a[1] is not None}) == 1
        repl = {a[2] for a in entry["attrib"]
                if a[0] == "tmr_stage" and a[2] is not None}
        entry["tmr_replica_unique"] = len(repl) == 1 if repl else None
        if entry["tmr_replica_unique"]:
            entry["tmr_replica"] = repl.pop()
        results.append(entry)
        st[entry["resource"]] += 1

    # diff vs whole-net attribution (section 4 result, read-only)
    orig_path = OUT / dname / "bit_attrib.json"
    diff = {"available": False}
    if orig_path.is_file():
        orig = {(e["la"], e["wd"], e["bt"]): e
                for e in load_json(orig_path).get("shots", [])}
        changed_examples = []
        for e in results:
            o = orig.get((e["la"], e["wd"], e["bt"]))
            if o is None:
                continue
            if [tuple(a) for a in e["attrib"]] != [tuple(a) for a in o["attrib"]]:
                changed += 1
                if len(changed_examples) < 50:
                    changed_examples.append({
                        "index": e["index"], "lfa": (e["la"], e["wd"], e["bt"]),
                        "wholenet": o["attrib"], "downstream": e["attrib"]})
            orep, drep = o.get("tmr_replica"), e.get("tmr_replica")
            if (o.get("tmr_replica_unique") and e.get("tmr_replica_unique")
                    and orep is not None and drep is not None
                    and orep != drep):
                replica_conflicts.append({
                    "index": e["index"], "lfa": (e["la"], e["wd"], e["bt"]),
                    "wholenet_replica": orep, "downstream_replica": drep})
        diff = {"available": True, "attrib_changed_shots": changed,
                "changed_examples": changed_examples,
                "replica_conflicts": replica_conflicts,
                "replica_conflict_count": len(replica_conflicts)}

    g4 = check_g4(results, singles)
    print("[%s] G4(downstream): corrected=%d judged=%d stage=%.4f lane=%s (%s)"
          % (dname, g4["corrected_singles"], g4["unique_stage_judged"],
             g4["stage_consistent_frac"] or 0,
             g4.get("lane_consistent_frac"), g4["verdict"]))

    out = {
        "design": dname,
        "step": "WP6-A section-8 downstream-load attribution (parallel rule)",
        "rule": "downstream",
        "date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "campaign_shots": len(singles),
        "pool_versions": dict(pool_versions),
        "map_status": dict(Counter(r["map_status"] for r in results)),
        "resource_categories": dict(st.most_common()),
        "g4_telemetry_downstream": g4,
        "diff_vs_wholenet": diff,
        "nload_rows": dd.rows,
        "shots": results,
        "runtime_seconds": round(time.time() - t_all, 1),
    }
    opath = OUT / dname / "bit_attrib_downstream.json"
    opath.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("[%s] wrote %s (%.1f s)" % (dname, opath, time.time() - t_all))
    return 0


# ------------------------------------------------------------------- main

def cmd_selfcheck(args):
    dname = args.design
    if dname not in DESIGNS:
        raise SystemExit("design must be one of %s" % sorted(DESIGNS))
    sem_design = DESIGNS[dname]
    design_dir = Path(args.input_dir) if args.input_dir else OUT / dname
    prereg = load_json(OUT / "prereg.json")
    frozen = Path(prereg["frozen_inputs"][sem_design]["frozen_dir"])
    ebc_path = frozen / "k7_paper_top.ebc"
    for k in ("pips", "cells", "luts", "sites", "lutpins"):
        f = design_dir / f"wp6a_dump.{k}.tsv"
        if not f.is_file() or f.stat().st_size == 0:
            raise SystemExit("missing or empty export: %s" % f)

    verdict = g0_verdict_from_report(OUT / "g0" / "REPORT.md")
    mode_arg = args.bit_mode
    if mode_arg == "auto":
        bit_modes = ["auto"]
    elif mode_arg == "m":
        bit_modes = ["M", "N"]
    else:
        bit_modes = [mode_arg]

    t_all = time.time()
    print("[%s] loading .ebc %s ..." % (dname, ebc_path))
    ebc = Ebc(ebc_path)
    print("[%s] ebc frames=%d sha256=%s" % (dname, ebc.n_frames,
                                            sha256_file(ebc_path)[:16]))
    cols, _lo = bm.load_columns()
    cidx = ColumnIndex(cols)
    feat_db, mask_db = load_feature_db()
    ppips = load_ppips()

    print("[%s] G1 pips ..." % dname)
    g1 = check_g1(design_dir, ebc, feat_db, mask_db, ppips, cidx)
    print("[%s] G1 agree %d/%d (%.4f) skipped_ppips=%s no_feature=%d unmapped=%d -> %s"
          % (dname, g1["agree"], g1["agree"] + g1["disagree"], g1["agree_frac"],
             g1["skipped_ppips"], g1["no_feature"], g1["unmapped_tile"],
             g1["verdict"].upper()))
    print("[%s] G2 LUT INIT (ruling: A6=64 bits, no-A6=INIT[32..63] + A6 audit) ..."
          % dname)
    g2 = check_g2(design_dir, ebc, feat_db, cidx)
    print("[%s] G2 comparable %d/%d, agree %d (A6:%d noA6:%d) disagree %d "
          "a6_audit=%s -> %s"
          % (dname, g2["comparable"], g2["luts_total"], g2["agree"],
             g2["agree_with_a6_pin"], g2["agree_without_a6_pin"],
             g2["disagree"], {k: v for k, v in g2["a6_pin_audit"].items()
                              if k != "other_cells"}, g2["verdict"].upper()))
    g3 = None
    if g2["verdict"] == "pass":
        print("[%s] G3 coverage (modes %s, g0=%s) ..." % (dname, bit_modes, verdict))
        g3 = check_g3(dname, sem_design, bit_modes)
        for mode, r in g3["bit_modes"].items():
            print("[%s] G3 mode %s: hit %d/%d (%.4f)"
                  % (dname, mode, r["hit"], r["pool_bits"], r["hit_frac"] or 0))
    else:
        print("[%s] G2 FAIL -> G3 skipped (stop condition)" % dname)

    summary = {"G1": g1["verdict"], "G2": g2["verdict"],
               "G3": "report-only" if g3 else "SKIPPED (G2 fail)"}
    out = {
        "design": dname,
        "sem_design": sem_design,
        "step": "WP6-A self-checks G1/G2/G3",
        "date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "inputs": {
            "ebc": str(ebc_path),
            "ebc_sha256": sha256_file(ebc_path),
            "ebc_frames": ebc.n_frames,
            "frame_table": str(bm.FTAB),
            "dump_tsvs": {k: str(design_dir / f"wp6a_dump.{k}.tsv")
                          for k in ("pips", "cells", "luts", "sites", "lutpins")},
            "prjxray_db": str(bm.DB_DIR),
        },
        "bit_numbering": {
            "g0_verdict": verdict,
            "modes_run": bit_modes,
            "rule": "v2.1 record -> LSB=bt; v2 record -> LSB=31-bt "
                    "(G0=M); --bit-mode M/N forces",
        },
        "g1_pips": g1,
        "g2_lut_init": g2,
        "g3_coverage": g3,
        "summary": summary,
        "runtime_seconds": round(time.time() - t_all, 1),
    }
    opath = OUT / dname / "selfcheck.json"
    opath.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    print("[%s] wrote %s (%.1f s total)" % (dname, opath,
                                            time.time() - t_all))
    print("SUMMARY %s: G1=%s G2=%s G3=%s" % (dname, summary["G1"].upper(),
                                             summary["G2"].upper(),
                                             summary["G3"]))
    ok = (g1["verdict"] == "pass" and g2["verdict"] == "pass")
    return 0 if ok else 2



# ---------------------------------------------------------------------------
# WP6 section-16: v2 downstream attribution
#   - loads resolved at physical leaf-pin level (NLP export + used site pips)
#   - traversal follows the actually-used routing direction only:
#     "->" pips keep physical direction; "->>" pips are oriented by the
#     route-tree root BFS (single used direction), constant nets are forests.

_IS_CONST_NET = re.compile(r"<const[01]>")


def _parse_loadpins(dd):
    """Read wp6a_dump.loadpins.tsv (NLD/NLP) and wp6a_dump.sitepips.tsv (USP)
    produced by the read-only section-16 Vivado export."""
    n_nld = n_nlp = n_usp = 0
    lp = dd.dir / "wp6a_dump.loadpins.tsv"
    with open(lp, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[0] == "NLD" and len(p) >= 5:
                dd.driver[p[1]] = (p[2], p[3], p[4])
                n_nld += 1
            elif p[0] == "NLP" and len(p) >= 7:
                net, site, spn, cell, pin, bel = p[1:7]
                dd.nlp[(net, site)].append((spn, cell, pin, bel))
                n_nlp += 1
    usp = dd.dir / "wp6a_dump.sitepips.tsv"
    with open(usp, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[0] == "USP" and len(p) >= 3:
                name = p[2].split("/", 1)[-1]      # SITE/PIP:SEL -> PIP:SEL
                dd.usp[p[1]].add(name)
                n_usp += 1
    return n_nld, n_nlp, n_usp


_FFMUX_RE = re.compile(r"^([ABCD])5?FFMUX:([A-Z0-9_]+)$")
_DI1MUX_RE = re.compile(r"^([ABCD])DI1MUX:([A-Z0-9_]+)$")
_CY0_RE = re.compile(r"^([ABCD])CY0:([A-Z0-9_]+)$")

# leaf bel-pin suffix -> entry site-pin candidates
_LEAF_SP = {
    "SR": ("SR",), "CE": ("CE",), "CLK": ("CLK", "CK"), "CK": ("CLK", "CK"),
    "R": ("SR", "R"), "S": ("SR", "S"), "PRE": ("SR",),
    "CI": ("CIN",), "CIN": ("CIN",), "CYINIT": ("CIN",),
    "EN": ("CE",), "WE": ("WE", "CE"), "GATE": ("CE",),
}


def _leaf_sp_candidates(dd, site, pin, bel):
    """Site-pin candidates through which a leaf pin (no direct site pin)
    may be fed, using the site's used site pips.  Returns (cands, intra):
    intra=True when the leaf is fed intra-site (LUT/carry output)."""
    usp = dd.usp.get(site, set())
    bn = (bel or pin or "").rsplit("/", 1)[-1]
    pn = pin or ""
    # FF D inputs: <X>FFMUX:<sel> decides the source
    m = re.match(r"^([ABCD])(5?)FF$", bel or "")
    if m and pn in ("D", "D1"):
        col, five = m.group(1), m.group(2)
        pre = "%s%sFFMUX:" % (col, five)
        for u in usp:
            if not u.startswith(pre):
                continue
            sel = u.split(":", 1)[1]
            if sel in ("AX", "BX", "CX", "DX"):
                return [sel], False
            if re.match(r"^IN_[ABCD]$", sel):
                return [sel[3] + "I"], False
            return [], True           # O5/O6/XOR/F7/F8/CY: intra-site
        return ["%sX" % col], False
    # CARRY4 leaf pins
    if (bel or "").startswith("CARRY4"):
        if pn in ("CI", "CIN"):
            return ["CIN"], False
        if pn == "CYINIT":
            return ["CIN"], False
        m = re.match(r"^DI([0-3])$", pn)
        if m:
            col = "ABCD"[int(m.group(1))]
            for u in usp:
                mm = _DI1MUX_RE.match(u)
                if mm and mm.group(1) == col:
                    return [mm.group(2)], False
            return [col + "I"], False
        if pn.startswith("S"):
            return [], True
        return ["CIN"], False
    # direct suffix rules (control pins)
    for suf, cands in _LEAF_SP.items():
        if bn == suf or pn == suf:
            return list(cands), False
    # DSP/BRAM cascade pins: fed intra-site
    if re.search(r"(CASC|MULTSIGN|CARRYINSEL)", pn):
        return [], True
    # same-name fallback: pin name matches a plausible site pin
    if re.match(r"^[A-D][1-6]$", pn) or re.match(r"^[A-D]I$", pn):
        return [pn], False
    if re.match(r"^[A-D]X$", pn):
        return [pn], False
    return [], True


def _resolve_pinloads(dd, xd):
    """Build dd._spmap: (net, site, site-pin) -> {leaf cells}.

    Direct NLP rows (site pin present) map one-to-one; leaf pins without a
    direct site pin are attached to the site-pin candidate(s) implied by the
    used site pips, or to "*" when fed intra-site (load counted whenever the
    net is present at that site)."""
    spmap = {}
    unresolved = 0
    for (net, site), rows in dd.nlp.items():
        direct = defaultdict(set)
        for spn, cell, pin, bel in rows:
            if spn and spn != "-":
                direct[spn].add(cell)
            else:
                cands, intra = _leaf_sp_candidates(dd, site, pin, bel)
                if cands:
                    for c in cands:
                        direct[c].add(cell)
                elif intra:
                    direct["*"].add(cell)
                else:
                    direct["*"].add(cell)
                    unresolved += 1
                    dd._spstats["leaf_no_candidate"] += 1
        for spn, cells in direct.items():
            spmap[(net, site, spn)] = cells
    dd._spmap = spmap
    dd._spstats["leaf_unresolved"] = unresolved


def _reach(fwd, bid, r):
    seen = {r}
    stack = [r]
    while stack:
        u = stack.pop()
        for v in fwd[u] | bid[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def _oriented(dd, net):
    """v2 adjacency: pips oriented by the actually-used direction.

    "->" pips are unidirectional (src->dst).  "->>" pips are bidirectional;
    they are oriented by first-discovery in a BFS from the route roots,
    i.e. the direction actually used by this net's tree.  Constant nets are
    forests: each component gets its own root."""
    adj = dd._adj2.get(net)
    if adj is not None:
        return adj
    w2n = dd.wire2node.get(net, {})
    und = defaultdict(set)
    fwd = defaultdict(set)
    bid = defaultdict(set)
    for tile, src, dst, arrow in dd.net_pips.get(net, []):
        sn = w2n.get("%s/%s" % (tile, src))
        dn = w2n.get("%s/%s" % (tile, dst))
        if sn is None or dn is None:
            dd.missing_wire[net] += 1
            continue
        if sn == dn:
            continue
        und[sn].add(dn)
        und[dn].add(sn)
        if arrow == "->":
            fwd[sn].add(dn)
        else:
            bid[sn].add(dn)
            bid[dn].add(sn)
    const = bool(_IS_CONST_NET.search(net))
    dnode, dsite, rtok = dd.driver.get(net, ("", "", ""))
    outn = dd.out_nodes.get(net, set())
    seen = set()
    roots = []
    for n0 in und:
        if n0 in seen:
            continue
        comp = {n0}
        stack = [n0]
        seen.add(n0)
        while stack:
            x = stack.pop()
            for y in und[x]:
                if y not in seen:
                    seen.add(y)
                    comp.add(y)
                    stack.append(y)
        cands = []
        if const:
            cands = [n for n in comp
                     if n.rsplit("/", 1)[-1].endswith("_WIRE")]
            if not cands:
                cands = [n for n in comp if n in outn]
        else:
            if dnode and dnode in comp:
                cands = [dnode]
            elif rtok:
                want = {rtok} if "/" in rtok else {
                    "%s/%s" % (t, rtok) for t in
                    {w.split("/")[0] for w in w2n}}
                hit = [w2n[w] for w in want
                       if w in w2n and w2n[w] in comp]
                if hit:
                    cands = hit
            if not cands:
                outs = [n for n in comp if n in outn]
                cands = outs or list(comp)
        if len(cands) == 1:
            roots.append(cands[0])
            dd.net_unreachable[net] += (
                len(comp) - len(_reach(fwd, bid, cands[0])))
            continue
        best, bestreach = None, -1
        for r in cands:
            reach = _reach(fwd, bid, r)
            if len(reach) > bestreach or (
                    len(reach) == bestreach and best not in outn
                    and r in outn):
                best, bestreach = r, len(reach)
        roots.append(best)
        dd.net_unreachable[net] += len(comp) - bestreach
    adj = defaultdict(set)
    for r in roots:
        stack = [r]
        done = {r}
        while stack:
            u = stack.pop()
            for v in fwd[u] | bid[u]:
                if v in done:
                    continue
                done.add(v)
                adj[u].add(v)
                stack.append(v)
    dd._adj2[net] = adj
    dd._adj2_root[net] = roots
    return adj


def downstream_loads_v2(dd, xd, net, tile, src, dst):
    """Pin-level downstream load cells reachable from the target pip.

    Returns (cells, tags, unresolved_sitepins).  The walk starts at the
    oriented exit node of the pip: "->" exits via dst; "->>" exits via
    whichever endpoint the used direction leaves from.  Site pins reached
    by the route that carry no leaf pin on the net (constant-net taps)
    resolve through sitepin_cells_v2 (BEL-level, no site expansion)."""
    tags = []
    w2n = dd.wire2node.get(net)
    if w2n is None:
        return set(), ["v2口径:网无节点导出"], []
    snode = w2n.get("%s/%s" % (tile, src))
    dnode = w2n.get("%s/%s" % (tile, dst))
    if dnode is None:
        return set(), ["v2口径:目标开关DST线缺节点"], []
    if dd.missing_wire.get(net):
        tags.append("v2口径:网内存在缺线开关")
    if dd.net_unreachable.get(net):
        tags.append("v2口径:网内不可达节点[%d]" % dd.net_unreachable[net])
    adj = _oriented(dd, net)
    start = dnode
    if snode is not None and snode in adj.get(dnode, ()):
        start = snode            # ->> pip actually driven dst -> src
    seen = set()
    stack = [start]
    cells = set()
    unres = []
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        for site, pin, d in dd.node_sp.get((net, node), []):
            if d != "IN":
                continue
            cs = dd._spmap.get((net, site, pin))
            star = dd._spmap.get((net, site, "*"))
            if star:
                cells |= star        # intra-site-fed leaf pins of the net
            if cs is None:
                cs, ok = sitepin_cells_v2(xd, site, pin)
                if cs:
                    cs = set(cs)
                    dd._spstats["bel_rule_cells"] += len(cs)
                if ok:
                    dd._spstats["bel_rule_pins"] += 1
            if cs:
                cells |= cs
            else:
                unres.append("%s/%s" % (site, pin))
        stack.extend(adj.get(node, ()))
    return cells, tags, unres


def loads_v1_of_hit(hit, xd, dd):
    """v1 (over-counted) load set for a routing hit: downstream site pins
    expanded through the bypass-style sitepin_cells rule."""
    tile, tt, feature, pos, cls = hit
    if cls != "routing":
        return None
    base = attribute_hit(hit, xd)
    if base is None or base.get("net") is None:
        return None
    parts = feature.split(".")
    if len(parts) != 3:
        return None
    dst, src = parts[1], parts[2]
    net = base["net"]
    sps, _tags = dd.downstream_sitepins(net, tile, src, dst)
    lds = set()
    for _node, site, pin, d in sps:
        if d != "IN":
            continue
        cs, ok = sitepin_cells(xd, site, pin)
        if ok:
            lds.update(cs)
    return lds


def attribute_hit_downstream_v2(hit, xd, dd):
    """Section-16 downstream rule: direction-aware, pin-level loads."""
    tile, tt, feature, pos, cls = hit
    if cls != "routing":
        return attribute_hit(hit, xd)
    base = attribute_hit(hit, xd)
    if base is None or base.get("net") is None:
        return base
    parts = feature.split(".")
    if len(parts) != 3:
        return base
    dst, src = parts[1], parts[2]
    net = base["net"]
    lds, dtags, unres = downstream_loads_v2(dd, xd, net, tile, src, dst)
    lds = sorted(lds)
    attribs = [attrib_of_cell(c) for c in lds]
    kern = [a for a in attribs if a[0] != "unmapped"]
    ty = base.get("net_type")
    if net in ("<const0>", "<const1>") or ty in ("GROUND", "POWER"):
        netcat = "常量网"
    elif len({a[0] for a in kern}) > 1:
        netcat = "混合（网）"
    elif kern:
        netcat = kern[0][0]
    else:
        netcat = "unmapped"
    out = dict(base)
    out["rule"] = "downstream_v2"
    out["loads_wholenet"] = base.get("loads", [])
    out["loads"] = lds
    out["objects"] = lds
    out["attrib"] = kern
    out["net_category_downstream"] = netcat
    out["primary"] = [(c, attrib_of_cell(c)) for c in lds
                      if attrib_of_cell(c)[0] != "unmapped"]
    out["tags"] = list(base.get("tags", [])) + dtags
    if unres:
        out["unresolved_sitepins"] = sorted(set(unres))[:20]
        out["tags"].append("v2口径:站点引脚未映射[%d]" % len(set(unres)))
    return out


def cmd_attrib_v2(args):
    """Section-16 v2 downstream attribution over all single shots."""
    dname = args.design
    if dname not in DESIGNS:
        raise SystemExit("design must be one of %s" % sorted(DESIGNS))
    design_dir = Path(args.input_dir) if args.input_dir else OUT / dname
    sc_path = OUT / dname / "selfcheck.json"
    if not sc_path.is_file():
        raise SystemExit("selfcheck.json missing; run selfcheck first")
    sc = load_json(sc_path)
    if sc["g1_pips"]["verdict"] != "pass" or \
            sc["g2_lut_init"]["verdict"] != "pass":
        raise SystemExit("selfcheck gates not passed; stop")
    for name in ("wp6a_dump.nload.tsv", "wp6a_dump.loadpins.tsv",
                 "wp6a_dump.sitepips.tsv"):
        f = design_dir / name
        if not f.is_file() or f.stat().st_size == 0:
            raise SystemExit("missing or empty export: %s" % f)

    t_all = time.time()
    print("[%s] loading exports (v2) ..." % dname)
    xd = ExportData(design_dir)
    dd = DownstreamData(design_dir)
    n = _parse_loadpins(dd)
    _resolve_pinloads(dd, xd)
    print("[%s] exports loaded (xd %.1f s, dd %.1f s, NLD=%d NLP=%d USP=%d, "
          "spmap=%d)" % (dname, xd.seconds, dd.seconds, n[0], n[1], n[2],
                         len(dd._spmap)))
    cols, lo = bm.load_columns()
    db = bm.load_db()

    singles = []
    for line in retry_read(OUT / dname / "campaign.jsonl").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("kind") == "single" and not r.get("void") and r.get("target"):
            singles.append(r)
    print("[%s] %d single shots" % (dname, len(singles)))

    pool_versions = Counter(s.get("pool_version") for s in singles)
    results = []
    st = Counter()
    for s in singles:
        t = s["target"][0]
        lsb = lsb_of(t["bt"], s.get("pool_version"))
        r = bm.map_bit(cols, lo, db, t["la"], t["wd"], lsb)
        det = []
        v1_all = set()
        for hit in r["hits"]:
            a = attribute_hit_downstream_v2(hit, xd, dd)
            lv1 = loads_v1_of_hit(hit, xd, dd)
            if lv1:
                v1_all |= lv1
            if a:
                det.append(a)
        det_c = []
        for h in det:
            c = {"resource": h["resource"], "feature": h["feature"],
                 "tags": h["tags"], "rule": h.get("rule"),
                 "attrib": [list(x) for x in sorted(
                     {tuple(a) for a in h["attrib"]},
                     key=lambda a: (a[0], a[1] if a[1] is not None else -1,
                                    a[2] if a[2] is not None else -1))],
                 "n_objects": len(h["objects"]),
                 "objects": h["objects"][:8]}
            for key in ("net", "net_type", "net_category", "pip", "drivers",
                        "net_category_downstream"):
                if h.get(key):
                    v = h[key]
                    c[key] = v[:8] if isinstance(v, list) else v
            det_c.append(c)
        v2_set = {c for h in det for c in h["objects"]}
        entry = {
            "index": s.get("index"),
            "category": s.get("category"),
            "la": t["la"], "wd": t["wd"], "bt": t["bt"], "lsb": lsb,
            "pool_version": s.get("pool_version"),
            "map_status": r["status"],
            "column_tile_kind": r.get("tile_kind"),
            "tile": corrected_tile(r.get("tile_kind"), r.get("tile_x"),
                                   r.get("row", 0), r.get("clock_region"))
            if r.get("tile_kind") else t.get("tile"),
            "hits_detail": det_c,
            "loads_v2": sorted(v2_set),
            "n_loads_v1": len(v1_all),
            "n_loads_v2": len(v2_set),
            "v2_not_in_v1": len(v2_set - v1_all) if v1_all else 0,
        }
        if det:
            cats = {h["resource"] for h in det}
            entry["resource"] = "+".join(sorted(cats))
            entry["feature"] = det[0]["feature"]
            att = {tuple(a) for h in det for a in h["attrib"]}
            entry["attrib"] = [list(a) for a in sorted(
                att, key=lambda a: (a[0], a[1] or -1,
                                    a[2] if a[2] is not None else -1))]
            entry["tags"] = sorted({tg for h in det for tg in h["tags"]})
        else:
            entry["resource"] = "未解析"
            ta = xd.tile_table.get(entry["tile"], {})
            entry["attrib"] = ta.get("attrib", [])
            entry["feature"] = None
            entry["tags"] = ["未解析"]
        entry["unique"] = len({a[1] for a in entry["attrib"]
                               if a[1] is not None}) == 1
        repl = {a[2] for a in entry["attrib"]
                if a[0] == "tmr_stage" and a[2] is not None}
        entry["tmr_replica_unique"] = len(repl) == 1 if repl else None
        if entry["tmr_replica_unique"]:
            entry["tmr_replica"] = repl.pop()
        results.append(entry)
        st[entry["resource"]] += 1

    # v1/v2 comparison: v1 loads recomputed live in this same run; the
    # section-8 v1 JSON (read-only) is used for attrib-level comparison.
    shrunk = multi2uni = changed = v2_extra = 0
    shrink_examples = []
    v1m = {}
    v1p_gz = OUT / dname / "bit_attrib_downstream.json.gz"
    v1p = OUT / dname / "bit_attrib_downstream.json"
    if v1p_gz.is_file() or v1p.is_file():
        v1 = load_json(v1p)      # load_json prefers the .gz sibling
        v1m = {(e["la"], e["wd"], e["bt"]): e for e in v1.get("shots", [])}
    for e in results:
        key = (e["la"], e["wd"], e["bt"])
        n1, n2 = e["n_loads_v1"], e["n_loads_v2"]
        e["load_shrunk"] = bool(n1) and n2 < n1
        if e["load_shrunk"]:
            shrunk += 1
            if len(shrink_examples) < 30:
                shrink_examples.append(
                    {"index": e["index"], "lfa": list(key),
                     "n_v1": n1, "n_v2": n2})
        if e.get("v2_not_in_v1"):
            v2_extra += 1
        o = v1m.get(key)
        if o is not None and [tuple(a) for a in e["attrib"]] != \
                [tuple(a) for a in o.get("attrib", [])]:
            changed += 1
        if e["load_shrunk"]:
            old_repl = {a[2] for a in (o or {}).get("attrib", [])
                        if a[0] == "tmr_stage" and a[2] is not None}
            new_repl = {a[2] for a in e["attrib"]
                        if a[0] == "tmr_stage" and a[2] is not None}
            if len(old_repl) > 1 and len(new_repl) == 1:
                e["tmr_multi_to_unique"] = True
                multi2uni += 1
    diff = {"available": True,
            "load_shrunk_bits": shrunk,
            "tmr_multi_to_unique": multi2uni,
            "attrib_changed_shots": changed,
            "v2_not_subset_shots": v2_extra,
            "shrink_examples": shrink_examples,
            "rule": "v1 loads recomputed live (bypass-style sitepin rule)"}

    g4 = check_g4(results, singles)
    print("[%s] G4(v2): corrected=%d judged=%d stage=%.4f lane=%s (%s)"
          % (dname, g4["corrected_singles"], g4["unique_stage_judged"],
             g4["stage_consistent_frac"] or 0,
             g4.get("lane_consistent_frac"), g4["verdict"]))
    print("[%s] v1/v2: shrunk=%d multi->unique=%d attrib_changed=%d "
          "v2_not_subset=%d" % (dname, shrunk, multi2uni, changed, v2_extra))

    out = {
        "design": dname,
        "step": "WP6 section-16 v2 downstream attribution "
                "(direction + pin-level loads)",
        "rule": "downstream_v2",
        "date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "campaign_shots": len(singles),
        "pool_versions": dict(pool_versions),
        "map_status": dict(Counter(r["map_status"] for r in results)),
        "resource_categories": dict(st.most_common()),
        "g4_telemetry_downstream_v2": g4,
        "loadpin_rows": {"NLD": n[0], "NLP": n[1], "USP": n[2]},
        "resolve_stats": dict(dd._spstats),
        "v1_v2_diff": diff,
        "shots": results,
        "runtime_seconds": round(time.time() - t_all, 1),
    }
    opath = OUT / dname / "bit_attrib_downstream_v2.json"
    opath.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("[%s] wrote %s (%.1f s)" % (dname, opath, time.time() - t_all))
    return 0 if g4["verdict"] != "fail" else 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("selfcheck")
    p.add_argument("design", choices=sorted(DESIGNS))
    p.add_argument("--bit-mode", choices=["auto", "m", "M", "N"], default="auto",
                   help="auto: per pool_version (v2.1 -> bt, v2 -> 31-bt); "
                        "m: both M and N; M: LSB=31-bt; N: LSB=bt")
    p.add_argument("--input-dir", default=None,
                   help="read wp6a_dump.*.tsv from this dir instead of "
                        "results/wp6/<design>")
    p.set_defaults(fn=cmd_selfcheck)
    p = sub.add_parser("attrib")
    p.add_argument("design", choices=sorted(DESIGNS))
    p.add_argument("--input-dir", default=None)
    p.set_defaults(fn=cmd_attrib)
    p = sub.add_parser("attrib_downstream")
    p.add_argument("design", choices=sorted(DESIGNS))
    p.add_argument("--input-dir", default=None)
    p.set_defaults(fn=cmd_attrib_downstream)
    p = sub.add_parser("attrib_v2")
    p.add_argument("design", choices=sorted(DESIGNS))
    p.add_argument("--input-dir", default=None)
    p.set_defaults(fn=cmd_attrib_v2)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
