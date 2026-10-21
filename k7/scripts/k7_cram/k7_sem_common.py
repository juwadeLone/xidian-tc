"""Shared helpers for the K7 SEM injection campaign.

The imaging step mirrors gate_fast_reload: params -> ARM_WR B0 ->
flat-out 1400-byte L2 stream -> CS_PARAM RUN -> RD OUT.  SEM commands go
through BoardHost.uart_cmd_sem (CMD_SEM=0x10 passthrough, same as A7).
"""
import hashlib
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from k7_lab_paths import add_layout_ecc  # noqa
add_layout_ecc(ROOT)

from k7_real_data_gate import (BoardHost, Wire, write16, read_range,  # noqa
                               wait_idle, SCRATCH, BASE_B0, BASE_OUT,
                               FRAME_BYTES)
from k7_param_send import build_packets  # noqa
from gui.protocol import CMD_WR, CMD_CS_PARAM, ACK_WR, ACK_CS_PARAM  # noqa
from layout_ecc.a7_far import ECC_WORD, pack_sem_n, unpack_sem_n  # noqa

RAW = ROOT / "sim" / "vectors" / "gf3_2048_raw.bin"
GOLDEN = ROOT / "results" / "k7_port" / "gf3_2048_golden_out.bin"

# GF-3 equivalent params (results/k7_port/gf3_board_run.json)
GF3_PARAMS = {"C": 299792448.0, "B": 102564104.0, "v": 7149.94091796875,
              "Da": 6.650197982788086, "Rs": 844293.0,
              "lam": 0.05551700002287958, "Tp": -2.5630769591240368e-05}

_LA_RE = re.compile(r"LA\s*([0-9A-Fa-f]{8,10})")
_PA_RE = re.compile(r"PA\s*([0-9A-Fa-f]{8})")
_WD_RE = re.compile(r"WD\s*([0-9A-Fa-f]{2,4})")
_BT_RE = re.compile(r"BT\s*([0-9A-Fa-f]{2,4})")


def s(b):
    if b is None:
        return ""
    if isinstance(b, bytes):
        return b.decode("ascii", "replace")
    return str(b)


def parse_la(text):
    hits = _LA_RE.findall(text or "")
    return hits[-1].upper() if hits else None


def parse_sem_report(text):
    t = text or ""
    wd = _WD_RE.search(t)
    bt = _BT_RE.search(t)
    pa = _PA_RE.search(t)
    return {"la": parse_la(t),
            "pa": pa.group(1).upper() if pa else None,
            "wd": wd.group(1).upper() if wd else None,
            "bt": bt.group(1).upper() if bt else None,
            "cor": ("COR" in t.upper()),
            "fc40": ("FC 40" in t.upper())}


def la_to_lfa(la_hex):
    """K7: LA == LFA identity (board-verified over lfa 0..23265, 800+ probes).
    Keep a packed-form fallback for safety only."""
    if not la_hex:
        return None
    raw = int(la_hex, 16)
    if raw > 0x5AE1:            # beyond SEM MF -> packed form
        return (raw >> 14) & 0x3FFFF
    return raw


def send_params(host, pkts):
    """Latch the five parameter words into the engine (RUN packet excluded)."""
    for idx, fl, w0, w1, _note in pkts[:5]:
        host.uart_cmd(CMD_CS_PARAM, w0, w1, expect=ACK_CS_PARAM,
                      timeout=10.0, flags=fl)


WR_CHUNK = 1400        # UDP payload; > ~1472 would IP-fragment (board can't reassemble)
WR_GAP_US = 150        # ~12x the measured ~12us/packet drain threshold


def _telem_state(host):
    return (host.last_telem or {}).get("state")


def _wr_bytes(host):
    return int((host.last_telem or {}).get("wr_bytes") or 0)


def _unarmed(host, settle=1.2):
    """True only if the cmd FSM is NOT in WAIT_WR -- confirmed by a second
    read after a telemetry period, so a stale pre-arm sample can never be
    mistaken for a completed write."""
    if _telem_state(host) == "WAIT_WR":
        return False
    time.sleep(settle)
    return _telem_state(host) != "WAIT_WR"


def _wait_wr_idle(host, timeout=20.0):
    """Poll telemetry until the cmd FSM leaves WAIT_WR (armed count met)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if _unarmed(host):
            return True
        time.sleep(0.1)
    return False


def _send_exact(wire, data_len, chunk=1400, gap_us=300, seq0=0):
    """Send exactly data_len bytes of zeros; never overshoots."""
    sent = 0
    while sent < data_len:
        n = min(chunk, data_len - sent)
        wire.link.send(wire.packet(bytes(n), (seq0 + sent) // chunk))
        sent += n
        if gap_us:
            time.sleep(gap_us / 1e6)
    return sent


def _fill_to_completion(host, wire, armed_len, c0, timeout=240.0):
    """Drive an armed write to completion by sending exactly the measured
    shortfall each round.  wr_bytes lags by in-flight bytes, so after each
    burst we settle and re-measure: lost packets just mean another round,
    and the fifo ends empty because the last counted byte was consumed."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if _unarmed(host):
            return True
        time.sleep(0.6)              # settle: pipeline bytes drain first,
        remaining = armed_len - (_wr_bytes(host) - c0)   # then measure exact
        if remaining <= 0:
            continue
        _send_exact(wire, remaining, chunk=1400, gap_us=150)
        time.sleep(0.5)              # settle, then re-measure exact
    return _unarmed(host)


def _unstick(host, wire, max_bytes=40 * 1024 * 1024):
    """A write is armed (WAIT_WR) but its length/baseline are unknown --
    e.g. a previous run was killed mid-write.  Feed zeros until the FSM
    unlatches.  Overshoot is harmless: queued bytes beyond the armed count
    are eaten by the caller's sacrificial flush; only >256 queued packets
    drop, which is benign."""
    sent = 0
    while not _unarmed(host) and sent < max_bytes:
        _send_exact(wire, 256 * 1024, chunk=1400, gap_us=150)
        sent += 256 * 1024
        time.sleep(0.3)
    return _unarmed(host)


def _flush_pipeline(host, wire):
    """Empty everything queued in the RX payload path.  Bytes left over from
    a failed/aborted write land at the START of the next armed write (the
    armed counter drains the fifo, not the packet count), silently shifting
    the payload.  Arm a 1 MB SCRATCH write, let it swallow the garbage until
    it stalls, then complete it with an exact send -- at completion the fifo
    is empty because the last counted byte was consumed."""
    L = 1024 * 1024
    c1 = _wr_bytes(host)
    try:
        host.uart_cmd(CMD_WR, SCRATCH, L, expect=ACK_WR, timeout=5.0)
    except Exception:
        # still armed from the failed write -> unlatch, then arm again
        _unstick(host, wire)
        c1 = _wr_bytes(host)
        host.uart_cmd(CMD_WR, SCRATCH, L, expect=ACK_WR, timeout=5.0)
    time.sleep(1.5)                  # >= 1 telem period before trusting state
    ok = _fill_to_completion(host, wire, L, c1)
    return ok and _unarmed(host)


def _recover_write_path(host, wire, armed_len, c0):
    """Recover after a failed frame write.  armed_len/c0 are the armed byte
    count and the wr_bytes reading taken just before the arm.

    Two phases, both overshoot-free:
      1. if the write went STUCK (lost packets -> armed count unmet), the FSM
         sits in WAIT_WR refusing new commands.  Complete it with exactly the
         missing byte count; in-flight garbage drains first and counts toward
         the armed length, so it is consumed for free.
      2. whatever residue the failed write left queued (folded/mis-drained
         bytes) sits in the 4 KB payload fifo and would land at the start of
         the next write.  Arm a 1 MB SCRATCH write, let it swallow the
         garbage until it stalls, then complete it with an exact send.
    """
    if _telem_state(host) == "WAIT_WR":
        _fill_to_completion(host, wire, armed_len, c0)
    return _flush_pipeline(host, wire)


def write_frame_verified(host, wire, raw, max_retries=3, progress=None):
    """Write one frame to B0 and verify by readback.

    A lost/corrupted packet can silently shift the payload (folding into the
    previous packet's drain window) -- and a corrupt input produces a
    mislabeled SDC in the campaign.  The readback catches both lost-packet
    hangs and in-packet corruption; each failure is recovered by completing
    the armed write + flushing the payload fifo before retrying.
    Returns write_s of the successful attempt.
    """
    raw_sha = hashlib.sha256(raw).hexdigest()
    last = None
    if _telem_state(host) == "WAIT_WR":
        # a previous run was killed mid-write: unlatch the FSM, then flush
        if not _unstick(host, wire):
            raise RuntimeError("write_frame_verified: board stuck armed")
        _flush_pipeline(host, wire)
    for attempt in range(max_retries):
        pace = WR_GAP_US if attempt == 0 else max(WR_GAP_US, 500)
        c0 = _wr_bytes(host)
        try:
            host.uart_cmd(CMD_WR, BASE_B0, FRAME_BYTES,
                          expect=ACK_WR, timeout=5.0)
        except Exception as e:                       # noqa: BLE001
            # arm refused -> still armed (a recovery left it latched or a
            # stale read passed): unlatch + flush, then retry the write
            last = "arm timeout: %s" % e
            _unstick(host, wire)
            _flush_pipeline(host, wire)
            continue
        t0 = time.monotonic()
        wire.stream(raw, chunk=WR_CHUNK, gap_us=pace, progress=progress)
        write_s = time.monotonic() - t0
        if not _wait_wr_idle(host, 20.0):
            last = "write stuck (armed count unmet)"
            if not _recover_write_path(host, wire, FRAME_BYTES, c0):
                last += " + recovery timeout"
                break            # still armed: further arms cannot ACK
            continue
        got, _ = read_range(host, BASE_B0, FRAME_BYTES)
        if hashlib.sha256(got).hexdigest() == raw_sha:
            return write_s
        last = "B0 readback mismatch"
        if not _recover_write_path(host, wire, FRAME_BYTES, c0):
            last += " + recovery timeout"
            break
    raise RuntimeError("write_frame_verified: %s after %d attempts"
                       % (last, max_retries))


def shot(host, wire, raw, golden, run_timeout=30.0, max_retries=3):
    """One frame: verified B0 fill -> RUN -> readback -> compare.

    Returns dict(ok, sha256, data, write_s, run_s, read_s).  `data` is the
    33.5 MB readback -- keep it only when saving an SDC image.
    """
    write_s = write_frame_verified(host, wire, raw, max_retries=max_retries)

    pkts = build_packets(GF3_PARAMS)
    _i, fl, w0, w1, _n = pkts[5]
    t0 = time.monotonic()
    host.uart_cmd(CMD_CS_PARAM, w0, w1, expect=ACK_CS_PARAM,
                  timeout=run_timeout, flags=fl)
    run_s = time.monotonic() - t0
    wait_idle(host, 30.0)

    got, read_s = read_range(host, BASE_OUT, FRAME_BYTES)
    ok = got == golden
    return {"ok": ok, "sha256": hashlib.sha256(got).hexdigest(),
            "data": got, "write_s": write_s, "run_s": run_s,
            "read_s": read_s}


def classify(ok0, uart_alive, timed_out, detected=False, la=None):
    """MASKED/SDC only after SEM confirm (FC40 or LA). Else INVALID_NO_DETECT."""
    if not uart_alive:
        return "UNRECOVERABLE"
    if timed_out:
        return "DUE"
    if not detected and not la:
        return "INVALID_NO_DETECT"
    if ok0:
        return "MASKED"
    return "SDC"


def _stitch(windows):
    acc = ""
    for w in windows:
        if not w:
            continue
        if not acc:
            acc = w
            continue
        n = min(len(w), len(acc))
        hit = 0
        for k in range(n, 0, -1):
            if acc.endswith(w[:k]):
                hit = k
                break
        acc += w[hit:] if hit else ("\n" + w)
    return acc


def capture_la_after_o(host, orep, n_pages=4):
    """Paged 64-byte unsol dump after 'O'; stitches the SEM repair report."""
    dumps = [s(orep)]
    pages = [s(p) for p in host.dump_monitor_pages(n_pages=n_pages,
                                                  settle_s=0.45)]
    dumps.extend(pages)
    stitched = s(orep) + "\n" + "".join(pages)
    return parse_la(stitched), dumps, stitched


def wait_observation_repair(host, timeout_s=1.5):
    """Let Observation scrub before the health frame (A7 semantics)."""
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout_s:
        time.sleep(0.15)
        last = s(host.uart_cmd_sem("", timeout_ms=200, flags=1))
        if "FC 40" not in last.upper():
            break
    host.uart_cmd_sem("I", timeout_ms=4000)
    time.sleep(0.15)
    return last
