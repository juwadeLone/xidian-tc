#!/usr/bin/env python3
"""Run the WP4-R P0 SECDED decoder diagnostic cases with one XSim compile."""
import argparse
import os
import re
import shutil
import sys
from pathlib import Path

from run_wp4r_p1_faults import ROOT, git_text, run, source_list, tool

CASES = (4, 8, 104, 108, 112, 116, 120, 124, 128)
GOLDEN_RE = re.compile(
    r"TB_GOLDEN_WG arch=5 vset=qual: (ALL 2048 BEATS BIT-EXACT|\d+ FAILURES)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default="results/wp4r/P0_p1/fault_probe_r1.log")
    parser.add_argument("--case-timeout", type=int, default=300)
    args = parser.parse_args()
    vivado_bin = Path(os.environ.get("VIVADO_BIN", r"C:\Xilinx\Vivado\2018.3\bin"))
    log_path = ROOT / args.log
    option_dir = ROOT / "results" / "xsim_qual"
    snapshot = "wp4r_p1_fault_probe"

    head = git_text(["rev-parse", "HEAD"]).strip()
    status = git_text(["status", "--porcelain", "k7/"])
    if status.strip():
        print("WP4R diagnostic run requires a clean k7/ worktree:")
        print(status, end="")
        return 2

    log_path.parent.mkdir(parents=True, exist_ok=True)
    option_dir.mkdir(parents=True, exist_ok=True)
    (ROOT / "sim" / "tb_arch_sel.svh").write_text(
        "localparam integer ARCH_SEL = 5;\n", encoding="ascii")
    shutil.rmtree(ROOT / "xsim.dir", ignore_errors=True)

    results: dict[int, str] = {}
    golden: dict[int, str] = {}
    probes: dict[int, int] = {}
    with log_path.open("w", encoding="utf-8") as log:
        log.write("WP4R_P1_FAULT_PROBE_RUN cases=4,8,104,108,112,116,120,124,128 vset=qual\n")
        log.write(f"WP4R_HEAD={head}\n")
        log.write("$ git -C .. status --porcelain k7/\n")
        log.write(status if status else "<empty>\n")
        log.write("xsim.dir removed before this run: yes\n")
        rc, _, timed_out = run([tool("xvlog", vivado_bin), "--sv", "-i", "sim",
                                "--work", "work", *source_list()], log)
        if rc or timed_out:
            return rc or 1
        rc, _, timed_out = run([tool("xelab", vivado_bin), "-debug", "off",
                                "tb_wp4r_fault_wg", "-s", snapshot], log)
        if rc or timed_out:
            return rc or 1
        for case_no in CASES:
            log.write(f"\n===== WP4R_PROBE_CASE {case_no} =====\n")
            option_file = option_dir / f"wp4r_p1_probe_case_{case_no}.f"
            plusargs = ("-testplusarg VSET=qual -testplusarg WP4R_FAULT=1 "
                        f"-testplusarg WP4R_CASE={case_no}")
            if case_no in (4, 8):
                plusargs += " -testplusarg WP4R_PROBE=1"
            option_file.write_text(plusargs + "\n", encoding="ascii")
            rc, output, timed_out = run(
                [tool("xsim", vivado_bin), snapshot, "-runall", "-f",
                 str(option_file)], log, timeout=args.case_timeout)
            match = GOLDEN_RE.search(output)
            golden[case_no] = match.group(1) if match else "未完成比较"
            probes[case_no] = len(re.findall(r"WP4R_PROBE case=", output))
            if timed_out:
                results[case_no] = "TIMEOUT"
            elif rc:
                results[case_no] = "XSIM_ERROR"
            elif case_no in (4, 8):
                results[case_no] = "DIAG" if probes[case_no] == 32 else "PROBE_INCOMPLETE"
            elif f"WP4R_FAULT_RESULT case={case_no}" in output and \
                    "result=RECORD_ONLY" in output:
                results[case_no] = "RECORD_ONLY"
            else:
                results[case_no] = "NO_RESULT"
            log.write(f"WP4R_PROBE_SUMMARY case={case_no} result={results[case_no]} "
                      f"golden={golden[case_no]} probe_lines={probes[case_no]}\n")

        bad = [case for case in CASES[:2] if results[case] != "DIAG"]
        bad += [case for case in CASES[2:] if results[case] != "RECORD_ONLY"]
        log.write(f"WP4R_PROBE_RUN_SUMMARY bad={bad} results={results} "
                  f"golden={golden} probe_lines={probes}\n")
        log.flush()
    print(f"WP4R_P1_FAULT_PROBE_LOG {log_path}")
    print(f"WP4R_PROBE_RUN_SUMMARY bad={bad}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
