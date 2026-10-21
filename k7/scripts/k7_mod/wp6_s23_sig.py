"""WP6 §23.2 output-signature matching (offline, diagnostic only).

Frozen rules: results/wp6/S23_freeze.md.  Injection points =
stage-boundary recovery-circuit outputs per lane/component/bit; modes =
const-0/const-1/invert/conditional-flip.  Conditional flip: least-
squares matched filter over per-beat exact effects, then exact joint
propagation for the final metrics.

Equivalence (frozen): an ECC decoder data-register bit b>=16 has the
same effect as stage-output word bit b-16; the pair is reported as ONE
equivalence class.  Register bits b<16 have zero output effect (they
are in the searched space but carry no signature).

Stage/stream mapping (frozen):
  pfft  : stage-k state[4t+lane] <- stream (lane,t); output stream word
          (b,l) = state10[4b+l] (the double bit-reversal cancels).
  subfft: stage k<=8 per-lane R2SDF state[t]; stage-9 lane l beat t ->
          a_l(bin bitrev8(t)); stage10 lane l beat t ->
          q_l(bin bitrev8(t)) = natural[l*256+bitrev8(t)].

Usage:
  python3 wp6_s23_sig.py <design> [nproc]   # -> s23_sig_<design>.json
  python3 wp6_s23_sig.py --combine          # -> S23_signature.{json,md}
"""
import gzip
import json
import pickle
import math
import sys
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sim"))
import fixed_fft_wgrow as mg  # noqa: E402
import gen_vectors_wg as gv  # noqa: E402

OUT = ROOT / "results" / "wp6"
STAGE_W = mg.STAGE_W
TW_FRAC = mg.TW_FRAC
IN_W = mg.IN_W

# searched shots: control 1264, second control 4005, then targets
SHOTS = {"P0_p1": [1264, 4005, 186, 3939, 1285],
         "S0_s3": [1712, 1751], "P1_p2": [1377], "S1_s2": [144]}
CONTROL = {"design": "P0_p1", "index": 1264,
           "expect": {"stage": 1, "lane": 0, "comp": "im",
                      "reg_bit": 29}}
SECOND = {"design": "P0_p1", "index": 4005,
          "expect": {"stage": 8, "lane": 3}}
ECC_STAGES = {"P0_p1": set(range(1, 8)), "S0_s3": set(range(1, 8))}
# NB: P1_p2's measured `exp` words equal the pfft_exchange golden at
# 14260/14260 fields -- the p2 bitstream implements the exchanged
# schedule (the campaign table's "pfft_no_exchange" label is stale).
DESIGN_MODEL = {"P0_p1": "pfft_exchange", "P1_p2": "pfft_exchange",
                "S0_s3": "subfft", "S1_s2": "subfft"}

BR8 = np.array([mg.bit_reverse(i, 8) for i in range(256)])
TW_BITS = TW_FRAC + 2                      # quantized coeff is 18-bit


def wrap(v, w):
    m = np.int64(1) << int(w)
    return ((v + (m >> 1)) % m) - (m >> 1)


def twiddle_table(n, exps):
    ang = -2.0 * math.pi * (np.asarray(exps, dtype=np.float64)
                            % n) / n

    def q(x):
        r = np.where(x >= 0, np.floor(x + 0.5), np.ceil(x - 0.5))
        return np.clip(r, mg.TW_MIN, mg.TW_MAX).astype(np.int64)
    return (q(np.cos(ang) * (1 << TW_FRAC)),
            q(np.sin(ang) * (1 << TW_FRAC)))


def mul_tw(re, im, wr, wi, w):
    return (wrap((re * wr - im * wi) >> TW_FRAC, w),
            wrap((re * wi + im * wr) >> TW_FRAC, w))


def dif_stage(re, im, stage, n, tw):
    """Canonical radix-2 DIF stage; arrays (..., n)."""
    length = 1 << (int(math.log2(n)) + 1 - stage)
    depth = length // 2
    stride = n // length
    w = STAGE_W[stage]
    st_re = re.reshape(-1, length)
    st_im = im.reshape(-1, length)
    a_re, a_im = st_re[:, :depth], st_im[:, :depth]
    b_re, b_im = st_re[:, depth:], st_im[:, depth:]
    lo_re, lo_im = mul_tw(
        wrap(a_re - b_re, w), wrap(a_im - b_im, w),
        tw[0][np.arange(depth) * stride],
        tw[1][np.arange(depth) * stride], w)
    os = re.shape
    return (np.concatenate([wrap(a_re + b_re, w), lo_re],
                           axis=-1).reshape(os),
            np.concatenate([wrap(a_im + b_im, w), lo_im],
                           axis=-1).reshape(os))


def bf_only(re, im, stage, n):
    """Butterfly-only radix-2 stage (exchanged schedule)."""
    length = 1 << (int(math.log2(n)) + 1 - stage)
    depth = length // 2
    w = STAGE_W[stage]
    st_re = re.reshape(-1, length)
    st_im = im.reshape(-1, length)
    a_re, a_im = st_re[:, :depth], st_im[:, :depth]
    b_re, b_im = st_re[:, depth:], st_im[:, depth:]
    os = re.shape
    return (np.concatenate([wrap(a_re + b_re, w),
                            wrap(a_re - b_re, w)], axis=-1).reshape(os),
            np.concatenate([wrap(a_im + b_im, w),
                            wrap(a_im - b_im, w)], axis=-1).reshape(os))


def exchanged_stage(re, im, stage, phi_row, tw, corrupt=None):
    """P0 stage: butterfly, then BOTH outputs rotated by phi.
    corrupt: optional (exp_e, comp, bit, lane_mask_set) — positions with
    phi_exp==exp_e and index%4 in lane_mask get the coefficient bit
    flipped before rotation (stage-6 ROM candidates)."""
    n = re.shape[-1]
    ure, uim = bf_only(re, im, stage, n)
    ure = ure.reshape(-1, n)
    uim = uim.reshape(-1, n)
    if stage <= 9:
        w = STAGE_W[stage]
        exps = np.asarray(phi_row) % 1024
        wr = np.broadcast_to(tw[0][exps], ure.shape).copy()
        wi = np.broadcast_to(tw[1][exps], uim.shape).copy()
        if corrupt is not None:
            kind = corrupt[0]
            arr = wi if corrupt[1] == "im" else wr
            if kind == "exp":
                _, _, cbit, lanes, exp_e = corrupt
                idx4 = np.arange(n) % 4
                sel = (exps == exp_e) & np.isin(idx4, list(lanes))
                if arr.ndim == 1:
                    vals = arr[sel]
                    b = (vals >> np.int64(cbit)) & 1
                    arr[sel] = vals + np.where(b == 0, 1, -1) \
                        * (np.int64(1) << cbit)
                else:
                    vals = arr[:, sel]
                    b = (vals >> np.int64(cbit)) & 1
                    arr[:, sel] = vals + np.where(b == 0, 1, -1) \
                        * (np.int64(1) << cbit)
            else:                       # bus / busT
                _, _, cbit, lanes = corrupt
                beats = None
                if kind == "busT":
                    lanes, beats = lanes
                idx4 = np.arange(n) % 4
                idxb = np.arange(n) // 4
                if arr.ndim == 1:
                    sel = np.isin(idx4, list(lanes))
                    if beats is not None:
                        sel = sel & np.isin(idxb, list(beats))
                    vals = arr[sel]
                    b = (vals >> np.int64(cbit)) & 1
                    arr[sel] = vals + np.where(b == 0, 1, -1) \
                        * (np.int64(1) << cbit)
                else:
                    nb = arr.shape[0]
                    rowok = (idxb[None, :] == np.arange(nb)[:, None])
                    sel = np.isin(idx4, list(lanes))[None, :] & rowok
                    if beats is not None:
                        sel = sel & np.isin(
                            np.arange(nb)[:, None], list(beats))
                    vals = arr[sel]
                    b = (vals >> np.int64(cbit)) & 1
                    arr[sel] = vals + np.where(b == 0, 1, -1) \
                        * (np.int64(1) << cbit)
        ure, uim = mul_tw(ure, uim, wr, wi, w)
    return ure.reshape(re.shape), uim.reshape(im.shape)


def combine_9_10(lre, lim):
    """subfft stages 9+10; input (...,4,256) stage-8 in-order state.
    Returns (a_re,a_im), (q_re,q_im), (o_re,o_im); a/q indexed
    [.., l, bin]; o[.., l*256+k] = q[.., l, k]."""
    w8, w9, w10 = STAGE_W[8], STAGE_W[9], STAGE_W[10]
    nat_re = lre[..., BR8]
    nat_im = lim[..., BR8]
    k = np.arange(256)
    cr = np.empty_like(nat_re)
    ci = np.empty_like(nat_im)
    for l in range(4):
        wr, wi = twiddle_table(1024, l * k)
        cr[..., l, :], ci[..., l, :] = mul_tw(
            nat_re[..., l, :], nat_im[..., l, :], wr, wi, w8)
    a_re = np.stack([wrap(cr[..., 0, :] + cr[..., 2, :], w9),
                     wrap(cr[..., 1, :] + cr[..., 3, :], w9),
                     wrap(cr[..., 0, :] - cr[..., 2, :], w9),
                     wrap(ci[..., 1, :] - ci[..., 3, :], w9)],
                    axis=-2)
    a_im = np.stack([wrap(ci[..., 0, :] + ci[..., 2, :], w9),
                     wrap(ci[..., 1, :] + ci[..., 3, :], w9),
                     wrap(ci[..., 0, :] - ci[..., 2, :], w9),
                     wrap(-(cr[..., 1, :] - cr[..., 3, :]), w9)],
                    axis=-2)
    q_re = np.stack([wrap(a_re[..., 0, :] + a_re[..., 1, :], w10),
                     wrap(a_re[..., 2, :] + a_re[..., 3, :], w10),
                     wrap(a_re[..., 0, :] - a_re[..., 1, :], w10),
                     wrap(a_re[..., 2, :] - a_re[..., 3, :], w10)],
                    axis=-2)
    q_im = np.stack([wrap(a_im[..., 0, :] + a_im[..., 1, :], w10),
                     wrap(a_im[..., 2, :] + a_im[..., 3, :], w10),
                     wrap(a_im[..., 0, :] - a_im[..., 1, :], w10),
                     wrap(a_im[..., 2, :] - a_im[..., 3, :], w10)],
                    axis=-2)
    o_re = np.concatenate([q_re[..., 0, :], q_re[..., 1, :],
                           q_re[..., 2, :], q_re[..., 3, :]], axis=-1)
    o_im = np.concatenate([q_im[..., 0, :], q_im[..., 1, :],
                           q_im[..., 2, :], q_im[..., 3, :]], axis=-1)
    return (a_re, a_im), (q_re, q_im), (o_re, o_im)


class Model:
    """Bit-exact numpy replica of sim/fixed_fft_wgrow.py, kept
    stage-by-stage for injection."""

    def __init__(self, name):
        self.model = name
        self.tw256 = twiddle_table(256, np.arange(256))
        self.tw1024 = twiddle_table(1024, np.arange(1024))
        if name == "pfft_exchange":
            self.phi = [np.asarray(r) % 1024
                        for r in mg.exchange_phi(1024)]

    def states(self, frame):
        """Golden per-stage states for one input frame."""
        re = wrap(np.array([v.real for v in frame], dtype=np.int64),
                  IN_W)
        im = wrap(np.array([v.imag for v in frame], dtype=np.int64),
                  IN_W)
        sts = {}
        if self.model == "subfft":
            re = np.stack([re[l::4] for l in range(4)])
            im = np.stack([im[l::4] for l in range(4)])
            for s in range(1, 9):
                nr = np.empty_like(re)
                ni = np.empty_like(im)
                for l in range(4):
                    nr[l], ni[l] = dif_stage(re[l], im[l], s, 256,
                                             self.tw256)
                re, im = nr, ni
                sts[s] = (re.copy(), im.copy())
            (a_r, a_i), (q_r, q_i), (o_r, o_i) = combine_9_10(re, im)
            sts[9] = (a_r, a_i)
            sts[10] = (q_r, q_i)
            sts["out"] = (o_r, o_i)
        else:
            for s in range(1, 11):
                if self.model == "pfft_exchange":
                    re, im = exchanged_stage(
                        re, im, s,
                        self.phi[s - 1] if s <= 9 else None,
                        self.tw1024)
                else:
                    re, im = dif_stage(re, im, s, 1024, self.tw1024)
                sts[s] = (re.copy(), im.copy())
            sts["out"] = (re, im)        # stream word o=4b+l = state10[o]
        return sts

    def propagate(self, stage, re, im):
        """Continue stages stage+1..N -> stream-order output words."""
        if self.model == "subfft":
            if stage <= 8:
                cre, cim = re, im
                for s in range(stage + 1, 9):
                    nr = np.empty_like(cre)
                    ni = np.empty_like(cim)
                    for l in range(4):
                        nr[..., l, :], ni[..., l, :] = dif_stage(
                            cre[..., l, :], cim[..., l, :], s, 256,
                            self.tw256)
                        cre, cim = nr, ni
                _a, _q, out = combine_9_10(cre, cim)
                return out
            if stage == 10:
                q_re, q_im = re, im
                return (np.concatenate(
                    [q_re[..., 0, :], q_re[..., 1, :],
                     q_re[..., 2, :], q_re[..., 3, :]], axis=-1),
                    np.concatenate(
                    [q_im[..., 0, :], q_im[..., 1, :],
                     q_im[..., 2, :], q_im[..., 3, :]], axis=-1))
            a_re, a_im = re, im               # stage 9
            w10 = STAGE_W[10]
            q_re = np.stack(
                [wrap(a_re[..., 0, :] + a_re[..., 1, :], w10),
                 wrap(a_re[..., 2, :] + a_re[..., 3, :], w10),
                 wrap(a_re[..., 0, :] - a_re[..., 1, :], w10),
                 wrap(a_re[..., 2, :] - a_re[..., 3, :], w10)],
                axis=-2)
            q_im = np.stack(
                [wrap(a_im[..., 0, :] + a_im[..., 1, :], w10),
                 wrap(a_im[..., 2, :] + a_im[..., 3, :], w10),
                 wrap(a_im[..., 0, :] - a_im[..., 1, :], w10),
                 wrap(a_im[..., 2, :] - a_im[..., 3, :], w10)],
                axis=-2)
            return (np.concatenate(
                [q_re[..., 0, :], q_re[..., 1, :],
                 q_re[..., 2, :], q_re[..., 3, :]], axis=-1),
                np.concatenate(
                [q_im[..., 0, :], q_im[..., 1, :],
                 q_im[..., 2, :], q_im[..., 3, :]], axis=-1))
        cre, cim = re, im
        for s in range(stage + 1, 11):
            if self.model == "pfft_exchange":
                cre, cim = exchanged_stage(
                    cre, cim, s,
                    self.phi[s - 1] if s <= 9 else None,
                    self.tw1024)
            else:
                cre, cim = dif_stage(cre, cim, s, 1024, self.tw1024)
        return cre, cim


def corrupt(vals, bit, mode, width):
    b = (vals >> np.int64(bit)) & 1
    if mode == "const0":
        return wrap(vals & ~(np.int64(1) << bit), width)
    if mode == "const1":
        return wrap(vals | (np.int64(1) << bit), width)
    return wrap(vals + np.where(b == 0, 1, -1)
                * (np.int64(1) << bit), width)


def inj_pos(model, stage, lane):
    """Index arrays for (stage,lane): (lane_idx[256], pos[256])."""
    t = np.arange(256)
    if model == "subfft":
        if stage <= 8:
            return np.full(256, lane), t
        return np.full(256, lane), BR8[t]
    return None, 4 * t + lane


def effect_beat_batch(model, sts, stage, lane, comp, bit):
    """Per-beat corruption for one frame -> (re,im) (256,1024)."""
    re, im = sts[stage]
    w = STAGE_W[stage]
    cre = np.broadcast_to(re, (256,) + re.shape).copy()
    cim = np.broadcast_to(im, (256,) + im.shape).copy()
    arr = cim if comp == "im" else cre
    li, pi = inj_pos(model.model, stage, lane)
    t = np.arange(256)
    if model.model == "subfft":
        v = arr[t, li, pi]
        arr[t, li, pi] = corrupt(v, bit, "invert", w)
    else:
        v = arr[t, pi]
        arr[t, pi] = corrupt(v, bit, "invert", w)
    return model.propagate(stage, cre, cim)


def effect_mode(model, sts, stage, lane, comp, bit, mode):
    """const0/const1/invert applied to ALL beats -> (re,im) (1024,)."""
    re, im = sts[stage]
    w = STAGE_W[stage]
    cre, cim = re.copy(), im.copy()
    arr = cim if comp == "im" else cre
    if model.model == "subfft":
        arr[lane] = corrupt(arr[lane], bit, mode, w)
    else:
        arr[lane::4] = corrupt(arr[lane::4], bit, mode, w)
    return model.propagate(stage, cre, cim)


def apply_beats(model, sts, stage, lane, comp, bit, beats):
    """Flip (stage,lane,comp,bit) on the given beats only."""
    re, im = sts[stage]
    w = STAGE_W[stage]
    sel = np.array(sorted(beats), dtype=int)
    cre, cim = re.copy(), im.copy()
    if len(sel) == 0:
        return model.propagate(stage, cre, cim)
    arr = cim if comp == "im" else cre
    li, pi = inj_pos(model.model, stage, lane)
    if model.model == "subfft":
        v = arr[li[sel], pi[sel]]
        arr[li[sel], pi[sel]] = corrupt(v, bit, "invert", w)
    else:
        v = arr[pi[sel]]
        arr[pi[sel]] = corrupt(v, bit, "invert", w)
    return model.propagate(stage, cre, cim)


def p0_stage6_twiddle(model, sts, exp_e, comp, cbit, lanes):
    """P0 stage-6 ROM candidate: coefficient bit cbit of exponent
    exp_e wrong on lanes (index%4 subset).  -> (re,im) (1024,)."""
    re, im = sts[5]
    ure, uim = exchanged_stage(re, im, 6, model.phi[5],
                               model.tw1024,
                               corrupt=("exp", comp, cbit, lanes,
                                        exp_e))
    cre, cim = ure, uim
    for s in range(7, 11):
        cre, cim = exchanged_stage(cre, cim, s,
                                   model.phi[s - 1] if s <= 9 else None,
                                   model.tw1024)
    return cre, cim


# ------------------------------------------------------------- measured

def omap_of(modelname):
    """lane -> array o[b] mapping output stream (beat,lane) -> index."""
    t = np.arange(256)
    if modelname == "subfft":
        return {l: l * 256 + BR8[t] for l in range(4)}
    return {l: 4 * t + l for l in range(4)}


def load_measured(design):
    """index -> rep -> {'patch': {f:{o:[re,im]}}, 'wrong':{(f,b,fld)}}"""
    p = OUT / design / "s22_output_diffs.jsonl.gz"
    out = defaultdict(dict)
    for ln in gzip.open(p, "rt", encoding="utf-8"):
        e = json.loads(ln)
        idx, rep = e["index"] // 100, e["index"] % 100
        wrong = set()
        patch = defaultdict(dict)
        for d in e["diffs"]:
            f, b = d["frame"], d["beat_in_frame"]
            for fl in d["fields"]:
                wrong.add((f, b, fl["field"]))
                o = b * 4 + fl["field"] // 2
                patch[f].setdefault(o, [None, None])
                patch[f][o][fl["field"] % 2] = fl["got"]
        out[idx][rep] = {"wrong": wrong, "patch": dict(patch)}
    return out


# ------------------------------------------------------------- scoring

def cand_fields(cre, cim, gre, gim, omap):
    """Candidate vs golden -> set of wrong (beat, field 0..7)."""
    w = set()
    for l in range(4):
        os = omap[l]
        for c, d in ((0, cre - gre), (1, cim - gim)):
            for b in np.nonzero(d[os])[0]:
                w.add((int(b), 2 * l + c))
    return w


def field_frames(fields, f):
    return {(f, b, fl) for b, fl in fields}


def metrics(cand_wrong, wrong_meas):
    """(coverage, excess_rate) on (frame,beat,field) triples."""
    if not wrong_meas:
        return 0.0, (1.0 if cand_wrong else 0.0)
    inter = len(cand_wrong & wrong_meas)
    cov = inter / len(wrong_meas)
    exc = (len(cand_wrong - wrong_meas)
           / len(cand_wrong)) if cand_wrong else 0.0
    return cov, exc


def exact_words(pred_by_frame, got_by_frame):
    """pred/got: {f: (re,im) arrays}.  True iff every word matches."""
    for f, (pr, pi) in pred_by_frame.items():
        gr, gi = got_by_frame[f]
        if not (np.array_equal(pr, gr) and np.array_equal(pi, gi)):
            return False
    return True


# ------------------------------------------------------------- worker

_G = {}


def init_ctx(design):
    model = Model(DESIGN_MODEL[design])
    _ids, frames = gv.build("rand_full")
    sts = [model.states(fr) for fr in frames[:8]]
    omap = omap_of(model.model)
    meas_raw = load_measured(design)
    shots = {}
    for idx in SHOTS[design]:
        shots[idx] = {}
        for rep, mm in meas_raw.get(idx, {}).items():
            # reconstruct full got words: golden + patches
            got = {}
            res = {}
            for f in range(8):
                gr, gi = sts[f]["out"]
                gotr = gr.copy()
                goti = gi.copy()
                R = np.zeros((1024, 2), dtype=np.int64)
                for o, (vr, vi) in mm["patch"].get(f, {}).items():
                    if vr is not None:
                        gotr[o] = vr
                        R[o, 0] = vr - gr[o]
                    if vi is not None:
                        goti[o] = vi
                        R[o, 1] = vi - gi[o]
                got[f] = (gotr, goti)
                res[f] = R
            shots[idx][rep] = {"wrong": mm["wrong"], "got": got,
                               "res": res}
    _G.update(design=design, model=model, sts=sts, omap=omap,
              shots=shots)


def score_pred(pred_by_frame, cw, sd, golden_outs):
    """Metrics for one candidate vs one measured rep.
    pred_by_frame: {f:(re,im)} candidate outputs (missing = golden).
    Returns (cov, exc, exact)."""
    exact = True
    for f in range(8):
        pr, pi = pred_by_frame.get(f, golden_outs[f])
        gr, gi = sd["got"][f]
        if not (np.array_equal(pr, gr) and np.array_equal(pi, gi)):
            exact = False
            break
    cov, exc = metrics(cw, sd["wrong"])
    return cov, exc, exact


def eval_candidate(arg):
    """One (stage,lane,comp,out_bit) point-class -> score rows for
    every shot/rep/mode.  All comparison happens in-worker; only the
    score rows cross back."""
    stage, lane, comp, bit = arg
    m = _G["model"]
    omap = _G["omap"]
    sts = _G["sts"]
    gold = [(s["out"][0], s["out"][1]) for s in sts]
    rows = []
    if bit >= STAGE_W[stage]:
        return rows
    # const0/const1/invert: joint propagation per frame
    for mode in ("const0", "const1", "invert"):
        cw = set()
        pred = {}
        for f in range(8):
            re_, im_ = effect_mode(m, sts[f], stage, lane, comp,
                                   bit, mode)
            gr, gi = gold[f]
            pred[f] = (re_, im_)
            cw |= field_frames(
                cand_fields(re_, im_, gr, gi, omap), f)
        for idx, reps in _G["shots"].items():
            for rep, sd in reps.items():
                cov, exc, ex = score_pred(pred, cw, sd, gold)
                rows.append({"point": (stage, lane, comp, bit),
                             "mode": mode, "shot": idx, "rep": rep,
                             "cov": cov, "exc": exc, "exact": ex,
                             "n_cand": len(cw)})
    # conditional flip: per-beat exact effects + matched filter
    E = {}
    for f in range(8):
        er, ei = effect_beat_batch(m, sts[f], stage, lane, comp, bit)
        gr, gi = gold[f]
        E[f] = (er - gr, ei - gi)
    for idx, reps in _G["shots"].items():
        for rep, sd in reps.items():
            T = {}
            for f in range(8):
                er, ei = E[f]
                R = sd["res"][f]
                num = (er * R[:, 0] + ei * R[:, 1]).sum(1)
                den = (er * er + ei * ei).sum(1)
                inc = np.nonzero((num > 0) & (2 * num > den))[0]
                if len(inc):
                    T[f] = inc.tolist()
            cw = set()
            pred = {}
            for f, beats in T.items():
                re_, im_ = apply_beats(m, sts[f], stage, lane, comp,
                                       bit, beats)
                gr, gi = gold[f]
                pred[f] = (re_, im_)
                cw |= field_frames(
                    cand_fields(re_, im_, gr, gi, omap), f)
            cov, exc, ex = score_pred(pred, cw, sd, gold)
            rows.append({"point": (stage, lane, comp, bit),
                         "mode": "cond", "shot": idx, "rep": rep,
                         "cov": cov, "exc": exc, "exact": ex,
                         "n_cand": len(cw),
                         "n_beats": sum(len(v) for v in T.values())})
    return rows


def eval_twiddle(arg):
    """P0 stage-6 ROM candidate -> score rows for shot 1285."""
    exp_e, comp, cbit, lanes = arg
    m = _G["model"]
    omap = _G["omap"]
    sts = _G["sts"]
    gold = [(s["out"][0], s["out"][1]) for s in sts]
    cw = set()
    pred = {}
    for f in range(8):
        re_, im_ = p0_stage6_twiddle(m, sts[f], exp_e, comp, cbit,
                                     lanes)
        gr, gi = gold[f]
        pred[f] = (re_, im_)
        cw |= field_frames(cand_fields(re_, im_, gr, gi, omap), f)
    rows = []
    for rep, sd in _G["shots"].get(1285, {}).items():
        cov, exc, ex = score_pred(pred, cw, sd, gold)
        rows.append({"point": ("tw6", int(exp_e), comp, cbit,
                               sorted(lanes)),
                     "mode": "rom_bit", "shot": 1285, "rep": rep,
                     "cov": cov, "exc": exc, "exact": ex,
                     "n_cand": len(cw)})
    return rows

def p0_stage6_bus_batch(model, sts, comp, cbit, lanes):
    """Per-beat effect batch for a stage-6 coefficient BUS bit: for
    beat t, the coefficient bit used at that beat's lane-subset
    positions is flipped.  Returns (re,im) (256,1024)."""
    re, im = sts[5]
    ure = np.broadcast_to(re, (256,) + re.shape).copy()
    uim = np.broadcast_to(im, (256,) + im.shape).copy()
    ure, uim = exchanged_stage(ure, uim, 6, model.phi[5],
                               model.tw1024,
                               corrupt=("bus", comp, cbit, lanes))
    cre, cim = ure, uim
    for s in range(7, 11):
        cre, cim = exchanged_stage(cre, cim, s,
                                   model.phi[s - 1] if s <= 9 else None,
                                   model.tw1024)
    return cre, cim


def eval_bus(arg):
    """Stage-6 coeff bus bit (comp,cbit,lanes) -> score rows for 1285,
    modes invert + cond."""
    comp, cbit, lanes = arg
    m = _G["model"]
    omap = _G["omap"]
    sts = _G["sts"]
    gold = [(s["out"][0], s["out"][1]) for s in sts]
    rows = []
    # invert: corrupt the bus bit on all lane-subset positions
    cw = set()
    pred = {}
    for f in range(8):
        re0, im0 = sts[f][5]
        ure, uim = exchanged_stage(re0, im0, 6, m.phi[5], m.tw1024,
                                   corrupt=("bus", comp, cbit, lanes))
        cre, cim = ure, uim
        for s in range(7, 11):
            cre, cim = exchanged_stage(
                cre, cim, s, m.phi[s - 1] if s <= 9 else None,
                m.tw1024)
        gr, gi = gold[f]
        pred[f] = (cre, cim)
        cw |= field_frames(cand_fields(cre, cim, gr, gi, omap), f)
    for rep, sd in _G["shots"].get(1285, {}).items():
        cov, exc, ex = score_pred(pred, cw, sd, gold)
        rows.append({"point": ("bus6", comp, cbit, sorted(lanes)),
                     "mode": "invert", "shot": 1285, "rep": rep,
                     "cov": cov, "exc": exc, "exact": ex,
                     "n_cand": len(cw)})
    # cond: per-beat batch + matched filter
    E = {}
    for f in range(8):
        er, ei = p0_stage6_bus_batch(m, sts[f], comp, cbit, lanes)
        gr, gi = gold[f]
        E[f] = (er - gr, ei - gi)
    for rep, sd in _G["shots"].get(1285, {}).items():
        T = {}
        for f in range(8):
            er, ei = E[f]
            R = sd["res"][f]
            num = (er * R[:, 0] + ei * R[:, 1]).sum(1)
            den = (er * er + ei * ei).sum(1)
            inc = np.nonzero((num > 0) & (2 * num > den))[0]
            if len(inc):
                T[f] = inc.tolist()
        cw = set()
        pred = {}
        for f, beats in T.items():
            re0, im0 = sts[f][5]
            ure, uim = exchanged_stage(
                re0, im0, 6, m.phi[5], m.tw1024,
                corrupt=("busT", comp, cbit, (lanes, set(beats))))
            cre, cim = ure, uim
            for s in range(7, 11):
                cre, cim = exchanged_stage(
                    cre, cim, s, m.phi[s - 1] if s <= 9 else None,
                    m.tw1024)
            gr, gi = gold[f]
            pred[f] = (cre, cim)
            cw |= field_frames(cand_fields(cre, cim, gr, gi, omap), f)
        cov, exc, ex = score_pred(pred, cw, sd, gold)
        rows.append({"point": ("bus6", comp, cbit, sorted(lanes)),
                     "mode": "cond", "shot": 1285, "rep": rep,
                     "cov": cov, "exc": exc, "exact": ex,
                     "n_cand": len(cw),
                     "n_beats": sum(len(v) for v in T.values())})
    return rows


def class_name(design, stage, lane, comp, bit):
    ecc = stage in ECC_STAGES.get(design, set())
    c = "i" if comp == "im" else "r"
    if ecc:
        return (f"s{stage} dec q{lane}{c}[{bit + TW_FRAC}] "
                f"= out_word[{bit}]")
    return f"s{stage} voter_word l{lane} {comp}[{bit}]"


def dedup_top(rows, design, k=5):
    """Best rows per (shot,rep): sort by exact>cov>low exc; keep top-k
    distinct point-classes."""
    best = {}
    for r in rows:
        rep = r["rep"]
        best.setdefault(rep, []).append(r)
    out = {}
    for rep, rs in best.items():
        rs.sort(key=lambda t: (not t["exact"], -t["cov"], t["exc"],
                               str(t["point"])))
        seen = set()
        top = []
        for t in rs:
            if t["point"] in seen:
                continue
            seen.add(t["point"])
            row = dict(t)
            if t["point"][0] == "tw6":
                row["name"] = (f"s6 ROM exp={t['point'][1]} "
                               f"{t['point'][2]}[{t['point'][3]}] "
                               f"lanes{t['point'][4]}")
            else:
                s, l, c, b = t["point"]
                row["name"] = class_name(design, s, l, c, b)
            top.append(row)
            if len(top) >= k:
                break
        out[str(rep)] = top
    return out


def run_design(design, nproc):
    init_ctx(design)
    cands = [(s, lane, comp, b)
             for s in range(1, 11)
             for lane in range(4)
             for comp in ("re", "im")
             for b in range(STAGE_W[s])]
    n_frac = 0
    for s in sorted(ECC_STAGES.get(design, set())):
        n_frac += 4 * 2 * TW_FRAC     # dec_reg bits<16: provably zero
    t0 = time.time()
    with Pool(nproc, initializer=_pool_init,
              initargs=(design,)) as pool:
        rows = pool.map(eval_candidate, cands, chunksize=2)
    rows = [r for sub in rows for r in sub]
    print(f"[{design}] {len(cands)} point-classes "
          f"(+{n_frac} fraction-bit classes, provably zero effect) "
          f"in {time.time()-t0:.0f}s", flush=True)
    (OUT / f"s23_sig_{design}_rows.pkl").write_bytes(pickle.dumps(rows))
    _finish(design, rows, len(cands), n_frac)

    if design == "P0_p1":
        phi6 = np.asarray(mg.exchange_phi(1024)[5]) % 1024
        exps = sorted({int(e) for e in phi6})
        lanesets = [frozenset({l}) for l in range(4)] + \
            [frozenset({0, 1}), frozenset({0, 2}), frozenset({0, 3}),
             frozenset({1, 2}), frozenset({1, 3}), frozenset({2, 3}),
             frozenset({0, 1, 2, 3})]
        targs = [(e, c, b, ls) for e in exps for c in ("re", "im")
                 for b in range(TW_BITS) for ls in lanesets]
        bargs = [(c, b, ls) for c in ("re", "im")
                 for b in range(TW_BITS) for ls in lanesets]
        with Pool(nproc, initializer=_pool_init,
                  initargs=(design,)) as pool:
            trows = pool.map(eval_twiddle, targs, chunksize=8)
            brows = pool.map(eval_bus, bargs, chunksize=4)
        trows = [r for sub in trows for r in sub]
        brows = [r for sub in brows for r in sub]
        rows += trows + brows
        print(f"[{design}] {len(targs)} stage-6 ROM entry candidates "
              f"+ {len(bargs)} bus-bit candidates", flush=True)

    (OUT / f"s23_sig_{design}_rows.pkl").write_bytes(pickle.dumps(rows))
    _finish(design, rows, len(cands), n_frac)
    return


def _finish(design, rows, n_cands, n_frac):
    result = {"design": design, "n_point_classes": n_cands,
              "n_fraction_classes": n_frac,
              "n_rows": len(rows), "shots": {}}
    for idx in SHOTS[design]:
        srows = [r for r in rows if r["shot"] == idx]
        top = dedup_top(srows, design)
        reps = _G["shots"].get(idx, {})
        result["shots"][str(idx)] = {
            "reps": {str(r): {"n_wrong": len(sd["wrong"])}
                     for r, sd in reps.items()},
            "top_by_rep": top}
        first = top.get("1") or next(iter(top.values()), [])
        if first:
            b0 = first[0]
            print(f"[{design}] shot {idx}: best {b0['name']} "
                  f"{b0['mode']} cov={b0['cov']:.3f} "
                  f"exc={b0['exc']:.3f} exact={b0['exact']}",
                  flush=True)
    p = OUT / f"s23_sig_{design}.json"
    p.write_text(json.dumps(result, indent=1, default=str))
    print(f"[{design}] -> {p}", flush=True)
    return result


def rom_only(design, nproc):
    """Load checkpointed point-class rows; run only the stage-6
    twiddle/bus candidates; merge and write final JSON."""
    init_ctx(design)
    rows = pickle.loads(
        (OUT / f"s23_sig_{design}_rows.pkl").read_bytes())
    phi6 = np.asarray(mg.exchange_phi(1024)[5]) % 1024
    exps = sorted({int(e) for e in phi6})
    lanesets = [frozenset({l}) for l in range(4)] + \
        [frozenset({0, 1}), frozenset({0, 2}), frozenset({0, 3}),
         frozenset({1, 2}), frozenset({1, 3}), frozenset({2, 3}),
         frozenset({0, 1, 2, 3})]
    targs = [(e, c, b, ls) for e in exps for c in ("re", "im")
             for b in range(TW_BITS) for ls in lanesets]
    bargs = [(c, b, ls) for c in ("re", "im")
             for b in range(TW_BITS) for ls in lanesets]
    with Pool(nproc, initializer=_pool_init,
              initargs=(design,)) as pool:
        trows = pool.map(eval_twiddle, targs, chunksize=8)
        brows = pool.map(eval_bus, bargs, chunksize=4)
    trows = [r for sub in trows for r in sub]
    brows = [r for sub in brows for r in sub]
    rows += trows + brows
    print(f"[{design}] {len(targs)} ROM entry + {len(bargs)} bus-bit "
          f"candidates done", flush=True)
    (OUT / f"s23_sig_{design}_rows.pkl").write_bytes(pickle.dumps(rows))
    _finish(design, rows, 1800, 896)


def _pool_init(design):
    init_ctx(design)


# ------------------------------------------------------------- combine

def combine():
    data = {}
    for d in SHOTS:
        p = OUT / f"s23_sig_{d}.json"
        if p.is_file():
            data[d] = json.loads(p.read_text())

    def best_of(shot):
        """rep-1 top row, or first available rep."""
        t = shot.get("top_by_rep", {})
        return (t.get("1") or next(iter(t.values()), [None]))[0]

    def verdict_of(shot):
        """§24.3 frozen criterion: a shot is located only when the SAME
        candidate is exact in every repetition; anything else -> 未定位
        (near-total corruption leaves many candidates tied, so coverage
        alone cannot locate)."""
        tops = shot.get("top_by_rep", {})
        if not tops:
            return "未定位", None
        firsts = [v[0] for v in tops.values() if v]
        if (len(firsts) == len(tops)
                and all(t.get("exact") for t in firsts)
                and len({(tuple(t["point"]), t["mode"])
                         for t in firsts}) == 1):
            return "已定位", firsts[0]
        return "未定位", firsts[0] if firsts else None

    for d in data.values():
        for sd in d.get("shots", {}).values():
            v, b = verdict_of(sd)
            sd["verdict"] = v
            sd["located_by"] = (b["name"] + " / " + b["mode"]
                                if (v == "已定位" and b) else None)

    (OUT / "S23_signature.json").write_text(
        json.dumps(data, indent=1, default=str))

    ctl = CONTROL
    ok = None
    if ctl["design"] in data:
        shot = data[ctl["design"]]["shots"].get(str(ctl["index"]))
        if shot:
            best = best_of(shot)
            if best:
                e = ctl["expect"]
                pt = best["point"]
                hit = (pt[0] == e["stage"] and pt[1] == e["lane"]
                       and pt[2] == e["comp"]
                       and pt[3] == e["reg_bit"] - TW_FRAC)
                ok = bool(hit and (best["exact"]
                                   or (best["cov"] >= 1.0
                                       and best["exc"] <= 0.0)))

    lines = ["# WP6 §23.2 输出差异特征匹配（离线，仅诊断）", ""]
    lines.append("冻结口径：results/wp6/S23_freeze.md。定点模型 = "
                 "sim/fixed_fft_wgrow.py 的位精确 numpy 复刻（8 帧逐位"
                 "校验通过）；注入点 = 每级恢复电路输出，逐路/虚实/逐位；"
                 "错法 = 恒0/恒1/取反/条件翻转（逐拍精确效应 + 最小二乘"
                 "匹配滤波 + 联合传播复核）。ECC 译码寄存器位 b>=16 与"
                 "级输出字位 b-16 为同一等价类；b<16 的小数位效果恒零。")
    lines.append("")
    lines.append(f"阳性对照 P0 1264：预期第 1 级译码器 lane-0 虚部 "
                 f"q0i_reg[29]（= 输出字位 13）唯一最优 —— "
                 f"**{'通过' if ok else '未通过（应停止）' if ok is False else '未评估'}**")
    lines.append("")
    # §24.3: per-shot verdicts under the frozen criterion — anything
    # short of an all-repetition exact match is recorded 未定位.
    lines += ["### 判定汇总（冻结判据：同一候选 5 次重复全部精确匹配 "
              "= 已定位，否则未定位）", "",
              "| 设计 | 发 | 判定 | 最优候选（仅说明用） | 备注 |",
              "| --- | ---: | --- | --- | --- |"]
    for d in sorted(data):
        for idx, sd in sorted(data[d]["shots"].items(),
                              key=lambda x: int(x[0])):
            v = sd.get("verdict", "未定位")
            b = best_of(sd)
            tag = ("阳性对照" if (d == ctl["design"]
                   and int(idx) == ctl["index"])
                   else "第二对照" if (d == SECOND["design"]
                   and int(idx) == SECOND["index"]) else "")
            lines.append("| %s | %s | %s | %s | %s |"
                         % (d, idx, v,
                            (b["name"] + " / " + b["mode"]) if b else "—",
                            tag))
    lines.append("")
    lines.append("注：非精确匹配时覆盖率与多余率按（拍、字）计；近全错"
                 "的实测差异下大量候选并列，最优候选只作成因线索，"
                 "不作定位结论。")
    lines.append("")
    for d in sorted(data):
        dd = data[d]
        lines.append(f"## {d}（{dd['n_point_classes']} 个注入点类，"
                     f"小数位等价类 {dd['n_fraction_classes']} 个"
                     f"{'' if d != 'P0_p1' else '，另加第 6 级 ROM 位错候选'}）")
        lines.append("")
        for idx, sd in sorted(dd["shots"].items(),
                              key=lambda x: int(x[0])):
            tag = "（阳性对照）" if (d == ctl["design"]
                                    and int(idx) == ctl["index"]) else                   ("（第二对照）" if (d == SECOND["design"]
                                     and int(idx) == SECOND["index"])
                   else "")
            lines.append(f"### 第 {idx} 发 {tag}判定：{sd.get('verdict', '未定位')}")
            lines.append("")
            for rep, tops in sorted(sd.get("top_by_rep", {}).items(),
                                    key=lambda x: int(x[0])):
                nw = sd["reps"].get(rep, {}).get("n_wrong", "?")
                lines.append(f"重复 {rep}（实测错字 {nw}）：")
                lines.append("")
                lines.append("| 名次 | 候选 | 模式 | 覆盖率 | 多余率 | "
                             "精确 | 拍数 |")
                lines.append("| --- | --- | --- | --- | --- | --- | "
                             "--- |")
                for rank, t in enumerate(tops, 1):
                    nb = t.get("n_beats", "-")
                    lines.append(
                        f"| {rank} | {t['name']} | {t['mode']} "
                        f"| {t['cov']:.3f} | {t['exc']:.3f} "
                        f"| {'是' if t['exact'] else '否'} | {nb} |")
                lines.append("")
    lines += ["结果仅用于解释，不改任何一发的位置类别。", ""]
    (OUT / "S23_signature.md").write_text("\n".join(lines))
    print("-> S23_signature.{md,json}", "control:", ok)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--combine":
        return combine()
    if len(sys.argv) > 2 and sys.argv[2] == "--rom-only":
        return rom_only(sys.argv[1],
                        int(sys.argv[3]) if len(sys.argv) > 3 else 8)
    run_design(sys.argv[1],
               int(sys.argv[2]) if len(sys.argv) > 2 else 8)


if __name__ == "__main__":
    main()
