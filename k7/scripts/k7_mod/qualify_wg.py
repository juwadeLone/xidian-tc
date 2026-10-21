#!/usr/bin/env python3
r"""Per-arch xsim golden qualification for the paper_fft12 kernels.

Usage: python scripts/k7_mod/qualify_wg.py <arch> [vset ...]
  arch in {s0,s1,s2,s3,p0,p1,p2,s2h,p2h,p0x,s2hr,p2hr}
        -> ARCH_SEL {0,1,2,3,4,5,6,7,8,9,10,11}
  vset: vector sets under sim/vectors_wg (default: qual); "all" runs
        every set.  The kernel is compiled once and simulated per set.

Compiles common sources, the golden testbench, the engine and the registered
family directories for an architecture.  Only architectures listed in OBS_TOPS
add an rtl/paper_fft12/obs wrapper.  The kernel is compiled once into a fresh
xvlog work lib, elaborated as tbwg16_<arch>, and run with `xsim -runall` from
the repo root.

The Vivado bin directory is taken from $VIVADO_BIN (default C:\Xilinx\Vivado\2018.3\bin);
tools get the .bat suffix only on Windows.

PASS marker on stdout:  TB_GOLDEN_WG arch=<n> vset=<s>: ALL 2048 BEATS BIT-EXACT
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIVADO_BIN = Path(os.environ.get("VIVADO_BIN", r"C:\Xilinx\Vivado\2018.3\bin"))


def tool(name):
    return str(VIVADO_BIN / (name + ".bat" if os.name == "nt" else name))


ARCH_SEL = {"s0": 0, "s1": 1, "s2": 2, "s3": 3, "p0": 4, "p1": 5, "p2": 6, "s2h": 7,
            "p2h": 8, "p0x": 9, "s2hr": 10, "p2hr": 11}
ARCH_FAMILIES = {"s2": ["s0", "s2"], "s2h": ["s0", "s2h"],
                 "s3": ["s0", "s3"], "s2hr": ["s0", "s2hr"],
                 "p1": ["p0", "p1"], "p2": ["p0", "p2"],
                 "p2h": ["p0", "p2h"], "p0x": ["p0", "p0x"],
                 "p2hr": ["p0", "p2hr"]}
OBS_TOPS = {"s1"}
VSETS = ["qual", "rand_full", "rand_amp", "tones", "edge"]


def ensure_vectors(vsets):
    """Write any vector set that is not on disk yet (they are generated,
    not stored in the repository, apart from the original qual files)."""
    missing = [v for v in vsets if v != "qual" and not
               (ROOT / "sim" / "vectors_wg" / v / "input_10frames.hex").is_file()]
    if missing:
        subprocess.run([sys.executable, str(ROOT / "sim" / "gen_vectors_wg.py"),
                        *missing], cwd=ROOT, check=True)


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
    arch = sys.argv[1].lower()
    vsets = sys.argv[2:] or ["qual"]
    if vsets == ["all"]:
        vsets = VSETS
    sel = ARCH_SEL[arch]
    ensure_vectors(vsets)
    (ROOT / "sim" / "tb_arch_sel.svh").write_text(
        f"localparam integer ARCH_SEL = {sel};\n", encoding="ascii")

    common_dir = ROOT / "rtl" / "paper_fft12" / "common"
    common_sources = sorted(common_dir.glob("*.sv"))
    package = common_dir / "fft_wg_pkg.sv"
    sources = [str(package)] + [str(p) for p in common_sources if p != package]
    sources += [str(ROOT / "sim" / "tb_paper_stream_golden_wg.sv"),
                str(ROOT / "rtl" / "top" / "paper_fft_stream_engine.sv")]
    if arch in {"s0", "s2h"}:
        sources.append(str(ROOT / "sim" / "tb_wp2_monitor_wg.sv"))
    if arch in {"p0x", "p2h"}:
        sources.append(str(ROOT / "sim" / "tb_wp4_monitor_wg.sv"))
    if arch in {"p1", "p2", "s3", "s2", "s2hr", "p2hr"}:
        sources.append(str(ROOT / "sim" / "tb_wp4r_monitor_wg.sv"))
    families = ARCH_FAMILIES.get(arch, [arch])
    for family in families:
        kdir = ROOT / "rtl" / "paper_fft12" / family
        sources += [str(p) for p in sorted(kdir.glob("*.sv"))
                    if p.name != f"top_{arch}_kernel.sv"]
    obs = ROOT / "rtl" / "paper_fft12" / "obs" / arch / f"top_{arch}_kernel.sv"
    if arch in OBS_TOPS:
        assert obs.is_file(), obs
        sources.append(str(obs))

    logdir = ROOT / "results" / "xsim_qual"
    logdir.mkdir(parents=True, exist_ok=True)
    snap = f"tbwg16_{arch}"
    with open(logdir / f"{arch}.log", "w", encoding="utf-8") as log:
        rc = run([tool("xvlog"), "--sv", "-i", "sim",
                  "--work", "work", *sources], log)
        if rc:
            return rc
        rc = run([tool("xelab"), "-debug", "off",
                  "tb_paper_stream_golden_wg", "-s", snap], log)
        if rc:
            return rc
        for vset in vsets:
            # xsim.bat splits arguments at '=', so the plusarg goes in a file
            optf = logdir / f"{arch}_{vset}.f"
            optf.write_text(f"-testplusarg VSET={vset}\n", encoding="ascii")
            rc = run([tool("xsim"), snap, "-runall",
                      "-f", str(optf)], log)
            if rc:
                return rc
    text = (logdir / f"{arch}.log").read_text(encoding="utf-8",
                                             errors="replace")
    m = re.findall(r"TB_GOLDEN_WG arch=\d+ vset=.*", text)
    print("\n".join(m))
    ok = [x for x in m if "ALL 2048 BEATS BIT-EXACT" in x]
    return 0 if len(ok) == len(vsets) else 1


if __name__ == "__main__":
    sys.exit(main())
