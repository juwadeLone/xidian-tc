"""Machine-independent paths for K7 module rebuild + inject.

Prefers files vendored in this tree so a cloned repo works on a lab PC
without D:\\FPGA_ENGER or C:\\hermes\\layout_ecc_platform.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _insert(p: Path) -> None:
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def add_host(root: Path) -> Path:
    cands = [
        root / "host",
        root / "scripts",  # 2048-2048cs-k7-sem vendors gui here
        Path(r"D:\FPGA_ENGER\microphase\ddr_prj\host"),
    ]
    for p in cands:
        if (p / "gui" / "board_if.py").exists():
            _insert(p)
            return p
    raise RuntimeError(
        "host/gui/board_if.py missing; expected %s or %s"
        % (root / "host", root / "scripts"))


def add_layout_ecc(root: Path) -> Path:
    cands = [
        root / "third_party" / "layout_ecc_sim",
        root / "scripts",  # 2048-2048cs-k7-sem: scripts/layout_ecc
        Path(r"C:\hermes\layout_ecc_platform\code\layout_ecc_sim"),
    ]
    for p in cands:
        if (p / "layout_ecc" / "a7_far.py").exists():
            _insert(p)
            return p
    raise RuntimeError(
        "layout_ecc.a7_far missing; expected %s"
        % (root / "third_party" / "layout_ecc_sim"))
