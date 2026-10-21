#!/usr/bin/env python3
"""WP7b non-vacuity audit: prove the bit-exact comparison was real.

Per <design>/<vset> run directory under results/wp7b this checks:

  A. sim.log -- the testbench's explicit $isunknown markers must all be
     zero, the golden compare must have printed, and no FAIL may appear:
       WP7B_XCHECK vectors input_x=0 expected_x=0
       WP7B_XCHECK state  commit_x=0 frames_x=0
       WP7B_XCHECK beats  got_x=0
       ALL 2048 BEATS BIT-EXACT / err=0 det=0 cor=0 unc=0 mis=0
  B. kernel.saif -- the nets V4 reasons about and the kernel boundary
     ports must not carry X:
       * syndrome register nets sy0r/sy0i/sy1r/sy1i  -> TX == 0
       * event nets det/cor/unc/mis/stage_mismatch   -> TX reported
         (det/cor/unc show a bounded pipeline-fill X flush, ~1.4 us max;
         TC==0 still proves no 0<->1 event -- see REPORT.md)
       * kernel boundary nets out_beat[*], u_pipeline_n_*, d_in_data_r[*],
         in_valid, clk_out2, proc_rst_n, rst, data2, rstate*, aux_*, co,
         cs_build_start                              -> TX == 0
         (in_last is allowed: it is an NLW port, TZ only, TX stays 0)

Usage: wp7b_vacuity.py <results/wp7b>          # audits all 20 runs
Exit 0 iff every run passes A + B(mandatory items).
"""
import json
import re
import sys
from pathlib import Path

NET_RE = re.compile(r"^\s*\(\s*(\S+)\s+(.*\))\s*$")
INST_OPEN_RE = re.compile(r"^\s*\(INSTANCE\s+(\S+)")
TX_RE = re.compile(r"\(TX\s+(\d+)\)")
TC_RE = re.compile(r"\(TC\s+(\d+)\)")

SYNDROME_RE = re.compile(r"^sy[01][ri](\\)?\[\d+(\\)?\]$")
EVENT_BASES = {"det", "cor", "unc", "mis"}
EVENT_RE = re.compile(r"^(stage_mismatch|stage_mismatch_reg)"
                      r"(\\)?(\[\d+\])?(\\)?$")
BOUNDARY_RE = re.compile(
    r"^(out_beat|u_pipeline_n_\d+|d_in_data_r|data2|in_valid|in_last|"
    r"clk_out2|proc_rst_n|rst|rstate\d*|aux_\w+|co|cs_build_start)"
    r"(\\)?(\[\d+\])?(\\)?$")

DESIGNS = {"P0_p1": "P0", "P1_p2": "P1", "S0_s3": "S0", "S1_s2": "S1"}
VSETS = ["qual", "edge", "rand_amp", "rand_full", "tones"]

XCHECK = {
    "vectors": re.compile(r"WP7B_XCHECK vectors input_x=(\d+) "
                          r"expected_x=(\d+)"),
    "state": re.compile(r"WP7B_XCHECK state commit_x=(\d+) "
                        r"frames_x=(\d+)"),
    "beats": re.compile(r"WP7B_XCHECK beats got_x=(\d+)"),
}


def audit_log(path):
    txt = path.read_text(errors="replace")
    res = {"bit_exact": "ALL 2048 BEATS BIT-EXACT" in txt,
           "counters0": bool(re.search(
               r"err=0 det=0 cor=0 unc=0 mis=0", txt)),
           "no_fail": "FAIL:" not in txt}
    for k, rx in XCHECK.items():
        m = rx.search(txt)
        res[f"xcheck_{k}"] = bool(m) and all(g == "0" for g in m.groups())
    return res, all(res.values())


def audit_saif(path):
    depth = 0
    scope_at = {}
    syn = ev = bnd = 0
    syn_bad, ev_tx, bnd_bad = [], [], []
    for line in open(path):
        nd = depth + line.count("(") - line.count(")")
        m = INST_OPEN_RE.match(line)
        if m:
            scope_at[nd] = m.group(1)
        for d in list(scope_at):
            if d > nd:
                del scope_at[d]
        depth = nd
        m = NET_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        _tx = TX_RE.search(m.group(2))
        _tc = TC_RE.search(m.group(2))
        tx = int(_tx.group(1)) if _tx else 0
        tc = int(_tc.group(1)) if _tc else 0
        if SYNDROME_RE.match(name):
            syn += 1
            if tx or tc:
                syn_bad.append((scope_at.get(depth, "?"), name, tx, tc))
        elif name in EVENT_BASES or EVENT_RE.match(name):
            ev += 1
            if tx:
                ev_tx.append(tx)
            if tc:
                ev_tx.append((name, "TC", tc))
        elif BOUNDARY_RE.match(name):
            bnd += 1
            if tx:
                bnd_bad.append((scope_at.get(depth, "?"), name, tx))
    return {"syndrome_n": syn, "syndrome_bad": syn_bad,
            "event_n": ev, "event_max_tx": max(
                (x for x in ev_tx if isinstance(x, int)), default=0),
            "event_tc_bad": [x for x in ev_tx if not isinstance(x, int)],
            "boundary_n": bnd, "boundary_bad": bnd_bad}


def main():
    root = Path(sys.argv[1])
    out, rc = {}, 0
    for sub in DESIGNS:
        for v in VSETS:
            d = root / sub / v
            logs, lok = audit_log(d / "sim.log")
            sa = audit_saif(d / "kernel.saif")
            ok = (lok and not sa["syndrome_bad"]
                  and not sa["boundary_bad"] and not sa["event_tc_bad"])
            out[f"{sub}/{v}"] = {**logs, **{k: (len(v2) if isinstance(v2, list)
                                                else v2)
                                          for k, v2 in sa.items()},
                                 "PASS": ok}
            print(f"{sub}/{v}: xchecks={'OK' if lok else 'FAIL'} "
                  f"syndrome={sa['syndrome_n']}(TX0={'Y' if not sa['syndrome_bad'] else 'N'}) "
                  f"event={sa['event_n']}(maxTX={sa['event_max_tx']}) "
                  f"boundary={sa['boundary_n']}(TX0={'Y' if not sa['boundary_bad'] else 'N'}) "
                  f"-> {'PASS' if ok else 'FAIL'}")
            for s, n, tx in sa["boundary_bad"][:10]:
                print(f"    BOUNDARY TX={tx} {n} @ {s}")
            for s, n, tx, tc in sa["syndrome_bad"][:10]:
                print(f"    SYNDROME TX={tx} TC={tc} {n} @ {s}")
            rc |= not ok
    (root / "vacuity_audit.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
