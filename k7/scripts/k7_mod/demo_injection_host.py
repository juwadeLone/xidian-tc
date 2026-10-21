#!/usr/bin/env python3
r"""Demonstration host for the K7 SEM fault-injection platform (setup photo).

Runs the WP6 board sequence on one shot and shows the FFT output of one
frame against the golden model:

    golden run : WR/RUN/RD with no injection, checked bit-exact
    inject run : SEM I -> N <lfa> (one or two bits) -> WR/RUN/RD
                 -> classify (wp6_sem_campaign.classify_shot)
                 -> SEM O repair + health shot (wp6_sem_campaign.repair_and_health)

The board, classification and repair code are imported from
wp6_sem_campaign.py unchanged; this file only adds the window.  Every shot is
appended to results/demo_host/<design>/demo_log.jsonl (gitignored), never to
the campaign logs: demo shots are not paper data.

Usage (lab machine, the design's frozen bitstream already programmed):
    python3 scripts/k7_mod/demo_injection_host.py --design P0 --com /dev/ttyUSB0
UI test without a board (every plot is watermarked SIMULATION):
    python3 scripts/k7_mod/demo_injection_host.py --design P0 --mock
    python3 scripts/k7_mod/demo_injection_host.py --design P0 --mock --selftest out.png
"""
from __future__ import annotations

import argparse
import copy
import json
import queue
import random
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import tkinter as tk
from tkinter import messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_cram"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))

import wp6_sem_campaign as camp  # noqa: E402

VEC = ROOT / "sim" / "vectors_wg"
DEMO_OUT = ROOT / "results" / "demo_host"
VECTOR_SETS = ["rand_full", "tones", "edge", "rand_amp", "qual"]

IN_FRAME_BYTES = 256 * 16
OUT_BEAT_BYTES = 27
OUT_FRAME_BYTES = 256 * OUT_BEAT_BYTES
OUT_W = 27
N_DATA_FRAMES = 8          # frames 8-9 of the 10 written are zero padding

# Same mapping as k7_paper_smoke.GOLDEN_MODEL (checked at connect time).
GOLDEN_MODEL = {"p1": "pfft_exchange", "p2": "pfft_exchange",
                "s3": "subfft", "s2": "subfft"}

DESIGN_DESC = {
    "P0": "PFFT, per-stage ECC (stages 1-7) + TMR (stages 8-10)",
    "P1": "PFFT, stage-partitioned TMR (stages 1-10)",
    "S0": "SubFFT, per-stage ECC (stages 1-7) + TMR (stages 8-10)",
    "S1": "SubFFT, stage-partitioned TMR (stages 1-10)",
}
CAT_EN = {"noimpact": "No effect", "corrected": "Corrected",
          "flagged": "Flagged error", "silent": "Silent error",
          "flagged_but_clean": "Flagged, output intact",
          "stuck": "Stuck (no output)", "invalid": "Invalid (link error)"}
CAT_COLOR = {"noimpact": "#6b7280", "corrected": "#15803d",
             "flagged": "#c2410c", "silent": "#b91c1c",
             "flagged_but_clean": "#a16207", "stuck": "#7e22ce",
             "invalid": "#374151"}
UI_FONT = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"
MONO_FONT = "Consolas" if sys.platform == "win32" else "DejaVu Sans Mono"
KIND_ORDER = {"pair_stress": 0, "pair_natural": 1, "pair_control": 2, "single": 3}
KIND_EN = {"single": "single", "pair_natural": "natural pair",
           "pair_stress": "stressed pair", "pair_control": "control pair"}


# ------------------------------------------------------------------ vectors

def bit_reverse(x, n):
    return int(format(x, "0%db" % n)[::-1], 2)


def load_set(vset, model):
    """(raw input bytes for 10 frames, golden bytes for 8 frames)."""
    d = VEC if vset == "qual" else VEC / vset
    inp = d / ("wg_input_10frames.hex" if vset == "qual" else "input_10frames.hex")
    gold = (VEC / ("qualification_%s_expected_8frames_wg.hex" % model)
            if vset == "qual" else d / ("%s_expected_8frames.hex" % model))
    if not (inp.is_file() and gold.is_file()):
        subprocess.run([sys.executable, str(ROOT / "sim" / "gen_vectors_wg.py"),
                        vset], cwd=ROOT, check=True)
    lines = [l.strip() for l in inp.read_text().splitlines() if l.strip()]
    raw = b"".join(int(l, 16).to_bytes(16, "little") for l in lines)
    glines = [l.strip() for l in gold.read_text().splitlines() if l.strip()]
    golden = b"".join(int(l, 16).to_bytes(OUT_BEAT_BYTES, "little")
                      for l in glines)
    assert len(raw) == 10 * IN_FRAME_BYTES, inp
    assert len(golden) == N_DATA_FRAMES * OUT_FRAME_BYTES, gold
    return raw, golden


def decode_frames(data, model, n_frames=N_DATA_FRAMES):
    """Output bytes -> complex array (n_frames, 1024) in natural bin order
    (gen_vectors_wg.expected_text ordering)."""
    out = np.zeros((n_frames, 1024), dtype=complex)
    mask = (1 << OUT_W) - 1
    for f in range(n_frames):
        for b in range(256):
            off = (f * 256 + b) * OUT_BEAT_BYTES
            w = int.from_bytes(data[off:off + OUT_BEAT_BYTES], "little")
            vals = []
            for i in range(8):
                v = (w >> (OUT_W * (7 - i))) & mask
                if v >> (OUT_W - 1):
                    v -= 1 << OUT_W
                vals.append(v)
            for lane in range(4):
                if model == "subfft":
                    k = lane * 256 + bit_reverse(b, 8)
                else:
                    k = bit_reverse(4 * b + lane, 10)
                out[f, k] = complex(vals[2 * lane], vals[2 * lane + 1])
    return out


def diff_words(got, golden):
    """Number of differing output words (complex samples) per frame."""
    g = decode_frames(golden, "subfft")      # ordering irrelevant for counts
    a = decode_frames(got, "subfft")
    return [int(np.count_nonzero(a[f] != g[f])) for f in range(N_DATA_FRAMES)]


# ------------------------------------------------------------ demo targets

def load_presets(design):
    """Recorded WP6 shots of this design (single and pair kinds)."""
    path = camp.OUT / camp.DESIGN_DIR[design] / "campaign.jsonl"
    presets = []
    if not path.is_file():
        return presets
    for rec in camp.read_jsonl(path):
        if rec.get("void") or rec.get("kind") not in KIND_EN:
            continue
        tg = rec.get("target") or []
        if isinstance(tg, dict):
            tg = [tg]
        if not tg or not all("lfa_hex" in t for t in tg):
            continue
        tel = rec.get("telemetry") or {}
        if all(t.get("stage") for t in tg):
            where = " + ".join("st%s L%s" % (t["stage"], t.get("lane", "?")) for t in tg)
        elif tel:
            where = "st%s L%s" % (tel.get("last_stage", 0) + 1, tel.get("last_loc"))
        else:
            where = "-"
        presets.append({
            "kind": rec["kind"], "sub": rec.get("subkind"), "index": rec.get("index"),
            "recorded": rec.get("category"), "where": where,
            "targets": [{"la": t.get("la"), "wd": t.get("wd"), "bt": t.get("bt"),
                         "lfa_hex": t["lfa_hex"], "stage": t.get("stage")}
                        for t in tg]})
    presets.sort(key=lambda p: (KIND_ORDER[p["kind"]], p["sub"] or "", p["index"] or 0))
    return presets


def preset_name(p):
    sub = " (%s)" % p["sub"].upper() if p.get("sub") in ("ecc", "tmr") else ""
    return "%s%s #%s" % (KIND_EN[p["kind"]], sub, p["index"])


def load_L(design):
    path = camp.OUT / camp.DESIGN_DIR[design] / "L_freeze.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("L", [])


# ---------------------------------------------------------------- sessions

class BoardSession:
    """Real board: wp6_sem_campaign.RealBoard + its repair/health path."""
    mock = False

    def __init__(self, design, com, bit=None, program=False):
        self.design = design
        self.prereg = copy.deepcopy(camp.load_prereg())
        self.info = self.prereg["designs"][design]
        self.bit, self.program = bit, program
        if bit:
            # a local copy of the frozen bitstream: same SHA-256 or refuse
            sha = camp.sha256_file(bit)
            if sha != self.info["bit_sha"]:
                raise SystemExit("bitstream SHA-256 %s does not match the frozen %s (%s)"
                                 % (sha, self.info["bit_sha"], design))
            self.prereg["frozen_inputs"][design]["bit"] = str(bit)
        elif program:
            raise SystemExit("--program needs --bit")
        self.board = camp.RealBoard(com, self.info["arch"])
        self.ctx = SimpleNamespace(
            design=design, board=self.board, prereg=self.prereg,
            info=self.info, shell_resets_total=0, reprogram_fails=0)

    def connect(self):
        import k7_paper_smoke as smoke
        assert smoke.GOLDEN_MODEL[self.info["arch"]] == GOLDEN_MODEL[self.info["arch"]]
        if self.program:                         # SHA re-checked inside
            self.board.reprogram(Path(self.bit), self.info["bit_sha"])
            self.program = False
        self.board.ensure_up()
        return camp.health_pass(self.ctx)        # asserts telemetry arch

    def shot(self, raw, golden):
        from k7_paper_smoke import do_shot
        rec = do_shot(self.board.host, self.board.wire, raw, golden)
        camp.assert_tel_arch(self.design, rec.get("telemetry"))
        return rec

    def inject(self, targets):
        replies = [self.board.sem_cmd("I", timeout_ms=4000)]
        for t in targets:
            replies.append(self.board.sem_cmd("N " + t["lfa_hex"],
                                              timeout_ms=4000))
        return replies

    def repair(self, targets):
        return camp.repair_and_health(self.ctx, targets)

    def close(self):
        self.board.close()


class MockSession:
    """No board.  Synthetic outputs for UI testing only (watermarked)."""
    mock = True

    def __init__(self, design):
        self.design = design
        self.pending = []
        self.rng = random.Random(1)

    def connect(self):
        return {"ok": True, "counters_zero": True}

    def inject(self, targets):
        self.pending = list(targets)
        return ["(mock) I"] + ["(mock) N " + t["lfa_hex"] for t in targets]

    def shot(self, raw, golden):
        data = bytearray(golden + bytes(2 * OUT_FRAME_BYTES))
        tel = {"det_cnt": 0, "cor_cnt": 0, "unc_cnt": 0, "err_flags": 0,
               "last_stage": 0, "last_loc": 0, "arch": camp.EXPECTED_TEL_ARCH[self.design]}
        if self.pending:
            stage = int(self.pending[0].get("stage") or 3)
            tel.update(det_cnt=480, cor_cnt=480, last_stage=stage - 1)
            if len(self.pending) == 2:          # pretend a same-stage pair failed
                for _ in range(40):
                    off = self.rng.randrange(N_DATA_FRAMES * 256) * OUT_BEAT_BYTES
                    data[off + self.rng.randrange(OUT_BEAT_BYTES)] ^= 1 << self.rng.randrange(8)
                tel["unc_cnt"] = 37
            self.pending = []
        ok = bytes(data[:len(golden)]) == golden
        return {"ok": ok, "err": None, "n_diff": None, "tail_zero": True,
                "telemetry": tel, "data": bytes(data)}

    def repair(self, targets):
        return ({"report": {"cor": True}, "repair_match": True}, "none",
                {"ok": True, "counters_zero": True})

    def close(self):
        pass


# --------------------------------------------------------------------- GUI

class DemoHost:
    def __init__(self, root, design, session, selftest=None, vset="rand_full",
                 auto=None, frame=0):
        self.root, self.design, self.session = root, design, session
        self.arch = camp.DESIGNS[design]["arch"]
        self.model = GOLDEN_MODEL[self.arch]
        self.presets = load_presets(design)
        self.L = load_L(design)
        self.q = queue.Queue()
        self.busy = False
        self.last = None          # dict(golden=..., got=..., ...)
        self.selftest = selftest
        # auto: dict(target, shot, fig, exit); selftest = a mock auto run
        self.auto = auto or ({"target": "pair_stress:flagged", "shot": None,
                              "fig": selftest, "exit": True} if selftest else None)
        self.vset0 = vset
        self.frame0 = frame
        self.log_path = DEMO_OUT / camp.DESIGN_DIR[design] / "demo_log.jsonl"
        self._build()
        self.root.after(100, self._poll)

    # ---- layout
    def _build(self):
        r = self.root
        r.title("K7 SEM Fault-Injection Host  -  %s" % self.design)
        r.geometry("1680x960")
        style = ttk.Style(r)
        style.theme_use("clam")
        style.configure(".", font=(UI_FONT, 11))
        style.configure("Big.TLabel", font=(UI_FONT, 22, "bold"))
        style.configure("Head.TLabel", font=(UI_FONT, 13, "bold"))

        top = ttk.Frame(r, padding=(8, 6, 8, 0))
        top.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(top, text="Design %s: %s" % (self.design, DESIGN_DESC[self.design]),
                  style="Head.TLabel").pack(side=tk.LEFT)
        mode = "SIMULATION (no board)" if self.session.mock else "Board: Kintex-7 XC7K325T, SEM IP"
        ttk.Label(top, text=mode, foreground="#b91c1c" if self.session.mock else "#1d4ed8"
                  ).pack(side=tk.RIGHT)
        bar = ttk.Frame(r, padding=(8, 4, 8, 4))
        bar.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(bar, text="Input vectors").pack(side=tk.LEFT)
        self.vset_var = tk.StringVar(value=self.vset0)
        ttk.Combobox(bar, values=VECTOR_SETS, textvariable=self.vset_var, width=10,
                     state="readonly").pack(side=tk.LEFT, padx=(4, 16))
        ttk.Label(bar, text="Frame").pack(side=tk.LEFT)
        self.frame_var = tk.IntVar(value=self.frame0)
        ttk.Spinbox(bar, from_=0, to=N_DATA_FRAMES - 1, width=3, textvariable=self.frame_var,
                    command=self.redraw).pack(side=tk.LEFT, padx=(4, 24))
        self.btn_conn = ttk.Button(bar, text="Connect", command=self.on_connect)
        self.btn_gold = ttk.Button(bar, text="Golden run", command=self.on_golden, state=tk.DISABLED)
        self.btn_inj = ttk.Button(bar, text="Inject + run + repair", command=self.on_inject, state=tk.DISABLED)
        self.btn_save = ttk.Button(bar, text="Save plot", command=self.on_save)
        self.btn_shot = ttk.Button(bar, text="Screenshot", command=self.on_shot)
        for b in (self.btn_conn, self.btn_gold, self.btn_inj, self.btn_save, self.btn_shot):
            b.pack(side=tk.LEFT, padx=4)

        body = ttk.Frame(r)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # left: targets
        left = ttk.Frame(body, padding=6, width=360)
        left.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(left, text="Injection target", style="Head.TLabel").pack(anchor="w")
        f1 = ttk.Frame(left)
        f1.pack(fill=tk.X, pady=2)
        self.kind_var = tk.StringVar(value="all")
        self.rec_var = tk.StringVar(value="all")
        ttk.Combobox(f1, values=["all"] + list(KIND_EN), textvariable=self.kind_var,
                     width=13, state="readonly").pack(side=tk.LEFT)
        ttk.Combobox(f1, values=["all"] + list(CAT_EN), textvariable=self.rec_var,
                     width=16, state="readonly").pack(side=tk.LEFT, padx=4)
        self.kind_var.trace_add("write", lambda *a: self._fill_presets())
        self.rec_var.trace_add("write", lambda *a: self._fill_presets())
        lf = ttk.Frame(left)
        lf.pack(fill=tk.BOTH, expand=True)
        self.lst = tk.Listbox(lf, width=58, height=24, font=(MONO_FONT, 10),
                              exportselection=False)
        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.lst.yview)
        self.lst.configure(yscrollcommand=sb.set)
        self.lst.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(left, text="or LFA hex (one or two, comma-separated):").pack(anchor="w", pady=(6, 0))
        self.lfa_var = tk.StringVar()
        ttk.Entry(left, textvariable=self.lfa_var, width=40).pack(fill=tk.X)
        ttk.Button(left, text="Random bit from library L", command=self.on_random_L).pack(fill=tk.X, pady=4)
        self.sel_lbl = ttk.Label(left, text="", wraplength=340, justify=tk.LEFT)
        self.sel_lbl.pack(anchor="w", pady=4)
        self.sel_idx = None
        self.lst.bind("<<ListboxSelect>>", lambda e: self._on_list_select())
        self._fill_presets()

        # right: result panel
        right = ttk.Frame(body, padding=8, width=380)
        right.pack(side=tk.RIGHT, fill=tk.Y)
        ttk.Label(right, text="Result", style="Head.TLabel").pack(anchor="w")
        self.cat_lbl = ttk.Label(right, text="-", style="Big.TLabel")
        self.cat_lbl.pack(anchor="w", pady=4)
        self.info_txt = tk.Text(right, width=44, height=26, font=(MONO_FONT, 11),
                                relief=tk.FLAT, background="#f8fafc")
        self.info_txt.pack(fill=tk.BOTH, expand=True)

        # center: plots
        center = ttk.Frame(body)
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.fig = Figure(figsize=(9.5, 7.2), dpi=100)
        self.ax1 = self.fig.add_subplot(2, 1, 1)
        self.ax2 = self.fig.add_subplot(2, 1, 2, sharex=self.ax1)
        self.fig.subplots_adjust(left=0.09, right=0.98, top=0.93, bottom=0.08, hspace=0.32)
        self.canvas = FigureCanvasTkAgg(self.fig, master=center)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.redraw()

        # bottom: log
        self.log_txt = tk.Text(r, height=7, font=(MONO_FONT, 10), background="#0f172a",
                               foreground="#e2e8f0")
        self.log_txt.pack(side=tk.BOTTOM, fill=tk.X)

    # ---- targets
    def _fill_presets(self):
        k, c = self.kind_var.get(), self.rec_var.get()
        self.shown = [p for p in self.presets
                      if (k == "all" or p["kind"] == k) and (c == "all" or p["recorded"] == c)]
        self.lst.delete(0, tk.END)
        self.sel_idx = None
        for p in self.shown[:3000]:
            rec = "Flagged, clean" if p["recorded"] == "flagged_but_clean" else \
                CAT_EN.get(p["recorded"], p["recorded"])
            self.lst.insert(tk.END, "%-26s %-16s %s" % (preset_name(p), p["where"], rec))

    def _selected_targets(self):
        txt = self.lfa_var.get().strip()
        if txt:
            hexes = [h.strip().upper() for h in txt.split(",") if h.strip()]
            if not 1 <= len(hexes) <= 2 or any(len(h) != 10 for h in hexes):
                raise ValueError("enter one or two 10-digit LFA hex values")
            out = []
            for h in hexes:
                u = camp.unpack_sem_n_local(h)
                out.append({"la": u["lfa"], "wd": u["word"], "bt": u["bit"],
                            "lfa_hex": h, "stage": None})
            # name the campaign record with exactly these bits, if any
            key = sorted(hexes)
            for p in self.presets:
                if sorted(t["lfa_hex"].upper() for t in p["targets"]) == key:
                    return out, "%s (%s)\ncampaign result: %s" % (
                        preset_name(p), p["where"], CAT_EN.get(p["recorded"], p["recorded"]))
            return out, "manual entry"
        sel = self.lst.curselection() or ((self.sel_idx,) if self.sel_idx is not None else ())
        if not sel:
            raise ValueError("select a recorded shot or enter an LFA")
        p = self.shown[sel[0]]
        return p["targets"], "%s\ncampaign result: %s" % (
            preset_name(p), CAT_EN.get(p["recorded"], p["recorded"]))

    def _on_list_select(self):
        cur = self.lst.curselection()
        if cur:
            self.sel_idx = cur[0]
            self.lfa_var.set("")
        self._show_selection()

    def _show_selection(self):
        try:
            tg, src = self._selected_targets()
            self.sel_lbl.configure(text="%s\n%s" % (src, ", ".join(t["lfa_hex"] for t in tg)))
        except ValueError as e:
            self.sel_lbl.configure(text=str(e))

    def on_random_L(self):
        if not self.L:
            self._warn("Library L", "L_freeze.json not found")
            return
        b = random.choice(self.L)
        self.lfa_var.set(b["lfa_hex"])
        self.sel_lbl.configure(text="random bit from L: %s (stage %s)" % (b["lfa_hex"], b.get("stage")))

    # ---- actions
    def _run(self, name, fn):
        if self.busy:
            return
        self.busy = True
        for b in (self.btn_conn, self.btn_gold, self.btn_inj):
            b.configure(state=tk.DISABLED)
        self.log("%s ..." % name)

        def work():
            try:
                self.q.put(("ok", name, fn()))
            except Exception as e:                            # noqa: BLE001
                self.q.put(("err", name, "%s: %s\n%s" % (type(e).__name__, e,
                                                         traceback.format_exc(limit=3))))
        threading.Thread(target=work, daemon=True).start()

    def on_connect(self):
        self._run("connect", lambda: {"op": "connect", "health": self.session.connect()})

    def on_golden(self):
        vset = self.vset_var.get()

        def fn():
            raw, golden = load_set(vset, self.model)
            rec = self.session.shot(raw, golden)
            return {"op": "golden", "vset": vset, "golden": golden, "rec": rec}
        self._run("golden run (%s)" % vset, fn)

    def on_inject(self):
        try:
            targets, src = self._selected_targets()
        except ValueError as e:
            self._warn("Target", str(e))
            return
        vset = self.vset_var.get()

        def fn():
            raw, golden = load_set(vset, self.model)
            sem_replies = self.session.inject(targets)
            rec = self.session.shot(raw, golden)
            tel = rec.get("telemetry") or {}
            stage = (tel.get("last_stage", 0) + 1) if tel else None
            tmr = stage in camp.DESIGNS[self.design]["tmr_stages"]
            cat, _ = camp.classify_shot(rec, tel, tmr)
            sem_info, path, health = self.session.repair(targets)
            return {"op": "inject", "vset": vset, "golden": golden, "rec": rec,
                    "targets": targets, "source": src, "category": cat,
                    "sem_replies": sem_replies, "sem_info": sem_info,
                    "recovery_path": path, "health": health}
        self._run("inject %s + run + repair (%s)" % (
            ", ".join(t["lfa_hex"] for t in targets), vset), fn)

    def on_save(self):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = self.log_path.parent / ("plot_%s_frame%d" % (stamp, int(self.frame_var.get() or 0)))
        base.parent.mkdir(parents=True, exist_ok=True)
        for ext in (".png", ".pdf"):
            self.fig.savefig(str(base) + ext, dpi=200)
        self.log("plot saved: %s.png / .pdf" % base)

    # ---- results
    def _poll(self):
        try:
            while True:
                status, name, payload = self.q.get_nowait()
                self.busy = False
                if status == "err":
                    self.log("ERROR in %s: %s" % (name, payload))
                    self._warn(name, payload.splitlines()[0], error=True)
                    self.btn_conn.configure(state=tk.NORMAL)
                    if name != "connect":
                        self.btn_gold.configure(state=tk.NORMAL)
                        self.btn_inj.configure(state=tk.NORMAL)
                else:
                    self._handle(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _handle(self, res):
        op = res["op"]
        self.btn_conn.configure(state=tk.NORMAL)
        if op == "connect":
            h = res["health"]
            ok = bool(h.get("ok") and h.get("counters_zero", True))
            self.log("connected; health shot %s; bitstream telemetry arch = %s (expected %s for %s)" % (
                "clean" if ok else "NOT clean", (h.get("telemetry") or {}).get("arch", "-"),
                camp.EXPECTED_TEL_ARCH[self.design], self.design))
            if ok:
                self.btn_gold.configure(state=tk.NORMAL)
                self.btn_inj.configure(state=tk.NORMAL)
            if self.auto and ok:
                self.root.after(200, self.on_golden)
            return
        self.btn_gold.configure(state=tk.NORMAL)
        self.btn_inj.configure(state=tk.NORMAL)
        rec = res["rec"]
        got = rec.get("data") or b""
        self.last = res
        tel = rec.get("telemetry") or {}
        if op == "golden":
            cat = "noimpact" if rec.get("ok") else ("stuck" if rec.get("err") else "silent")
            res["category"] = cat
            self.log("golden run: output %s golden model (%s)" % (
                "bit-exact with" if rec.get("ok") else "DIFFERS from", res["vset"]))
        else:
            cat = res["category"]
            h = res["health"] or {}
            self.log("result: %s; repair path %s; post-repair health %s" % (
                CAT_EN.get(cat, cat), res["recovery_path"],
                "clean" if h.get("ok") and h.get("counters_zero") else "NOT clean"))
        self._show_result(res, tel)
        self.redraw()
        self._append_log(res)
        if self.auto:
            if op == "golden":
                self._auto_select(self.auto["target"])
                self.root.after(200, self.on_inject)
            else:
                auto, self.auto = self.auto, None
                if auto.get("fig"):
                    self.fig.savefig(auto["fig"], dpi=200)
                    self.log("figure written: %s" % auto["fig"])
                if auto.get("shot"):
                    self.root.after(800, lambda: self.grab_window(auto["shot"]))
                if auto.get("exit"):
                    self.root.after(3000, self.root.destroy)

    def _auto_select(self, spec):
        """'lfa:HEX[,HEX]' | 'L' | '<kind>:<index>' | '<kind>:<recorded category>'."""
        kind, _, val = spec.partition(":")
        kind, _, sub = kind.partition("/")
        if kind == "lfa":
            self.lfa_var.set(val)
            self._show_selection()
            return
        if kind == "L":
            self.on_random_L()
            return
        self.kind_var.set("all")
        self.rec_var.set("all")
        for i, p in enumerate(self.shown):
            if p["kind"] == kind and (not sub or p.get("sub") == sub) and \
                    (str(p["index"]) == val or p["recorded"] == val):
                self.lst.selection_set(i)
                self.lst.see(i)
                self.sel_idx = i
                self._show_selection()
                return
        raise SystemExit("auto target not found: %s" % spec)

    def grab_window(self, png):
        """Screenshot of this window only (raised to the top first)."""
        r = self.root
        r.attributes("-topmost", True)
        r.lift()
        r.update()

        def grab():
            try:
                from PIL import ImageGrab
                box = (r.winfo_rootx(), r.winfo_rooty(),
                       r.winfo_rootx() + r.winfo_width(), r.winfo_rooty() + r.winfo_height())
                ImageGrab.grab(bbox=box).save(png)
                self.log("window screenshot written: %s" % png)
            except Exception as e:                            # noqa: BLE001
                self.log("window screenshot failed: %s" % e)
            finally:
                r.attributes("-topmost", False)
        r.after(500, grab)

    def on_shot(self):
        png = self.log_path.parent / ("window_%s.png" % time.strftime("%Y%m%d_%H%M%S"))
        png.parent.mkdir(parents=True, exist_ok=True)
        self.grab_window(str(png))

    def _show_result(self, res, tel):
        cat = res["category"]
        title = ("Golden run: " if res["op"] == "golden" else "") + CAT_EN.get(cat, cat)
        self.cat_lbl.configure(text=title, foreground=CAT_COLOR.get(cat, "#111827"))
        rec = res["rec"]
        got = rec.get("data") or b""
        lines = []
        if res["op"] == "inject":
            lines.append("Injected bits (SEM LFA):")
            for t in res["targets"]:
                lines.append("  %s  (frame %s, word %s, bit %s)" % (t["lfa_hex"], t["la"], t["wd"], t["bt"]))
            lines.extend("  " + x for x in res["source"].split("\n"))
            lines.append("")
        lines.append("Input vectors : %s" % res["vset"])
        if got:
            per = diff_words(got[:len(res["golden"])], res["golden"])
            lines.append("Output vs golden: %d of %d words differ" % (sum(per), N_DATA_FRAMES * 1024))
            lines.append("  per frame: " + " ".join(str(x) for x in per))
            lines.append("  zero-padding frames clean: %s" % (not any(got[len(res["golden"]):])))
        else:
            lines.append("No output read back: %s" % rec.get("err"))
        lines.append("")
        lines.append("Kernel event counters (beats):")
        lines.append("  detected     %8s" % tel.get("det_cnt", "-"))
        lines.append("  corrected    %8s" % tel.get("cor_cnt", "-"))
        lines.append("  uncorrectable%8s" % tel.get("unc_cnt", "-"))
        if tel:
            lines.append("  last event   stage %s, location %s" % (tel.get("last_stage", 0) + 1, tel.get("last_loc")))
            lines.append("  bitstream arch id %s" % tel.get("arch"))
        if res["op"] == "inject":
            si = res["sem_info"] or {}
            h = res["health"] or {}
            lines.append("")
            lines.append("SEM repair: %s (report COR=%s)" % (res["recovery_path"], (si.get("report") or {}).get("cor")))
            lines.append("Post-repair health shot: %s" % ("clean" if h.get("ok") and h.get("counters_zero") else "NOT clean"))
        self.info_txt.delete("1.0", tk.END)
        self.info_txt.insert(tk.END, "\n".join(lines))

    def redraw(self):
        ax1, ax2 = self.ax1, self.ax2
        ax1.clear()
        ax2.clear()
        k = int(self.frame_var.get() or 0)
        bins = np.arange(1024)
        if self.last and self.last["rec"].get("data"):
            g = decode_frames(self.last["golden"], self.model)[k]
            a = decode_frames(self.last["rec"]["data"], self.model)[k]
            gd = 20 * np.log10(np.abs(g) + 1.0)
            ad = 20 * np.log10(np.abs(a) + 1.0)
            ax1.plot(bins, gd, color="#1d4ed8", lw=1.6, label="Golden (bit-accurate model)")
            ax1.plot(bins, ad, color="#ea580c", lw=0.9, ls="--",
                     label="Board output" + (" (after injection)" if self.last["op"] == "inject" else ""))
            ax1.legend(loc="upper right", fontsize=9)
            err = np.abs(a - g)
            nz = np.flatnonzero(err)
            if nz.size:
                ax2.vlines(nz, 0, err[nz], color="#b91c1c", lw=1.2)
                ax2.plot(nz, err[nz], "o", color="#b91c1c", ms=3)
                ax2.set_yscale("symlog", linthresh=1)
                ax2.set_title("|Board - Golden|: %d of 1024 bins differ in frame %d" % (nz.size, k), fontsize=11)
            else:
                ax2.axhline(0, color="#15803d", lw=1.5)
                ax2.text(0.5, 0.55, "Bit-exact: 0 of 1024 bins differ", transform=ax2.transAxes,
                         ha="center", va="center", fontsize=15, color="#15803d")
                ax2.set_title("|Board - Golden| in frame %d" % k, fontsize=11)
        else:
            ax1.text(0.5, 0.5, "Connect, then run", transform=ax1.transAxes, ha="center", fontsize=13)
            ax2.set_title("|Board - Golden|", fontsize=11)
        ax1.set_title("1024-point FFT output, frame %d (natural bin order)" % k, fontsize=11)
        ax1.set_ylabel("|X[k]| (dB, LSB units)")
        ax2.set_ylabel("error (LSB)")
        ax2.set_xlabel("frequency bin k")
        ax1.set_xlim(0, 1023)
        for ax in (ax1, ax2):
            ax.grid(alpha=0.3)
        if self.session.mock:
            for ax in (ax1, ax2):
                ax.text(0.5, 0.5, "SIMULATION - NOT BOARD DATA", transform=ax.transAxes,
                        ha="center", va="center", fontsize=26, color="#b91c1c", alpha=0.25, rotation=12)
        self.canvas.draw_idle()

    # ---- logging
    def _warn(self, title, msg, error=False):
        self.log("%s: %s" % (title, msg))
        if self.selftest:
            return
        (messagebox.showerror if error else messagebox.showwarning)(title, msg)

    def log(self, msg):
        ts = time.strftime("%H:%M:%S")
        print("[%s] %s" % (ts, msg), flush=True)
        self.log_txt.insert(tk.END, "[%s] %s\n" % (ts, msg))
        self.log_txt.see(tk.END)

    def _append_log(self, res):
        rec = res["rec"]
        entry = {"t": datetime.now(timezone.utc).isoformat(), "design": self.design,
                 "mode": "mock" if self.session.mock else "board",
                 "note": "demo for the setup photo; not paper data",
                 "op": res["op"], "vector_set": res["vset"], "category": res.get("category"),
                 "targets": res.get("targets"), "telemetry": rec.get("telemetry"),
                 "ok": rec.get("ok"), "err": rec.get("err"),
                 "recovery_path": res.get("recovery_path")}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--design", required=True, choices=list(camp.DESIGNS))
    ap.add_argument("--com", help="SEM/UART serial port of the board")
    ap.add_argument("--mock", action="store_true", help="no board; UI test only")
    ap.add_argument("--frame", type=int, default=0, choices=range(N_DATA_FRAMES),
                    help="frame shown at start")
    ap.add_argument("--vset", default="rand_full", choices=VECTOR_SETS,
                    help="initial input vector set (tones gives clear spectral lines)")
    ap.add_argument("--selftest", metavar="PNG", help="(mock) run golden + one injection, save the figure, exit")
    ap.add_argument("--bit", help="local copy of the design's frozen bitstream (SHA-256 checked)")
    ap.add_argument("--program", action="store_true",
                    help="program --bit into the FPGA (volatile, via Vivado) before connecting")
    ap.add_argument("--vivado-bin", help="Vivado bin directory (default: wp6_sem_campaign.VIVADO_BIN)")
    ap.add_argument("--auto", metavar="TARGET",
                    help="connect, golden run, inject TARGET ('lfa:HEX', 'L', '<kind>:<index>')")
    ap.add_argument("--auto-shot", metavar="PNG", help="with --auto: window screenshot afterwards")
    ap.add_argument("--auto-fig", metavar="PNG", help="with --auto: save the plot afterwards")
    ap.add_argument("--auto-exit", action="store_true", help="with --auto: close afterwards")
    args = ap.parse_args(argv)
    if args.vivado_bin:
        camp.VIVADO_BIN = Path(args.vivado_bin)
    if args.mock:
        session = MockSession(args.design)
    else:
        if not args.com:
            ap.error("--com is required unless --mock")
        session = BoardSession(args.design, args.com, bit=args.bit, program=args.program)
    if sys.platform == "win32":                  # physical pixels for screenshots
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:                                     # noqa: BLE001
            pass
    root = tk.Tk()
    auto = ({"target": args.auto, "shot": args.auto_shot, "fig": args.auto_fig,
             "exit": args.auto_exit} if args.auto else None)
    app = DemoHost(root, args.design, session, selftest=args.selftest, vset=args.vset, auto=auto,
                   frame=args.frame)
    if args.selftest or args.auto:
        root.after(300, app.on_connect)
    try:
        root.mainloop()
    finally:
        session.close()


if __name__ == "__main__":
    main()
