"""WP6 mock-board full-branch test (WP6 3.4: 用模拟板对象跑通全部分支).

Drives wp6_sem_campaign phases against a scripted MockBoard (duck-typed like
(BoardHost, Wire): sem_cmd / run_shot / health_shot / reprogram / reconnect)
with a tiny fake pool/L in a tmp dir ($WP6_OUT / --out), exercising every
recovery-gradient branch of WP6 section 8:

    normal / noimpact, corrected, 误纠 silent, flagged, flagged_but_clean,
    stuck (run_error), write error, SEM NAK (void), host timeout (void +
    standby), repair-report mismatch (path repeat_O), repair FC 40 (path
    reprogram), post-repair health fail once (path repeat_O), persistent
    health fail (path reprogram), shell reset mid-campaign (8.6), TMR
    silent override (三模输出出错一律记静默), pair failure -> repeat + two
    single reprobes, repeatability mismatch listing.

All campaign artifacts land in a tmp dir; only this summary log goes to
results/wp6/mock_test.log.

Usage:
    python scripts/k7_mod/wp6_mock_test.py [--out /tmp/wp6_mock]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))

import wp6_sem_campaign as camp

OUT_BEAT_BYTES = 27
FRAME_BEATS = 256
OUT_FRAME_BYTES = FRAME_BEATS * OUT_BEAT_BYTES
N_FRAMES = 10
GOLDEN_BYTES = N_FRAMES * OUT_FRAME_BYTES

PASS = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    PASS.append((name, bool(cond)))
    print("[%s] %s %s" % (status, name, detail), flush=True)
    if not cond:
        raise AssertionError("%s %s" % (name, detail))


# ---------------------------------------------------------------- MockBoard

def _tel(stage=None, lane=None, det=0, cor=0, unc=0, flags=0):
    return {"det_cnt": det, "cor_cnt": cor, "unc_cnt": unc,
            "err_flags": flags, "last_loc": 0 if lane is None else lane,
            "last_class": 0, "last_stage": 0 if stage is None else stage - 1,
            "last_beat": 0, "last_frame": 0, "last_win": 0, "out_commit": 0,
            "frames_committed": N_FRAMES, "target_frames": N_FRAMES,
            "arch": 5, "ing_active": 0, "rstate": 0}


class MockBoard:
    """Scripted board: behaviors keyed by injected lfa_hex (+ pair keys)."""

    def __init__(self, golden, behaviors, fail_pairs=()):
        self.golden_bytes = golden
        self.behaviors = behaviors          # {lfa_hex: behavior dict}
        self.fail_pairs = {frozenset(p) for p in fail_pairs}
        self.injected = []
        self.cmd_log = []
        self.reprogrammed = 0
        self.reconnects = 0
        self.closed = False
        self._health_fail_once = False
        self._health_comm_once = False
        self._persistent_health_fail = False
        self._mismatch_report_used = False

    # -- campaign interface -----------------------------------------------
    def golden(self):
        return self.golden_bytes

    def sem_cmd(self, cmd, timeout_ms=4000, flags=0):
        if isinstance(cmd, bytes):
            cmd = cmd.decode("ascii", "replace")
        cmd = cmd.strip()
        self.cmd_log.append("sem:" + (cmd or "<poll>"))
        if cmd == "I":
            self.injected = []
            return "I ACK"
        if cmd.startswith("N "):
            lfa = cmd.split()[1].upper()
            beh = self.behaviors.get(lfa, {})
            if beh.get("inject") == "sem_nak":
                raise RuntimeError("SEM NAK seq=3")
            self.injected.append(lfa)
            if beh.get("health") == "fail_once":
                self._health_fail_once = True
            elif beh.get("health") == "persistent":
                self._persistent_health_fail = True
            elif beh.get("health") == "comm_once":
                self._health_comm_once = True
            return "N ACK"
        if cmd == "O":
            return self._repair_reply()
        if cmd == "S":
            return "S ACK"
        return ""

    def sem_pages(self, n_pages=4):
        return [""] * n_pages

    def _repair_reply(self):
        beh = (self.behaviors.get(self.injected[-1], {}) if self.injected
               else {})
        rep = beh.get("report", "ok")
        if rep == "fc40":
            return "O ACK\r\nFC 40 ..."
        lines = []
        for lfa in self.injected:
            u = camp.unpack_sem_n_local(lfa)
            la, wd, bt = u["lfa"], u["word"], u["bit"]
            if rep == "mismatch" and not self._mismatch_report_used:
                self._mismatch_report_used = True
                bt ^= 1                          # wrong bit in the report
            lines.append("LA %08X WD %02X BT %02X COR" % (la, wd, bt))
        return "O ACK\r\n" + "\r\n".join(lines)

    def run_shot(self):
        self.cmd_log.append("shot:%s" % "+".join(self.injected))
        behs = [self.behaviors.get(l, {}) for l in self.injected]
        if len(self.injected) == 2:
            return self._pair_shot(behs)
        beh = behs[0] if behs else {}
        name = beh.get("shot", "noimpact")
        if name == "host_timeout":
            return {"ok": False, "sha256": None,
                    "err": "UART ACK 超时 cmd=0x01", "n_diff": None}
        if name == "stuck_run":
            return {"ok": False, "sha256": None,
                    "err": "RuntimeError: NAK seq=9 (run_error watchdog)",
                    "n_diff": None}
        if name == "write_err":
            return {"ok": False, "sha256": None,
                    "err": "ingest verify failed: committed=3 "
                           "target=10 want=10",
                    "n_diff": None}
        tel = dict(beh.get("tel") or _tel())
        ok = name in ("noimpact", "corrected", "flagged_but_clean")
        data = self.golden_bytes if ok else self._corrupt()
        return {"ok": ok, "err": None, "data": data,
                "sha256": hashlib.sha256(data).hexdigest(),
                "n_diff": 0 if ok else 5, "tail_zero": True,
                "write_s": 0.01, "run_s": 0.02, "read_s": 0.01,
                "telemetry": tel}

    def _pair_shot(self, behs):
        if frozenset(self.injected) in self.fail_pairs:
            beh = behs[0]
            tel = dict(beh.get("tel") or _tel())
            tel.update({"det_cnt": 0, "cor_cnt": 0, "unc_cnt": 0,
                        "err_flags": 0})
            data = self._corrupt()
            return {"ok": False, "err": None, "data": data,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "n_diff": 7, "tail_zero": True,
                    "write_s": 0.01, "run_s": 0.02, "read_s": 0.01,
                    "telemetry": tel}
        beh = behs[0]
        tel = dict(beh.get("tel") or _tel(stage=beh.get("stage", 3)))
        tel.update({"cor_cnt": 1, "unc_cnt": 0, "err_flags": 0})
        return {"ok": True, "err": None, "data": self.golden_bytes,
                "sha256": hashlib.sha256(self.golden_bytes).hexdigest(),
                "n_diff": 0, "tail_zero": True,
                "write_s": 0.01, "run_s": 0.02, "read_s": 0.01,
                "telemetry": tel}

    def health_shot(self):
        self.cmd_log.append("health")
        if self._health_comm_once:
            self._health_comm_once = False
            raise RuntimeError("UART ACK 超时 cmd=0x01 (shell lost)")
        if self._health_fail_once:
            self._health_fail_once = False
            return self._health_rec(ok=False, cor=1,
                                    note="post-repair health fail once")
        if self._persistent_health_fail:
            return self._health_rec(ok=False, cor=1,
                                    note="persistent until reprogram")
        return self._health_rec(ok=True)

    def _health_rec(self, ok, cor=0, note=""):
        tel = _tel(cor=cor)
        return {"ok": ok, "err": None if ok else note,
                "n_diff": 0 if ok else 4, "tail_zero": True,
                "sha256": hashlib.sha256(self.golden_bytes).hexdigest(),
                "telemetry": tel, "counters_zero": ok and cor == 0}

    def reprogram(self, bit_path, bit_sha):
        if camp.sha256_file(bit_path) != bit_sha:
            raise camp.CampaignStop("mock reprogram: SHA mismatch")
        self.reprogrammed += 1
        self.injected = []
        self._persistent_health_fail = False
        self.cmd_log.append("reprogram")

    def reconnect(self):
        self.reconnects += 1
        self.cmd_log.append("reconnect")
        self.reconnects += 1
        self.cmd_log.append("reconnect")

    def close(self):
        self.closed = True

    # -- helpers ------------------------------------------------------------
    def _corrupt(self):
        buf = bytearray(self.golden_bytes)
        for i in range(64):
            buf[i] ^= 0x5A
        return bytes(buf)


# ------------------------------------------------------------ fake artifacts

def make_golden():
    rng = random.Random(20261016)
    return bytes(rng.randrange(256) for _ in range(GOLDEN_BYTES))


def make_ebd(path, n_frames, words=(0, 1, 2, 3), mask=0xF0000000):
    """Fake .ebd: n_frames+1 frames (frame 0 = leading pad), `words` carry
    the same essential-bit mask each."""
    lines = []
    for fr in range(n_frames + 1):
        for wd in range(camp.WPW):
            w = mask if (fr > 0 and wd in words) else 0
            lines.append(format(w, "032b"))
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def make_ftab(path, n_las):
    cols = [{"clock_region": "X0Y4", "tile_x": la, "top": 0, "row": 1,
             "column": la, "tile_kind": "CLBLL_L", "frame_lo": la,
             "frame_hi": la, "n_minors": 1,
             "lfa_source": "measured", "n_dead_probes": 0,
             "cr_label_v1": "X0Y4"} for la in range(n_las)]
    path.write_text(json.dumps({"part": "xc7k325tffg900-2",
                                "status": "MOCK", "map_kind": "mock",
                                "source": "mock", "n_columns": 1,
                                "n_measured": 1, "n_dead_probe_lfas": 0,
                                "dead_lfas": [], "columns": cols,
                                "cr_space": {}, "cr_label_note": "mock"}),
                    encoding="utf-8")


# --------------------------------------------------------------- behaviors

def beh_corrected(stage, lane=None, replica=None, flags=0):
    b = {"shot": "corrected", "tel": _tel(stage=stage, lane=lane, cor=1,
                                           flags=flags)}
    b["stage"], b["lane"], b["replica"] = stage, lane, replica
    return b


def singles_script():
    """Behavior table for the 40-shot singles campaign (draw-order index)."""
    s = {
        8: beh_corrected(3, lane=1),
        9: beh_corrected(3, lane=4),
        10: beh_corrected(2, lane=0, flags=1),      # err_flags!=0 -> not L
        11: beh_corrected(8, replica=0),
        12: {"shot": "silent", "tel": _tel(stage=2, lane=1, cor=1)},  # 误纠
        13: {"shot": "silent", "tel": _tel(stage=6, lane=2)},
        14: {"shot": "flagged", "tel": _tel(stage=4, lane=0, unc=1)},
        15: {"shot": "silent_tmr_unc",              # mismatch + unc but TMR
             "tel": _tel(stage=9, unc=1)},          # -> silent + override
        16: {"shot": "flagged_but_clean",
             "tel": _tel(stage=7, lane=3, unc=1)},
        17: {"shot": "stuck_run"},
        18: {"shot": "write_err"},
        19: {"shot": "host_timeout"},               # void + standby
        20: {"inject": "sem_nak"},                  # void + standby
        21: {"shot": "noimpact", "report": "mismatch"},   # repeat_O
        22: {"shot": "noimpact", "report": "fc40"},       # reprogram
        23: {"shot": "noimpact", "health": "fail_once"},  # repeat_O
        24: {"shot": "noimpact", "health": "persistent"},  # reprogram
        25: {"shot": "noimpact", "health": "comm_once"},   # 8.6 shell reset
        26: beh_corrected(8, replica=1),
        27: beh_corrected(9, replica=2),
        28: beh_corrected(3, lane=1),
        29: beh_corrected(8, replica=0),
    }
    return s


# --------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=None,
                    help="mock artifact dir (default: fresh tmp under /tmp)")
    args = ap.parse_args(argv)
    tmp = Path(args.out or tempfile.mkdtemp(prefix="wp6_mock_"))
    tmp.mkdir(parents=True, exist_ok=True)
    print("mock dir: %s" % tmp, flush=True)

    # WP6-G0 M-path: bit-order assertion (synthetic single-bit word)
    check_bit_order_lsb(tmp)

    # -- fake frozen inputs + prereg ----------------------------------------
    design = "P0"
    fdir = tmp / "frozen" / "P1_WP4R2"
    fdir.mkdir(parents=True, exist_ok=True)
    bit_file = fdir / "k7_paper_top.bit"
    bit_file.write_bytes(b"MOCK-FROZEN-BITSTREAM")
    bit_sha = hashlib.sha256(bit_file.read_bytes()).hexdigest()
    ebd_file = fdir / "k7_paper_top.ebd"
    make_ebd(ebd_file, n_frames=24)
    ebd_sha = hashlib.sha256(ebd_file.read_bytes()).hexdigest()
    manifest = tmp / "freeze_manifest.md"
    rows = "\n".join(
        "| %s | %s | `%s` | `%s` | `%s` | `%s` |" % (d, z, b, e, "ab" * 32,
                                                     "cd" * 32)
        for d, z, b, e in [
            ("P1_WP4R2", "P0（p1）", bit_sha, ebd_sha),
            ("P2_WP4R2", "P1（p2）", "22" * 32, "33" * 32),
            ("S3_WP4R2", "S0（s3）", "44" * 32, "55" * 32),
            ("S2_WP4R2", "S1（s2）", "66" * 32, "77" * 32)])
    manifest.write_text("# mock freeze manifest\n\n"
                        "| 目录 | 设计 | .bit SHA-256 | .ebd | .ll | 检查点 |\n"
                        "| --- | --- | --- | --- | --- | --- |\n"
                        + rows + "\n", encoding="utf-8")
    camp.OUT = tmp
    camp.FREEZE_MANIFEST = manifest
    camp.FREEZE_ROOT = tmp / "frozen"
    camp.FRAME_TABLE_V2 = tmp / "frame_table_k7_v2.json"
    make_ftab(camp.FRAME_TABLE_V2, n_las=23)

    camp.cmd_prereg(argparse.Namespace(out=tmp))
    prereg = camp.load_prereg(tmp)
    prereg["frozen_inputs"][design]["bit"] = str(bit_file)
    prereg["frozen_inputs"][design]["ebd"] = str(ebd_file)
    prereg["designs"][design]["bit_sha"] = bit_sha
    prereg["frame_table"]["path"] = str(camp.FRAME_TABLE_V2)
    (tmp / "prereg.json").write_text(json.dumps(prereg, ensure_ascii=False,
                                                indent=2), encoding="utf-8")
    check("prereg no board", (tmp / "prereg.json").is_file())

    # -- determinism of the draw --------------------------------------------
    pool = camp.build_pool(design, prereg, tmp)
    seed = camp.design_seed(prereg["seeds"]["singles"], design)
    prim, standby = camp.draw_singles(pool, 40, 2, seed)
    prim2, _ = camp.draw_singles(pool, 40, 2, seed)
    check("singles draw deterministic", prim == prim2)
    check("pool big enough", len(pool) >= 42, "pool=%d" % len(pool))
    # unit: same-frame pair rejection
    fakeL = [{"la": 1, "wd": 0, "bt": 0}, {"la": 1, "wd": 3, "bt": 2},
             {"la": 2, "wd": 0, "bt": 0}]
    _p, skipped = camp.draw_natural_pairs(fakeL, 1, 7)
    check("same-frame pair rejection counted", skipped >= 1,
          "skipped=%d" % skipped)

    # -- map behaviors by draw order ----------------------------------------
    idx_beh = singles_script()
    behaviors = {}
    for i, b in enumerate(prim):
        if i in idx_beh:
            behaviors[b["lfa_hex"]] = dict(idx_beh[i])
    for b in standby:
        behaviors.setdefault(b["lfa_hex"], {"shot": "noimpact"})

    golden = make_golden()
    board = MockBoard(golden, behaviors)
    sizes = {"n_singles": 40, "pilot": 12, "standby": 2, "health_gate": 2}
    ns = argparse.Namespace(design=design, com="MOCK", mock_board=board,
                            out=tmp)
    camp.cmd_singles(ns, sizes=sizes)

    log_path = tmp / camp.DESIGN_DIR[design] / "campaign.jsonl"
    recs = camp.read_jsonl(log_path)
    single_recs = [r for r in recs if r["kind"] == "single"]
    check("singles count (40 + 2 standby)", len(single_recs) == 42,
          "got %d" % len(single_recs))
    check("standby consumed x2",
          sum(1 for r in single_recs if r.get("standby")) == 2)
    voids = [r for r in single_recs if r.get("void")]
    check("voids = host timeout + SEM NAK", len(voids) == 2,
          [r.get("err") for r in voids])

    REQ = ["design", "bitstream_sha", "frame_table_version", "pool_version",
           "seed", "index", "kind", "subkind", "target", "category",
           "telemetry", "err_flags", "output_diff_summary", "sem", "health",
           "recovery_path", "shell_resets", "wall_s", "t"]
    missing = [(i, k) for i, r in enumerate(recs)
               for k in REQ if k not in r]
    check("jsonl required fields (3.4)", not missing, str(missing[:4]))

    by_idx = {r["index"]: r for r in single_recs if not r.get("standby")}
    check("stuck run_error", by_idx[17]["category"] == "stuck")
    check("write error stuck", by_idx[18]["category"] == "stuck")
    check("flagged", by_idx[14]["category"] == "flagged")
    check("flagged_but_clean", by_idx[16]["category"] == "flagged_but_clean")
    sil12 = by_idx[12]
    check("误纠 silent (cor>0)", sil12["category"] == "silent"
          and sil12["telemetry"]["cor_cnt"] > 0)
    check("TMR override silent", by_idx[15]["category"] == "silent"
          and by_idx[15]["tmr_override"] and by_idx[15]["tmr_stage"])
    check("repair mismatch -> repeat_O", by_idx[21]["recovery_path"]
          == "repeat_O")
    check("FC40 -> reprogram", by_idx[22]["recovery_path"] == "reprogram")
    check("health fail once -> repeat_O", by_idx[23]["recovery_path"]
          == "repeat_O")
    check("persistent fail -> reprogram", by_idx[24]["recovery_path"]
          == "reprogram")
    check("shell reset recorded", by_idx[25]["shell_resets"]
          and by_idx[25]["recovery_path"] == "none")
    check("reprograms == 2", board.reprogrammed == 2)
    check("repair reports confirmed for corrected",
          all((r["sem"].get("repair_match")) for i, r in by_idx.items()
              if r["category"] == "corrected"))
    check("output diff summary on silent",
          by_idx[13]["output_diff_summary"]["n_beats"] > 0)
    check("wrong-output rec carries n_diff/tail_zero",
          by_idx[13]["n_diff"] > 0
          and by_idx[13]["tail_zero"] is True)
    check("diff summary not an error dict",
          "error" not in by_idx[13]["output_diff_summary"])
    import gzip as _gz
    od_path = tmp / camp.DESIGN_DIR[design] / "output_diffs.jsonl.gz"
    od_entries = [json.loads(l)
                  for l in _gz.open(od_path, "rt", encoding="utf-8")]
    idx_with_summary = {r["index"] for r in single_recs
                        if r.get("output_diff_summary")}
    check("per-beat diffs gz: one entry per wrong-output shot",
          {e["index"] for e in od_entries if e["kind"] == "single"}
          == idx_with_summary,
          str({e["index"] for e in od_entries} ^ idx_with_summary))
    check("gz entry holds beat-level diffs",
          all(e["diffs"] and "beat" in e["diffs"][0]
              for e in od_entries))

    # -- resume: nothing re-recorded ----------------------------------------
    board2 = MockBoard(golden, {})
    ns2 = argparse.Namespace(design=design, com="MOCK", mock_board=board2,
                             out=tmp)
    camp.cmd_singles(ns2, sizes=sizes)
    recs_after = camp.read_jsonl(log_path)
    check("resume adds no shots", len(recs_after) == len(recs),
          "%d -> %d" % (len(recs), len(recs_after)))

    # -- repeatability (flip one picked L bit) -------------------------------
    Lprov = camp.provisional_L(design, tmp)
    check("provisional L = 7 bits", len(Lprov) == 7, "got %d" % len(Lprov))
    rseed = camp.design_seed(prereg["seeds"]["repeat"], design)
    rng = random.Random(rseed)
    pick_l = rng.sample(Lprov, min(4, len(Lprov)))
    flip = pick_l[0]["lfa_hex"]
    behaviors[flip] = {"shot": "silent", "tel": _tel(stage=3, lane=1)}
    board3 = MockBoard(golden, behaviors)
    ns3 = argparse.Namespace(design=design, com="MOCK", mock_board=board3,
                             out=tmp)
    camp.cmd_repeat(ns3, sizes={"n_repeat": 4, "health_gate": 2})
    rep = json.loads((tmp / camp.DESIGN_DIR[design]
                      / "repeat_report.json").read_text(
        encoding="utf-8"))
    check("repeat mismatch listed (1)", len(rep["mismatches"]) == 1,
          json.dumps(rep["mismatches"], ensure_ascii=False)[:120])

    # -- freeze L -------------------------------------------------------------
    ns4 = argparse.Namespace(design=design, i_am_sure=False, out=tmp)
    try:
        camp.cmd_freeze_L(ns4)
        check("freeze_L requires --i-am-sure", False)
    except SystemExit:
        check("freeze_L requires --i-am-sure", True)
    # tile attribution: replicas for TMR L bits + one stage conflict
    # tile attribution (wp6_tile_table.py schema): one tile per L bit,
    # replicas for the TMR bits (from the script), one forced stage
    # conflict (kept + flagged per WP6 2.3)
    replica_by_lfa = {prim[i]["lfa_hex"]: idx_beh[i].get("replica")
                      for i in (11, 26, 27, 29)}
    tiles = {}
    for b in camp.provisional_L(design, tmp):
        key = b["tile"]                      # recorded per shot by the pool
        if b["stage"] in camp.DESIGNS[design]["ecc_stages"]:
            entry = ["protected_datapath", b["stage"], b.get("lane")]
        else:
            entry = ["tmr_stage", b["stage"],
                     replica_by_lfa.get(b["lfa_hex"])]
        if (b["la"], b["wd"], b["bt"]) == (pick_l[-1]["la"],
                                           pick_l[-1]["wd"],
                                           pick_l[-1]["bt"]):
            entry[1] = 8 if b["stage"] != 8 else 9     # force a conflict
        if key in tiles:                  # same tile: merge attrib sets
            tiles[key]["attrib"].append(entry)
        else:
            tiles[key] = {"attrib": [entry], "special": []}
    (tmp / camp.DESIGN_DIR[design] / "tile_table.json").write_text(
        json.dumps({"_design": camp.DESIGN_DIR[design], "tiles": tiles},
                   ensure_ascii=False), encoding="utf-8")
    ns5 = argparse.Namespace(design=design, i_am_sure=True, out=tmp)
    camp.cmd_freeze_L(ns5)
    freeze = json.loads((tmp / camp.DESIGN_DIR[design]
                         / "L_freeze.json").read_text(
        encoding="utf-8"))
    check("L frozen n=7", freeze["n"] == 7, "n=%d" % freeze["n"])
    expect_fbar = (3 / 7) ** 2 * (1 - (2 / 3) ** 2 - (1 / 3) ** 2) * 2
    check("f_bar formula value", abs(freeze["f_bar"] - expect_fbar) < 1e-9,
          "f_bar=%.6f expect=%.6f" % (freeze["f_bar"], expect_fbar))
    check("stage conflict flagged", freeze["stage_conflict_count"] == 1,
          "count=%d" % freeze["stage_conflict_count"])

    # -- pairs ---------------------------------------------------------------
    Lf = camp.load_L_freeze(design, tmp)
    seed_n = camp.design_seed(prereg["seeds"]["pairs_natural"], design)
    natural, _sk = camp.draw_natural_pairs(Lf, 6, seed_n)
    fail_pair = [b["lfa_hex"] for b in natural[2]]
    board4 = MockBoard(golden, behaviors, fail_pairs=[fail_pair])
    ns6 = argparse.Namespace(design=design, com="MOCK", mock_board=board4,
                             out=tmp)
    psizes = {"n_natural": 6, "stress_ecc": 1, "stress_tmr": 2,
              "control_ecc": 1, "control_tmr": 1, "health_gate": 2}
    camp.cmd_pairs(ns6, sizes=psizes)
    recs = camp.read_jsonl(log_path)
    kinds = {}
    for r in recs:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    check("natural pairs = 6", kinds.get("pair_natural") == 6,
          str(kinds))
    check("failed pair -> repeat + 2 reprobes",
          kinds.get("pair_natural_repeat") == 1
          and kinds.get("single_reprobe") == 2, str(kinds))
    check("stress 3 + control 1 (ecc deficit by same-frame rule)",
          kinds.get("pair_stress") == 3 and kinds.get("pair_control") == 1,
          str(kinds))
    deficit = json.loads((tmp / camp.DESIGN_DIR[design]
                          / "pair_deficit.json").read_text(
        encoding="utf-8"))
    check("control ecc deficit reported", deficit["control"].get("ecc") == 1,
          json.dumps(deficit, ensure_ascii=False))
    npair = next(r for r in recs if r["kind"] == "pair_natural")
    check("pair repair confirms BOTH bits",
          npair["sem"]["repair_match"] is True)
    od2 = [json.loads(l) for l in _gz.open(od_path, "rt", encoding="utf-8")]
    fail_pair = next((r for r in recs
                      if r["kind"] == "pair_natural"
                      and r.get("output_diff_summary")), None)
    check("failed pair -> per-beat diffs in gz",
          fail_pair is not None
          and any(e["kind"] == "pair_natural"
                  and e["index"] == fail_pair["index"] for e in od2))

    # -- natural-only pairs (WP6-G0 M path / addendum s.3) --------------------
    tmp_no = tmp / "natural_only_run"
    (tmp_no / camp.DESIGN_DIR[design]).mkdir(parents=True, exist_ok=True)
    for fn in ("prereg.json",):
        (tmp_no / fn).write_text((tmp / fn).read_text(encoding="utf-8"),
                                 encoding="utf-8")
    (tmp_no / camp.DESIGN_DIR[design] / "L_freeze.json").write_text(
        (tmp / camp.DESIGN_DIR[design] / "L_freeze.json").read_text(
            encoding="utf-8"), encoding="utf-8")
    saved_out = camp.OUT
    camp.OUT = tmp_no
    try:
        ns_no = argparse.Namespace(design=design, com="MOCK",
                                   mock_board=MockBoard(golden, behaviors),
                                   out=tmp_no, natural_only=True)
        camp.cmd_pairs(ns_no, sizes={"n_natural": 4, "health_gate": 1})
    finally:
        camp.OUT = saved_out
    recs_no = camp.read_jsonl(tmp_no / camp.DESIGN_DIR[design]
                              / "campaign.jsonl")
    kinds_no = {}
    for r in recs_no:
        kinds_no[r["kind"]] = kinds_no.get(r["kind"], 0) + 1
    check("natural-only: 4 natural pairs, no stress/control",
          kinds_no.get("pair_natural") == 4
          and kinds_no.get("pair_stress") is None
          and kinds_no.get("pair_control") is None,
          str(kinds_no))

    # -- telemetry arch assertion (author ruling 2026-10-17) ------------------
    check("arch assert passes on match/missing",
          camp.assert_tel_arch("P0", {"arch": 5}) is None
          and camp.assert_tel_arch("P0", {"err_flags": 0}) is None
          and camp.assert_tel_arch("P0", None) is None)
    try:
        camp.assert_tel_arch("P0", {"arch": 3})
        check("arch assert raises on mismatch", False)
    except camp.CampaignStop:
        check("arch assert raises on mismatch", True)
    beh_wrong = {prim[0]["lfa_hex"]: {"shot": "noimpact",
                                    "tel": dict(_tel(), arch=3)}}
    ctx_wrong = camp.Ctx(design, prereg, MockBoard(golden, beh_wrong),
                         out=tmp)
    try:
        camp.inject_shot(ctx_wrong, "single", None, [prim[0]], 900, 1)
        check("shot arch mismatch -> CampaignStop", False)
    except camp.CampaignStop:
        check("shot arch mismatch -> CampaignStop", True)

    class _WrongArchHealth(MockBoard):
        def health_shot(self):
            rec = dict(self._health_rec(ok=True))
            rec["telemetry"] = dict(rec["telemetry"], arch=9)
            return rec
    ctx_bad_h = camp.Ctx(design, prereg,
                         _WrongArchHealth(golden, {}), out=tmp)
    try:
        camp.health_pass(ctx_bad_h)
        check("health arch mismatch -> CampaignStop", False)
    except camp.CampaignStop:
        check("health arch mismatch -> CampaignStop", True)

    # -- analysis -------------------------------------------------------------
    import wp6_analyze as ana
    ana.OUT = tmp
    text = ana.analyze_design(design, tmp)
    for marker in ("第 4 节", "第 5 节", "第 6 节", "f̄", "95% CI", "可重复性",
                   "误纠", "对照组", "落点"):
        check("analysis mentions %s" % marker, marker in text)

    # -- summary log -----------------------------------------------------------
    n_pass = sum(1 for _n, ok in PASS if ok)
    summary = [
        "WP6 mock-board full-branch test (%d checks, all passed)" % n_pass,
        "mock dir: %s" % tmp,
        "branches exercised: noimpact/corrected/误纠silent/flagged/"
        + "flagged_but_clean/stuck_run/write_err/host_timeout(void+standby)/"
        + "SEM_NAK(void+standby)/repair_mismatch(repeat_O)/FC40(reprogram)/"
        + "health_fail_once(repeat_O)/health_persistent(reprogram)/"
        + "shell_reset(8.6)/TMR_override/repair_match_multi(pairs)/"
        + "pair_fail(repeat+2 reprobes)/repeat_mismatch/resume/"
        + "arch_mismatch(inject_shot+health_pass)",
        "recovery_path values seen: %s" % sorted({r["recovery_path"]
                                                  for r in recs}),
        "reprograms=%d shell_resets=%d" % (
            sum(1 for r in recs if r.get("recovery_path") == "reprogram"),
            sum(len(r.get("shell_resets") or []) for r in recs)),
        "checks:",
    ] + ["  [PASS] %s" % n for n, ok in PASS if ok]
    log_path_out = ROOT / "results" / "wp6" / "mock_test.log"
    log_path_out.parent.mkdir(parents=True, exist_ok=True)
    log_path_out.write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("wrote %s" % log_path_out, flush=True)
    print("ALL %d CHECKS PASSED" % n_pass, flush=True)
    return 0


def check_bit_order_lsb(tmp):
    """WP6-G0 M-path assertion: a synthetic single-bit .ebd word must give
    an lfa_hex whose bit address is the bit's LSB index (SEM numbering)."""
    import wp6_sem_campaign as wc
    ftab = {"dead_lfas": [],
            "columns": [{"clock_region": "X1Y4", "tile_x": 52,
                         "tile_kind": "CLBLL_L", "frame_lo": 100,
                         "frame_hi": 135, "lfa_source": "measured",
                         "n_dead_probes": 0}]}
    la, wd, k = 100, 7, 13
    words = [[0] * wc.WPW for _ in range(1 + 136)]  # pad + la 0..135
    words[1 + la][wd] = 1 << k
    ebd = tmp / "g0_bitorder.ebd"
    ebd.write_text("\n".join(format(w, "032b") for fr in words for w in fr)
                   + "\n", encoding="ascii")
    ft = tmp / "g0_bitorder_ftab.json"
    ft.write_text(json.dumps(ftab), encoding="utf-8")
    ebd_sha = hashlib.sha256(ebd.read_bytes()).hexdigest()
    prereg = {"designs": {"P0": {"ebd_sha": ebd_sha}},
              "frame_table": {"path": str(ft)},
              "frozen_inputs": {"P0": {"ebd": str(ebd),
                                      "ebd_sha": ebd_sha}}}
    bits = wc.build_pool("P0", prereg, tmp)
    assert len(bits) == 1, "expected exactly one pool bit, got %d" % len(bits)
    b = bits[0]
    dec = wc.unpack_sem_n_local(b["lfa_hex"])
    ok = (dec["lfa"] == la and dec["word"] == wd and dec["bit"] == k
          and b["bt"] == k)
    check("g0_bit_order_lsb", ok,
          "lfa_hex=%s decoded=(%d,%d,%d) expected bit %d"
          % (b["lfa_hex"], dec["lfa"], dec["word"], dec["bit"], k))


if __name__ == "__main__":
    sys.exit(main())
