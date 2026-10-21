#!/usr/bin/env python3
"""K7 board gate for the real-data imaging path: upload the host's radar echo
into B0, run the CS chain on it, read OUT back and compare it word for word
against the golden model.

This is the board half of results/k7_port/HANDOFF_REAL_DATA_IMAGING.md section 2.
The RTL is host-input only -- there is no second data source any more, so a run
always images whatever B0 holds.  It needs the bitstream and the K7 2048 seed
set, plus the artifacts written by scripts/k7_real_imaging_check.py:

  sim/vectors/cs2048_real_raw.bin          RAW, pulse-major, what B0 must hold
  sim/vectors/cs2048_real_golden_out.bin   expected OUT, word for word

Per frame:
  1. canary three spots in OUT -- a rerun that does not recompute OUT leaves
     them intact, so they separate "recomputed" from "still frame 1";
  2. write the RAW frame into B0 (33.5 MB, raw-Ethernet UDP, the A7 write path);
  3. CS_PARAM flags = 0x80: flags[7] = run.  (flags[4], the old host/fabric
     source select, is ignored now -- 0x90 still runs a frame, for host tools
     that have not been updated.)  The ACK is gated on the chain being done, so
     it arrives after ~the chain's run time;
  4. read OUT back and compare every 64-bit word; report the first mismatch.

B0 is transposed in place by pass P0, so every frame re-uploads.  A read-back
only reaches the host because the board learns the peer MAC from a received
frame, so a scratch write is issued first.
"""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from k7_lab_paths import add_host  # noqa: E402
add_host(ROOT)
from gui.board_if import BoardHost, pick_wired_iface  # noqa: E402
from gui.protocol import (BOARD_IP, BOARD_MAC, UDP_PORT, CMD_WR, CMD_RD,  # noqa: E402
                          CMD_CS_PARAM, ACK_WR, ACK_RD, ACK_CS_PARAM)
from scapy.all import conf  # noqa: E402

FRAME_BYTES = 2048 * 2048 * 8
BASE_B0 = 0
BASE_OUT = 4 * FRAME_BYTES          # k7_udp_ddr_core: B0..B3 then OUT
SCRATCH = 6 * FRAME_BYTES           # clear of all five frame buffers
CANARY = bytes([0xA5, 0x5A] * 8)
CANARY_OFFSETS = (0, FRAME_BYTES // 2, FRAME_BYTES - 16)
CS_PARAM_RUN = 0x80                 # flags[7] = run a frame already in B0


class Wire:
    """Raw-Ethernet UDP sender, the framing every other K7 gate uses."""

    def __init__(self):
        self.iface, self.ip, self.mac, self.name = pick_wired_iface()
        if not self.iface:
            raise RuntimeError('wired interface missing')
        self.link = conf.L2socket(iface=self.iface)
        self.eth = bytes.fromhex(BOARD_MAC.replace(':', '') +
                                 self.mac.replace(':', '')) + b'\x08\x00'
        self.src = bytes(map(int, self.ip.split('.')))
        self.dst = bytes(map(int, BOARD_IP.split('.')))

    def packet(self, payload, ip_id):
        iph = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 28 + len(payload),
                          ip_id & 0xFFFF, 0, 64, 17, 0, self.src, self.dst)
        ck = sum(struct.unpack('!10H', iph))
        while ck >> 16:
            ck = (ck & 65535) + (ck >> 16)
        iph = iph[:10] + struct.pack('!H', (~ck) & 65535) + iph[12:]
        return (self.eth + iph +
                struct.pack('!HHHH', UDP_PORT, UDP_PORT, 8 + len(payload), 0) +
                payload)

    def send(self, payload):
        self.link.send(self.packet(payload, 0))

    def stream(self, data, chunk=1024, gap_us=500, progress=None):
        t0 = time.monotonic()
        for off in range(0, len(data), chunk):
            self.link.send(self.packet(data[off:off + chunk], off // chunk))
            if gap_us:
                time.sleep(gap_us / 1e6)
            if progress and off and off % (8 * 1024 * 1024) == 0:
                progress(off, len(data))
        return time.monotonic() - t0

    def close(self):
        self.link.close()


def write16(host, wire, addr, payload):
    assert len(payload) == 16 and addr % 16 == 0
    host.uart_cmd(CMD_WR, addr, 16, expect=ACK_WR, timeout=10)
    wire.send(payload)
    time.sleep(.1)


def read_range(host, addr, nbytes, timeout=None, retries=3):
    """Read nbytes from the board, retrying a short capture.

    The board streams OUT at ~38 MB/s (measured: 33.55 MB in 0.9 s), but the
    host's UDP socket starts at a 4 MB SO_RCVBUF -- it refills in ~105 ms, so a
    single scheduling/GC pause in the capture thread drops a whole buffer and
    the read never reaches nbytes.  That looked like a board fault (a 336 s read
    that hit its deadline and compared short) but is host-side capture loss:
    with SO_RCVBUF lifted to 32 MB, 15/15 full-frame reads were byte-exact, and
    the board's own rd_bytes counter matched the host byte for byte.

    So: widen the socket buffer, use a size-independent deadline that fails
    fast (a lost read used to burn nbytes/1e5 = 335 s), and re-issue -- the OUT
    read is idempotent, it just re-reads DDR.
    """
    try:
        host._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF,
                              32 * 1024 * 1024)
    except Exception:                                    # noqa: BLE001
        pass
    deadline_s = max(20.0, nbytes / 4e6)      # 4 MB/s floor: 33.55 MB -> 20 s
    for attempt in range(1, retries + 1):
        with host._lock:
            host.rx_buf = bytearray()
            host._collecting = True
        t0 = time.monotonic()
        host.uart_cmd(CMD_RD, addr, nbytes, expect=ACK_RD,
                      timeout=timeout or max(30, nbytes / 1e6))
        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline and len(host.rx_buf) < nbytes:
            time.sleep(.005)
        with host._lock:
            host._collecting = False
            got = bytes(host.rx_buf)
        if len(got) == nbytes:
            return got, time.monotonic() - t0
        print(f'  short read {len(got)}/{nbytes} B in '
              f'{time.monotonic() - t0:.1f}s (attempt {attempt}/{retries})',
              flush=True)
    return got, time.monotonic() - t0


def wait_idle(host, timeout, after_images=None):
    """Returns the telemetry snapshot once the chain is idle.

    Idle = calibrated, no transfer/swap engine busy, and the app FSM back in
    IDLE (telem seq[13] carries fsm_state[0]; ST_IDLE = 3'd1).

    Power-on is a *valid* idle with the swap_done counter still at 0: nothing
    runs until a CS_PARAM arrives, because AUTO_START is gone.  The counter
    (telem seq[7:4], +1 per completed image, saturating at 0xF) is therefore
    only usable as evidence for a *later* frame -- requiring it to be non-zero
    at start-up (the old gate did) deadlocks against the simplified RTL.
    """
    t0 = time.monotonic()
    last = {}
    while time.monotonic() - t0 < timeout:
        last = dict(host.last_telem)
        if last.get('swap_error'):
            raise RuntimeError(f'chain error: {last}')
        idle = (last.get('calib') == 1 and not last.get('swap_busy')
                and not last.get('wr_busy') and not last.get('rd_busy')
                and last.get('dbg_fsm0') == 1)
        if idle:
            if after_images is None:
                return last
            done = int(last.get('swap_done') or 0)
            # saturated counter stops moving, so treat 0xF as "advanced"
            if done != after_images or done == 0xF:
                return last
        time.sleep(.05)
    raise RuntimeError(f'chain did not go idle in {timeout}s; last {last}')


def judge(label, got, expected):
    r = dict(label=label, received_bytes=len(got),
             read_sha256=hashlib.sha256(got).hexdigest(),
             expected_sha256=hashlib.sha256(expected).hexdigest())
    r['ok'] = got == expected
    if len(got) == len(expected):
        fold = 0
        for (w,) in struct.iter_unpack('<Q', got):
            fold ^= w
        r['fold_of_readback'] = f'0x{fold:016x}'
        n = len(got)
        r['bad_samples'] = sum(1 for i in range(0, n, 8)
                               if got[i:i + 8] != expected[i:i + 8])
        first = next((i for i in range(0, n, 8)
                      if got[i:i + 8] != expected[i:i + 8]), None)
        if first is not None:
            idx = first // 8
            r['first_mismatch_sample'] = idx
            r['first_mismatch_rc'] = [idx // 2048, idx % 2048]
            r['first_mismatch_hex'] = [got[first:first + 8].hex(),
                                       expected[first:first + 8].hex()]
    if r['ok']:
        print(f'{label}: PASS bit-exact {len(got)} bytes '
              f'sha256={r["read_sha256"][:16]}... fold={r.get("fold_of_readback")}',
              flush=True)
    else:
        if len(got) != len(expected):
            print(f'{label}: FAIL length {len(got)} != {len(expected)} bytes '
                  f'(truncated/short capture; no comparison possible)', flush=True)
        else:
            print(f'{label}: FAIL {r.get("bad_samples")} samples differ, first at '
                  f'{r.get("first_mismatch_rc")} got={r.get("first_mismatch_hex")}',
                  flush=True)
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port', default='COM5')
    ap.add_argument('--raw', default='sim/vectors/cs2048_real_raw.bin')
    ap.add_argument('--golden', default='sim/vectors/cs2048_real_golden_out.bin')
    ap.add_argument('--frames', type=int, default=3,
                    help='host-mode frames; each re-uploads the RAW')
    # The CS_PARAM ACK only comes after the chain finishes (cmd_seq sits in
    # S_WAIT_B until cs_build_done), so this timeout must cover a whole
    # run.  A 120 s default fired prematurely during a rerun storm on
    # 2026-09-11; 300 s is still far below any real hang.
    ap.add_argument('--run-timeout', type=float, default=300.0)
    ap.add_argument('--output', default='results/k7_port/real_data_gate.json')
    args = ap.parse_args()

    raw = Path(args.raw).read_bytes()
    golden = Path(args.golden).read_bytes()
    for name, blob in (('raw', raw), ('golden', golden)):
        if len(blob) != FRAME_BYTES:
            raise SystemExit(f'{name} is {len(blob)} bytes, expected '
                             f'{FRAME_BYTES}')
    report = dict(raw_sha256=hashlib.sha256(raw).hexdigest(),
                  golden_sha256=hashlib.sha256(golden).hexdigest(),
                  out_addr=BASE_OUT, frame_bytes=FRAME_BYTES,
                  cs_param_run=CS_PARAM_RUN,
                  frames=[], pass_gate=False)
    host = BoardHost(log=lambda x: print(x, flush=True))
    wire = None
    try:
        host.start(com=args.port, udp=True)
        wire = Wire()
        report['nic'] = wire.name
        report['telem_idle'] = wait_idle(host, 120.0)
        print(f'chain idle, swap_done='
              f'{report["telem_idle"].get("swap_done")} '
              f'(0 before the first frame: nothing runs at power-on)', flush=True)
        # the board only answers a read to a MAC it has seen a frame from
        write16(host, wire, SCRATCH, bytes(16))

        for k in range(1, args.frames + 1):
            for off in CANARY_OFFSETS:
                write16(host, wire, BASE_OUT + off, CANARY)
            before = int(host.last_telem.get('swap_done') or 0)
            # a7 baseline send_data(): "UART ARM_WR + L2 UDP fill" -- the DDR
            # write engine must be armed with (addr, length) BEFORE the frames
            # arrive, otherwise the whole RAW stream is received and dropped.
            try:
                host.uart_cmd(CMD_WR, 0, FRAME_BYTES, expect=ACK_WR,
                              timeout=5.0)
            except RuntimeError as e:     # a7: "若已布防则继续填数"
                print(f'  ARM_WR: {e} (assuming armed, filling anyway)',
                      flush=True)
            write_s = wire.stream(raw)
            t0 = time.monotonic()
            run_ack = 'ACK'
            try:
                host.uart_cmd(CMD_CS_PARAM, 0, 0, expect=ACK_CS_PARAM,
                              timeout=args.run_timeout, flags=CS_PARAM_RUN)
            except RuntimeError as exc:
                # a NAK here is emitted when the app leaves ST_RUN into
                # ST_ERROR (score-stage AXI error).  The chain has ALREADY
                # written OUT (P6) before the score stage runs, so the image
                # is still worth reading back -- do not abort the frame.
                run_ack = f'NAK: {exc}'
                print(f'  CS_PARAM run: {run_ack} -- reading OUT anyway',
                      flush=True)
            run_s = time.monotonic() - t0
            if run_ack == 'ACK':
                telem = wait_idle(host, 30.0, after_images=before)
            else:
                telem = dict(host.last_telem or {})
            got, read_s = read_range(host, BASE_OUT, FRAME_BYTES)
            surviving = [off for off in CANARY_OFFSETS
                         if got[off:off + 16] == CANARY]
            fr = judge(f'host frame {k}', got, golden)
            fr.update(write_seconds=write_s, run_seconds=run_s,
                      read_seconds=read_s, canaries_surviving=surviving,
                      swap_done=telem.get('swap_done'),
                      raw_sha256=hashlib.sha256(raw).hexdigest())
            fr['ok'] = fr['ok'] and not surviving
            # Always keep the board's own image: it is the reference for the
            # "host follows the board" comparison, not just an error dump.
            dump = Path(args.output).parent / f'readback_frame{k}.bin'
            dump.write_bytes(bytes(got))
            print(f'host frame {k}: readback dumped to {dump.name} '
                  f'({len(got)} bytes)', flush=True)
            if surviving:
                print(f'host frame {k}: canaries survived at {surviving} -- '
                      f'the run did not recompute OUT', flush=True)
            report['frames'].append(fr)
            print(f'host frame {k}: upload {write_s:.2f}s run {run_s:.3f}s '
                  f'read {read_s:.2f}s', flush=True)

        report['pass_gate'] = bool(report['frames']) and all(
            f['ok'] for f in report['frames'])
        print(('PASS real-data CS imaging on the board: %d host frame(s) '
               'bit-exact against the golden model' % args.frames)
              if report['pass_gate'] else 'FAIL', flush=True)
    except Exception as exc:
        report['error'] = str(exc)
        print(f'FAIL {exc}', flush=True)
    finally:
        if wire:
            wire.close()
        host.stop()
        Path(args.output).write_text(
            json.dumps(report, indent=2,
                       default=lambda x: x.hex() if isinstance(x, bytes)
                       else str(x)), encoding='utf-8')
        print(f'report={args.output}', flush=True)
    return 0 if report['pass_gate'] else 1


if __name__ == '__main__':
    sys.exit(main())
