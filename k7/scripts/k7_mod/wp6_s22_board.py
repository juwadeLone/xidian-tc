"""WP6 §22.5 approved board re-shot batch (author approval 2026-10-18,
commit 3cc8724).  13 bits x 5 re-shots, serial, grouped by design in the
order P0 -> S0 -> P1 -> S1.

Frozen rules (tasks/WP6_sem_board.md §22.5):
  * bitstream SHA-256 must match the campaign record before programming;
  * one fault-free health shot after every bitstream load;
  * repair report mismatch -> original reload/recovery rule
    (repair_and_health, unchanged);
  * communication timeout -> ONE retry inside the same run;
  * STOP immediately on: sha mismatch / post-reload health fail /
    comm fail after retry / output-diff summary not recorded;
  * original campaign results are not modified; re-shots are diagnostics.

Records go to results/wp6/<design_dir>/s22_reshoot.jsonl (NOT
campaign.jsonl) and are aggregated into results/wp6/S22_reshoot.{md,json}.

Usage:
    python3 wp6_s22_board.py --com /dev/ttyACM0           # run all
    python3 wp6_s22_board.py --com /dev/ttyACM0 --design P0
    python3 wp6_s22_board.py --combine                    # reports only
    python3 wp6_s22_board.py --check                      # sha/original
                                                          # records only
"""
import argparse
import gzip
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wp6_sem_campaign as sc  # noqa: E402

OUT = sc.OUT
DESIGN_DIR = sc.DESIGN_DIR

# author-approved batch (WP6_sem_board.md §22.5), executed in this order
RESHOT = [("P0", [1264, 4990, 186, 3939, 4005, 4962, 1285]),
          ("S0", [1712, 1751, 4816]),
          ("P1", [1377]),
          ("S1", [144, 2317])]


def s22_log_path(design):
    return OUT / DESIGN_DIR[design] / "s22_reshoot.jsonl"


def campaign_records(design):
    """index -> original 'single' record for the design."""
    out = {}
    for name in ("campaign.jsonl", "campaign.jsonl.gz"):
        p = OUT / DESIGN_DIR[design] / name
        if not p.is_file():
            continue
        op = gzip.open if name.endswith("gz") else open
        for ln in op(p, "rt", encoding="utf-8"):
            r = json.loads(ln)
            if r.get("kind") == "single" and not r.get("void"):
                out[r["index"]] = r
        if out:
            return out
    return out


class _InjectComm(RuntimeError):
    """Communication error during the inject phase (I/N) of a run."""


def run_one(ctx, targets, orig_idx, rep):
    """One re-shot run: I -> N -> WR/RUN/RD -> classify -> diff ->
    repair+health.  Returns the record (not yet appended).

    Comm-error policy (one retry inside the same run):
      * inject phase -> _InjectComm -> caller retries the whole run once;
      * shot phase -> reconnect + one in-shot retry here;
      * repair phase -> repair_and_health's original single retry.
    """
    board = ctx.board
    rec = {"design": ctx.design, "bitstream_sha": ctx.info["bit_sha"],
           "frame_table_version": "v2", "kind": "reshoot22",
           "subkind": "s22", "orig_index": orig_idx,
           "index": orig_idx * 100 + rep, "reshot_no": rep,
           "target": targets, "t": sc.now_iso(), "void": False,
           "shell_resets": [], "retry_used": False}
    resets0 = ctx.shell_resets_total
    t0 = time.monotonic()
    sem = {"I": None, "N": []}
    try:
        sem["I"] = board.sem_cmd("I", timeout_ms=4000)
        for t in targets:
            sem["N"].append(board.sem_cmd("N " + t["lfa_hex"],
                                          timeout_ms=4000))
    except Exception as e:                          # noqa: BLE001
        if sc.is_comm_error(e):
            raise _InjectComm(str(e))
        raise
    shot = board.run_shot()
    if sc.is_comm_error(shot.get("err")):
        # injected state still applied; retry the WR/RUN/RD once
        sc.log("shot comm error (%s) -> reconnect + one retry"
               % shot["err"])
        board.reconnect()
        shot = board.run_shot()
        rec["retry_used"] = True
        rec["retry_where"] = "shot"
        if sc.is_comm_error(shot.get("err")):
            rec.update(category="invalid", void=True,
                       err="comm fail after retry: %s" % shot["err"],
                       sem=sem, telemetry=None, output_diff_summary=None,
                       health=None, recovery_path="none")
            sc.append_jsonl(s22_log_path(ctx.design), rec)
            raise sc.CampaignStop(
                "§22.5 stop: comm failure after the one in-run retry "
                "(idx=%s rep=%s)" % (orig_idx, rep))
    tel = shot.get("telemetry")
    sc.assert_tel_arch(ctx.design, tel)
    stage = ((tel or {}).get("last_stage", 0) + 1) if tel else None
    tmr_stage = stage in sc.DESIGNS[ctx.design]["tmr_stages"]
    category, tmr_override = sc.classify_shot(shot, tel, tmr_stage)
    void = sc.is_comm_error(shot.get("err"))
    if void:
        category = "invalid"
    rec.update(category=category, tmr_stage=tmr_stage,
               tmr_override=tmr_override, void=void,
               err=shot.get("err"), telemetry=tel,
               err_flags=(tel or {}).get("err_flags"),
               n_diff=shot.get("n_diff"), tail_zero=shot.get("tail_zero"))
    if not shot.get("ok") and not shot.get("err"):
        try:
            diff_shot, summarize = sc._diff_tools()
            diffs = diff_shot(shot["data"], board.golden())
            rec["output_diff_summary"] = summarize(diffs)
            # keep reshoot per-beat diffs out of the original campaign
            # diff log: separate file, same entry schema
            p = s22_log_path(ctx.design).with_name(
                "s22_output_diffs.jsonl.gz")
            entry = {"index": rec["index"], "kind": "reshoot22",
                     "subkind": "s22", "target_key": sc.target_key(rec),
                     "diffs": diffs}
            with gzip.open(p, "at", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:                      # noqa: BLE001
            rec["output_diff_summary"] = {"error": str(e)}
    else:
        rec["output_diff_summary"] = None
    try:
        sem_info, recovery_path, health_rec = sc.repair_and_health(
            ctx, targets)
    except Exception as e:
        if sc.is_comm_error(e):
            board.reconnect()
            sem_info, recovery_path, health_rec = sc.repair_and_health(
                ctx, targets)
        else:
            raise
    rec["sem"] = sem
    rec["sem"].update(sem_info)
    rec["health"] = health_rec
    rec["recovery_path"] = recovery_path
    rec["shell_resets"] = list(range(resets0 + 1,
                                   ctx.shell_resets_total + 1))
    rec["wall_s"] = round(time.monotonic() - t0, 3)
    return rec


def reshot(ctx, orig, rep):
    """run_one with the §22.5 retry/stop rules."""
    logp = s22_log_path(ctx.design)
    try:
        rec = run_one(ctx, orig["target"], orig["index"], rep)
    except _InjectComm as e:
        # one retry of the whole run on an inject-phase comm error
        sc.log("reshot %s#%d inject comm error (%s) -> retry once"
               % (ctx.design, orig["index"], e))
        try:
            ctx.board.sem_cmd("O", timeout_ms=4000)
        except Exception:                           # noqa: BLE001
            pass
        ctx.board.reconnect()
        try:
            rec = run_one(ctx, orig["target"], orig["index"], rep)
        except _InjectComm as e2:
            bad = {"design": ctx.design, "kind": "reshoot22",
                   "subkind": "s22", "orig_index": orig["index"],
                   "index": orig["index"] * 100 + rep, "reshot_no": rep,
                   "target": orig["target"], "t": sc.now_iso(),
                   "category": "invalid", "void": True,
                   "err": "inject comm fail after retry: %s" % e2,
                   "retry_used": True, "retry_where": "inject",
                   "telemetry": None, "output_diff_summary": None,
                   "health": None, "recovery_path": "none"}
            sc.append_jsonl(logp, bad)
            raise sc.CampaignStop(
                "§22.5 stop: comm failure after the one in-run retry "
                "(idx=%s rep=%s): %s" % (orig["index"], rep, e2))
        rec["retry_used"] = True
        rec["retry_where"] = "inject"
        rec["retry_cause"] = str(e)
    # §22.5 stop condition: diff summary must have been recorded when the
    # shot produced wrong output
    if not rec.get("void") and rec.get("err") is None:
        ds = rec.get("output_diff_summary")
        # flagged_but_clean means the output vector was clean -- a diff
        # summary of None is correct there; the stop only applies when
        # output was actually wrong (n_diff>0 / silent / flagged).
        bad_out = (rec.get("n_diff") not in (0, None)
                   or rec.get("category") in ("silent", "flagged"))
        if bad_out and (not isinstance(ds, dict) or "error" in ds):
            sc.append_jsonl(logp, rec)
            raise sc.CampaignStop(
                "§22.5 stop: output diff summary not recorded "
                "(design=%s idx=%s rep=%s)" % (ctx.design,
                                               orig["index"], rep))
    sc.append_jsonl(logp, rec)
    return rec


def run_design(design, com):
    prereg = sc.load_prereg(OUT)
    info = prereg["designs"][design]
    bit = Path(prereg["frozen_inputs"][design]["bit"])
    sha_file = sc.sha256_file(bit)
    if sha_file != info["bit_sha"]:
        raise sc.CampaignStop(
            "§22.5 stop: frozen bitstream SHA-256 mismatch %s "
            "(file %s vs record %s)" % (bit, sha_file, info["bit_sha"]))
    orig = campaign_records(design)
    board = sc.RealBoard(com, info["arch"])
    ctx = sc.Ctx(design, prereg, board)
    ctx.log_path = s22_log_path(design)
    done = {(r.get("orig_index"), r.get("reshot_no"))
            for r in sc.read_jsonl(ctx.log_path)}
    try:
        board.reprogram(bit, info["bit_sha"])   # sha-checked, then connect
        h = sc.health_pass(ctx)
        if not h.get("counters_zero"):
            raise sc.CampaignStop(
                "§22.5 stop: fault-free shot after bitstream load failed "
                "(design=%s health=%s)" % (design, h.get("err")))
        sc.log("%s: post-program fault-free shot clean" % design)
        want = dict(RESHOT)[design]
        for idx in want:
            if idx not in orig:
                raise sc.CampaignStop("original record missing: %s #%s"
                                      % (design, idx))
            for rep in range(1, 6):
                if (idx, rep) in done:
                    continue
                rec = reshot(ctx, orig[idx], rep)
                sc.log("%s #%d rep%d -> %s tel=%s n_diff=%s"
                       % (design, idx, rep, rec.get("category"),
                          (rec.get("telemetry") or {}).get("det_cnt"),
                          rec.get("n_diff")))
    finally:
        board.close()


def combine():
    evs = {}
    for d in DESIGN_DIR:
        p = s22_log_path(d)
        if p.is_file():
            evs[d] = [r for r in sc.read_jsonl(p)
                      if r.get("kind") == "reshoot22"]
    orig = {d: campaign_records(d) for d in evs}
    md = ["# WP6 §22.5 板上重打批次（13 位×5，作者 2026-10-18 批准）\n",
          "诊断性重打；原始 campaign 记录未改。顺序 P0→S0→P1→S1。\n"]
    js = {"designs": {}}
    for d, bits in RESHOT:
        recs = evs.get(d, [])
        md.append("\n## %s\n" % d)
        dj = {}
        for idx in bits:
            o = (orig.get(d) or {}).get(idx, {})
            rs = sorted((r for r in recs if r.get("orig_index") == idx),
                        key=lambda r: r.get("reshot_no", 0))
            md.append("\n### #%d（原结果 %s）\n"
                      % (idx, o.get("category")))
            md.append("| 次 | 类别 | det/unc/cor | n_diff | 差异摘要 "
                      "| 修复 | 恢复路径 | 重试 | 与原一致 |\n"
                      "| --- | --- | --- | ---: | --- | --- | --- | --- "
                      "| --- |\n")
            rows = []
            for r in rs:
                tel = r.get("telemetry") or {}
                ds = r.get("output_diff_summary")
                ds_s = ("n_beats=%s n_fields=%s" % (ds.get("n_beats"),
                                                    ds.get("n_fields"))
                        if isinstance(ds, dict) and "error" not in ds
                        else ("未记录" if ds is None else
                              "错误:" + str(ds.get("error"))))
                agree = r.get("category") == o.get("category")
                sem = r.get("sem") or {}
                rep = sem.get("report") or {}
                rows.append({"reshot_no": r.get("reshot_no"),
                             "category": r.get("category"),
                             "telemetry": tel,
                             "n_diff": r.get("n_diff"),
                             "output_diff_summary": ds,
                             "repair_match": sem.get("repair_match"),
                             "repair_matched": sem.get("repair_matched"),
                             "fc40": rep.get("fc40"),
                             "recovery_path": r.get("recovery_path"),
                             "retry_used": r.get("retry_used"),
                             "void": r.get("void"), "err": r.get("err"),
                             "agrees_with_original": agree})
                md.append("| %s | %s | %s/%s/%s | %s | %s | %s | %s | %s "
                          "| %s |\n"
                          % (r.get("reshot_no"), r.get("category"),
                             tel.get("det_cnt"), tel.get("unc_cnt"),
                             tel.get("cor_cnt"), r.get("n_diff"), ds_s,
                             sem.get("repair_match"),
                             r.get("recovery_path"),
                             "是" if r.get("retry_used") else "-",
                             "一致" if agree else "**不一致**"))
            dj[str(idx)] = {"original_category": o.get("category"),
                            "original_telemetry": o.get("telemetry"),
                            "reshots": rows}
            if len(rs) < 5:
                md.append("\n（仅完成 %d/5 次）\n" % len(rs))
        js["designs"][d] = dj
    (OUT / "S22_reshoot.md").write_text("".join(md), encoding="utf-8")
    (OUT / "S22_reshoot.json").write_text(
        json.dumps(js, ensure_ascii=False, indent=1), encoding="utf-8")
    sc.log("wrote S22_reshoot.md/.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--com", default="COM5")
    ap.add_argument("--design", choices=[d for d, _ in RESHOT])
    ap.add_argument("--combine", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="offline only: verify sha + original records")
    args = ap.parse_args()
    if args.combine:
        combine()
        return
    if args.check:
        prereg = sc.load_prereg(OUT)
        for d, bits in RESHOT:
            info = prereg["designs"][d]
            bit = Path(prereg["frozen_inputs"][d]["bit"])
            ok = sc.sha256_file(bit) == info["bit_sha"]
            orig = campaign_records(d)
            missing = [i for i in bits if i not in orig]
            print("%s sha_ok=%s missing_orig=%s bitstream_sha=%s"
                  % (d, ok, missing, info["bit_sha"][:16]))
        return
    order = [args.design] if args.design else [d for d, _ in RESHOT]
    for d in order:
        sc.log("=== S22.5 board batch: %s ===" % d)
        run_design(d, args.com)
    combine()


if __name__ == "__main__":
    main()
