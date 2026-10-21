#!/usr/bin/env python3
"""Run the 37 frozen WP4-R P0 fault cases with one XSim compile."""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIVADO_BIN_DEFAULT = r"C:\Xilinx\Vivado\2018.3\bin"


def tool(name: str, vivado_bin: Path) -> str:
    suffix = ".bat" if sys.platform == "win32" else ""
    return str(vivado_bin / (name + suffix))


def source_list() -> list[str]:
    common_dir = ROOT / "rtl" / "paper_fft12" / "common"
    package = common_dir / "fft_wg_pkg.sv"
    common = sorted(p for p in common_dir.glob("*.sv") if p != package)
    sources = [package, *common]
    sources += [
        ROOT / "sim" / "tb_paper_stream_golden_wg.sv",
        ROOT / "rtl" / "top" / "paper_fft_stream_engine.sv",
        ROOT / "sim" / "tb_wp4r_monitor_wg.sv",
        ROOT / "sim" / "tb_wp4r_fault_wg.sv",
    ]
    for family in ("p0", "p1"):
        sources += sorted((ROOT / "rtl" / "paper_fft12" / family).glob("*.sv"))
    return [str(p) for p in sources]


def git_text(args: list[str]) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT.parent, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace")
    return proc.stdout


def run(cmd: list[str], log, timeout: int | None = None) -> tuple[int | None, str, bool]:
    log.write("$ " + " ".join(cmd) + "\n")
    log.flush()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              errors="replace", timeout=timeout)
        log.write(proc.stdout)
        log.flush()
        return proc.returncode, proc.stdout, False
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        log.write(output)
        log.write(f"\nWP4R_XSIM_TIMEOUT seconds={timeout}\n")
        log.flush()
        return None, output, True


def case_status(case_no: int, rc: int | None, output: str,
                timed_out: bool) -> str:
    golden_marker = ("TB_GOLDEN_WG arch=5 vset=qual: "
                     "ALL 2048 BEATS BIT-EXACT") in output
    result_marker = f"WP4R_FAULT_RESULT case={case_no}" in output
    result_pass = result_marker and "result=PASS" in output
    result_record = result_marker and "result=RECORD_ONLY" in output
    if timed_out:
        return "TIMEOUT"
    if case_no >= 36:
        if result_record:
            return "RECORD_ONLY"
        if rc:
            return "ABORT_RECORD"
        return "NO_RESULT_RECORD"
    if rc:
        return "XSIM_ERROR"
    return "PASS" if golden_marker and result_pass else "FAIL"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default="results/wp4r/P0_p1/fault_qual_r1.log")
    parser.add_argument("--keep-xsim-dir", action="store_true")
    parser.add_argument("--case-timeout", type=int, default=300,
                        help="wall-clock seconds for each xsim process")
    args = parser.parse_args()
    vivado_bin = Path(os.environ.get("VIVADO_BIN", VIVADO_BIN_DEFAULT))
    log_path = ROOT / args.log
    option_dir = ROOT / "results" / "xsim_qual"
    snapshot = "wp4r_p1_fault"

    head = git_text(["rev-parse", "HEAD"]).strip()
    status = git_text(["status", "--porcelain", "k7/"])
    if status.strip():
        print("WP4R_FAULT_RUN requires a clean k7/ worktree:")
        print(status, end="")
        return 2

    log_path.parent.mkdir(parents=True, exist_ok=True)
    option_dir.mkdir(parents=True, exist_ok=True)
    (ROOT / "sim" / "tb_arch_sel.svh").write_text(
        "localparam integer ARCH_SEL = 5;\n", encoding="ascii")
    if not args.keep_xsim_dir:
        shutil.rmtree(ROOT / "xsim.dir", ignore_errors=True)

    case_results: dict[int, str] = {}
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"WP4R_P1_FAULT_RUN cases=1..37 vset=qual\n")
        log.write(f"WP4R_HEAD={head}\n")
        log.write("$ git -C .. status --porcelain k7/\n")
        log.write(status if status else "<empty>\n")
        log.write(f"xsim.dir removed before this run: "
                  f"{'no (--keep-xsim-dir)' if args.keep_xsim_dir else 'yes'}\n")
        rc, _, timed_out = run([tool("xvlog", vivado_bin), "--sv", "-i", "sim",
                                "--work", "work", *source_list()], log)
        if rc or timed_out:
            return rc or 1
        rc, _, timed_out = run([tool("xelab", vivado_bin), "-debug", "all",
                                "tb_wp4r_fault_wg", "-s", snapshot], log)
        if rc or timed_out:
            return rc or 1
        for case_no in range(1, 38):
            log.write(f"\n===== WP4R_CASE {case_no} =====\n")
            option_file = option_dir / f"wp4r_p1_case_{case_no}.f"
            option_file.write_text(
                "-testplusarg VSET=qual "
                "-testplusarg WP4R_FAULT=1 "
                f"-testplusarg WP4R_CASE={case_no}\n",
                encoding="ascii")
            rc, output, timed_out = run(
                [tool("xsim", vivado_bin), snapshot, "-runall", "-f",
                 str(option_file)], log, timeout=args.case_timeout)
            case_results[case_no] = case_status(case_no, rc, output, timed_out)
            log.write(f"WP4R_CASE_SUMMARY case={case_no} result={case_results[case_no]}\n")

        failed = [case for case in range(1, 36)
                  if case_results.get(case) != "PASS"]
        record_statuses = {case: case_results.get(case) for case in (36, 37)}
        missing_records = [case for case, status_value in record_statuses.items()
                           if status_value not in {"RECORD_ONLY", "ABORT_RECORD",
                                                   "TIMEOUT"}]
        log.write(f"WP4R_FAULT_RUN_SUMMARY failed={failed} "
                  f"record_statuses={record_statuses} "
                  f"record_only_missing={missing_records}\n")
        log.flush()
    print(f"WP4R_P1_FAULT_LOG {log_path}")
    print(f"WP4R_FAULT_RUN_SUMMARY failed={failed} "
          f"record_statuses={record_statuses} "
          f"record_only_missing={missing_records}")
    return 0 if not failed and not missing_records else 1


if __name__ == "__main__":
    sys.exit(main())
