"""WP6 batch-3 host campaign: single-shot prescreen + two-bit pair campaigns.

Implements tasks/WP6_sem_board.md sections 2 (frozen into results/wp6/prereg.json
by the `prereg` subcommand), 3.4 (jsonl logging), 4 (singles), 4.4 (repeat
check), 5 (natural pairs), 6 (stress + control pairs) and 8 (recovery
gradient / shell reset).  All sampling is deterministic given prereg.json:
same seeds + frozen pool inputs -> same target lists, with no board attached.

Board interaction goes through a small duck-typed interface (sem_cmd /
run_shot / health_shot / reprogram / reconnect) so wp6_mock_test.py can drive
every recovery branch without hardware:

    prereg                     write results/wp6/prereg.json (no board)
    singles <design>           section 4 single-shot campaign
    repeat <design>            section 4.4 repeatability re-shoot
    freeze_L <design>          freeze library L + f_bar (needs --i-am-sure)
    pairs <design>             sections 5 + 6 (needs L_freeze.json)
    resume <design>            continue all remaining phases, skipping shots
                               already recorded in campaign.jsonl

Usage:
    python scripts/k7_mod/wp6_sem_campaign.py prereg
    python scripts/k7_mod/wp6_sem_campaign.py singles P0 --com COM5
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import random
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_cram"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))

OUT = Path(os.environ.get("WP6_OUT", ROOT / "results" / "wp6"))
FRAME_TABLE_V2 = ROOT / "results" / "k7_mod_fft" / "frame_table_k7_v2.json"
FREEZE_MANIFEST = OUT / "freeze_manifest.md"
FREEZE_ROOT = Path(os.environ.get(
    "WP6_FREEZE_ROOT", "/home/xdu/frozen/wp6"))
VIVADO_BIN = Path(os.environ.get(
    "VIVADO_BIN", "/tools/Xilinx/Vivado/2018.3/bin"))
PROGRAM_TCL = ROOT / "vivado" / "program_k7_volatile.tcl"

POOL_VERSION = "v2.1"
WPW = 101                 # words per configuration frame (word 50 = ECC)
ECC_WORD = 50
SEM_MF = 23265            # max frame index of the K7 SEM address space
PB_REGIONS = {"X0Y4", "X0Y5", "X0Y6", "X1Y3", "X1Y4", "X1Y5", "X1Y6"}

# Design table (WP6 section 1 + rtl/paper_fft12 datapaths): stage kinds are
# ECC [6,4,3]-coded stages 1-7 and real-TMR stages 8-10 for P0/S0, real-TMR
# on all ten stages for P1/S1.
DESIGNS = {
    "P0": {"arch": "p1", "frozen_dir": "P1_WP4R2",
           "golden_model": "pfft_exchange",
           "ecc_stages": list(range(1, 8)), "tmr_stages": [8, 9, 10]},
    "P1": {"arch": "p2", "frozen_dir": "P2_WP4R2",
           "golden_model": "pfft_no_exchange",
           "ecc_stages": [], "tmr_stages": list(range(1, 11))},
    "S0": {"arch": "s3", "frozen_dir": "S3_WP4R2", "golden_model": "subfft",
           "ecc_stages": list(range(1, 8)), "tmr_stages": [8, 9, 10]},
    "S1": {"arch": "s2", "frozen_dir": "S2_WP4R2", "golden_model": "subfft",
           "ecc_stages": [], "tmr_stages": list(range(1, 11))},
}
DESIGN_ORDER = ["P0", "P1", "S0", "S1"]
# results/wp6/<design>/ per WP6 section 10 naming (P0_p1 等):
DESIGN_DIR = {"P0": "P0_p1", "P1": "P1_p2", "S0": "S0_s3", "S1": "S1_s2"}
# Telemetry `arch` constants reported by each frozen bitstream (the DUT
# telemetry row carries the design arch id; values verified against the
# clean campaigns).  Author ruling 2026-10-17: assert it on every shot so
# a record can never be taken on the wrong bitstream silently.
EXPECTED_TEL_ARCH = {"P0": 5, "P1": 6, "S0": 3, "S1": 2}
# tile_table.json schema (scripts/k7_mod/wp6_tile_table.py, WP6 3.3):
#   {"tiles": {tile_name: {"attrib": [[category, stage, lane_or_repl]],
#                          "special": [...]}}, ...}
# tile_name = "<frame-table column tile_kind>_X<tile_x>Y<tile_y>",
# row = word//2 (word<50), (word-1)//2 (word>50), word 50 = ECC;
# tile_y = 50*region_y + row (verified against the .ll by row_check.json).
CAT_ZH = {
    "protected_datapath": "受算术码保护的数据通路",
    "tmr_stage": "三模级",
    "shared_control": "共享控制与指数生成",
    "decoder_voter": "译码器与表决器",
    "stage_boundary": "级间寄存器",
    "mixed": "混合",
    "unmapped": "未定",
    "shell_related": "外壳相关",
}

# Fixed, recorded sample seeds (prereg "seeds"; per-design seed documented
# in prereg.json as base + 104729 * design_index).
SEEDS = {
    "singles": 202610161,
    "pairs_natural": 202610162,
    "pairs_stress": 202610163,
    "pairs_control": 202610164,
    "repeat": 202610165,
}

CATEGORY_ZH = {
    "stuck": "卡住", "noimpact": "无影响", "corrected": "已纠正",
    "flagged": "标出的错", "silent": "静默的错",
    "flagged_but_clean": "标出但输出完好", "invalid": "作废",
}
AFFECTED = {"stuck", "flagged", "silent", "flagged_but_clean"}

# Pilot auto-gates (section 7): full-config numbers.
PILOT_N = 300
N_SINGLES = 5000
N_REPEAT_EACH = 50
N_PAIRS_NATURAL = 500
STRESS_QUOTAS = {"P0": {"ecc": 100, "tmr": 50}, "S0": {"ecc": 100, "tmr": 50},
                 "P1": {"tmr": 150}, "S1": {"tmr": 150}}
CONTROL_QUOTAS = {"P0": {"ecc": 30, "tmr": 20}, "S0": {"ecc": 30, "tmr": 20},
                  "P1": {"tmr": 50}, "S1": {"tmr": 50}}


class CampaignStop(RuntimeError):
    """Section 8 step 5 / section 9 stop conditions: halt the whole design."""


def assert_tel_arch(design, tel):
    """Telemetry arch assertion (author ruling 2026-10-17): a telemetry
    row naming a different arch means the wrong bitstream is on the
    board -> CampaignStop.  Telemetry without an arch field is left to
    the health gates."""
    if not tel:
        return
    arch = tel.get("arch")
    if arch is not None and arch != EXPECTED_TEL_ARCH[design]:
        raise CampaignStop(
            "telemetry arch mismatch for %s: expected %d, read %r "
            "(wrong bitstream on the board?)"
            % (design, EXPECTED_TEL_ARCH[design], arch))


# --------------------------------------------------------------------- utils

def now_iso():
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def log(msg):
    print(now_iso(), msg, flush=True)


def design_dir(design):
    d = OUT / DESIGN_DIR[design]
    d.mkdir(parents=True, exist_ok=True)
    return d


def campaign_log_path(design):
    return design_dir(design) / "campaign.jsonl"


def append_jsonl(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_jsonl(path):
    if not path.is_file():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def snapshot_gz(path):
    """Phase-end .gz copy next to the jsonl (never gzip in place)."""
    if not path.is_file():
        return None
    gz_path = path.with_name(path.name + ".gz")
    with open(path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        for chunk in iter(lambda: f_in.read(1 << 20), b""):
            f_out.write(chunk)
    return gz_path


def parse_freeze_manifest(path=None):
    """Parse the freeze manifest table -> {design: {bit_sha, ebd_sha, ...}}."""
    path = Path(path) if path else FREEZE_MANIFEST
    if not path.is_file():
        raise RuntimeError("freeze manifest missing: %s" % path)
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(\S+_WP4R2)\s*\|\s*(P0|P1|S0|S1)", line)
        if not m:
            continue
        shas = re.findall(r"`([0-9a-f]{64})`", line)
        if len(shas) < 4:
            raise RuntimeError("freeze manifest row without 4 sha256: %s"
                               % line)
        rows[m.group(2)] = {"frozen_dir": m.group(1), "bit_sha": shas[0],
                            "ebd_sha": shas[1], "ll_sha": shas[2],
                            "dcp_sha": shas[3]}
    missing = set(DESIGNS) - set(rows)
    if missing:
        raise RuntimeError("freeze manifest missing designs: %s" % missing)
    return rows


# -------------------------------------------------------------------- prereg

def build_prereg(out=OUT):
    """Everything WP6 section 2 + sample sizes/seeds/algorithms (pure
    computation from the freeze manifest + frozen inputs; no board)."""
    manifest = parse_freeze_manifest(out / "freeze_manifest.md")
    inputs = {}
    for design, info in manifest.items():
        fdir = FREEZE_ROOT / info["frozen_dir"]
        inputs[design] = {
            "frozen_dir": str(fdir),
            "bit": str(fdir / "k7_paper_top.bit"),
            "ebd": str(fdir / "k7_paper_top.ebd"),
            "ll": str(fdir / "k7_paper_top.ll"),
            "files_present": {p.name: p.is_file() for p in fdir.glob("*")},
        }
    return {
        "prereg_version": "wp6-batch3-1",
        "date": now_iso(),
        "wp6_task": "tasks/WP6_sem_board.md",
        "freeze_manifest": str(out / "freeze_manifest.md"),
        "frozen_root": str(FREEZE_ROOT),
        "designs": {d: {**DESIGNS[d], **manifest[d]} for d in DESIGN_ORDER},
        "frozen_inputs": inputs,
        "frame_table": {"version": "v2",
                        "path": str(FRAME_TABLE_V2),
                        "exists": FRAME_TABLE_V2.is_file(),
                        "v1_path": str(ROOT / "results" / "k7_mod_fft"
                                        / "frame_table_k7.json")},
        "pool": {"version": POOL_VERSION,
                 "definition": ".ebd essential bits whose LA falls in a "
                               "frame-table v2 core-region column; exclude "
                               "only ECC word 50 and still-dead frames/"
                               "probes (WP6 3.2); no functional pruning",
                 "storage": "deterministic regeneration only (ebd + frame "
                            "table v2, both frozen); per-bit list NOT stored "
                            "in the repo (WP6 3.3 spirit); meta + spot bits "
                            "in <out>/<design>/pool_v2_meta.json",
                 "path_tpl": "<out>/<design>/pool_v2_meta.json"},
        "tile_tables": {d: {"path": str(out / d / "tile_table.json"),
                            "exists": (out / d / "tile_table.json").is_file()}
                        for d in DESIGN_ORDER},
        "L_freeze": {"path_tpl": "<out>/<design>/L_freeze.json",
                     "requires_flag": "--i-am-sure"},
        "definitions": {
            "shot_protocol": "SEM I -> N <lfa> per bit (1 bit singles, 2 "
                             "bits pairs) -> write 10 rand_full frames -> "
                             "run -> read output -> read telemetry -> "
                             "SEM O repair -> verify repair report -> "
                             "health shot (WP6 2.1); inputs rewritten every "
                             "shot, so shell reset reload is built in",
            "result_categories": [
                {"key": "stuck", "zh": "卡住",
                 "rule": "运行未完成: run_error (stream-engine watchdog "
                         "~64 ms)、写入或读出错误、主机超时"},
                {"key": "noimpact", "zh": "无影响",
                 "rule": "运行完成; 8 帧逐位一致且尾两帧全零; "
                         "det = cor = unc = 0; err_flags = 0"},
                {"key": "corrected", "zh": "已纠正",
                 "rule": "运行完成; 输出逐位一致; cor > 0 或 det > 0; "
                         "unc = 0"},
                {"key": "flagged", "zh": "标出的错",
                 "rule": "运行完成; 输出不一致; unc > 0"},
                {"key": "silent", "zh": "静默的错",
                 "rule": "运行完成; 输出不一致; unc = 0 (另记 cor/det; "
                         "cor > 0 的列为误纠子行)"},
                {"key": "flagged_but_clean", "zh": "标出但输出完好",
                 "rule": "运行完成; 输出一致; unc > 0 (罕见, 单列)"},
            ],
            "tmr_silent_rule": "标出只认 unc。三模级的不一致标志对每个"
                               "被屏蔽的单故障都会拉起，系统无法据它区分"
                               "一份副本坏与两份副本坏，所以三模输出出错"
                               "一律记静默，不记标出；正文写明。",
            "tmr_silent_rule_key": "若输出不一致且 unc > 0，但目标位归属"
                                   "三模级，类别记 silent 并记 "
                                   "tmr_override = true",
            "library_L": "单发预筛里类别为已纠正、err_flags = 0、且 SEM "
                         "修复报告确认该位被纠正的位 (WP6 2.3)",
            "stage": "遥测 last_stage + 1",
            "lane": "遥测 last_loc (0-3 功能路、4-5 校验路), 仅编码级",
            "replica": "三模级只能由瓦片归属给出; 归属不唯一记 未定",
            "stage_conflict_policy": "遥测级与瓦片归属级不一致时保留该位、"
                                     "单独标出 (stage_conflict = true); "
                                     "遥测只留最后一个事件的级与路；一发若"
                                     "在几级都有事件，以瓦片归属复核，复核"
                                     "不了记 多级。遥测无法单独检出多级"
                                     "事件（只有 last_*），检出依赖瓦片"
                                     "归属复核。",
            "position_categories": [
                "受算术码保护的数据通路", "三模级",
                "共享控制与指数生成", "译码器与表决器",
                "级间寄存器", "混合", "外壳相关",
            ],
            "position_category_note": "按瓦片归属 (WP6 2.4)；归属不唯一"
                                      "或跨类记 混合；3.3 特殊位置"
                                      "(tile_x49 列、P1 的 INT_R_X51Y156 / "
                                      "INT_R_X53Y152、X0Y4 tile_x23) 单列。",
            "f_bar": {
                "text": "f̄ = Σ_{编码级 s} (n_s/n)²·(1−Σ_l q_{s,l}²) + "
                        "Σ_{三模级 s} (n_s/n)²·(1−Σ_r p_{s,r}²)；n = |L|，"
                        "n_s 为第 s 级位数，q_{s,l} 为编码级第 l 路占比，"
                        "p_{s,r} 为三模级第 r 副本占比（未定的按 1/3）。"
                        "假设：同一恢复边界内不同路/不同副本的两错必失效，"
                        "其余都不失效 (WP6 2.5)",
                "assumption": "同一恢复边界内不同路/不同副本的两错必失"
                              "效，其余都不失效",
            },
        },
        "sample_sizes": {
            "singles": N_SINGLES,
            "pilot_first": PILOT_N,
            "standby_spare": "ceil(5% of singles), pre-drawn, consumed in "
                             "order for voided shots; sampling order of the "
                             "primary list never changes",
            "repeat": {"from_L": N_REPEAT_EACH,
                       "from_other_affected": N_REPEAT_EACH},
            "pairs_natural": N_PAIRS_NATURAL,
            "pairs_stress": STRESS_QUOTAS,
            "pairs_control": CONTROL_QUOTAS,
        },
        "seeds": {**SEEDS,
                  "per_design_derivation": "seed(design) = base_seed + "
                                           "104729 * design_index "
                                           "(P0=0, P1=1, S0=2, S1=3)"},
        "sampling": {
            "singles": "uniform without replacement from pool v2; primary "
                       "5000 + standby ceil(5%) drawn in one pass",
            "pairs_natural": "uniform over unordered distinct-bit pairs of L "
                             "(i, j i.i.d. uniform, order normalised), "
                             "rejection-sampling out same-frame pairs "
                             "(same la); skipped draws counted",
            "pairs_stress": "uniform (rejection) over qualified pairs: same "
                            "stage AND (ECC stage: different last_loc) / "
                            "(TMR stage: different replica, unique-"
                            "attribution bits only); same-frame pairs "
                            "rejected; shortfall: draw all available, report "
                            "deficit, never relax criteria",
            "pairs_control": "same as stress but same lane (ECC) / same "
                             "replica (TMR, unique attribution)",
        },
        "recovery_gradient": [
            "1. 每发后 SEM O，等修复报告；核对帧/字/位与注入位一致（配对"
            + "两位都要出现）",
            "2. 健康发: 逐位一致且计数全 0 才继续",
            "3. 不通过: 再 SEM O、再健康发 (recovery_path = repeat_O)",
            "4. 仍不通过或 SEM 报不可纠 (FC 40): 核对冻结位流 SHA-256 后"
            + "用同一冻结位流重编程 (vivado program_k7_volatile.tcl)，"
            + "SEM 状态，连续 3 发健康发 (recovery_path = reprogram)；"
            + "该发自己的类别按原判定不变",
            "5. 重编程后仍不通过: 停整批 (CampaignStop)",
            "6. 外壳一旦复位 (上电、时钟失锁、主机重连): 重新初始化，"
            + "连续 3 发健康发全部无影响后再继续，记录在案 "
            + "(shell_resets 记录)",
        ],
        "void_rule": "主机通信超时导致的单发作废 (category = invalid)，"
                     "记 作废，用预登记里预抽的备用位依次补，不改抽样"
                     "顺序",
        "pilot_gates": {
            "when": "after the first %d shots of a full-size singles run"
                    % PILOT_N,
            "gates": [
                "恢复梯度从未走到 重编程仍失败 (CampaignStop)",
                "健康发失败率 < 1%",
                "已纠正占比按试运行比例外推到 5000 发 >= 100 位 L",
                "单发平均耗时写进报告",
            ],
            "on_fail": "abort this design with a pilot report",
            "note": "skipped for small configs (pilot < %d)" % PILOT_N,
        },
        "stop_conditions": [
            "恢复梯度第 5 步", "试运行门槛不满足",
            "可重复性抽检不一致超过 5 发", "某设计的 L 少于 100 位",
            "某设计加压配对合格位对总数少于 50 (分项缺额只报告、不停)",
        ],
        "jsonl_record_fields": [
            "design", "bitstream_sha", "frame_table_version", "pool_version",
            "seed", "index", "kind", "subkind",
            "target (la/wd/bt/lfa_hex/column/row/tile)",
            "category", "tmr_stage", "tmr_override",
            "telemetry (full row)", "err_flags", "output_diff_summary",
            "sem (I/N/O replies + repair report parse + repair_match)",
            "health", "recovery_path", "shell_resets", "wall_s", "t",
            "void", "notes",
        ],
        "tile_table_schema": {
            "note": "WP6 3.3 output, written by scripts/k7_mod/"
                    "wp6_tile_table.py (Vivado query); absence must be "
                    "tolerated (category/replica resolve to 未定 at "
                    "analysis time)",
            "format": '{"tiles": {tile_name: {"attrib": [[category, '
                      'stage|null, lane_or_replica|null], ...], '
                      '"special": [...]}}}',
            "tile_name": "<frame-table column tile_kind>_X<tile_x>Y<"
                         "tile_y>; row = word//2 (word<50), (word-1)//2 "
                         "(word>50), word 50 = ECC; tile_y = 50*region_y "
                         "+ row (verified against the .ll by "
                         "row_check.json)",
            "categories": CAT_ZH,
            "unique_rule": "瓦片 attrib 集合唯一（单条）时归属唯一；多条记 "
                           "未定/混合 (WP6 2.3)",
        },
    }


def cmd_prereg(args):
    prereg = build_prereg(OUT)
    path = OUT / "prereg.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prereg, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    log("wrote %s (seeds=%s sizes=%s)"
        % (path, SEEDS, prereg["sample_sizes"]))
    if not prereg["frame_table"]["exists"]:
        log("NOTE: frame table v2 not present yet at %s" % FRAME_TABLE_V2)
    return 0


def load_prereg(out=OUT):
    path = Path(out) / "prereg.json"
    if not path.is_file():
        raise RuntimeError("prereg missing: %s (run the prereg subcommand)"
                           % path)
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------- pool

def build_pool(design, prereg, out=OUT):
    """Pool v2: .ebd essential bits in frame-table v2 core-region columns,
    excluding ECC word 50 and still-dead frames/probes (WP6 3.2).
    Deterministic: sorted by (la, wd, bt); writes pool_v2.json."""
    info = prereg["designs"][design]
    ebd = Path(prereg["frozen_inputs"][design]["ebd"])
    ftab_path = Path(prereg["frame_table"]["path"])
    if not ftab_path.is_file():
        raise RuntimeError("frame table v2 missing: %s" % ftab_path)
    if sha256_file(ebd) != info.get("ebd_sha"):
        raise RuntimeError("frozen .ebd SHA-256 mismatch: %s" % ebd)

    ftab = json.loads(ftab_path.read_text(encoding="utf-8"))
    dead = {int(x) for x in ftab.get("dead_lfas") or []}
    col_of_la = {}
    for c in ftab["columns"]:
        if (c.get("clock_region") in PB_REGIONS
                and c.get("frame_lo") is not None
                and c.get("lfa_source") == "measured"
                and c.get("n_dead_probes", 0) == 0):
            for la in range(int(c["frame_lo"]), int(c["frame_hi"]) + 1):
                col_of_la[la] = c

    bits = []
    with open(ebd, "r", encoding="ascii", errors="replace") as f:
        words = [int(line.strip(), 2) for line in f
                 if len(line.strip()) == 32 and set(line.strip()) <= {"0", "1"}]
    n_frames = len(words) // WPW
    for fr in range(n_frames):
        la = fr - 1                                # .ebd leading pad frame
        if la < 0 or la > SEM_MF or la in dead or la not in col_of_la:
            continue
        c = col_of_la[la]
        base = fr * WPW
        for wd in range(WPW):
            if wd == ECC_WORD:
                continue
            w = words[base + wd]
            for bt in range(32):
                # WP6-G0 verdict M: SEM addresses bits LSB-first (LSB = 0).
                # Pool bt is the LSB index of the physical bit; the bit's
                # physical position is unchanged from v2 (v2's bt was the
                # MSB-based index of the same bit).
                if not (w >> bt) & 1:
                    continue
                row = wd // 2 if wd < 50 else (wd - 1) // 2
                region_y = int(str(c["clock_region"])[-1])
                tile_y = 50 * region_y + row
                bits.append({
                    "la": la, "wd": wd, "bt": bt,
                    "lfa_hex": pack_sem_n_local(la, wd, bt),
                    "frame_table_version": "v2",
                    "clock_region": c["clock_region"],
                    "column": c.get("column"),
                    "tile_kind": c["tile_kind"], "tile_x": c["tile_x"],
                    "row": row,
                    "tile": "%s_X%dY%d" % (c["tile_kind"], c["tile_x"],
                                           tile_y),
                })
    bits.sort(key=lambda b: (b["la"], b["wd"], b["bt"]))
    packed = bytearray()
    for b in bits:
        packed += int(b["la"]).to_bytes(4, "little")
        packed += int(b["wd"]).to_bytes(2, "little")
        packed += int(b["bt"]).to_bytes(2, "little")
    pdir = Path(out) / DESIGN_DIR[design]
    pdir.mkdir(parents=True, exist_ok=True)
    meta_path = pdir / "pool_v2_meta.json"
    supersedes = None
    if meta_path.is_file():
        try:
            old_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if old_meta.get("version") != POOL_VERSION:
                supersedes = {"version": old_meta.get("version"),
                              "packed_bits_sha256":
                              old_meta.get("packed_bits_sha256"),
                              "n_bits": old_meta.get("n_bits")}
        except (json.JSONDecodeError, OSError):
            supersedes = None
    meta = {"design": design, "version": POOL_VERSION,
            "bit_numbering": "LSB-first (SEM/G0 verdict M); v2 used "
                             "MSB-based bt of the same physical bits",
            "frame_table": str(ftab_path),
            "frame_table_sha256": sha256_file(ftab_path),
            "ebd": str(ebd), "ebd_sha256": sha256_file(ebd),
            "n_bits": len(bits),
            "packed_bits_sha256": hashlib.sha256(bytes(packed)).hexdigest(),
            "packed_format": "le u32 la, le u16 wd, le u16 bt, "
                             "sorted by (la, wd, bt)",
            "spot_first5": [[b["la"], b["wd"], b["bt"]]
                            for b in bits[:5]],
            "spot_last5": [[b["la"], b["wd"], b["bt"]]
                           for b in bits[-5:]]}
    if supersedes:
        meta["supersedes"] = supersedes
    (meta_path).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log("pool v2 %s: %d bits (meta only; bit list not stored)"
        % (design, len(bits)))
    return bits


def pack_sem_n_local(lfa, word, bit):
    raw = ((0xC0 << 32) | ((int(lfa) & 0xFFFFF) << 12)
           | ((int(word) & 0x7F) << 5) | (int(bit) & 0x1F))
    return "%010X" % raw


def unpack_sem_n_local(hex10):
    raw = int(str(hex10).strip().upper(), 16)
    return {"prefix": (raw >> 32) & 0xFF, "lfa": (raw >> 12) & 0xFFFFF,
            "word": (raw >> 5) & 0x7F, "bit": raw & 0x1F}


def load_pool(design, out=OUT):
    """Regenerate the pool deterministically (meta file only is stored)."""
    prereg = load_prereg(out)
    return build_pool(design, prereg, out)


# ------------------------------------------------------------------ sampling

def design_seed(base_seed, design):
    return base_seed + 104729 * DESIGN_ORDER.index(design)


def draw_singles(pool, n_primary, n_standby, seed):
    """Uniform without replacement; primary list + pre-drawn standby list."""
    rng = random.Random(seed)
    want = min(n_primary + n_standby, len(pool))
    pick = rng.sample(range(len(pool)), want)
    prim = [pool[i] for i in pick[:n_primary]]
    standby = [pool[i] for i in pick[n_primary:]]
    return prim, standby


def _bit_key(b):
    return "%010X" % (((int(b["la"]) & 0xFFFFF) << 12)
                      | ((int(b["wd"]) & 0x7F) << 5) | (int(b["bt"]) & 0x1F))


def load_attrib_json(ddir, fname):
    """bit_attrib JSON: prefer .json.gz (post 14.3), fall back to .json."""
    base = Path(ddir) / fname
    for cand in (base.with_suffix(base.suffix + ".gz"), base):
        if cand.is_file():
            if str(cand).endswith(".gz"):
                import gzip
                return json.loads(gzip.open(
                    cand, "rt", encoding="utf-8").read())
            return json.loads(cand.read_text(encoding="utf-8"))
    return None


def bit_replica_map(ddir, fname="bit_attrib.json"):
    """{(la,wd,bt): replica} where the bit's attrib names exactly one
    tmr_stage replica (WP6-A whole-net or downstream口径 file)."""
    ba = load_attrib_json(ddir, fname)
    out = {}
    if not ba:
        return out
    for s in ba.get("shots") or []:
        key = (s.get("la"), s.get("wd"), s.get("bt"))
        if key[0] is None:
            continue
        reps = set()
        for h in s.get("hits_detail") or []:
            for a in h.get("attrib") or []:
                if len(a) >= 3 and a[0] == "tmr_stage":
                    reps.add(a[2])
        if len(reps) == 1:
            out[key] = next(iter(reps))
    return out


def apply_replica_override(L, design, ddir):
    """Apply the corrected rule to a copy of L; returns (L2, stats).
    Only meaningful for designs with tmr_stages; others pass through."""
    import copy
    wmap = bit_replica_map(ddir)
    dmap = bit_replica_map(ddir, "bit_attrib_downstream.json")
    if not wmap and not dmap:
        return list(L), {"note": "no bit_attrib maps; L replicas unchanged"}
    st = {"downstream": 0, "wholenet": 0, "conflict_dropped": 0,
          "undetermined": 0, "non_tmr": 0}
    tmr = set(DESIGNS[design]["tmr_stages"])
    L2 = []
    for b in L:
        if b.get("stage") not in tmr:
            st["non_tmr"] += 1
            L2.append(b)
            continue
        b2 = copy.copy(b)
        w = wmap.get((b["la"], b["wd"], b["bt"]))
        d = dmap.get((b["la"], b["wd"], b["bt"]))
        if w is not None and d is not None and w != d:
            b2["replica"] = "未定"
            st["conflict_dropped"] += 1
        elif d is not None:
            b2["replica"] = d
            st["downstream"] += 1
        elif w is not None:
            b2["replica"] = w
            st["wholenet"] += 1
        else:
            b2["replica"] = "未定"
            st["undetermined"] += 1
        L2.append(b2)
    return L2, st


def _serial_pair(pair):
    return [{"la": t["la"], "wd": t["wd"], "bt": t["bt"],
             "lfa_hex": t.get("lfa_hex"), "stage": t.get("stage"),
             "lane": t.get("lane"), "replica": t.get("replica")}
            for t in pair]


def draw_natural_pairs(L_bits, n_pairs, seed):
    """Uniform over unordered distinct-bit pairs of L, same-frame pairs
    rejected (WP6 5).  Returns (pairs, n_skipped)."""
    rng = random.Random(seed)
    n = len(L_bits)
    pairs, seen, skipped = [], set(), 0
    if n < 2:
        return pairs, skipped
    guard = 0
    while len(pairs) < n_pairs:
        guard += 1
        if guard > max(1000, n_pairs * 10000):
            raise RuntimeError("natural pair draw: exhausted pool")
        i, j = rng.randrange(n), rng.randrange(n)
        if i == j:
            continue
        a, b = (i, j) if i < j else (j, i)
        key = (_bit_key(L_bits[a]), _bit_key(L_bits[b]))
        if key in seen:
            continue
        if L_bits[a]["la"] == L_bits[b]["la"]:
            skipped += 1
            continue
        seen.add(key)
        pairs.append([L_bits[a], L_bits[b]])
    return pairs, skipped


def _pair_ok(a, b, stage_kind, control):
    """Qualified stress/control pair predicate (WP6 6)."""
    sa, sb = a.get("stage"), b.get("stage")
    if sa is None or sa != sb:
        return False
    kind = stage_kind(sa)
    if kind == "ecc":
        if a.get("lane") is None or b.get("lane") is None:
            return False
        same = a["lane"] == b["lane"]
    else:
        ra, rb = a.get("replica"), b.get("replica")
        if ra in (None, "未定") or rb in (None, "未定"):
            return False                       # unique attribution only
        same = ra == rb
    return same if control else not same


def draw_quota_pairs(L_bits, design, quotas, seed, control=False):
    """Uniform (rejection) draw over qualified pairs per stage-kind quota.
    Shortfall: draw all available, report deficit, never relax criteria."""
    info = DESIGNS[design]

    def stage_kind(stage):
        if stage in info["ecc_stages"]:
            return "ecc"
        if stage in info["tmr_stages"]:
            return "tmr"
        return None

    rng = random.Random(seed)
    out, deficit = {}, {}
    eligible = [b for b in L_bits
                if stage_kind(b.get("stage")) in quotas]
    for cls, quota in quotas.items():
        want = []
        seen = set()
        cls_bits = [b for b in eligible if stage_kind(b["stage"]) == cls]
        n = len(cls_bits)
        guard = 0
        # first pass: rejection sample up to quota
        while len(want) < quota and n >= 2:
            guard += 1
            if guard > max(1000, quota * 20000):
                break
            i, j = rng.randrange(n), rng.randrange(n)
            if i == j:
                continue
            a, b = (i, j) if i < j else (j, i)
            key = (_bit_key(cls_bits[a]), _bit_key(cls_bits[b]))
            if key in seen:
                continue
            if cls_bits[a]["la"] == cls_bits[b]["la"]:
                continue
            if not _pair_ok(cls_bits[a], cls_bits[b], stage_kind, control):
                continue
            seen.add(key)
            want.append([cls_bits[a], cls_bits[b]])
        if len(want) < quota:
            # shortfall: enumerate remaining qualified pairs (no criteria
            # relaxation), shuffle uniformly, take what is left
            rest = []
            for i in range(n):
                for j in range(i + 1, n):
                    key = (_bit_key(cls_bits[i]), _bit_key(cls_bits[j]))
                    if key in seen:
                        continue
                    if cls_bits[i]["la"] == cls_bits[j]["la"]:
                        continue
                    if _pair_ok(cls_bits[i], cls_bits[j], stage_kind,
                                control):
                        rest.append([cls_bits[i], cls_bits[j]])
            rng.shuffle(rest)
            want.extend(rest[:quota - len(want)])
        out[cls] = want[:quota]
        if len(out[cls]) < quota:
            deficit[cls] = quota - len(out[cls])
    return out, deficit


# -------------------------------------------------------------- classification

def is_comm_error(err):
    """Host-communication failure (section 9 void rule), as opposed to a
    board-side run/write/read failure which classifies as 卡住.  Engine
    run_error arrives as a bare 'NAK seq' from CMD_CS_PARAM and is NOT a
    comm error; only 'SEM NAK' is."""
    if not err:
        return False
    pats = ("UART ACK 超时", "SEM ACK 超时", "SEM NAK",
            "UART 未连接", "host timeout", "TimeoutError", "连接",
            "host.start", "reconnect")
    return any(p in str(err) for p in pats)


def classify_shot(shot, tel, tmr_stage):
    """WP6 2.2 five/six-class decision on observables.  `shot` is a do_shot-
    style dict (ok/err/n_diff/tail_zero); tel may be None for stuck shots."""
    if shot.get("err"):
        return "stuck", False
    unc = (tel or {}).get("unc_cnt", 0)
    det = (tel or {}).get("det_cnt", 0)
    cor = (tel or {}).get("cor_cnt", 0)
    flags = (tel or {}).get("err_flags", 0)
    ok = bool(shot.get("ok"))
    if ok and unc == 0 and det == 0 and cor == 0 and flags == 0:
        return "noimpact", False
    if ok and unc == 0 and (cor > 0 or det > 0):
        return "corrected", False
    if ok and unc > 0:
        return "flagged_but_clean", False
    if not ok and unc > 0:
        if tmr_stage:
            return "silent", True
        return "flagged", False
    if not ok and unc == 0:
        return "silent", False
    return "stuck", False


# ------------------------------------------------------------------ boards

class RealBoard:
    """Board-backed implementation of the campaign's duck-typed interface."""

    def __init__(self, com, arch, reprogram_cmd=None):
        self.com = com
        self.arch = arch
        self.host = None
        self.wire = None
        self.reprogram_cmd = reprogram_cmd or default_reprogram_cmd

    def ensure_up(self):
        if self.host is not None:
            return
        from k7_lab_paths import add_host, add_layout_ecc
        add_host(ROOT)
        add_layout_ecc(ROOT)
        from k7_real_data_gate import BoardHost, Wire, wait_idle
        from wp6_frame_probe import sem_resync
        self._wait_idle = wait_idle
        self.host = BoardHost(log=lambda m: log("  board: " + m))
        self.host.start(com=self.com, udp=True)
        sem_resync(self.host, tag=1)
        self.wire = Wire()
        self._wait_idle(self.host, 60.0)
        st = self.host.uart_cmd_sem("S", timeout_ms=4000)
        log("SEM status at connect: %s" % st)

    def sem_cmd(self, cmd, timeout_ms=4000, flags=0):
        self.ensure_up()
        rep = self.host.uart_cmd_sem(cmd, timeout_ms=timeout_ms, flags=flags)
        return rep.decode("ascii", "replace") if isinstance(
            rep, (bytes, bytearray)) else str(rep)

    def sem_pages(self, n_pages=4):
        self.ensure_up()
        pages = self.host.dump_monitor_pages(n_pages=n_pages)
        out = []
        for p in pages:
            out.append(p.decode("ascii", "replace") if isinstance(
                p, (bytes, bytearray)) else str(p))
        return out

    def run_shot(self):
        """One full WR->RUN->RD shot (no injection bookkeeping)."""
        self.ensure_up()
        from k7_paper_smoke import do_shot
        raw, golden = self._stim()
        return do_shot(self.host, self.wire, raw, golden)

    def health_shot(self):
        rec = self.run_shot()
        tel = rec.get("telemetry") or {}
        rec["counters_zero"] = bool(
            rec.get("ok") and tel.get("det_cnt", -1) == 0
            and tel.get("cor_cnt", -1) == 0 and tel.get("unc_cnt", -1) == 0
            and tel.get("err_flags", -1) == 0)
        rec.pop("data", None)
        return rec

    def _stim(self):
        if not hasattr(self, "_raw"):
            from k7_paper_smoke import load_golden, load_input_raw
            self._raw = load_input_raw()
            self._golden = load_golden(self.arch)
        return self._raw, self._golden

    def golden(self):
        return self._stim()[1]

    def reprogram(self, bit_path, bit_sha):
        if sha256_file(bit_path) != bit_sha:
            raise CampaignStop("frozen bitstream SHA-256 mismatch: %s"
                               % bit_path)
        cmd = self.reprogram_cmd(bit_path)
        log("reprogram: %s" % " ".join(shlex.quote(c) for c in cmd))
        subprocess.run(cmd, cwd=ROOT, check=True, timeout=900)
        self.reconnect()

    def reconnect(self):
        log("shell reset: re-initialising host")
        try:
            if self.host is not None:
                self.host.stop()
        except Exception:                          # noqa: BLE001, S110
            pass
        self.host = None
        self.wire = None
        self.ensure_up()

    def close(self):
        try:
            if self.host is not None:
                self.host.stop()
        except Exception:                          # noqa: BLE001, S110
            pass
        self.host = None
        self.wire = None


def default_reprogram_cmd(bit_path):
    vivado = VIVADO_BIN / ("vivado.bat" if sys.platform == "win32"
                           else "vivado")
    return [str(vivado), "-mode", "batch", "-source", str(PROGRAM_TCL),
            "-tclargs", str(bit_path)]


# ------------------------------------------------------------------ campaign

class Ctx:
    """Per-design campaign context (all phases share it)."""

    def __init__(self, design, prereg, board, out=OUT):
        self.design = design
        self.prereg = prereg
        self.board = board
        self.out = Path(out)
        self.info = prereg["designs"][design]
        self.log_path = self.out / DESIGN_DIR[design] / "campaign.jsonl"
        self.seed = design_seed(prereg["seeds"]["singles"], design)
        self.recorded = recorded_keys(self.log_path)
        self.shell_resets_total = 0
        self.reprogram_fails = 0


def recorded_keys(log_path):
    """Resume key set: (kind, target-key) per design log."""
    keys = set()
    for rec in read_jsonl(Path(log_path)):
        keys.add((rec.get("kind"), rec.get("subkind"), target_key(rec)))
    return keys


def append_output_diffs(ctx, index, kind, subkind, tkey, diffs):
    """WP6 section 13: per-beat output diffs for wrong-output shots,
    appended to <design>/output_diffs.jsonl.gz (one gzip per design,
    entries indexed by shot index).  Raw output bytes stay out of the
    campaign jsonl."""
    path = ctx.out / DESIGN_DIR[ctx.design] / "output_diffs.jsonl.gz"
    entry = {"index": index, "kind": kind, "subkind": subkind,
             "target_key": tkey, "diffs": diffs}
    with gzip.open(path, "at", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def target_key(rec):
    tgt = rec.get("target") or []
    if isinstance(tgt, dict):
        tgt = [tgt]
    ks = sorted(_bit_key(t) if isinstance(t, dict) and "la" in t else str(t)
                for t in tgt)
    return "+".join(ks)


def _diff_tools():
    from k7_paper_sdc_probe import diff_shot, summarize
    return diff_shot, summarize


def health_pass(ctx):
    """One health shot, section 8.6 shell-reset aware.  Returns a health
    rec whose 'counters_zero' decides the gradient step."""
    board = ctx.board
    try:
        rec = board.health_shot()
    except Exception as e:
        if not is_comm_error(e):
            raise
        rec = {"ok": False, "err": "%s: %s" % (type(e).__name__, e)}
    if not is_comm_error(rec.get("err")):
        assert_tel_arch(ctx.design, rec.get("telemetry"))
        return rec
    # section 8.6: shell reset -> re-init + 3 consecutive clean health shots
    board.reconnect()
    ctx.shell_resets_total += 1
    clean, last = 0, rec
    for _ in range(3):
        last = board.health_shot()
        assert_tel_arch(ctx.design, last.get("telemetry"))
        if last.get("counters_zero"):
            clean += 1
        elif is_comm_error(last.get("err")):
            board.reconnect()
            ctx.shell_resets_total += 1
            clean = 0
        else:
            break
    log("shell reset #%d: %d/3 clean health shots"
        % (ctx.shell_resets_total, clean))
    return {"ok": clean == 3, "counters_zero": clean == 3,
            "err": None if clean == 3 else "shell reset unclean",
            "shell_reset_recovered": clean == 3}


def repair_and_health(ctx, injected):
    """WP6 section 8 recovery gradient after SEM O.

    injected: list of target dicts (one for singles, two for pairs).
    Returns (sem_info, recovery_path, health_rec).  Raises CampaignStop at
    step 5.
    """
    board = ctx.board
    sem_info = {"O": None, "pages": [], "report": None, "repair_match": None,
                "fc40": False}

    def do_repair():
        # Real SEM needs one correction pass per error; after a double
        # injection the first O can take much longer (or NAK while the
        # previous correction is still running).  Retry the O itself a
        # few times with settle time before giving up on the reply.
        last = ""
        for attempt in range(3):
            last = board.sem_cmd("O", timeout_ms=30000)
            if last:
                break
            log("O reply empty (attempt %d); settling 3 s and retrying"
                % attempt)
            time.sleep(3.0)
        sem_info["O"] = last
        time.sleep(1.0)
        pages = board.sem_pages(4)
        sem_info["pages"] = pages
        stitched = sem_info["O"] + "\n" + "\n".join(pages)
        sem_info["report"] = parse_sem_report_local(stitched)
        rep = sem_info["report"]
        sem_info["fc40"] = bool(rep.get("fc40"))
        # SEM monitor buffer keeps only the latest repair report, so for
        # pairs the older correction may be overwritten: record which
        # injected bits appear (None = nothing parsed, list otherwise).
        triples = []
        for la, wd, bt in _SEM_TRIPLE_RE.findall(stitched or ""):
            triples.append((la_to_lfa_local(la.upper()),
                            int(wd, 16), int(bt, 16)))
        injected_triples = [(t["la"], t["wd"], t["bt"]) for t in injected]
        matched = [t for t in injected_triples if t in triples]
        sem_info["repair_matched"] = matched
        sem_info["repair_match"] = (all(t in triples for t in injected_triples)
                                    if triples else None)
        # Correction is only provable once SEM is back to idle.
        wait_sem_idle(board, timeout_s=60.0)

    def health():
        return health_pass(ctx)

    def confirmed(h):
        # Health clean + a COR report naming every injected bit; accept a
        # partial name match for pairs when the monitor buffer only kept
        # the latest report (WP6 8.1 note recorded in the shot).
        rep = sem_info["report"] or {}
        cor_ok = bool(rep.get("cor"))
        full = sem_info["repair_match"] is True
        partial = (sem_info["repair_match"] is False
                   and len(sem_info["repair_matched"]) >= 1)
        return (h.get("ok") and h.get("counters_zero") and cor_ok
                and (full or partial))

    # step 1-2: repair report + health shot
    do_repair()
    h = health()
    if confirmed(h):
        return sem_info, "none", h
    # step 3: repeat O + health (one O per pass, up to 3 rounds)
    for attempt in range(3):
        log("post-repair unconfirmed (match=%s matched=%s) -> step 3: "
            "repeat O + health (round %d)"
            % (sem_info["repair_match"], sem_info["repair_matched"],
               attempt + 1))
        time.sleep(2.0)
        do_repair()
        h = health()
        if confirmed(h):
            return sem_info, "repeat_O", h
    # step 4: reprogram with the frozen bitstream (SHA verified inside)
    board.reprogram(Path(ctx.prereg["frozen_inputs"][ctx.design]["bit"]),
                    ctx.info["bit_sha"])
    board.sem_cmd("S", timeout_ms=4000)
    clean = 0
    h = None
    for _ in range(3):
        h = health()
        if h.get("counters_zero"):
            clean += 1
        else:
            break
    if clean == 3:
        return sem_info, "reprogram", h
    ctx.reprogram_fails += 1
    raise CampaignStop("recovery gradient step 5: still failing after "
                       "reprogram")


_SEM_WD_RE = re.compile(r"WD\s*([0-9A-Fa-f]{2,4})")
_SEM_BT_RE = re.compile(r"BT\s*([0-9A-Fa-f]{2,4})")
_SEM_PA_RE = re.compile(r"PA\s*([0-9A-Fa-f]{8})")
_SEM_LA_RE = re.compile(r"LA\s*([0-9A-Fa-f]{8,10})")


def parse_sem_report_local(text):
    t = text or ""
    wd = _SEM_WD_RE.search(t)
    bt = _SEM_BT_RE.search(t)
    pa = _SEM_PA_RE.search(t)
    las = _SEM_LA_RE.findall(t)
    return {"la": las[-1].upper() if las else None,
            "pa": pa.group(1).upper() if pa else None,
            "wd": wd.group(1).upper() if wd else None,
            "bt": bt.group(1).upper() if bt else None,
            "cor": "COR" in t.upper(), "fc40": "FC 40" in t.upper()}


_SEM_TRIPLE_RE = re.compile(
    r"LA\s*([0-9A-Fa-f]{8,10}).{0,80}?WD\s*([0-9A-Fa-f]{2,4})"
    r".{0,40}?BT\s*([0-9A-Fa-f]{2,4})", re.DOTALL)


def wait_sem_idle(board, timeout_s=60.0):
    """Poll SEM status until the correction/observation state clears
    (board-verified: a double-bit repair can take ~15 s after the O)."""
    host = getattr(board, "host", None)
    if host is None:
        return True                      # mock board: nothing to wait for
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            board.sem_cmd("S", timeout_ms=4000)
            st = getattr(host, "last_sem_status", None) or {}
        except Exception:                          # noqa: BLE001
            time.sleep(1.0)
            continue
        # Board-calibrated: SEM stays in observation (idle=0) after the
        # repair — that is normal.  Completion = no correction in
        # progress, no classification/injection, no uncorrectable latch.
        busy = (st.get("sem_correction") or st.get("sem_classification")
                or st.get("sem_injection") or st.get("sem_uncorrectable"))
        if not busy:
            return True
        time.sleep(1.0)
    log("wait_sem_idle: still busy after %.0f s" % timeout_s)
    return False


def repair_match_multi(stitched, injected):
    """Section 8.1: every injected bit must appear in the repair report
    (a consistent LA/WD/BT triple).  None if nothing was parsed at all."""
    triples = []
    for la, wd, bt in _SEM_TRIPLE_RE.findall(stitched or ""):
        lfa = la_to_lfa_local(la.upper())
        triples.append((lfa, int(wd, 16), int(bt, 16)))
    if not triples:
        return None
    return all((t["la"], t["wd"], t["bt"]) in triples for t in injected)


def la_to_lfa_local(la_hex):
    if not la_hex:
        return None
    raw = int(la_hex, 16)
    if raw > 0x5AE1:
        return (raw >> 14) & 0x3FFFF
    return raw


def inject_shot(ctx, kind, subkind, targets, index, seed, standby=False,
                notes=None):
    """One injection shot: I -> N* -> shot -> classify -> O/repair/health
    (section 8) -> one jsonl record (section 3.4)."""
    board = ctx.board
    rec = {"design": ctx.design, "bitstream_sha": ctx.info["bit_sha"],
           "frame_table_version": "v2", "pool_version": POOL_VERSION,
           "seed": seed, "index": index, "kind": kind, "subkind": subkind,
           "target": targets, "t": now_iso(), "void": False,
           "standby": standby, "notes": notes,
           "shell_resets": []}
    resets0 = ctx.shell_resets_total
    t0 = time.monotonic()
    sem = {"I": None, "N": []}
    try:
        sem["I"] = board.sem_cmd("I", timeout_ms=4000)
        for t in targets:
            sem["N"].append(board.sem_cmd("N " + t["lfa_hex"],
                                          timeout_ms=4000))
    except Exception as e:                          # noqa: BLE001
        rec.update(category="invalid", void=True,
                   err="inject: %s: %s" % (type(e).__name__, e),
                   sem=sem, telemetry=None, err_flags=None,
                   output_diff_summary=None, health=None,
                   recovery_path="none")
        rec["wall_s"] = round(time.monotonic() - t0, 3)
        append_jsonl(ctx.log_path, rec)
        return rec

    try:
        shot = board.run_shot()
    except Exception as e:                              # noqa: BLE001
        shot = {"ok": False, "err": "%s: %s" % (type(e).__name__, e),
                "telemetry": None}
    tel = shot.get("telemetry")
    assert_tel_arch(ctx.design, tel)
    stage = ((tel or {}).get("last_stage", 0) + 1) if tel else None
    tmr_stage = stage in DESIGNS[ctx.design]["tmr_stages"]
    category, tmr_override = classify_shot(shot, tel, tmr_stage)
    if is_comm_error(shot.get("err")):
        category, void = "invalid", True
    else:
        void = False
    rec.update(category=category, tmr_stage=tmr_stage,
               tmr_override=tmr_override, void=void, err=shot.get("err"),
               telemetry=tel, err_flags=(tel or {}).get("err_flags"),
               n_diff=shot.get("n_diff"),
               tail_zero=shot.get("tail_zero"))
    if not shot.get("ok") and not shot.get("err"):
        try:
            diff_shot, summarize = _diff_tools()
            golden = board.golden()
            diffs = diff_shot(shot["data"], golden)
            rec["output_diff_summary"] = summarize(diffs)
            append_output_diffs(ctx, index, kind, subkind,
                                target_key(rec), diffs)
        except Exception as e:                      # noqa: BLE001
            rec["output_diff_summary"] = {"error": str(e)}
    else:
        rec["output_diff_summary"] = None

    try:
        sem_info, recovery_path, health_rec = repair_and_health(ctx, targets)
    except Exception as e:
        if is_comm_error(e):
            board.reconnect()
            sem_info, recovery_path, health_rec = repair_and_health(
                ctx, targets)
        else:
            raise
    rec["sem"] = {**sem, **sem_info}
    rec["health"] = health_rec
    rec["recovery_path"] = recovery_path
    rec["shell_resets"] = list(range(resets0 + 1, ctx.shell_resets_total + 1))
    rec["wall_s"] = round(time.monotonic() - t0, 3)
    if void:
        rec["category"] = "invalid"
    append_jsonl(ctx.log_path, rec)
    return rec


def run_health_gate(ctx, n=10):
    """Section 4.1: n clean health shots before the first injection."""
    for i in range(n):
        h = health_pass(ctx)
        if not h.get("counters_zero"):
            raise CampaignStop("initial health gate failed at shot %d "
                               "(n_diff=%s err=%s)" % (i, h.get("n_diff"),
                                                       h.get("err")))
        log("health gate %d/%d clean" % (i + 1, n))


def check_pilot_gates(ctx, n_shots):
    """Section 7 auto-gates after the pilot prefix (full-size runs only)."""
    recs = [r for r in read_jsonl(ctx.log_path)
            if r.get("kind") == "single" and not r.get("standby")]
    pilot = [r for r in recs if isinstance(r.get("index"), int)
             and r["index"] < n_shots]
    if len(pilot) < n_shots:
        return None
    health_fails = sum(1 for r in pilot if r.get("health")
                       and not r["health"].get("counters_zero", True))
    corrected = sum(1 for r in pilot if r.get("category") == "corrected")
    extrapolated = corrected / max(1, len(pilot)) * N_SINGLES
    walls = [r.get("wall_s") for r in pilot if r.get("wall_s")]
    gates = {
        "n_pilot": len(pilot),
        "health_fail_rate": health_fails / len(pilot),
        "gate_health_fail_lt_1pct": health_fails / len(pilot) < 0.01,
        "corrected_frac": corrected / len(pilot),
        "gate_L_extrapolates_ge_100": extrapolated >= 100,
        "mean_wall_s": (sum(walls) / len(walls)) if walls else None,
        "gate_no_reprogram_fail": ctx.reprogram_fails == 0,
        "passed": None,
    }
    gates["passed"] = all([gates["gate_health_fail_lt_1pct"],
                           gates["gate_L_extrapolates_ge_100"],
                           gates["gate_no_reprogram_fail"]])
    report = {"design": ctx.design, "gates": gates,
              "prereg": "pilot gates per WP6 section 7"}
    path = ctx.out / DESIGN_DIR[ctx.design] / "pilot_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    log("pilot gates: %s" % json.dumps(gates))
    if not gates["passed"]:
        raise CampaignStop("pilot gates failed for %s (report %s)"
                           % (ctx.design, path))
    return gates


# ------------------------------------------------------------------- singles

def cmd_singles(args, sizes=None):
    prereg = load_prereg(OUT)
    design = args.design
    sizes = sizes or {}
    n_singles = sizes.get("n_singles", prereg["sample_sizes"]["singles"])
    pilot = sizes.get("pilot", min(PILOT_N, n_singles))
    standby_n = sizes.get("standby", max(1, -(-n_singles * 5 // 100)))
    pool = build_pool(design, prereg, OUT)
    seed = design_seed(prereg["seeds"]["singles"], design)
    prim, standby = draw_singles(pool, n_singles, standby_n, seed)
    log("singles %s: pool=%d primary=%d standby=%d seed=%d"
        % (design, len(pool), len(prim), len(standby), seed))

    board = make_board(args, prereg, design)
    ctx = Ctx(design, prereg, board, OUT)
    try:
        run_health_gate(ctx, n=sizes.get("health_gate", 10))
        voids_seen = sum(1 for r in read_jsonl(ctx.log_path)
                         if r.get("kind") == "single" and r.get("void"))
        for i, bit in enumerate(prim):
            key = ("single", None, _bit_key(bit))
            if key in ctx.recorded:
                continue
            rec = inject_shot(ctx, "single", None, [bit], i, seed)
            ctx.recorded.add(key)
            if rec.get("void"):
                if voids_seen >= len(standby):
                    raise CampaignStop("standby spare exhausted "
                                       "(voided shots exceed pre-drawn 5%)")
                sbit = standby[voids_seen]
                log("shot %d voided (%s) -> standby #%d %s"
                    % (i, rec.get("err"), voids_seen, sbit["lfa_hex"]))
                inject_shot(ctx, "single", "standby", [sbit], i, seed,
                            standby=True, notes="replaces voided index %d" % i)
                voids_seen += 1
            if i + 1 == pilot and pilot >= PILOT_N:
                check_pilot_gates(ctx, pilot)
            if (i + 1) % 100 == 0:
                log("singles %s: %d/%d done" % (design, i + 1, len(prim)))
    finally:
        board.close()
    gz = snapshot_gz(ctx.log_path)
    log("singles %s done; gz copy %s" % (design, gz))
    return 0


def make_board(args, prereg, design):
    if getattr(args, "mock_board", None) is not None:
        return args.mock_board
    return RealBoard(args.com, prereg["designs"][design]["arch"])


# -------------------------------------------------------------------- repeat

def provisional_L(design, out=OUT):
    """L rule (2.3) applied to recorded singles, pre-freeze."""
    bits = []
    for r in read_jsonl(Path(out) / DESIGN_DIR[design]
                      / "campaign.jsonl"):
        if r.get("kind") != "single" or r.get("void"):
            continue
        if r.get("category") != "corrected" or r.get("err_flags") != 0:
            continue
        sem = r.get("sem") or {}
        if not sem.get("repair_match"):
            continue
        t = dict(r["target"][0])
        tel = r.get("telemetry") or {}
        t["stage"] = tel.get("last_stage", 0) + 1
        t["lane"] = tel.get("last_loc")
        t["replica"] = "未定"
        bits.append(t)
    bits.sort(key=lambda b: (b["la"], b["wd"], b["bt"]))
    return bits


def cmd_repeat(args, sizes=None):
    prereg = load_prereg(OUT)
    design = args.design
    sizes = sizes or {}
    n_each = sizes.get("n_repeat", N_REPEAT_EACH)
    L = provisional_L(design, OUT)
    affected = []
    for r in read_jsonl(design_dir(design) / "campaign.jsonl"):
        if r.get("kind") == "single" and r.get("category") in AFFECTED:
            affected.append(dict(r["target"][0]))
    seed = design_seed(prereg["seeds"]["repeat"], design)
    rng = random.Random(seed)
    pick_l = rng.sample(L, min(n_each, len(L)))
    pick_a = rng.sample(affected, min(n_each, len(affected)))
    log("repeat %s: L=%d affected=%d reshooting %d + %d (seed=%d)"
        % (design, len(L), len(affected), len(pick_l), len(pick_a), seed))

    board = make_board(args, prereg, design)
    ctx = Ctx(design, prereg, board, OUT)
    mismatches = []
    try:
        run_health_gate(ctx, n=sizes.get("health_gate", 10))
        n_done = 0
        for subkind, picks in (("L", pick_l), ("affected", pick_a)):
            for t in picks:
                key = ("repeat", subkind, _bit_key(t))
                if key in ctx.recorded:
                    continue
                first_cat = first_category(ctx, t)
                rec = inject_shot(ctx, "repeat", subkind, [t],
                                  index=n_done, seed=seed)
                n_done += 1
                ctx.recorded.add(key)
                if first_cat and rec.get("category") != first_cat:
                    mismatches.append({"target": t, "first": first_cat,
                                       "repeat": rec.get("category")})
        report = {"design": design, "n_L": len(pick_l),
                  "n_affected": len(pick_a), "mismatches": mismatches}
        path = design_dir(design) / "repeat_report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        log("repeat %s: %d mismatches %s" % (design, len(mismatches),
                                             [m["target"]["lfa_hex"]
                                              for m in mismatches]))
        if len(mismatches) > 5:
            raise CampaignStop("repeatability mismatch > 5 (%d)" %
                               len(mismatches))
    finally:
        board.close()
    return 0


def first_category(ctx, bit):
    key = _bit_key(bit)
    for r in read_jsonl(ctx.log_path):
        if r.get("kind") == "single" and target_key(r) == key:
            return r.get("category")
    return None


# ------------------------------------------------------------------ freeze_L

def cmd_freeze_L(args, sizes=None):
    if not getattr(args, "i_am_sure", False):
        raise SystemExit("freeze_L is the freeze point before pairs; "
                         "re-run with --i-am-sure")
    prereg = load_prereg(OUT)
    design = args.design
    L = provisional_L(design, OUT)
    info = DESIGNS[design]
    tile_attr = load_tile_table(design, OUT)

    stages = {}
    for b in L:
        attr = bit_attribution(tile_attr, b)
        if attr:
            b["position_category"] = attr.get("category")
            if attr.get("special"):
                b["special_position"] = attr["special"]
            if attr.get("stage") is not None and attr["stage"] != b["stage"]:
                b["stage_conflict"] = True
            if attr.get("replica") is not None:
                b["replica"] = attr["replica"]
            if attr.get("lane") is not None:
                b["lane"] = attr["lane"]
        else:
            b["position_category"] = None
        stages.setdefault(b["stage"], []).append(b)

    n = len(L)
    by_stage = {}
    for s, bits in stages.items():
        ns = len(bits)
        if s in info["ecc_stages"]:
            lanes = {}
            for b in bits:
                lanes[b["lane"]] = lanes.get(b["lane"], 0) + 1
            q = {l: c / ns for l, c in lanes.items()}
            term = (ns / n) ** 2 * (1 - sum(v * v for v in q.values()))
            kind = "ecc"
        elif s in info["tmr_stages"]:
            reps = {}
            unknown = 0
            for b in bits:
                r = b.get("replica")
                if r in (None, "未定"):
                    unknown += 1
                else:
                    reps[r] = reps.get(r, 0) + 1
            p = {r: (c + unknown / 3) / ns for r, c in reps.items()}
            for r in range(3):
                p.setdefault(r, (unknown / 3) / ns)
            term = (ns / n) ** 2 * (1 - sum(v * v for v in p.values()))
            kind = "tmr"
        else:
            log("freeze_L %s: stage %s in neither ecc/tmr list; dropped "
                "from f_bar" % (design, s))
            continue
        by_stage[str(s)] = {"n_s": ns, "kind": kind, "term": term}
    f_bar = sum(v["term"] for v in by_stage.values()) if n else None

    freeze = {"design": design, "n": n, "f_bar": f_bar,
              "f_bar_formula": prereg["definitions"]["f_bar"]["text"],
              "by_stage": by_stage, "L": L, "frozen_at": now_iso(),
              "stage_conflict_count": sum(1 for b in L if b.get(
                  "stage_conflict"))}
    path = design_dir(design) / "L_freeze.json"
    path.write_text(json.dumps(freeze, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    log("freeze_L %s: n=%d f_bar=%s -> %s" % (design, n, f_bar, path))
    return 0


def load_tile_table(design, out=OUT):
    """WP6 3.3 tile attribution table (wp6_tile_table.py output); absence
    -> {} (tolerated: categories/replicas resolve to 未定 at analysis)."""
    path = Path(out) / DESIGN_DIR[design] / "tile_table.json"
    if not path.is_file():
        return {}
    try:
        tt = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return tt.get("tiles") or {}


def bit_attribution(tiles, bit):
    """Resolve one pool bit's attribution from its tile entry (WP6 3.3:
    the bit's tile name was recorded per shot; the tile's attrib SET gives
    category/stage/lane-or-replica; a multi-entry set is 归属不唯一).
    Returns dict(category, stage, lane, replica, unique, special) or None
    when the tile table or tile is missing."""
    if not tiles:
        return None
    t = tiles.get(bit.get("tile"))
    if t is None:
        return None
    special = list(t.get("special") or [])
    attrib = [a for a in (t.get("attrib") or []) if a]
    real = [a for a in attrib if a[0] != "unmapped"] or attrib
    if not real:
        return {"category": "未定", "stage": None, "lane": None,
                "replica": None, "unique": False, "special": special}
    unique = len(real) == 1
    cat = real[0][0] if unique else (
        "mixed" if len({a[0] for a in real}) > 1 else real[0][0])
    stage = real[0][1] if unique else None
    lr = real[0][2] if unique else None
    lane = lr if (unique and cat == "protected_datapath") else None
    replica = None
    if cat == "tmr_stage":
        replica = lr if lr is not None else "未定"
    return {"category": CAT_ZH.get(cat, cat), "stage": stage, "lane": lane,
            "replica": replica, "unique": unique, "special": special,
            "attrib": real}


# -------------------------------------------------------------------- pairs

def load_L_freeze(design, out=OUT):
    path = Path(out) / DESIGN_DIR[design] / "L_freeze.json"
    if not path.is_file():
        raise RuntimeError("L_freeze missing: %s (run freeze_L --i-am-sure)"
                           % path)
    return json.loads(path.read_text(encoding="utf-8"))["L"]


PREDRAW_PATH = "prereg_batch4.json"


def load_predraw(out=OUT):
    path = Path(out) / PREDRAW_PATH
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def draw_batch4_lists(design):
    """Deterministic batch-4 draw (WP6 14.2): corrected replica rule
    applied to L before stress/control qualification; seeds unchanged."""
    prereg = load_prereg(OUT)
    L = load_L_freeze(design, OUT)
    L2, ov = apply_replica_override(L, design, design_dir(design))
    seed_s = design_seed(prereg["seeds"]["pairs_stress"], design)
    seed_c = design_seed(prereg["seeds"]["pairs_control"], design)
    stress, stress_deficit = draw_quota_pairs(
        L2, design, dict(STRESS_QUOTAS[design]), seed_s)
    control, control_deficit = draw_quota_pairs(
        L2, design, dict(CONTROL_QUOTAS[design]), seed_c, control=True)
    return {"design": design, "replica_override": ov,
            "stress": {k: [_serial_pair(p) for p in v]
                       for k, v in stress.items()},
            "stress_deficit": stress_deficit,
            "control": {k: [_serial_pair(p) for p in v]
                        for k, v in control.items()},
            "control_deficit": control_deficit}


def cmd_predraw(args):
    """WP6 14.2 'parallel & resources': pre-draw all four designs' stress /
    control lists (+ S0 reshoot list) before the board starts, write the
    batch-4 preregistration file.  No board access."""
    out_all = {}
    for design in DESIGN_ORDER:
        out_all[design] = draw_batch4_lists(design)
        log("predraw %s: stress=%s deficit=%s control=%s deficit=%s "
            "replica_override=%s" % (
                design,
                {k: len(v) for k, v in out_all[design]["stress"].items()},
                out_all[design]["stress_deficit"],
                {k: len(v) for k, v in out_all[design]["control"].items()},
                out_all[design]["control_deficit"],
                out_all[design]["replica_override"]))
    # S0 natural pairs 115 & 212 reshoot list (WP6 14.2 item 2): look the
    # original pair_natural records up and freeze the targets.
    reshoot = []
    ddir = design_dir("S0")
    for r in read_jsonl(ddir / "campaign.jsonl"):
        if (r.get("kind") == "pair_natural" and r.get("index") in (115, 212)
                and not r.get("void")):
            tgt = _serial_pair(r.get("target") or [])
            reshoot.append({"pair_index": r["index"],
                            "category": r.get("category"),
                            "target": tgt})
    out_all["S0_reshoot"] = reshoot
    path = Path(OUT) / PREDRAW_PATH
    path.write_text(json.dumps(out_all, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    log("predraw -> %s (S0 reshoot pairs: %s)"
        % (path, [x["pair_index"] for x in reshoot]))
    return 0


def _unserial(pair):
    return [dict(t) for t in pair]


def cmd_pairs(args, sizes=None):
    prereg = load_prereg(OUT)
    design = args.design
    sizes = sizes or {}
    L = load_L_freeze(design, OUT)

    n_natural = sizes.get("n_natural", N_PAIRS_NATURAL)
    stress_q = {k: sizes.get("stress_" + k, v)
                for k, v in STRESS_QUOTAS[design].items()}
    control_q = {k: sizes.get("control_" + k, v)
                 for k, v in CONTROL_QUOTAS[design].items()}

    seed_n = design_seed(prereg["seeds"]["pairs_natural"], design)
    natural_only = getattr(args, "natural_only", False)
    stress_only = getattr(args, "stress_only", False)
    skipped = 0
    if stress_only:
        # WP6 14.2 batch 4: natural pairs already done; run stress+control.
        natural = []
    else:
        natural, skipped = draw_natural_pairs(L, n_natural, seed_n)
    if natural_only:
        # WP6-G0 M path / addendum section 3: stress+control wait for WP6-A.
        stress, stress_deficit = {}, {}
        control, control_deficit = {}, {}
    else:
        pre = load_predraw(OUT)
        if pre and design in pre:
            # batch-4 preregistered lists (same seeds; replica override
            # already applied at predraw time)
            entry = pre[design]
            stress = {k: [_unserial(p) for p in v]
                      for k, v in entry["stress"].items()}
            stress_deficit = entry["stress_deficit"]
            control = {k: [_unserial(p) for p in v]
                       for k, v in entry["control"].items()}
            control_deficit = entry["control_deficit"]
            log("pairs %s: using preregistered batch-4 lists "
                "(replica_override=%s)"
                % (design, entry.get("replica_override")))
        else:
            L2, ov = apply_replica_override(L, design, design_dir(design))
            log("pairs %s: replica_override=%s" % (design, ov))
            seed_s = design_seed(prereg["seeds"]["pairs_stress"], design)
            seed_c = design_seed(prereg["seeds"]["pairs_control"], design)
            stress, stress_deficit = draw_quota_pairs(
                L2, design, stress_q, seed_s)
            control, control_deficit = draw_quota_pairs(
                L2, design, control_q, seed_c, control=True)
    total_qualified = sum(len(v) for v in stress.values())
    if (not natural_only and n_natural == N_PAIRS_NATURAL
            and total_qualified < 50):
        raise CampaignStop("stress qualified pairs < 50 (%d)"
                           % total_qualified)
    log("pairs %s: natural=%d (skipped same-frame %d) stress=%s deficit=%s "
        "control=%s deficit=%s" % (design, len(natural), skipped,
                                   {k: len(v) for k, v in stress.items()},
                                   stress_deficit,
                                   {k: len(v) for k, v in control.items()},
                                   control_deficit))
    deficit = {"stress": stress_deficit, "control": control_deficit,
               "natural_skipped_same_frame": skipped}
    (design_dir(design) / "pair_deficit.json").write_text(
        json.dumps(deficit, ensure_ascii=False, indent=2), encoding="utf-8")

    seed_s = design_seed(prereg["seeds"]["pairs_stress"], design)
    seed_c = design_seed(prereg["seeds"]["pairs_control"], design)
    board = make_board(args, prereg, design)
    ctx = Ctx(design, prereg, board, OUT)
    try:
        run_health_gate(ctx, n=sizes.get(
            "health_gate", getattr(args, "health_gate", 10)))
        for i, pair in enumerate(natural):
            _pair_or_skip(ctx, "pair_natural", None, pair, i, seed_n)
            r = ctx.last_pair_rec
            if r and not r.get("void") and r.get("category") in AFFECTED:
                # section 5: re-shoot the pair + each bit individually
                _pair_or_skip(ctx, "pair_natural_repeat", None, pair, i,
                              seed_n, notes="repeat of failed pair %d" % i)
                for b in pair:
                    inject_shot(ctx, "single_reprobe", "natural", [b], i,
                                seed_n, notes="reprobe of pair %d" % i)
        for cls, pairs in stress.items():
            for i, pair in enumerate(pairs):
                _pair_or_skip(ctx, "pair_stress", cls, pair, i, seed_s)
        for cls, pairs in control.items():
            for i, pair in enumerate(pairs):
                _pair_or_skip(ctx, "pair_control", cls, pair, i, seed_c)
                r = ctx.last_pair_rec
                if r and not r.get("void") and r.get("category") in AFFECTED:
                    # WP6 14.2: failed controls get the section-5 reshoot
                    _pair_or_skip(ctx, "pair_control_repeat", cls, pair, i,
                                  seed_c,
                                  notes="repeat of failed control %d" % i)
                    for b in pair:
                        inject_shot(ctx, "single_reprobe", "control", [b],
                                    i, seed_c,
                                    notes="reprobe of control pair %d" % i)
    finally:
        board.close()
    gz = snapshot_gz(ctx.log_path)
    log("pairs %s done; gz copy %s" % (design, gz))
    return 0


def cmd_reshoot(args):
    """WP6 14.2 item 2: re-shoot S0 natural pairs 115 & 212 — pair plus each
    bit's singles, twice each (12 shots total), to separate count drift
    from two-error interaction."""
    prereg = load_prereg(OUT)
    design = args.design
    want = set(int(x) for x in args.pairs)
    ddir = design_dir(design)
    todo = {}
    for r in read_jsonl(ddir / "campaign.jsonl"):
        if (r.get("kind") == "pair_natural" and r.get("index") in want
                and not r.get("void")):
            todo[r["index"]] = r["target"]
    missing = want - set(todo)
    if missing:
        raise SystemExit("no pair_natural records for indices %s in %s"
                         % (sorted(missing), ddir / "campaign.jsonl"))
    # idempotent resume: (kind, subkind, index) already present -> skip
    done = {(r.get("kind"), r.get("subkind"), r.get("index"))
            for r in read_jsonl(ddir / "campaign.jsonl")
            if r.get("subkind") == "reshoot14"}
    board = make_board(args, prereg, design)
    ctx = Ctx(design, prereg, board, OUT)
    seed = design_seed(prereg["seeds"]["pairs_natural"], design)
    try:
        run_health_gate(ctx, n=getattr(args, "health_gate", 3))
        for idx in sorted(todo):
            pair = todo[idx]
            for rep in (1, 2):
                if ("pair_natural_repeat", "reshoot14",
                        idx * 100 + rep) not in done:
                    inject_shot(ctx, "pair_natural_repeat", "reshoot14",
                                pair, idx * 100 + rep, seed,
                                notes="14.2 reshoot of pair %d (rep %d/2)"
                                      % (idx, rep))
                for bi, b in enumerate(pair):
                    ix = idx * 10 + rep * 2 + bi
                    if ("single_reprobe", "reshoot14", ix) in done:
                        continue
                    inject_shot(ctx, "single_reprobe", "reshoot14", [b],
                                ix, seed,
                                notes="14.2 reshoot of pair %d bit %d "
                                      "(rep %d/2)" % (idx, bi, rep))
    finally:
        board.close()
    gz = snapshot_gz(ctx.log_path)
    log("reshoot %s done; gz copy %s" % (design, gz))
    return 0


def _pair_or_skip(ctx, kind, subkind, pair, index, seed, notes=None):
    key = (kind, subkind,
           "+".join(sorted(_bit_key(t) for t in pair)))
    if key in ctx.recorded:
        ctx.last_pair_rec = None
        return None
    rec = inject_shot(ctx, kind, subkind, pair, index, seed, notes=notes)
    ctx.recorded.add(key)
    ctx.last_pair_rec = rec
    return rec


# ------------------------------------------------------------------- resume

def cmd_resume(args):
    """Continue a design: singles (skip recorded), repeat, pairs (needs a
    frozen L).  Never re-runs freeze_L implicitly."""
    rc = cmd_singles(args)
    if rc:
        return rc
    try:
        rc = cmd_repeat(args)
    except RuntimeError as e:
        log("resume: repeat skipped: %s" % e)
    load_L_freeze(args.design, OUT)
    return cmd_pairs(args)


# --------------------------------------------------------------------- main

def main(argv=None):
    global OUT, FREEZE_MANIFEST
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT,
                    help="output dir (default $WP6_OUT or results/wp6)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("prereg")

    p = sub.add_parser("singles")
    p.add_argument("design", choices=DESIGN_ORDER)
    p.add_argument("--com", default="COM5")

    p = sub.add_parser("repeat")
    p.add_argument("design", choices=DESIGN_ORDER)
    p.add_argument("--com", default="COM5")

    p = sub.add_parser("freeze_L")
    p.add_argument("design", choices=DESIGN_ORDER)
    p.add_argument("--i-am-sure", action="store_true")

    p = sub.add_parser("pairs")
    p.add_argument("design", choices=DESIGN_ORDER)
    p.add_argument("--com", default="COM5")
    p.add_argument("--natural-only", action="store_true",
                   help="WP6-G0 M path / addendum s.3: run natural pairs "
                        "only; stress+control wait for WP6-A")
    p.add_argument("--stress-only", action="store_true",
                   help="WP6 14.2 batch 4: natural already done; run "
                        "stress+control only")
    p.add_argument("--health-gate", type=int, default=10,
                   help="clean health shots before the first injection")

    p = sub.add_parser("reshoot")
    p.add_argument("design", choices=DESIGN_ORDER)
    p.add_argument("--com", default="COM5")
    p.add_argument("--pairs", nargs="+", type=int, required=True,
                   help="natural-pair indices to re-shoot (14.2 item 2)")
    p.add_argument("--health-gate", type=int, default=3)

    sub.add_parser("predraw")

    p = sub.add_parser("resume")
    p.add_argument("design", choices=DESIGN_ORDER)
    p.add_argument("--com", default="COM5")

    args = ap.parse_args(argv)
    OUT = Path(args.out)
    FREEZE_MANIFEST = OUT / "freeze_manifest.md"
    if args.cmd == "prereg":
        return cmd_prereg(args)
    if args.cmd == "singles":
        return cmd_singles(args)
    if args.cmd == "repeat":
        return cmd_repeat(args)
    if args.cmd == "freeze_L":
        return cmd_freeze_L(args)
    if args.cmd == "pairs":
        return cmd_pairs(args)
    if args.cmd == "reshoot":
        return cmd_reshoot(args)
    if args.cmd == "predraw":
        return cmd_predraw(args)
    if args.cmd == "resume":
        return cmd_resume(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
