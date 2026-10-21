"""Board I/O for the K7 paper-FFT stream image (k7_paper_top).

Same command layer as the DDR builds, different data path:

    WR  CMD_WR(addr ignored, len = n_frames*IN_FRAME_BYTES) + paced UDP payload
        -> paper_fft_stream_engine in_buf (128-bit beats, byte k = beat[8k+7:8k])
    RUN CMD_CS_PARAM(flags=0x80) -> kernel streams all committed frames
    RD  CMD_RD(addr ignored, len = n_frames*6912) -> out_buf over UDP

There is no DDR readback of the input, so write verification uses the
engine's own counters through the CS_RB row port instead of re-reading B0:

    row 3 = {64'd0, target_frames[31:16], frames_committed[15:0]}
"""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_cram"))
sys.path.insert(0, str(ROOT / "scripts" / "k7_mod"))

from k7_sem_common import BoardHost, Wire, wait_idle  # noqa: E402
from k7_real_data_gate import read_range  # noqa: E402
from k7_seed_upload import CMD_CS_RB, ACK_CS_RB, ROW_BYTES  # noqa: E402
from gui.protocol import (  # noqa: E402
    CMD_WR, CMD_RD, CMD_CS_PARAM, ACK_WR, ACK_CS_PARAM, pack_hdr, unpack_hdr)

IN_BEAT_BYTES = 16       # 128-bit kernel input beat on the wire, little-endian
OUT_BEAT_BYTES = 27      # 216-bit kernel output beat on the wire, little-endian
FRAME_BEATS = 256
IN_FRAME_BYTES = FRAME_BEATS * IN_BEAT_BYTES    # 4096
OUT_FRAME_BYTES = FRAME_BEATS * OUT_BEAT_BYTES  # 6912
MAX_FRAMES = 16

ARCH_NAMES = {0: "s0", 1: "s1", 2: "s2", 3: "s3", 4: "p0", 5: "p1", 6: "p2"}


def pack_frame(beats):
    """beats: list[int] of 128-bit values -> little-endian wire bytes."""
    assert len(beats) == FRAME_BEATS
    return b"".join(int(b).to_bytes(IN_BEAT_BYTES, "little") for b in beats)


def unpack_frames(data, n_frames):
    """Wire bytes -> list of frames, each a list[int] of 216-bit beats."""
    assert len(data) == n_frames * OUT_FRAME_BYTES
    return [[int.from_bytes(
                data[OUT_BEAT_BYTES * b:OUT_BEAT_BYTES * b + OUT_BEAT_BYTES],
                "little")
             for b in range(FRAME_BEATS)]
            for _ in range(n_frames)]


def beat_from_fields(fields):
    """8 signed 16-bit fields -> packed 128-bit input beat (engine order)."""
    assert len(fields) == 8
    beat = 0
    for j, f in enumerate(fields):
        beat |= (int(f) & ((1 << 16) - 1)) << (112 - 16 * j)
    return beat


def gen_stim_frame(seed=0):
    """Deterministic bring-up frame: ramping pattern with per-beat tag."""
    beats = []
    for i in range(FRAME_BEATS):
        v = ((seed * 256 + i) * 8) & ((1 << 15) - 1)
        beats.append(beat_from_fields(
            [v + j if (v + j) < (1 << 15) else (v + j) - (1 << 16)
             for j in range(8)]))
    return beats


def _uart_cmd_tight(host, cmd, addr=0, length=0, expect=None,
                    timeout=3.0, flags=0):
    """Same helper as k7_mod_host (poll 0.2 ms) kept local to avoid the
    module import side-effect of importing k7_mod_host itself."""
    if not host._ser:
        raise RuntimeError("UART 未连接")
    with host._lock:
        seq = host._seq
        host._seq = (host._seq + 1) & 0xFFFF
        if host._seq == 0:
            host._seq = 1
        host.last_ack = None
    host._ser.write(pack_hdr(cmd, seq, addr, length, flags=flags))
    host._ser.flush()
    t0 = time.time()
    while time.time() - t0 < timeout:
        with host._lock:
            ack = host.last_ack
        if ack and ack.get("seq") == seq:
            if expect is not None and ack["cmd"] != expect:
                raise RuntimeError("ACK 不符 got 0x%02x" % ack["cmd"])
            return ack
        time.sleep(0.0002)
    raise RuntimeError("UART ACK 超时 cmd=0x%02x" % cmd)


def read_engine_row(host, idx):
    """One engine telemetry row via CS_RB (sel/dbg fields unused)."""
    ack = _uart_cmd_tight(host, CMD_CS_RB, idx, ROW_BYTES,
                          expect=ACK_CS_RB, timeout=3.0)
    payload = (ack or {}).get("payload") or b""
    if len(payload) != ROW_BYTES:
        raise RuntimeError("CS_RB short idx=%d got %d" % (idx, len(payload)))
    return int.from_bytes(payload, "little")


def read_engine_rows(host):
    """All engine telemetry rows, decoded.  Layouts match rb_idx in
    paper_fft_stream_engine.sv (MSB-first Verilog concatenation)."""
    r = {i: read_engine_row(host, i) for i in range(6)}
    return {
        "cor_cnt": r[0] & 0xFFFFFFFF, "det_cnt": (r[0] >> 32) & 0xFFFFFFFF,
        "out_commit": r[1] & 0xFFFFFFFF, "unc_cnt": (r[1] >> 32) & 0xFFFFFFFF,
        "last_loc": r[2] & 0xFF, "last_class": (r[2] >> 8) & 0xFF,
        "last_stage": (r[2] >> 16) & 0xFF, "last_beat": (r[2] >> 24) & 0xFFFF,
        "last_frame": (r[2] >> 40) & 0xFFFF, "last_win": (r[2] >> 56) & 0xFF,
        "frames_committed": r[3] & 0xFFFF,
        "target_frames": (r[3] >> 16) & 0xFFFF,
        "err_flags": r[4] & 0xFFFFFFFF,
        "arch": r[5] & 0xF, "ing_active": (r[5] >> 8) & 1,
        "rstate": (r[5] >> 9) & 0x3,
    }


def write_frames(host, wire, raw, gap_us=300, timeout=30.0):
    """WR one or more frames.  raw must be n*IN_FRAME_BYTES bytes of packed input beats."""
    n = len(raw) // IN_FRAME_BYTES
    assert len(raw) == n * IN_FRAME_BYTES and 1 <= n <= MAX_FRAMES
    host.uart_cmd(CMD_WR, 0, len(raw), expect=ACK_WR, timeout=10.0)
    t0 = time.monotonic()
    wire.stream(raw, chunk=1400, gap_us=gap_us)
    send_s = time.monotonic() - t0
    # The engine reports completion itself; frames_committed is the proof.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row3 = read_engine_row(host, 3)
        committed = row3 & 0xFFFF
        target = (row3 >> 16) & 0xFFFF
        if committed == n and target == n:
            return send_s
        time.sleep(0.05)
    raise RuntimeError("ingest verify failed: committed=%d target=%d "
                       "want %d" % (committed, target, n))


def run_fft(host, timeout=60.0):
    """CS_PARAM flags[7]=1 -> cs_build_start -> kernel streams all frames.
    ACK_CS_PARAM returns only after run_done (or NAK on run_error)."""
    host.uart_cmd(CMD_CS_PARAM, 0, 0, expect=ACK_CS_PARAM,
                  timeout=timeout, flags=0x80)


def shot(host, wire, raw, golden=None, gap_us=300, run_timeout=60.0):
    """One full WR -> RUN -> RD cycle.  Returns dict(ok, sha256, data, ...)."""
    n = len(raw) // IN_FRAME_BYTES
    write_s = write_frames(host, wire, raw, gap_us=gap_us)
    t0 = time.monotonic()
    run_fft(host, timeout=run_timeout)
    run_s = time.monotonic() - t0
    wait_idle(host, 10.0)
    got, read_s = read_range(host, 0, n * OUT_FRAME_BYTES)
    ok = (golden is None) or (got == golden)
    return {"ok": ok, "sha256": hashlib.sha256(got).hexdigest(),
            "data": got, "write_s": write_s, "run_s": run_s,
            "read_s": read_s,
            "telemetry": read_engine_rows(host)}
