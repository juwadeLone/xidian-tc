#!/usr/bin/env python3
"""WP7b V4 self-check: zero fault/event toggles inside the SAIF window.

Reads a kernel SAIF produced by tb_power_saif_wg + sim_saif.tcl and verifies:

  * ECC designs (P0_p1, S0_s3): every per-stage syndrome register net
    u_decode.sy0r[*] / sy0i[*] / sy1r[*] / sy1i[*] has TC == 0.
  * All designs: every event-flag net whose basename is one of
    {det, cor, unc, mis, stage_mismatch, stage_mismatch[*]} has TC == 0.

TX (time in X) is reported for information only -- unisim nets start in X
during GSR and settle to 0; what matters is that no 0<->1 transition ever
occurs (TC == 0).

Usage: wp7b_saif_v4check.py <kernel.saif> [kernel2.saif ...]
Exit 0 iff every file passes; prints one verdict line per file.
"""
import re
import sys

NET_RE = re.compile(r"^\s*\(\s*(\S+)\s+(.*\))\s*$")
INST_OPEN_RE = re.compile(r"^\s*\(INSTANCE\s+(\S+)")
TC_RE = re.compile(r"\(TC\s+(\d+)\)")
TX_RE = re.compile(r"\(TX\s+(\d+)\)")

SYNDROME_RE = re.compile(r"^sy[01][ri](\\)?\[\d+(\\)?\]$")
EVENT_BASES = {"det", "cor", "unc", "mis"}
EVENT_RE = re.compile(r"^(stage_mismatch|stage_mismatch_reg)"
                      r"(\\)?(\[\d+\])?(\\)?$")


def classify(basename):
    if SYNDROME_RE.match(basename):
        return "syndrome"
    if basename in EVENT_BASES or EVENT_RE.match(basename):
        return "event"
    return None


def check(path):
    inst_at_depth = {}
    depth = 0
    hits = {"syndrome": [], "event": []}
    with open(path) as fh:
        for line in fh:
            opens = line.count("(")
            closes = line.count(")")
            new_depth = depth + opens - closes
            m = INST_OPEN_RE.match(line)
            if m:
                inst_at_depth[new_depth] = m.group(1)
            for d in list(inst_at_depth):
                if d > new_depth:
                    del inst_at_depth[d]
            depth = new_depth
            m = NET_RE.match(line)
            if not m:
                continue
            name, rest = m.group(1), m.group(2)
            cls = classify(name)
            if cls is None:
                continue
            tc = TC_RE.search(rest)
            tx = TX_RE.search(rest)
            scope = inst_at_depth.get(depth, "?")
            hits[cls].append((scope, name,
                              int(tc.group(1)) if tc else -1,
                              int(tx.group(1)) if tx else 0))
    bad = [(s, n, tc) for cls in hits for (s, n, tc, _tx) in hits[cls]
           if tc != 0]
    ok = not bad and bool(hits["event"])
    return ok, hits, bad


def main():
    rc = 0
    for path in sys.argv[1:]:
        ok, hits, bad = check(path)
        nsyn = len(hits["syndrome"])
        nev = len(hits["event"])
        maxtx = max((tx for _s, _n, _t, tx in hits["event"]), default=0)
        print(f"{path}: syndrome_nets={nsyn} event_nets={nev} "
              f"max_event_TX_ps={maxtx} -> {'PASS' if ok else 'FAIL'}")
        for s, n, tc in bad[:20]:
            print(f"    NONZERO TC={tc} {n} @ {s}")
        if not ok:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
