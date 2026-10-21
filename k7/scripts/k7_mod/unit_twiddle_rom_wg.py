#!/usr/bin/env python3
"""Unit test of the twiddle bank (criterion 3): sim/tb_twiddle_rom_wg.sv.

Usage: python scripts/k7_mod/unit_twiddle_rom_wg.py

Runs sim/gen_twiddle_rom_wg.py --check, compiles rtl/paper_fft12/common/*.sv
and the testbench into a fresh xvlog work lib, elaborates and runs it with
xsim from the repo root. Transcript: results/xsim_qual/tb_twiddle_rom_wg.log.

PASS marker on stdout:  TB_TWIDDLE_ROM: PASS   (exit 0)
The Vivado bin directory comes from $VIVADO_BIN (default C:\\Xilinx\\Vivado\\2018.3\\bin);
tools get the .bat suffix only on Windows.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIVADO_BIN = Path(os.environ.get("VIVADO_BIN", r"C:\Xilinx\Vivado\2018.3\bin"))
TOP = "tb_twiddle_rom_wg"


def tool(name):
    return str(VIVADO_BIN / (name + ".bat" if os.name == "nt" else name))


def run(cmd, log):
    log.write("$ " + " ".join(cmd) + "\n")
    log.flush()
    p = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True,
                       errors="replace")
    log.write(p.stdout)
    log.flush()
    return p.returncode


def main() -> int:
    logdir = ROOT / "results" / "xsim_qual"
    logdir.mkdir(parents=True, exist_ok=True)
    sources = sorted((ROOT / "rtl" / "paper_fft12" / "common").glob("*.sv"))
    sources.append(ROOT / "sim" / f"{TOP}.sv")
    with open(logdir / f"{TOP}.log", "w", encoding="utf-8") as log:
        rc = run([sys.executable, str(ROOT / "sim" / "gen_twiddle_rom_wg.py"), "--check"], log)
        if rc:
            return rc
        rc = run([tool("xvlog"), "--sv", "-i", "sim", "--work", "work",
                  *map(str, sources)], log)
        if rc:
            return rc
        rc = run([tool("xelab"), "-debug", "off", TOP, "-s", TOP], log)
        if rc:
            return rc
        rc = run([tool("xsim"), TOP, "-runall"], log)
        if rc:
            return rc
    text = (logdir / f"{TOP}.log").read_text(encoding="utf-8", errors="replace")
    markers = re.findall(r"^TB_TWIDDLE_ROM: .*$", text, re.M)
    print("\n".join(markers))
    return 0 if markers == ["TB_TWIDDLE_ROM: PASS"] else 1


if __name__ == "__main__":
    sys.exit(main())
