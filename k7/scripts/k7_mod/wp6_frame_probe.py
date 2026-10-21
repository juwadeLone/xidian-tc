"""WP6 batch-3 step 2: frame-table reprobe on the physical board.

Probes SEM LFA -> LA/PA by injecting one bit at (lfa, ECC_WORD=50, bit 0)
- a frame-ECC-word flip the SEM always detects and self-repairs:

    I -> N <pack_sem_n(lfa, ECC_WORD, 0)> -> O -> 4 dump pages -> parse LA/PA

probe_one() is vendored from the proven 2048-2048cs-k7-sem
scripts/k7_cram/k7_la_probe.py (that file has a hardcoded Windows sys.path
line, so the logic is copied here instead of imported).

Campaign (append + resume into results/wp6/frameprobe/la_probe.jsonl):
  1. dense   LFAs 3880..4030 stride 1   (v1 X0Y4 tile_x=23 66-frame column
                                         + both neighbours, for re-segment)
  2. dead    each of the 49 v1 dead_lfas (frame_table_k7.json) individually
  3. coarse  stride 200 over 0..23265 minus the dense range (drift check)

One paper-image health shot (k7_paper_smoke.do_shot, arch p1 = the frozen
P1_WP4R2 bitstream's model) runs before and after the campaign.  Between
probes wait_observation_repair() lets the SEM settle back out of FC40.

Safety: on NAK wait 2 s and retry once; a persistent per-probe failure stops
the campaign (board left with O + verified S status).  UART/SEM/health
failures stop the run with evidence in the jsonl/console.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_cram"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))

from k7_lab_paths import add_host, add_layout_ecc  # noqa: E402
add_layout_ecc(ROOT)
add_host(ROOT)

from k7_sem_common import (  # noqa: E402
    BoardHost, s, parse_sem_report, ECC_WORD, pack_sem_n,
    wait_observation_repair, la_to_lfa, Wire, wait_idle)
from layout_ecc.a7_far import unpack_far  # noqa: E402
import k7_paper_smoke as smoke  # noqa: E402

FTAB_V1 = ROOT / "results" / "k7_mod_fft" / "frame_table_k7.json"
OUT_DIR = ROOT / "results" / "wp6" / "frameprobe"
PROBE_JSONL = OUT_DIR / "la_probe.jsonl"
CAMPAIGN_JSON = OUT_DIR / "campaign.json"
SEM_MF = 23265            # 0x5AE1, checked against the S reply
DENSE_LO, DENSE_HI = 3880, 4030
HEALTH_ARCH = "p1"        # frozen P1_WP4R2 == design P0 (p1) per freeze_manifest


def ts():
    return datetime.now(timezone.utc).isoformat()


def sem_resync(host, tag=0):
    """Measure and repair the FPGA UART command-window desync.

    Every fresh /dev/ttyACM0 connection injects a variable number of spurious
    RX bytes (observed d = 1..5) before/around the first host write.  The
    FPGA's uart_cmd_if counts 16-byte header windows blindly, so all commands
    land misaligned and get NAK'd (or time out) until the counter is
    realigned.  A bad-magic probe with distinctive seq/addr/len is guaranteed
    a NAK whose echoed fields reveal the window offset d; sending (16-d)%16
    filler bytes then realigns.  Verified twice before returning.
    """
    ser = host._ser
    last = {"d": None}

    def probe(seq):
        p = struct.pack("<IBBHII", 0xBAD0BAD0, 0x77, 0x00, seq,
                        0x5A5A0000 | (tag & 0xFFFF), 0x0000C3C3)
        with host._lock:
            host.last_ack = None
            ser.write(p)
            ser.flush()
        t0 = time.time()
        while time.time() - t0 < 8.0:
            with host._lock:
                ack = host.last_ack
            if ack and ack.get("cmd") == 0x8F and ack.get("seq") == seq:
                fld = (struct.pack("<H", ack["seq"])
                       + struct.pack("<I", ack["addr"])
                       + struct.pack("<I", ack["length"]))
                return p, fld
            time.sleep(0.02)
        return None, None

    for attempt in range(3):
        seq = 0xA500 + attempt
        p, fld = probe(seq)
        if fld is None:
            print("resync: probe NAK timeout (attempt %d)" % attempt,
                  flush=True)
            continue
        d = None
        for cand in range(0, 7):
            if p[6 - cand:16 - cand] == fld:
                d = cand
                break
        if d is None:
            print("resync: unmatched NAK fields %s (attempt %d)"
                  % (fld.hex(), attempt), flush=True)
            time.sleep(0.5)
            continue
        fill = (16 - d) % 16
        if fill:
            ser.write(b"\x00" * fill)
            ser.flush()
            time.sleep(0.5)
        # verify: a fresh probe must now report d == 0
        p2, fld2 = probe(0xB000 + attempt)
        ok = fld2 is not None and p2[6:16] == fld2
        print("resync: attempt %d d=%d fill=%d verify=%s" %
              (attempt, d, fill, ok), flush=True)
        if ok:
            last["d"] = d
            return d
    raise RuntimeError("sem_resync failed after 3 attempts")


def probe_one(host, lfa):
    """One ECC-word probe; vendored from 2048-2048cs-k7-sem k7_la_probe.py."""
    ncmd = "N " + pack_sem_n(lfa, ECC_WORD, 0)
    host.uart_cmd_sem("I", timeout_ms=3000)
    nrep = s(host.uart_cmd_sem(ncmd, timeout_ms=3000))
    orep = s(host.uart_cmd_sem("O", timeout_ms=8000))
    pages = [s(p) for p in host.dump_monitor_pages(n_pages=4,
                                                  settle_s=0.35)]
    stitched = orep + "\n" + "".join(pages)
    rep = parse_sem_report(stitched)
    pa = int(rep["pa"], 16) if rep.get("pa") else None
    la_lfa = la_to_lfa(rep["la"]) if rep.get("la") else None
    return {"lfa": lfa, "ncmd": ncmd, "n_rep": nrep,
            "la": rep["la"], "la_lfa": la_lfa,
            "la_matches_cmd": (la_lfa == lfa) if la_lfa is not None else None,
            "pa": rep["pa"], "pa_fields": unpack_far(pa) if pa is not None else None,
            "wd": rep["wd"], "bt": rep["bt"], "cor": rep["cor"],
            "reply_snippet": stitched[-300:]}


def load_done_lfas(path):
    """lfas with a usable record (LA or PA parsed) -> done; error-only -> redo."""
    done, failed = set(), set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            lf = r.get("lfa")
            if lf is None:
                continue
            if r.get("la") or r.get("pa"):
                done.add(int(lf))
            else:
                failed.add(int(lf))
    return done, failed


def build_plan(v1):
    dense = list(range(DENSE_LO, DENSE_HI + 1))
    dead = sorted(int(x) for x in v1.get("dead_lfas") or [])
    in_dense = set(range(DENSE_LO, DENSE_HI + 1))
    stride = v1.get("_coarse_stride", 200)
    coarse = [lf for lf in range(0, SEM_MF + 1, stride)
              if lf not in in_dense]
    return [("dense", dense), ("dead", dead), ("coarse", coarse)]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--com", default="/dev/ttyACM0")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--stride", type=int, default=200,
                    help="coarse sweep stride (200 default; 400 if pressed)")
    ap.add_argument("--no-coarse", action="store_true")
    args = ap.parse_args(argv)

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    jpath = out / "la_probe.jsonl"

    v1 = json.loads(FTAB_V1.read_text(encoding="utf-8"))
    v1["_coarse_stride"] = args.stride
    plan = build_plan(v1)
    if args.no_coarse:
        plan = plan[:2]

    done, failed = load_done_lfas(jpath)
    print("resume: done=%d error-only=%d" % (len(done), len(failed)),
          flush=True)

    summary = {"started_utc": ts(), "com": args.com,
               "bitstream": "/home/xdu/frozen/wp6/P1_WP4R2/k7_paper_top.bit",
               "dense_range": [DENSE_LO, DENSE_HI],
               "coarse_stride": args.stride,
               "categories": {}, "health": {}, "sem": {}, "stop_reason": None}

    jf = jpath.open("a", encoding="utf-8")
    host = BoardHost(log=lambda x: print("  [host] " + str(x), flush=True))
    wire = None
    n_new = n_skip = 0
    try:
        host.start(com=args.com, udp=True)
        wire = Wire()
        wait_idle(host, 60.0)
        time.sleep(2.0)          # let connect-time spurious RX bytes land
        sem_resync(host)         # realign the FPGA 16-byte command window

        srep = s(host.uart_cmd_sem("S", timeout_ms=4000))
        summary["sem"]["S_boot"] = srep
        summary["sem"]["status_boot"] = dict(host.last_sem_status)
        print("SEM S: %r status=%s" % (srep, host.last_sem_status), flush=True)
        if "5AE1" not in srep.upper():
            raise RuntimeError("SEM MF mismatch: %r (want MF 00005AE1)" % srep)

        raw = smoke.load_input_raw()
        golden = smoke.load_golden(HEALTH_ARCH)
        h = smoke.do_shot(host, wire, raw, golden)
        summary["health"]["pre"] = h
        print("health pre : ok=%s n_diff=%s err=%s" %
              (h.get("ok"), h.get("n_diff"), h.get("err")), flush=True)
        if not h.get("ok"):
            raise RuntimeError("pre-campaign health shot failed: %s" % h)

        for cat, lfas in plan:
            stat = summary["categories"].setdefault(
                cat, {"planned": len(lfas), "probed": 0, "skipped_done": 0,
                      "no_report": 0, "error": 0, "la_mismatch": 0})
            for lfa in lfas:
                if lfa in done:
                    stat["skipped_done"] += 1
                    n_skip += 1
                    continue
                rec = None
                for attempt in (1, 2):
                    try:
                        rec = probe_one(host, lfa)
                        break
                    except Exception as e:  # noqa: BLE001
                        msg = str(e)
                        print("probe lfa=%d attempt %d: %s" %
                              (lfa, attempt, msg), flush=True)
                        if "NAK" in msg.upper():
                            time.sleep(2.0)     # NAK_BUSY: settle, retry once
                            continue
                        if "超时" in msg and attempt == 1:
                            # possible mid-session window desync: realign once
                            try:
                                sem_resync(host, tag=lfa & 0xFFFF)
                                continue
                            except Exception as e2:  # noqa: BLE001
                                print("mid-session resync failed: %s" % e2,
                                      flush=True)
                        rec = {"lfa": lfa, "error": msg}
                        break
                if rec is None:
                    rec = {"lfa": lfa, "error": "NAK_BUSY after retry"}
                rec["ts"] = ts()
                rec["category"] = cat
                jf.write(json.dumps(rec) + "\n")
                jf.flush()
                stat["probed"] += 1
                n_new += 1
                f = rec.get("pa_fields")
                if rec.get("error"):
                    stat["error"] += 1
                    summary["stop_reason"] = (
                        "persistent probe failure at lfa %d: %s"
                        % (lfa, rec["error"]))
                elif not rec.get("la") and not rec.get("pa"):
                    stat["no_report"] += 1   # still silent (dead probe)
                elif rec.get("la_matches_cmd") is False:
                    stat["la_mismatch"] += 1
                print("lfa=%5d [%s] la=%s pa=%s %s" % (
                    lfa, cat, rec.get("la"), rec.get("pa"),
                    ("top%d row%d col%d min%d blk%d" % (
                        f["top"], f["row"], f["column"], f["minor"],
                        f.get("block", 0))) if f else rec.get("error", "")),
                    flush=True)
                if rec.get("error"):
                    break
                try:
                    wait_observation_repair(host)
                except Exception as e:  # noqa: BLE001
                    print("wait_observation_repair: %s" % e, flush=True)
            if summary["stop_reason"]:
                break

        if not summary["stop_reason"]:
            h2 = smoke.do_shot(host, wire, raw, golden)
            summary["health"]["post"] = h2
            print("health post: ok=%s n_diff=%s err=%s" %
                  (h2.get("ok"), h2.get("n_diff"), h2.get("err")), flush=True)
    except Exception as e:  # noqa: BLE001
        summary["stop_reason"] = "exception: %s: %s" % (type(e).__name__, e)
        print("STOP: %s" % summary["stop_reason"], flush=True)
    finally:
        # leave the SEM out of injection: O, then verify with S
        try:
            host.uart_cmd_sem("O", timeout_ms=8000)
            time.sleep(0.5)
            srep = s(host.uart_cmd_sem("S", timeout_ms=4000))
            summary["sem"]["S_final"] = srep
            summary["sem"]["status_final"] = dict(host.last_sem_status)
            print("SEM S final: %r status=%s" % (srep, host.last_sem_status),
                  flush=True)
        except Exception as e:  # noqa: BLE001
            summary["sem"]["final_error"] = str(e)
        try:
            if wire:
                wire.close()
            host.stop()
        except Exception:  # noqa: BLE001
            pass
        jf.close()

    summary["finished_utc"] = ts()
    summary["n_new_records"] = n_new
    summary["n_skipped"] = n_skip
    (out / "campaign.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    print("PROBE_DONE new=%d skipped=%d stop=%s" %
          (n_new, n_skip, summary["stop_reason"]), flush=True)
    return 1 if summary["stop_reason"] else 0


if __name__ == "__main__":
    sys.exit(main())
