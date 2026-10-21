#!/usr/bin/env python3
"""Run the 18 frozen WP4-R P1 fault cases with one XSim compile."""
import argparse
import os
import shutil
import sys
from pathlib import Path

from run_wp4r_p1_faults import git_text, run, tool

ROOT = Path(__file__).resolve().parents[2]
VIVADO_BIN_DEFAULT = r"C:\Xilinx\Vivado\2018.3\bin"
GOLDEN_MARKER = ("TB_GOLDEN_WG arch=6 vset=qual: "
                 "ALL 2048 BEATS BIT-EXACT")


def source_list() -> list[str]:
    common_dir = ROOT / "rtl" / "paper_fft12" / "common"
    package = common_dir / "fft_wg_pkg.sv"
    common = sorted(p for p in common_dir.glob("*.sv") if p != package)
    sources = [package, *common]
    sources += [
        ROOT / "sim" / "tb_paper_stream_golden_wg.sv",
        ROOT / "rtl" / "top" / "paper_fft_stream_engine.sv",
        ROOT / "sim" / "tb_wp4r_monitor_wg.sv",
        ROOT / "sim" / "tb_wp4r_p2_fault_wg.sv",
    ]
    for family in ("p0", "p2"):
        sources += sorted((ROOT / "rtl" / "paper_fft12" / family).glob("*.sv"))
    return [str(p) for p in sources]


def case_status(case_no: int, rc: int | None, output: str,
                timed_out: bool) -> str:
    result_marker = f"WP4R_P2_FAULT_RESULT case={case_no}" in output
    result_pass = result_marker and "result=PASS" in output
    if timed_out:
        return "TIMEOUT"
    if rc:
        return "XSIM_ERROR"
    return "PASS" if GOLDEN_MARKER in output and result_pass else "FAIL"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default="results/wp4r/P1_p2/fault_qual_r1.log")
    parser.add_argument("--case-timeout", type=int, default=300,
                        help="wall-clock seconds for each xsim process")
    args = parser.parse_args()
    vivado_bin = Path(os.environ.get("VIVADO_BIN", VIVADO_BIN_DEFAULT))
    log_path = ROOT / args.log
    option_dir = ROOT / "results" / "xsim_qual"
    snapshot = "wp4r_p2_fault"

    head = git_text(["rev-parse", "HEAD"]).strip()
    status = git_text(["status", "--porcelain", "k7/"])
    if status.strip():
        print("WP4R P1 fault run requires a clean k7/ worktree:")
        print(status, end="")
        return 2

    log_path.parent.mkdir(parents=True, exist_ok=True)
    option_dir.mkdir(parents=True, exist_ok=True)
    (ROOT / "sim" / "tb_arch_sel.svh").write_text(
        "localparam integer ARCH_SEL = 6;\n", encoding="ascii")
    shutil.rmtree(ROOT / "xsim.dir", ignore_errors=True)

    results: dict[int, str] = {}
    with log_path.open("w", encoding="utf-8") as log:
        log.write("WP4R_P2_FAULT_RUN cases=1..18 vset=qual\n")
        log.write(f"WP4R_HEAD={head}\n")
        log.write("$ git -C .. status --porcelain k7/\n")
        log.write(status if status else "<empty>\n")
        log.write("xsim.dir removed before this run: yes\n")
        rc, _, timed_out = run([tool("xvlog", vivado_bin), "--sv", "-i", "sim",
                                "--work", "work", *source_list()], log)
        if rc or timed_out:
            return rc or 1
        rc, _, timed_out = run([tool("xelab", vivado_bin), "-debug", "all",
                                "tb_wp4r_p2_fault_wg", "-s", snapshot], log)
        if rc or timed_out:
            return rc or 1
        for case_no in range(1, 19):
            log.write(f"\n===== WP4R_P2_CASE {case_no} =====\n")
            option_file = option_dir / f"wp4r_p2_case_{case_no}.f"
            option_file.write_text(
                "-testplusarg VSET=qual "
                "-testplusarg WP4R_FAULT=1 "
                f"-testplusarg WP4R_CASE={case_no}\n",
                encoding="ascii")
            rc, output, timed_out = run(
                [tool("xsim", vivado_bin), snapshot, "-runall", "-f",
                 str(option_file)], log, timeout=args.case_timeout)
            results[case_no] = case_status(case_no, rc, output, timed_out)
            log.write(f"WP4R_P2_CASE_SUMMARY case={case_no} result={results[case_no]}\n")

        failed = [case for case in range(1, 19) if results.get(case) != "PASS"]
        log.write(f"WP4R_P2_FAULT_RUN_SUMMARY failed={failed}\n")
        log.flush()
    print(f"WP4R_P2_FAULT_LOG {log_path}")
    print(f"WP4R_P2_FAULT_RUN_SUMMARY failed={failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
