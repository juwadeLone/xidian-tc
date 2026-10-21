"""WP6-G0: SEM bit-order verification on the physical board.

tasks/WP6_G0_bit_order.md: 75 single-bit shots at the *mirror* bit address
(bt_inject = 31 - bt_pool) of the frozen P0 bitstream.  Group A (66): the
mirror bit (bt) has no known function, the key bit (31 - bt) does; group B
(9): the reverse.  Verdict (frozen before the run): a = #non-void A shots
with category != noimpact, b likewise for B; a >= 3 and b <= 1 -> M (SEM
numbers bits LSB-first; P0 hit mirror bits); a == 0 -> N (MSB-first; P0 hit
key bits); anything else (or > 5 voids) -> indeterminate, stop, report.

Reuses wp6_sem_campaign helpers (RealBoard, Ctx, health_pass,
repair_and_health, classify_shot, is_comm_error, read_jsonl, append_jsonl,
run_health_gate) without modifying that module.  inject_shot's essential
sequence is replicated here (rather than called) so each record can carry
the G0-specific fields (bt_pool / bt_inject, repair-report WD/BT verify)
and so per-shot logging lands in results/wp6/g0/campaign.jsonl only —
results/wp6/P0_p1/ is never written.

Usage:
    python scripts/k7_mod/wp6_g0_bitorder.py run   [--com /dev/ttyACM0]
                                                   [--no-program] [--limit N]
    python scripts/k7_mod/wp6_g0_bitorder.py report
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_cram"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))

from wp6_sem_campaign import (                     # noqa: E402
    OUT,
    POOL_VERSION,
    CampaignStop,
    Ctx,
    RealBoard,
    _bit_key,
    append_jsonl,
    classify_shot,
    health_pass,
    is_comm_error,
    load_prereg,
    log,
    now_iso,
    read_jsonl,
    recorded_keys,
    repair_and_health,
    run_health_gate,
    sha256_file,
)

G0_OUT = OUT / "g0"
G0_TARGETS = G0_OUT / "g0_targets_p0.json"
G0_LOG = G0_OUT / "campaign.jsonl"
DESIGN = "P0"
MAX_VOIDS = 5                     # WP6-G0 section 6 stop condition


# ------------------------------------------------------------- shot sequence

def g0_shot(ctx, tgt, index, seed):
    """One G0 injection shot: SEM I -> N <lfa_hex_inject> -> run -> classify
    (WP6 2.2) -> SEM O repair confirm + health (WP6 section 8 gradient via
    repair_and_health) -> one jsonl record.  Sequence mirrors inject_shot;
    adds bt_pool / bt_inject and the repair-report WD/BT equality check."""
    board = ctx.board
    target = {
        "la": int(tgt["la"]), "wd": int(tgt["wd"]),
        "bt": int(tgt["bt_inject"]),               # LSB-numbered inject bit
        "lfa_hex": tgt["lfa_hex_inject"],
        "bt_pool": int(tgt["bt_pool"]),            # v2 pool bit numbering
        "bt_inject": int(tgt["bt_inject"]),        # = 31 - bt_pool
        "tile": tgt.get("tile"),
        "p0_index": tgt.get("p0_index"),
        "p0_category": tgt.get("p0_category"),
    }
    rec = {"design": ctx.design, "bitstream_sha": ctx.info["bit_sha"],
           "frame_table_version": "v2", "pool_version": POOL_VERSION,
           "seed": seed, "index": index, "kind": "g0",
           "subkind": tgt["group"], "target": [target], "t": now_iso(),
           "void": False, "standby": False, "notes": None,
           "shell_resets": [], "bt_pool": target["bt_pool"],
           "bt_inject": target["bt_inject"]}
    resets0 = ctx.shell_resets_total
    t0 = time.monotonic()
    sem = {"I": None, "N": []}
    try:
        sem["I"] = board.sem_cmd("I", timeout_ms=4000)
        sem["N"].append(board.sem_cmd("N " + target["lfa_hex"],
                                      timeout_ms=4000))
    except Exception as e:                          # noqa: BLE001
        rec.update(category="invalid", void=True,
                   err="inject: %s: %s" % (type(e).__name__, e),
                   sem=sem, telemetry=None, err_flags=None,
                   output_diff_summary=None, health=None,
                   recovery_path="none",
                   repair_report_wd_bt_match=None)
        rec["wall_s"] = round(time.monotonic() - t0, 3)
        append_jsonl(ctx.log_path, rec)
        return rec

    try:
        shot = board.run_shot()
    except Exception as e:                          # noqa: BLE001
        shot = {"ok": False, "err": "%s: %s" % (type(e).__name__, e),
                "telemetry": None}
    tel = shot.get("telemetry")
    stage = ((tel or {}).get("last_stage", 0) + 1) if tel else None
    from wp6_sem_campaign import DESIGNS
    tmr_stage = stage in DESIGNS[ctx.design]["tmr_stages"]
    category, tmr_override = classify_shot(shot, tel, tmr_stage)
    if is_comm_error(shot.get("err")):
        category, void = "invalid", True
    else:
        void = False
    rec.update(category=category, tmr_stage=tmr_stage,
               tmr_override=tmr_override, void=void, err=shot.get("err"),
               telemetry=tel, err_flags=(tel or {}).get("err_flags"))

    def _diff_summary():
        try:
            from k7_paper_sdc_probe import diff_shot, summarize
            diffs = diff_shot(shot["data"], board.golden())
            return summarize(diffs)
        except Exception as e:                      # noqa: BLE001
            return {"error": str(e)}

    rec["output_diff_summary"] = (_diff_summary()
                                  if not shot.get("ok") and not shot.get("err")
                                  else None)

    try:
        sem_info, recovery_path, health_rec = repair_and_health(ctx,
                                                                [target])
    except Exception as e:
        if is_comm_error(e):
            board.reconnect()
            sem_info, recovery_path, health_rec = repair_and_health(
                ctx, [target])
        else:
            raise
    rec["sem"] = {**sem, **sem_info}
    rec["health"] = health_rec
    rec["recovery_path"] = recovery_path
    rec["shell_resets"] = list(range(resets0 + 1, ctx.shell_resets_total + 1))
    rec["wall_s"] = round(time.monotonic() - t0, 3)
    if void:
        rec["category"] = "invalid"

    # WP6-G0 section 3.3: the SEM repair report's WD/BT must equal the
    # injected wd / bt_inject (this shot is what verifies SEM's numbering).
    rep = (sem_info.get("report") or {})

    def _h2i(x):
        return int(x, 16) if x else None

    rec["repair_report_wd"] = rep.get("wd")
    rec["repair_report_bt"] = rep.get("bt")
    rec["repair_report_wd_bt_match"] = (
        _h2i(rep.get("wd")) == target["wd"]
        and _h2i(rep.get("bt")) == target["bt_inject"])
    append_jsonl(ctx.log_path, rec)
    return rec


# ------------------------------------------------------------------ campaign

def cmd_run(args):
    prereg = load_prereg(OUT)
    targets = json.loads(G0_TARGETS.read_text(encoding="utf-8"))
    if args.limit:
        targets = targets[:args.limit]
    bit = prereg["frozen_inputs"][DESIGN]["bit"]
    bit_sha = prereg["designs"][DESIGN]["bit_sha"]

    # board prep: SHA check + JTAG program + resync + idle + 10 health shots
    if sha256_file(bit) != bit_sha:
        raise CampaignStop("frozen bitstream SHA-256 mismatch: %s" % bit)
    log("bitstream SHA-256 verified: %s" % bit)
    board = RealBoard(args.com, prereg["designs"][DESIGN]["arch"])
    ctx = Ctx(DESIGN, prereg, board, OUT)
    ctx.log_path = G0_LOG                      # never write P0_p1/
    ctx.recorded = recorded_keys(ctx.log_path)
    G0_OUT.mkdir(parents=True, exist_ok=True)
    wall0 = time.monotonic()
    recoveries = []
    try:
        if args.program:
            board.reprogram(Path(bit), bit_sha)    # includes resync+idle
            recoveries.append("initial JTAG program %s" % bit)
        else:
            board.ensure_up()
        run_health_gate(ctx, n=10)
        log("initial health gate: 10/10 clean")

        seed = prereg["seeds"]["singles"]          # same seed field as P0
        n_void = 0
        for i, tgt in enumerate(targets):
            key = ("g0", tgt["group"], _bit_key(
                {"la": tgt["la"], "wd": tgt["wd"], "bt": tgt["bt_inject"]}))
            if key in ctx.recorded:
                log("g0 %d/%d %s %s already recorded; skip"
                    % (i + 1, len(targets), tgt["group"],
                       tgt["lfa_hex_inject"]))
                continue
            rec = g0_shot(ctx, tgt, i, seed)
            ctx.recorded.add(key)
            if rec.get("recovery_path") not in (None, "none"):
                recoveries.append("shot %d (%s): recovery_path=%s"
                                  % (i, tgt["lfa_hex_inject"],
                                     rec["recovery_path"]))
            tag = "%d/%d grp=%s %s cat=%s wd_bt_match=%s wall=%.1fs" % (
                i + 1, len(targets), tgt["group"], tgt["lfa_hex_inject"],
                rec.get("category"), rec.get("repair_report_wd_bt_match"),
                rec.get("wall_s") or 0)
            if rec.get("void"):
                n_void += 1
                tag += " [VOID %d/%d]" % (n_void, MAX_VOIDS)
            log(tag)
            if n_void > MAX_VOIDS:
                raise CampaignStop("G0 stop condition: voids > %d" % MAX_VOIDS)
    finally:
        board.close()
    wall = time.monotonic() - wall0
    log("g0 run done in %.1f s; recoveries: %s" % (wall, recoveries))
    write_report(wall_total_s=wall, recoveries=recoveries)
    return 0


# -------------------------------------------------------------------- report

CATEGORY_ZH = {"stuck": "卡住", "noimpact": "无影响", "corrected": "已纠正",
               "flagged": "标出的错", "silent": "静默的错",
               "flagged_but_clean": "标出但输出完好", "invalid": "作废"}


def write_report(wall_total_s=None, recoveries=None):
    recs = read_jsonl(G0_LOG)
    by_group = {"A": [], "B": []}
    for r in recs:
        if r.get("kind") == "g0":
            by_group.setdefault(r.get("subkind"), []).append(r)
    lines = []
    w = lines.append
    w("# WP6-G0 SEM 位序检验报告")
    w("")
    w("> 任务: tasks/WP6_G0_bit_order.md; 位流: P1_WP4R2 "
      "(SHA-256 `53735c3a…c47707`); 目标: results/wp6/g0/g0_targets_p0.json"
      " (A=66, B=9); 日志: results/wp6/g0/campaign.jsonl。")
    w("")
    if wall_total_s is not None:
        w("- 运行墙钟: %.1f s" % wall_total_s)
    if recoveries:
        w("- 恢复动作: " + ("; ".join(recoveries)))
    else:
        w("- 恢复动作: 无")
    n_void = sum(1 for r in recs if r.get("kind") == "g0" and r.get("void"))
    w("- 作废数: %d (停止条件 > %d)" % (n_void, MAX_VOIDS))
    w("")

    counts = {}
    a = b = 0
    for g in ("A", "B"):
        counts[g] = {}
        for r in by_group.get(g, []):
            counts[g][r["category"]] = counts[g].get(r["category"], 0) + 1
            if not r.get("void") and r.get("category") != "noimpact":
                if g == "A":
                    a += 1
                else:
                    b += 1
    w("## 五类计数 (WP6 2.2)")
    w("")
    w("| 组 | 无影响 | 已纠正 | 卡住 | 标出的错 | 静默的错 | 标出但输出完好 | 作废 |")
    w("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for g in ("A", "B"):
        c = counts[g]
        w("| %s | %d | %d | %d | %d | %d | %d | %d |" % (
            g, c.get("noimpact", 0), c.get("corrected", 0),
            c.get("stuck", 0), c.get("flagged", 0), c.get("silent", 0),
            c.get("flagged_but_clean", 0), c.get("invalid", 0)))
    w("")
    w("- a = 非作废 A 组里有影响的发数 = **%d**" % a)
    w("- b = 非作废 B 组里有影响的发数 = **%d**" % b)
    w("")

    # frozen verdict (WP6-G0 section 4)
    if n_void > MAX_VOIDS:
        verdict = "不定（作废 %d 发 > %d）" % (n_void, MAX_VOIDS)
    elif a >= 3 and b <= 1:
        verdict = "M：SEM 按最低位编号，P0 打的是镜像位"
    elif a == 0:
        verdict = "N：SEM 按最高位编号，P0 打的是关键位"
    else:
        verdict = "不定：停下，报告，等作者"
    w("## 判定（第 4 节冻结判据）")
    w("")
    w("**结论: %s** (a=%d, b=%d)" % (verdict, a, b))
    w("")
    if a >= 3 and b <= 1 and n_void <= MAX_VOIDS:
        w("按 M 的改正与 P0 重跑已获批（WP6-G0 第 5 节）。")
    else:
        w("非 M 结论：停下等作者（WP6-G0 第 5 节）。")
    w("")

    w("## 每一发")
    w("")
    w("| # | 组 | 类别 | lfa_hex_inject | wd | bt_inject | "
      "修复报告WD/BT匹配 | 作废 | 恢复 |")
    w("| ---: | --- | --- | --- | ---: | ---: | --- | --- | --- |")
    for r in sorted((x for x in recs if x.get("kind") == "g0"),
                    key=lambda x: x.get("index", 0)):
        t = (r.get("target") or [{}])[0]
        w("| %d | %s | %s (%s) | %s | %s | %s | %s | %s | %s |" % (
            r.get("index"), r.get("subkind"), r.get("category"),
            CATEGORY_ZH.get(r.get("category"), "?"),
            t.get("lfa_hex"), t.get("wd"), t.get("bt_inject"),
            r.get("repair_report_wd_bt_match"), "是" if r.get("void") else "",
            r.get("recovery_path") or ""))
    w("")
    path = G0_OUT / "REPORT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    log("wrote %s" % path)
    return path


def cmd_report(args):
    write_report()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--com", default="/dev/ttyACM0")
    p.add_argument("--program", dest="program", action="store_true",
                   default=True)
    p.add_argument("--no-program", dest="program", action="store_false")
    p.add_argument("--limit", type=int, default=0)
    sub.add_parser("report")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "report":
        return cmd_report(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
