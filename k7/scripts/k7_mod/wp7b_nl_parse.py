#!/usr/bin/env python3
"""WP7b netlist parser helpers: module/instance tables for the funcsim
netlists.  Used by wp7b_power_saif.gen_targets to classify xsim scopes
(module scope vs unisim-leaf/process scope).

NOTE: an earlier revision of this file also implemented a `assign w = 0`
tie-off pass for SAIF Z-dominant wires.  It was proven unnecessary --
Vivado read_saif counts name-matched nets regardless of Z activity --
and would have violated the task rule against modifying netlist content.
The final package uses the untouched netlists.
"""
import re
import sys
import os

K7 = os.path.dirname(os.path.abspath(__file__)) + "/../.."
TOPMOD = {"P0_p1": "top_p1_pfft_ecc",
          "P1_p2": "top_p2_pfft_tmr_no_framebuf_v1",
          "S0_s3": "top_s3_subfft_ecc",
          "S1_s2": "top_s2_subfft_tmr"}
VSETS = ["qual", "edge", "rand_amp", "rand_full", "tones"]

UNISIM_OUT = re.compile(
    r"^(O|O5|O6|Q\d*|CO\d*|DO[A-Z]?|DOP[A-Z]?|SPO|DPO|LO|SO|PO|COUT|XOROUT|"
    r"MULTSIGNOUT|PATTERNB?DETECT|OVERFLOW|UNDERFLOW|CARRYOUT|CLKOUT\d*|"
    r"CLKFBOUT|LOCKED|DRDY|STATUS\d*|BUSY|EMPTY|FULL|ALMOST\w*|RDCOUNT\w*|"
    r"WRCOUNT\w*|PROG\w*|ECC\w*|SBITERR|DBITERR|RDADDRECC\w*|VALID|SOUT|"
    r"CASCOUT|ACOUT|BCOUT|PCOUT|MUXOUT|OUT)$")

IDENT = r"(\\[^\s;,()]+|\w+)"
BITSEL = re.compile(r"^([^\[\s]+?)(?:\[(\d+)\])?$")


def unesc(s):
    return s.replace("\\", "")


def saif_name_to_verilog(nm):
    """'sy0r\\[0\\]' -> ('sy0r','0');  '\\^sy0r\\[0\\]' -> ('\\^sy0r','0')"""
    s = unesc(nm).strip()
    m = re.match(r"^(.*?)\[(\d+)(?::\d+)?\]$", s)
    if m:
        return m.group(1).strip(), m.group(2)
    return s, None


def parse_modules(txt):
    mods = {}
    for m in re.finditer(r"\bmodule\s+(\w+)", txt):
        name = m.group(1)
        end = txt.find("endmodule", m.end())
        body = txt[m.end():end]
        d = {"inputs": set(), "outputs": set(), "wires": set(),
             "insts": {}, "span": (m.start(), end), "decl": {}}
        for dm in re.finditer(
                r"\b(input|output|inout|wire|tri|reg)\b\s*(\[[^\]]*\]\s*)?"
                r"([^;]+);", body):
            kind, _r, names = dm.group(1), dm.group(2), dm.group(3)
            key = {"input": "inputs", "output": "outputs"}.get(kind)
            for nm in names.split(","):
                nm = nm.strip()
                if not nm:
                    continue
                if key:
                    d[key].add(unesc(nm.rstrip()))
                d["decl"][unesc(nm.rstrip())] = nm.rstrip()
        for im in re.finditer(r"(?<![\w.])(\w+)\s+(\\?[^\s(;,]+)\s*\(", body):
            typ, inst = im.group(1), im.group(2)
            if typ in ("module", "wire", "input", "output", "inout", "tri",
                       "reg", "assign", "if", "else", "for", "always",
                       "begin", "function", "endfunction", "while"):
                continue
            d["insts"].setdefault(unesc(inst.rstrip()), typ)
        mods[name] = d
    return mods


def pin_args(pins_txt):
    for m in re.finditer(r"\.(\w+)\s*\(([^;]*?)\)(?=\s*,|\s*\)\s*;|\s*$)",
                         pins_txt):
        yield m.group(1), m.group(2)


def zfull_nets(saif_path):
    dur = None
    stack = {}
    depth = 0
    out = []
    for line in open(saif_path):
        if dur is None and "DURATION" in line:
            dur = int(re.search(r"DURATION\s+(\d+)", line).group(1))
        o, c = line.count("("), line.count(")")
        nd = depth + o - c
        mm = re.match(r"\s*\(INSTANCE\s+(\S+)", line)
        if mm:
            stack[nd] = mm.group(1)
        for dd in list(stack):
            if dd > nd:
                del stack[dd]
        if "(TZ" in line:
            tz = int(re.search(r"\(TZ\s+(\d+)\)", line).group(1))
            if tz == dur:
                nm = re.match(r"\s*\(\s*(\S+)", line).group(1)
                out.append(("/".join(stack[d] for d in sorted(stack)), nm))
        depth = nd
    return out


def resolve_scope(mods, top, elems):
    cur = top
    for e in elems:
        eu = unesc(e)
        hit = mods.get(cur, {}).get("insts", {}).get(eu)
        if hit is None:
            return None
        cur = hit
    return cur


def driven_names(mods, txt_by_mod):
    """wire names (unescaped) strong-driven inside each module:
    assign LHS, unisim output pin args, module input ports."""
    driven = {}
    for name, d in mods.items():
        s = set(d["inputs"])
        body = txt_by_mod[name]
        for m in re.finditer(r"assign\s+(\\?[^\s=;]+)", body):
            base, _ = saif_name_to_verilog(m.group(1))
            s.add(base)
        for im in re.finditer(r"(?<![\w.])(\w+)\s+(\\?[^\s(;,]+)\s*\((.*?)\)\s*;",
                              body, re.S):
            typ, pins = im.group(1), im.group(3)
            if typ in mods:      # module inst: child output pins are weak
                continue
            for pin, arg in pin_args(pins):
                if not UNISIM_OUT.match(pin):
                    continue
                for tok in re.findall(r"(\\?[^\s,{}()[\]]+|\w+(?:\[\d+\])?)",
                                      arg):
                    base, _ = saif_name_to_verilog(tok)
                    if base:
                        s.add(base)
        driven[name] = s
    return driven


