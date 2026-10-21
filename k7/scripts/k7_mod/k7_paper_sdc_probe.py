"""Re-run the SDC targets from a k7_paper_smoke campaign and dump
sample-level diffs vs golden.

For each recorded injected shot whose shot_result.ok is False:
  inject same (la, wd, bt) -> WR/RUN/RD -> decode 216b beats into
  8 x 27-bit signed fields -> diff vs golden -> repair -> health shot.

Writes one jsonl record per shot with the full diff detail, plus a
console summary.

Usage:
    python scripts/k7_mod/k7_paper_sdc_probe.py --com COM5
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_cram"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))

from k7_lab_paths import add_layout_ecc, add_host  # noqa: E402
add_layout_ecc(ROOT)
add_host(ROOT)

from k7_real_data_gate import BoardHost, Wire, wait_idle  # noqa: E402
import k7_paper_host as ph  # noqa: E402

from k7_paper_smoke import load_input_raw, load_golden, do_shot, s  # noqa

OUT_W = 27
FIELDS_PER_BEAT = 8


def decode_beat(word: int):
    """216-bit beat -> 8 signed 27-bit fields, MSB-first (w0.re ... w3.im)."""
    out = []
    for i in range(FIELDS_PER_BEAT):
        v = (word >> (OUT_W * (FIELDS_PER_BEAT - 1 - i))) & ((1 << OUT_W) - 1)
        if v & (1 << (OUT_W - 1)):
            v -= 1 << OUT_W
        out.append(v)
    return out


def diff_shot(got: bytes, golden: bytes):
    """Beat/field-level diff.  Returns list of per-beat diff records."""
    n_beats = len(golden) // ph.OUT_BEAT_BYTES
    recs = []
    for b in range(n_beats):
        gw = int.from_bytes(golden[ph.OUT_BEAT_BYTES * b:
                                   ph.OUT_BEAT_BYTES * b + ph.OUT_BEAT_BYTES],
                            "little")
        bw = int.from_bytes(got[ph.OUT_BEAT_BYTES * b:
                                ph.OUT_BEAT_BYTES * b + ph.OUT_BEAT_BYTES],
                            "little")
        if gw == bw:
            continue
        gf, bf = decode_beat(gw), decode_beat(bw)
        fields = []
        for i, (e, a) in enumerate(zip(gf, bf)):
            if e == a:
                continue
            xor = (e ^ a) & ((1 << OUT_W) - 1)
            fields.append({"field": i, "exp": e, "got": a,
                           "err": a - e, "xor": xor,
                           "xor_pow2": bool(xor and not (xor & (xor - 1)))})
        recs.append({"beat": b, "frame": b // ph.FRAME_BEATS,
                     "beat_in_frame": b % ph.FRAME_BEATS,
                     "xor_beat": gw ^ bw, "fields": fields})
    return recs


FIELD_NAME = {i: "w%d.%s" % (i // 2, "re" if i % 2 == 0 else "im")
              for i in range(FIELDS_PER_BEAT)}


def summarize(diffs):
    n_fields = sum(len(d["fields"]) for d in diffs)
    pow2 = sum(1 for d in diffs for f in d["fields"] if f["xor_pow2"])
    frames = sorted({d["frame"] for d in diffs})
    by_field = collections.Counter()
    bit_pos = [0] * OUT_W
    for d in diffs:
        for f in d["fields"]:
            by_field[FIELD_NAME[f["field"]]] += 1
            x = f["xor"]
            for b in range(OUT_W):
                if (x >> b) & 1:
                    bit_pos[b] += 1
    beats = [d["beat"] for d in diffs]
    return {"n_beats": len(diffs), "n_fields": n_fields,
            "pow2_fields": pow2, "frames_hit": frames,
            "beats_per_frame": {f: sum(1 for d in diffs if d["frame"] == f)
                                for f in frames},
            "first_beat": beats[0] if beats else None,
            "last_beat": beats[-1] if beats else None,
            "first_frame": diffs[0]["frame"] if diffs else None,
            "last_frame": diffs[-1]["frame"] if diffs else None,
            "by_field": dict(sorted(by_field.items())),
            "bit_pos_hist": bit_pos}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--com", default="COM5")
    ap.add_argument("--arch", choices=["s0", "p0"], default="s0")
    ap.add_argument("--log", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    resdir = ROOT / "results" / f"k7_paper_{args.arch}"
    if args.log is None:
        args.log = resdir / "smoke.jsonl"
    if args.out is None:
        args.out = resdir / "sdc_diff.jsonl"

    raw = load_input_raw()
    golden = load_golden(args.arch)

    # pick the recorded SDC targets (keeps LFA/word/bit identical to campaign)
    targets = []
    for line in args.log.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d.get("kind") == "injected" and not d["shot_result"]["ok"]:
            targets.append((d["shot"], d["target"]))
    print(f"sdc targets: {[t[0] for t in targets]}")

    out = args.out.open("w", encoding="utf-8")

    def emit(kind, rec):
        rec = {"kind": kind, "t": datetime.now(timezone.utc).isoformat(),
               **rec}
        out.write(json.dumps(rec) + "\n")
        out.flush()

    host = BoardHost(log=lambda m: print("  ", m, flush=True))
    try:
        host.start(com=args.com, udp=True)
        wire = Wire()
        wait_idle(host, 60.0)
        s(host.uart_cmd_sem("S", timeout_ms=4000))

        h = do_shot(host, wire, raw, golden)
        if not h["ok"]:
            # a previous crashed run may have left a flipped bit resident;
            # issue 'O' once to repair, then re-verify before continuing.
            print(f"pre-existing fault (n_diff={h['n_diff']}) -> 'O' repair")
            s(host.uart_cmd_sem("O", timeout_ms=8000))
            time.sleep(1.0)
            h = do_shot(host, wire, raw, golden)
        emit("health", h)
        print("health", h["ok"], "n_diff", h["n_diff"])
        if not h["ok"]:
            print("STOP: health shot failed")
            return 2

        for shot_id, t in targets:
            s(host.uart_cmd_sem("I", timeout_ms=4000))
            ack = bool(s(host.uart_cmd_sem("N " + t["lfa_hex"],
                                         timeout_ms=4000)))
            try:
                res = ph.shot(host, wire, raw, golden=None)
                diffs = diff_shot(res["data"], golden)
                summ = summarize(diffs)
                emit("sdc_diff", {"shot": shot_id, "target": t,
                                  "inject_ack": ack,
                                  "sha256": res["sha256"],
                                  "summary": summ, "diffs": diffs})
            finally:
                s(host.uart_cmd_sem("O", timeout_ms=8000))
                time.sleep(1.0)
            print(f"shot {shot_id} {t['lfa_hex']}: beats={summ['n_beats']} "
                  f"fields={summ['n_fields']} pow2={summ['pow2_fields']} "
                  f"frames={summ['frames_hit']}")
            for d in diffs[:12]:
                print(f"   f{d['frame']} beat{d['beat_in_frame']:3d} "
                      f"xor=0x{d['xor_beat']:044x}")
                for f in d["fields"]:
                    print(f"      fld{f['field']} exp={f['exp']:>9d} "
                          f"got={f['got']:>9d} err={f['err']:>+10d} "
                          f"xor=0x{f['xor']:06x}"
                          f"{' [single-bit]' if f['xor_pow2'] else ''}")
            if len(diffs) > 12:
                print(f"   ... +{len(diffs) - 12} more beats")

            # repair already issued in finally; prove clean before next shot
            hh = do_shot(host, wire, raw, golden)
            emit("post_health", {"shot": shot_id, "ok": hh["ok"],
                                 "n_diff": hh["n_diff"]})
            print(f"   post-repair health ok={hh['ok']}")
            if not hh["ok"]:
                print("STOP: image did not recover")
                return 3
    finally:
        host.stop()
        out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
