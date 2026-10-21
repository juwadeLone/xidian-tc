#!/usr/bin/env python3
r"""WP7b: SAIF-producing functional sim of the routed kernel netlists.

Usage:
  python scripts/k7_mod/wp7b_power_saif.py --build <design>      # xvlog+xelab once
  python scripts/k7_mod/wp7b_power_saif.py --sim   <design> <vset>
  python scripts/k7_mod/wp7b_power_saif.py --memprobe <design>   # 1 sim + /usr/bin/time -v

  design in {P0_p1, P1_p2, S0_s3, S1_s2};  vset in the vectors_wg dirs.

The routed kernel netlists are compiled with every module renamed
(wp7b_<DESIGN>_<mod>) via a generated `define file so they cannot collide
with the RTL kernel modules used by the stream engine.  The testbench is
sim/tb_power_saif_wg.sv + sim/wp7b_nl_shells.sv; SAIF window is driven by the
testbench flags saif_begin/saif_end (results/wp7b/sim_saif.tcl).

PASS markers in sim.log:  "ALL 2048 BEATS BIT-EXACT" and
"WP7B_NL arch=.. vset=..: beats=.. err=0 det=0 cor=0 unc=0 mis=0".
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIVADO_BIN = Path(os.environ.get("VIVADO_BIN", "/tools/Xilinx/Vivado/2018.3/bin"))

DESIGNS = {
    # design -> (arch_sel, family macro, netlist top module, kernel rtl families)
    "P0_p1": ("p1", "WP7B_ECC", "top_p1_pfft_ecc", ["p0", "p1"]),
    "P1_p2": ("p2", "WP7B_TMR", "top_p2_pfft_tmr_no_framebuf_v1", ["p0", "p2"]),
    "S0_s3": ("s3", "WP7B_ECC", "top_s3_subfft_ecc", ["s0", "s3"]),
    "S1_s2": ("s2", "WP7B_TMR", "top_s2_subfft_tmr", ["s0", "s2"]),
}
ARCH_SEL = {"s0": 0, "s1": 1, "s2": 2, "s3": 3, "p0": 4, "p1": 5, "p2": 6}
ARCH_EXTRA = {"p1": ["s1"], "p2": [], "s3": ["s1"], "s2": []}  # monitor needs?
# wp4r monitor arch set: {p1,p2,s3,s2} -> all four designs get the monitor.

NL_DIR = ROOT / "results" / "wp7b" / "netlists"
GEN_DIR = ROOT / "results" / "wp7b" / "gen"
SIM_DIR = ROOT / "results" / "wp7b"


def tool(name):
    return str(VIVADO_BIN / name)


def run(cmd, log, env=None, cwd=None):
    log.write("$ " + " ".join(str(c) for c in cmd) + "\n")
    log.flush()
    p = subprocess.run([str(c) for c in cmd], cwd=cwd or ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, errors="replace", env=env)
    log.write(p.stdout)
    log.flush()
    return p.returncode


def netlist_modules(nlfile):
    mods = []
    for m in re.finditer(r"^module\s+([^\s(]+)", nlfile.read_text(),
                         re.MULTILINE):
        mods.append(m.group(1))
    return mods


def nllib(design):
    return f"wp7bnl_{design}"


def gen_netlist_unit(design, nlfile):
    """Compile unit for the design's private netlist library: a `uselib
    directive so the netlist's internal module references resolve inside its
    own library first (its module names collide with the RTL kernel's names
    in `work`), then the netlist text verbatim.  The shipped netlist file
    stays untouched."""
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    out = GEN_DIR / f"{design}_nl.sv"
    out.write_text(f"`uselib lib={nllib(design)} lib=work\n"
                   + nlfile.read_text())
    return out


def gen_shell_unit(design):
    """Per-design variant of sim/wp7b_nl_shells.sv pointing its `uselib at
    this design's netlist library."""
    src = (ROOT / "sim" / "wp7b_nl_shells.sv").read_text()
    src = src.replace("`uselib lib=wp7bnl lib=work",
                      f"`uselib lib={nllib(design)} lib=work")
    out = GEN_DIR / f"{design}_shells.sv"
    out.write_text(src)
    return out


def build(design):
    arch, fam, top, families = DESIGNS[design]
    sel = ARCH_SEL[arch]
    nlfile = NL_DIR / f"{design}.v"
    nlunit = gen_netlist_unit(design, nlfile)
    shellunit = gen_shell_unit(design)
    (ROOT / "sim" / "tb_arch_sel.svh").write_text(
        f"localparam integer ARCH_SEL = {sel};\n", encoding="ascii")

    logdir = SIM_DIR / design
    logdir.mkdir(parents=True, exist_ok=True)
    blog = open(logdir / "build.log", "w", encoding="utf-8")

    # Module-name collisions between the routed netlist and the RTL kernel are
    # resolved by compile ORDER (xvlog binds names against already-compiled
    # libraries):
    #   1) work: RTL + TB  -> engine binds the RTL kernel (wp7bnl absent yet)
    #   2) wp7bnl: netlist -> its internals bind wp7bnl-first via `uselib`
    #   3) work: shells    -> netlist top binds wp7bnl via `uselib`
    common_dir = ROOT / "rtl" / "paper_fft12" / "common"
    common_sources = sorted(common_dir.glob("*.sv"))
    package = common_dir / "fft_wg_pkg.sv"
    glbl = Path(os.environ.get(
        "XILINX_VIVADO", "/tools/Xilinx/Vivado/2018.3")) / \
        "data" / "verilog" / "src" / "glbl.v"
    sources = [str(package), str(glbl)] + \
        [str(p) for p in common_sources if p != package]
    sources += [str(ROOT / "sim" / "tb_power_saif_wg.sv"),
                str(ROOT / "rtl" / "top" / "paper_fft_stream_engine.sv"),
                str(ROOT / "sim" / "tb_wp4r_monitor_wg.sv")]
    defines = ["WP7B_" + fam.replace("WP7B_", ""),
               f"WP7B_NL_TOP={top}",
               "WP4R_NO_BIND"]
    if design == "S1_s2":
        defines.append("WP7B_S1")
    define_args = [a for d in defines for a in ("-d", d)]
    for family in families:
        kdir = ROOT / "rtl" / "paper_fft12" / family
        sources += [str(p) for p in sorted(kdir.glob("*.sv"))
                    if p.name != f"top_{arch}_kernel.sv"]
    if run([tool("xvlog"), "--sv", "-i", "sim", *define_args,
            "--work", "work", *sources], blog):
        return 1

    if run([tool("xvlog"), "--sv", "--work", nllib(design), str(nlunit)],
           blog):
        return 1

    if run([tool("xvlog"), "--sv", "-i", "sim", *define_args,
            str(shellunit)], blog):
        return 1

    # elaborate: need debug info for saif; link unisim models; glbl comes
    # from the netlist itself.
    if run([tool("xelab"), "-debug", "all", "-L", "unisims_ver",
            "tb_power_saif_wg", "glbl", "-s", f"tbwp7b_{design}"], blog):
        return 1
    blog.close()
    return 0


def dump_scopes(design):
    """Snapshot must exist. Write all u_core/u_dut descendant scope paths."""
    out = GEN_DIR / f"{design}_scopes.txt"
    env = dict(os.environ, WP7B_SCOPES=str(out))
    with open(SIM_DIR / design / "scopes.log", "w", encoding="utf-8") as log:
        rc = run([tool("xsim"), f"tbwp7b_{design}", "-t",
                  str(ROOT / "results" / "wp7b" / "dump_scopes.tcl")],
                 log, env=env)
    return rc if rc else (0 if out.exists() else 1)


def gen_targets(design):
    """Emit saif_targets.tcl: one log_saif per netlist-module scope.

    Every scope under u_dut whose instance chain resolves to a *netlist
    module* (via the netlist's own instance table) is logged.  That keeps
    leaf modules (mem_reg, out_im_reg, u_decode -- modules with only
    unisim/process children) and drops unisim-leaf and Always*/Initial*
    process scopes, whose objects are model internals the checkpoint does
    not know."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "wp7b_nl_parse", ROOT / "scripts" / "k7_mod" / "wp7b_nl_parse.py")
    t0 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t0)
    txt = (NL_DIR / f"{design}.v").read_text()
    mods = t0.parse_modules(txt)
    top = t0.TOPMOD[design]
    scopes = [l.rstrip("\n") for l in
              open(GEN_DIR / f"{design}_scopes.txt", encoding="utf-8")]
    lines = []
    for s in scopes:
        parts = s.split("/")
        if parts[:3] != ["", "tb_power_saif_wg", "u_core"]:
            continue
        elems = [e.rstrip() for e in parts[3:]]
        if elems == ["u_dut"]:
            # engine scope itself: kernel boundary nets (out_beat, aux
            # inputs, clk_out2) live here -- log for boundary coverage
            lines.append(f"current_scope {{{s}}}")
            lines.append("log_saif [get_objects *]")
            continue
        if (len(elems) == 2 and elems[0] == "u_dut"
                and elems[1].startswith("\\g_k")):
            # kernel top-module scope: port wires + inter-instance nets
            lines.append(f"current_scope {{{s}}}")
            lines.append("log_saif [get_objects *]")
            continue
        if elems and elems[0] == "u_dut":
            elems = elems[1:]
        if elems and elems[0].startswith("\\g_k"):
            elems = elems[1:]
        if any(e.startswith(("Always", "Initial", "deassign", "assign_"))
               for e in elems):
            continue
        if not elems:
            continue
        mod = t0.resolve_scope(mods, top, elems)
        if mod in mods:
            lines.append(f"current_scope {{{s}}}")
            lines.append("log_saif [get_objects *]")
    lines.append("current_scope /tb_power_saif_wg")
    tgt = GEN_DIR / f"{design}_saif_targets.tcl"
    tgt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{design}: {len(lines)//2} module scopes logged")
    return tgt


def simulate(design, vset, time_it=False):
    snap = f"tbwp7b_{design}"
    tgt = GEN_DIR / f"{design}_saif_targets.tcl"
    if not tgt.exists():
        if dump_scopes(design):
            return 1
        gen_targets(design)
    outdir = SIM_DIR / design / vset
    outdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, WP7B_SAIF=str(outdir / "kernel.saif"),
               WP7B_SAIF_TARGET=str(tgt))
    optf = outdir / "xsim.f"
    optf.write_text(f"-testplusarg VSET={vset}\n", encoding="ascii")
    cmd = [tool("xsim"), snap, "-t", str(ROOT / "results" / "wp7b" / "sim_saif.tcl"),
           "-f", str(optf)]
    if time_it:
        cmd = ["/usr/bin/time", "-v", "-o", str(outdir / "time.txt"), *cmd]
    with open(outdir / "sim.log", "w", encoding="utf-8") as log:
        return run(cmd, log, env=env)


def main() -> int:
    args = sys.argv[1:]
    mode = args[0]
    if mode == "--buildall":
        import shutil
        shutil.rmtree(ROOT / "xsim.dir", ignore_errors=True)
        rc = 0
        for d in DESIGNS:
            r = build(d)
            print(f"{d} build rc={r}")
            rc = rc or r
        return rc
    design = args[1]
    if mode == "--build":
        rc = build(design)
        print(f"{design} build rc={rc}")
        return rc
    if mode == "--gentargets":
        gen_targets(design)
        return 0
    if mode == "--sim":
        return simulate(design, args[2])
    if mode == "--memprobe":
        return simulate(design, "qual", time_it=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
