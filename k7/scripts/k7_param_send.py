"""P3 主机侧：把 7 个物理标量（9 个字）按冻结协议 v2 下发到 K7 板。

协议见 results/k7_port/K7_P3_SEED_BUILDER_SPEC.md 第 1 节：

    CS_PARAM 包 = 2 个 32-bit 载荷字（w0 = addr 字段, w1 = length 字段），
    槽位号取 flags[3:0]；flags[7] = 1 表示"建表并跑一帧"。

    idx 0 : w0 = C      w1 = lam_hi
    idx 1 : w0 = B      w1 = Tp_hi
    idx 2 : w0 = v      w1 = Da
    idx 3 : w0 = Rs
    idx 4 : w0 = Tp_lo  w1 = lam_lo      <- v2: 两个非二进制精确数的低位字
    flags 0x80 : 建表 + 跑链

    lam_hi = f32(lam), lam_lo = f32(lam - f64(lam_hi))（Tp 同理）。
    C/B/v/Da/Rs 在 f32 里精确可表示，单字无损，不设 lo 槽。

用法：
    python scripts/k7_param_send.py --dry-run        # 只在终端打印，不碰板子
    python scripts/k7_param_send.py                  # 只写参数（不跑链）
    python scripts/k7_param_send.py --run            # 写参数 + 建表 + 跑一帧
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cs_params import PARAMS_2048  # noqa: E402  唯一参数来源
from k7_lab_paths import add_host  # noqa: E402

add_host(ROOT)
from gui.board_if import BoardHost  # noqa: E402
from gui.protocol import CMD_CS_PARAM, ACK_CS_PARAM  # noqa: E402

CS_PARAM_RUN = 0x80
# 协议 v3（板上 k7_seed_engine_f64 的契约，见 rtl/coeff/k7_seed_engine_f64.sv 头）：
#   idx 0 : w0 = C        (f32)  w1 = B     (f32)
#   idx 1 : w0 = v        (f32)  w1 = Da    (f32)
#   idx 2 : w0 = Rs       (f32)  w1 = lam[63:32]
#   idx 3 : w0 = lam[31:0]       w1 = Tp[63:32]
#   idx 4 : w0 = Tp[31:0]        w1 = unused
# C/B/v/Da/Rs 走 f32（参考表就是这些 f32 值算出来的，差一个 bit 都会动表）；
# lam/Tp 走完整 binary64 —— c4 的相位跨 5.9e5 圈，lam 少 1 个 f32 LSB 就会把
# 编码字推开 5e6 个 count，所以这里不再拆 hi/lo，直接送 64 位。
# 槽位表：(idx, flags, 参数名w0, 参数名w1)  —— None = 填 0
SLOTS = [
    (0, 0x00, "C", "B"),
    (1, 0x01, "v", "Da"),
    (2, 0x02, "Rs", "lam_hi"),
    (3, 0x03, "lam_lo", "tp_hi"),
    (4, 0x04, "tp_lo", None),
]


def f32(x: float) -> float:
    return float(np.float32(x))


def split_hi_lo(x: float) -> tuple[float, float]:
    """把 f64 标量拆成 (f32 高位, f32 低位)，二者 f64 相加还原到 ~2^-48 相对。

    协议 v3 起 lam/Tp 直接送 64 位，不再需要这个折中；留着是因为旧 v2 记录里
    的 hi/lo 数字靠它解释，删掉会让历史 json 无从复算。
    """
    hi = f32(x)
    return hi, f32(x - float(hi))


def f32_words(v: float) -> int:
    return struct.unpack("!I", struct.pack("!f", f32(v)))[0]


def f64_words(v: float) -> tuple[int, int]:
    """→ (高 32 位, 低 32 位)。板上按「先 hi 后 lo」装字，顺序反了 lam 会变成
    0xd9e83e423facfaac（已踩过）。"""
    u = struct.unpack("!Q", struct.pack("!d", float(v)))[0]
    return (u >> 32) & 0xFFFFFFFF, u & 0xFFFFFFFF


def build_packets(p: dict) -> list[tuple[int, int, int, int, str]]:
    """→ [(idx, flags, c0, c1, 说明)]"""
    lam_hi, lam_lo = f64_words(p["lam"])
    tp_hi, tp_lo = f64_words(p["Tp"])
    val = {
        "C": p["C"], "B": p["B"], "v": p["v"], "Da": p["Da"], "Rs": p["Rs"],
        "lam_hi": lam_hi, "lam_lo": lam_lo, "tp_hi": tp_hi, "tp_lo": tp_lo,
    }
    out = []
    for idx, fl, n0, n1 in SLOTS:
        w0 = f32_words(val[n0]) if n0 in ("C", "B", "v", "Da", "Rs") else (val[n0] & 0xFFFFFFFF)
        w1 = 0 if n1 is None else (
            f32_words(val[n1]) if n1 in ("C", "B", "v", "Da", "Rs") else (val[n1] & 0xFFFFFFFF))
        out.append((idx, fl, w0, w1, "%s|%s" % (n0, n1 or "-")))
    # RUN 包：cmd_seq 对每个 CS_PARAM 包都会拉 cs_param_we，而 idx = flags[3:0]，
    # 所以 flags=0x80 的这包会把槽位 0 重写一遍 —— 那就干脆让它重写同样的 C|B，
    # 而不是写 0（写 0 会把 C/B 清零，表全废）。板上引擎此刻已锁好 0..4 槽，
    # 重写只是写入相同的值。
    cb0 = f32_words(p["C"])
    cb1 = f32_words(p["B"])
    out.append((5, CS_PARAM_RUN, cb0, cb1, "RUN(建表+跑链)"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="最后一包带 flags[7]=1，建表并跑一帧")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不发板")
    ap.add_argument("--output", default="results/k7_port/param_send.json")
    ap.add_argument("--port", default="COM5", help="板卡串口（默认 COM5）")
    args = ap.parse_args()
    global _PORT
    _PORT = args.port

    pk = build_packets(PARAMS_2048)

    print("=== CS_PARAM 下发序列（协议 v3，板上 f64 建表）===")
    for idx, fl, w0, w1, note in pk:
        print("  idx=%d flags=0x%02X  w0=0x%08x  w1=0x%08x   %s" % (idx, fl, w0, w1, note))
    _lhi, _llo = f64_words(PARAMS_2048["lam"])
    _thi, _tlo = f64_words(PARAMS_2048["Tp"])
    lam_bits = (_lhi << 32) | _llo
    tp_bits = (_thi << 32) | _tlo
    print("  64 位载荷校验: lam=%.17g (0x%016x)  Tp=%.17g (0x%016x)" % (
        PARAMS_2048["lam"], lam_bits, PARAMS_2048["Tp"], tp_bits))
    print("  还原校验(板上所见): lam=%.17g  Tp=%.17g" % (
        struct.unpack("!d", struct.pack("!Q", lam_bits))[0],
        struct.unpack("!d", struct.pack("!Q", tp_bits))[0]))

    if args.dry_run:
        print("[dry-run] 未发板")
        return 0

    res = {"packets": [], "ok": True}
    for idx, fl, w0, w1, note in pk:
        if idx > 4 and not args.run:
            print("  idx=%d 跳过（需 --run）" % idx)
            res["packets"].append({"idx": idx, "note": note, "sent": False})
            continue
        print("-> UART CS_PARAM idx=%d flags=0x%02X ..." % (idx, fl), flush=True)
        try:
            r = _send(CMD_CS_PARAM, w0, w1, fl)
            # 板回显的 addr/len 必须与我发的 32-bit 字逐位相同 —— 这是"字真的落进 cmd_seq"的证据
            ok = (r["addr"] == w0) and (r["len"] == w1)
            print("   ACK=%s  回显 addr=%s len=%s（发 0x%08x / 0x%08x）"
                  % (ok, r["addr"], r["len"], w0, w1))
            res["packets"].append({"idx": idx, "note": note, "sent": True, "ack": bool(ok),
                                   "w0": w0, "w1": w1, "echo_addr": r["addr"],
                                   "echo_len": r["len"], "raw": r["raw"]})
            res["ok"] = res["ok"] and bool(ok)
        except Exception as e:  # noqa: BLE001
            print("   FAIL %s" % e)
            res["packets"].append({"idx": idx, "note": note, "sent": True, "error": str(e)})
            res["ok"] = False

    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, ensure_ascii=False))
    print("报告 -> %s   ok=%s" % (out, res["ok"]))
    return 0 if res["ok"] else 2


_HOST = None
_PORT = "COM5"
_LOGS: list[str] = []


def _host():
    """一个进程内只开一次板卡连接（a7 主机库：构造 + start(com, udp)）。"""
    global _HOST
    if _HOST is None:
        h = BoardHost(log=lambda x: (_LOGS.append(str(x)), print(x, flush=True))[1])
        h.start(com=_PORT, udp=True)
        _HOST = h
    return _HOST


def _send(cmd, w0, w1, flags):
    """用 a7 主机库的 uart_cmd 发一个 CS_PARAM 包（flags 决定槽位号与 run 位）。

    uart_cmd 在 expect 不匹配/超时时抛异常，所以"没抛"即收到正确 ACK；
    板子回显的 addr/len 从主机库日志行里取，作为可核查证据。
    """
    before = len(_LOGS)
    _host().uart_cmd(cmd, w0, w1, expect=ACK_CS_PARAM, timeout=5.0, flags=flags)
    ack = [l for l in _LOGS[before:] if "CS_PARAM_ACK" in l]
    m = re.search(r"addr=(\d+) len=(\d+)", ack[-1]) if ack else None
    return {"raw": ack[-1] if ack else None,
            "addr": int(m.group(1)) if m else None,
            "len": int(m.group(2)) if m else None}


if __name__ == "__main__":
    raise SystemExit(main())
