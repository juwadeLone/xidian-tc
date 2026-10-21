"""把 4 张 2048x96 位系数表逐行灌进 K7 板上的 seed RAM，并回读校验。

新增命令（见 rtl/board/cmd/udp_cmd_hdr.vh）：

    CS_ROW      0x07   flags[1:0] = 表号, addr[10:0] = 行号, length = 12,
                       后面跟 12 字节载荷（96 位行数据，小端）
    CS_ROW_ACK  0x88
    CS_RB       0x08   同上选址, length = 12
    CS_RB_ACK   0x89   头之后跟 12 字节载荷（96 位行数据，小端）

表文件与构建时 $readmemh 预载的是同一批（sim/vectors/cs2048_*_seeds.mem），
所以"灌进去的表 == 预载表"，回读校验完全可比。

用法：
    python scripts/k7_seed_upload.py --dry-run          # 只打印前 3 包，不碰板子
    python scripts/k7_seed_upload.py --port COM5        # 灌表 + 抽样回读校验
    python scripts/k7_seed_upload.py --verify-rows 0    # 只灌表，不回读
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MAGIC = 0x3348504D
CMD_CS_ROW = 0x07
CMD_CS_RB = 0x08
ACK_CS_ROW = 0x88
ACK_CS_RB = 0x89
NAK = 0x8F

SIDE = 2048
ROW_BYTES = 12
TABLES = [("h1", 0), ("h2", 1), ("h3", 2), ("c4", 3)]


def pack_hdr(cmd: int, seq: int, addr: int, length: int, flags: int = 0) -> bytes:
    return struct.pack("<IBBHII", MAGIC, cmd, flags, seq, addr, length)


def unpack_hdr(b: bytes):
    magic, cmd, flags, seq, addr, length = struct.unpack("<IBBHII", b)
    return magic, cmd, flags, seq, addr, length


def load_table(path: Path) -> list[int]:
    rows = []
    for line in path.read_text().splitlines():
        t = line.strip()
        if not t or t.startswith("//") or t.startswith("#"):
            continue
        rows.append(int(t, 16))
    if len(rows) != SIDE:
        raise SystemExit("行数不对 %s: %d" % (path, len(rows)))
    for v in rows:
        if v < 0 or v >= (1 << 96):
            raise SystemExit("行数据超出 96 位: %s" % path)
    return rows


class Link:
    """极简 UART 帧收发（不依赖 a7 的 host 库，避免动共享代码）。"""

    def __init__(self, port: str, baud: int):
        import serial  # noqa: WPS433
        self.ser = serial.Serial(port, baud, timeout=3.0)
        self.ser.reset_input_buffer()
        self.seq = 0
        self.short = 0

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def _next_seq(self):
        self.seq = (self.seq + 1) & 0xFFFF
        if self.seq == 0:
            self.seq = 1
        return self.seq

    def _read_frame(self, want_seq: int, timeout: float = 3.0):
        """读一个 16 字节头；若是 CS_RB_ACK 再读 12 字节载荷。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            hdr = self.ser.read(16)
            if len(hdr) != 16:
                self.short += 1
                continue
            magic, cmd, flags, seq, addr, length = unpack_hdr(hdr)
            if magic != MAGIC:
                continue
            payload = b""
            if cmd == ACK_CS_RB and 0 < length <= 16:
                payload = self.ser.read(length)
                if len(payload) != length:
                    self.short += 1
                    continue
            if seq != want_seq:
                continue
            return cmd, flags, addr, length, payload
        raise RuntimeError("ACK 超时 seq=%d" % want_seq)

    def write_row(self, sel: int, idx: int, value: int) -> None:
        seq = self._next_seq()
        row = value.to_bytes(ROW_BYTES, "little")
        self.ser.write(pack_hdr(CMD_CS_ROW, seq, idx, ROW_BYTES, flags=sel) + row)
        self.ser.flush()
        cmd, flags, addr, length, _ = self._read_frame(seq)
        if cmd == NAK:
            raise RuntimeError("NAK：行写被拒 sel=%d idx=%d" % (sel, idx))
        if cmd != ACK_CS_ROW:
            raise RuntimeError("ACK 不符 0x%02x sel=%d idx=%d" % (cmd, sel, idx))
        if addr != idx:
            raise RuntimeError("ACK 行号不符 %d != %d" % (addr, idx))

    def read_row(self, sel: int, idx: int) -> int:
        seq = self._next_seq()
        self.ser.write(pack_hdr(CMD_CS_RB, seq, idx, ROW_BYTES, flags=sel))
        self.ser.flush()
        cmd, flags, addr, length, payload = self._read_frame(seq)
        if cmd == NAK:
            raise RuntimeError("NAK：行读被拒 sel=%d idx=%d" % (sel, idx))
        if cmd != ACK_CS_RB:
            raise RuntimeError("ACK 不符 0x%02x sel=%d idx=%d" % (cmd, sel, idx))
        return int.from_bytes(payload, "little")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-rows", type=int, default=256,
                    help="每张表抽样回读多少行（0 = 不回读）")
    ap.add_argument("--read-only", action="store_true",
                    help="只回读校验，不写表 —— 用来证明表是板上自己建出来的"
                         "（配合 --verify-rows 2048 = 8192 行全查）")
    ap.add_argument("--quiet-every", type=int, default=512)
    args = ap.parse_args()

    tables = []
    for name, sel in TABLES:
        path = ROOT / "sim" / "vectors" / ("cs2048_%s_seeds.mem" % name)
        rows = load_table(path)
        tables.append((name, sel, rows))
        print("载入 %-3s sel=%d 行数=%d 首行=%024x" % (name, sel, len(rows), rows[0]))

    if args.dry_run:
        name, sel, rows = tables[0]
        for idx in (0, 1, 2047):
            pkt = pack_hdr(CMD_CS_ROW, 1, idx, ROW_BYTES, flags=sel) + \
                rows[idx].to_bytes(ROW_BYTES, "little")
            print("idx=%4d 包=%s" % (idx, pkt.hex()))
        print("DRY_RUN_OK（未触碰串口）")
        return 0

    link = Link(args.port, args.baud)
    t0 = time.time()
    try:
        written = 0
        if args.read_only:
            print("READ_ONLY：不写表，直接回读校验（表应由板上 f64 引擎自建）")
        else:
            for name, sel, rows in tables:
                tb = time.time()
                for idx, val in enumerate(rows):
                    link.write_row(sel, idx, val)
                    written += 1
                    if args.quiet_every and (idx + 1) % args.quiet_every == 0:
                        print("  %s %4d/%d  (%.1fs)" % (name, idx + 1, SIDE,
                                                        time.time() - tb))
                print("表 %-3s 灌完 %d 行，用时 %.1fs" % (name, SIDE, time.time() - tb))
            print("共写入 %d 行，总用时 %.1fs，短读 %d 次" %
                  (written, time.time() - t0, link.short))

        if args.verify_rows > 0:
            step = max(1, SIDE // args.verify_rows)
            bad = 0
            checked = 0
            for name, sel, rows in tables:
                for idx in range(0, SIDE, step):
                    got = link.read_row(sel, idx)
                    checked += 1
                    if got != rows[idx]:
                        bad += 1
                        if bad <= 8:
                            print("  失配 %s[%d] 板上=%024x 文件=%024x" %
                                  (name, idx, got, rows[idx]))
            print("回读校验：抽查 %d 行（每 %d 行取 1），失配 %d" %
                  (checked, step, bad))
            if bad:
                print("SEED_UPLOAD_VERDICT=MISMATCH")
                return 2
        print("SEED_UPLOAD_VERDICT=PASS")
        return 0
    finally:
        link.close()


if __name__ == "__main__":
    sys.exit(main())
