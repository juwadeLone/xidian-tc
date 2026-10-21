"""MPH3 / TLM1 protocol helpers for the ddr_prj host GUI."""
from __future__ import print_function

import struct

MAGIC = 0x3348504D
MAGIC_B = struct.pack("<I", MAGIC)
PAY_MAGIC = 0x314D4C54  # 'TLM1'

CMD_WR = 0x01
CMD_XPOSE = 0x02
CMD_RD = 0x03
CMD_FFT2D = 0x04
CMD_CS_COEFF = 0x05
CMD_CS_PARAM = 0x06
CMD_CS_DUMP = 0x09
CMD_SEM = 0x10
CMD_WR_ACK = 0x81
CMD_XPOSE_ACK = 0x82
CMD_RD_ACK = 0x83
CMD_TELEM = 0x84
CMD_FFT2D_ACK = 0x85
CMD_CS_COEFF_ACK = 0x86
CMD_CS_PARAM_ACK = 0x87
CMD_CS_ROW_ACK = 0x88
CMD_CS_RB_ACK = 0x89
CMD_CS_DUMP_ACK = 0x8A
CMD_SEM_ACK = 0x90
CMD_NAK = 0x8F
ACK_WR = CMD_WR_ACK
ACK_XPOSE = CMD_XPOSE_ACK
ACK_RD = CMD_RD_ACK
ACK_FFT2D = CMD_FFT2D_ACK
ACK_CS_COEFF = CMD_CS_COEFF_ACK
ACK_CS_PARAM = CMD_CS_PARAM_ACK
ACK_CS_DUMP = CMD_CS_DUMP_ACK
ACK_SEM = CMD_SEM_ACK
NAK = CMD_NAK

CS_SEL_HCS = 0
CS_SEL_HR = 1
CS_SEL_HA = 2
CS_SEL_CLR = 3

CMD_NAMES = {
    CMD_WR: "ARM_WR",
    CMD_XPOSE: "TRANSPOSE",
    CMD_RD: "RD_BACK",
    CMD_FFT2D: "FFT2D",
    CMD_CS_COEFF: "CS_COEFF",
    CMD_CS_PARAM: "CS_PARAM",
    CMD_CS_DUMP: "CS_DUMP",
    CMD_SEM: "SEM",
    CMD_WR_ACK: "WR_ACK",
    CMD_XPOSE_ACK: "XPOSE_ACK",
    CMD_RD_ACK: "RD_ACK",
    CMD_TELEM: "TELEM",
    CMD_FFT2D_ACK: "FFT2D_ACK",
    CMD_CS_COEFF_ACK: "CS_COEFF_ACK",
    CMD_CS_PARAM_ACK: "CS_PARAM_ACK",
    CMD_CS_ROW_ACK: "CS_ROW_ACK",
    CMD_CS_RB_ACK: "CS_RB_ACK",
    CMD_CS_DUMP_ACK: "CS_DUMP_ACK",
    CMD_SEM_ACK: "SEM_ACK",
    CMD_NAK: "NAK",
}

STATES = {0: "IDLE", 1: "ACK", 2: "WAIT_WR", 3: "WAIT_X", 4: "WAIT_RD",
          5: "WAIT_F", 6: "WAIT_B"}
MUX_NAMES = {0: "WR", 1: "XPOSE/FFT", 2: "RD"}
FFT_MODE_CS = 0          # per-fa CS imaging (MATLAB order)
FFT_MODE_FFT = 1
FFT_MODE_IFFT = 2
FFT_MODE_CS_QUAD = 3     # legacy fixed-9-coeff four-pass

CS_TAB_H1 = 0
CS_TAB_H2 = 1
CS_TAB_HAZ = 2
CS_FLAG_TABLE = 0x04     # CMD_CS_COEFF flags[2]: write one fa of a table
CS_PARAM_COMMIT = 0x80   # CMD_CS_PARAM flags: build H1/H2/Haz tables on board
CS_PARAM_N = 7           # C, V, lambda, Rs, gama, Fs, PRF
SEM_PAY_MAX = 16         # CMD_SEM payload bytes (uart_cmd_if PAY_MAX)
SEM_FLAG_DUMP = 0x01     # CMD_SEM flags[0]: read the unsol buffer, send nothing
SEM_DUMP_PAGE_SHIFT = 1  # CMD_SEM flags[3:1]: which 16-byte page
SEM_FLAG_FECC = 0x10     # CMD_SEM flags[4]: read the FRAME_ECCE2 latch (6 bytes)

BOARD_IP = "192.168.0.4"
BOARD_MAC = "00:11:22:33:44:55"
UDP_PORT = 8080

MATRIX = 256
SAMPLE_B = 8
MATRIX_B = MATRIX * MATRIX * SAMPLE_B
UDP_CHUNK = 512


def pack_hdr(cmd, seq, addr, length, flags=0):
    return struct.pack("<IBBHII", MAGIC, cmd, flags, seq, addr, length)


def unpack_hdr(buf):
    return struct.unpack("<IBBHII", buf[:16])


def is_mph3(buf):
    return len(buf) >= 16 and struct.unpack("<I", buf[:4])[0] == MAGIC


def pattern_word(row, col):
    i_word = 0x10000000 | ((row & 0xFFF) << 12) | (col & 0xFFF)
    q_word = 0xE0000000 | ((col & 0xFFF) << 12) | (row & 0xFFF)
    return struct.pack("<II", i_word, q_word)


def build_matrix():
    return b"".join(pattern_word(r, c) for r in range(MATRIX) for c in range(MATRIX))


def expected_transposed():
    return b"".join(pattern_word(c, r) for r in range(MATRIX) for c in range(MATRIX))


def decode_uart_telem(flags, seq, addr, length):
    mux = flags & 0x3
    return {
        "source": "uart",
        "heart": (seq >> 8) & 0xFF,
        # K7 debug (telem_uart puts {host_raw_latch, cs_app_fsm[1:0]} in
        # seq[15:13]; heart lost its top 3 bits, which the GUI never used)
        "dbg_flags4": (seq >> 15) & 1,      # flags[4] seen at cmd_seq
        "dbg_latch": (seq >> 14) & 1,       # cs_host_raw latch output
        "dbg_fsm0": (seq >> 13) & 1,        # cs_app fsm_state[0]
        "calib": (seq >> 3) & 1,
        "state": STATES.get(seq & 0x7, "S%d" % (seq & 0x7)),
        "mux": mux,
        "mux_name": MUX_NAMES.get(mux, "?"),
        "wr_busy": (flags >> 2) & 1,
        "swap_busy": (flags >> 3) & 1,
        "rd_busy": (flags >> 4) & 1,
        "wr_err_sticky": (flags >> 5) & 1,
        "swap_error": (flags >> 6) & 1,
        "rd_error": (flags >> 7) & 1,
        "wr_bytes": addr,
        "rd_bytes": length,
        "swap_done": (seq >> 4) & 0xF,
    }


def decode_udp_telem(hdr, payload):
    words = struct.unpack("<16I", payload[:64])
    flags = words[1]
    mux = (flags >> 4) & 3
    d = {
        "source": "udp",
        "sig": words[0],
        "calib": flags & 1,
        "state": STATES.get((flags >> 1) & 7, "S%d" % ((flags >> 1) & 7)),
        "mux": mux,
        "mux_name": MUX_NAMES.get(mux, "?"),
        "wr_busy": (flags >> 6) & 1,
        "swap_busy": (flags >> 7) & 1,
        "rd_busy": (flags >> 8) & 1,
        "wr_err_sticky": (flags >> 9) & 1,
        "swap_error": (flags >> 10) & 1,
        "rd_error": (flags >> 11) & 1,
        "uart_ovf": (flags >> 12) & 1,
        "tx_busy": (flags >> 13) & 1,
        "cmd_active": (flags >> 14) & 1,
        "learned": (flags >> 15) & 1,
        "wr_bytes": words[2],
        "rd_bytes": words[3],
        "swap_done": words[4] & 0xF,
        "rx_frames": words[5],
        "rx_err_frames": words[6],
        "tx_frames": words[7],
        "heart": words[8] & 0xFF,
        "o_status": (words[8] >> 8) & 0xF,
        "sem_stat16": (words[8] >> 16) & 0xFFFF,
        "uart_ovf_cnt": words[9],
        "w10": words[10],
        "w11": words[11],
        "w12": words[12],
        "wr_awaddr": words[13] & 0x1FFFFFFF,
        "mux_awaddr": words[14] & 0x1FFFFFFF,
        "rd_arlen": words[15] & 0xFF,
        "wr_awlen": (words[15] >> 8) & 0xFF,
        "mux_awlen": (words[15] >> 16) & 0xFF,
    }
    w10, w11, w12 = words[10], words[11], words[12]
    d.update({
        "wr_tvalid": w10 & 1, "wr_tready": (w10 >> 1) & 1,
        "wr_awvalid": (w10 >> 2) & 1, "wr_awready": (w10 >> 3) & 1,
        "wr_wvalid": (w10 >> 4) & 1, "wr_wready": (w10 >> 5) & 1,
        "wr_wlast": (w10 >> 6) & 1,
        "wr_bvalid": (w10 >> 7) & 1, "wr_bready": (w10 >> 8) & 1,
        "m_awvalid": (w10 >> 9) & 1, "m_awready": (w10 >> 10) & 1,
        "m_wvalid": (w10 >> 11) & 1, "m_wready": (w10 >> 12) & 1,
        "m_wlast": (w10 >> 13) & 1,
        "m_bvalid": (w10 >> 14) & 1, "m_bready": (w10 >> 15) & 1,
        "wr_dbg": (w10 >> 23) & 7,
        "rd_arvalid": w11 & 1, "rd_arready": (w11 >> 1) & 1,
        "rd_rvalid": (w11 >> 2) & 1, "rd_rready": (w11 >> 3) & 1,
        "m_arvalid": (w11 >> 7) & 1, "m_arready": (w11 >> 8) & 1,
        "m_rvalid": (w11 >> 9) & 1, "m_rready": (w11 >> 10) & 1,
        "uart_rx_hold": (w11 >> 12) & 1,
        "rd_dbg": (w11 >> 13) & 3,
        "sw_awvalid": w12 & 1, "sw_awready": (w12 >> 1) & 1,
        "sw_wvalid": (w12 >> 2) & 1, "sw_wready": (w12 >> 3) & 1,
        "dw_awvalid": (w12 >> 10) & 1, "dw_wvalid": (w12 >> 12) & 1,
    })
    d.update(decode_sem_stat16(d["sem_stat16"]))
    d.update({
        # FRAME_ECCE2 tap, kept for bitstreams that still ship the Ethernet
        # telemetry engine. The current build strips it to fit the SEM bridge
        # and serves the same record over CMD_SEM flags[4] instead
        # (decode_fecc_record), so on that build these fields never arrive.
        # Sticky either way: fecc_evt_cnt increments on each SYNDROMEVALID,
        # so a repeat read with the same count is stale.
        "fecc_far": (words[4] >> 6) & 0x3FFFFFF,
        "fecc_evt_cnt": (w12 >> 16) & 0xF,
        "fecc_synbit": (w12 >> 20) & 0x1F,
        "fecc_synword": (w12 >> 25) & 0x7F,
        "fecc_eccerrsingle": (w11 >> 16) & 1,
        "fecc_eccerr": (w11 >> 17) & 1,
        "fecc_crcerr": (w11 >> 18) & 1,
    })
    return d


def decode_fecc_record(payload):
    """6 little-endian bytes from CMD_SEM flags[4] -> FRAME_ECCE2 fields.

    Independent of the SEM monitor's own LA report: this is the physical frame
    address the readback was on when the ECC error was found. fecc_evt_cnt
    increments on every SYNDROMEVALID, so an unchanged count means the latch is
    stale and the other fields describe an older event.
    """
    if not payload or len(payload) < 6:
        return {}
    v = int.from_bytes(bytes(payload[:6]), "little")
    return {
        "fecc_far": v & 0x3FFFFFF,
        "fecc_synword": (v >> 29) & 0x7F,
        "fecc_synbit": (v >> 36) & 0x1F,
        "fecc_eccerrsingle": (v >> 41) & 1,
        "fecc_eccerr": (v >> 42) & 1,
        "fecc_crcerr": (v >> 43) & 1,
        "fecc_evt_cnt": (v >> 44) & 0xF,
    }


def decode_sem_stat16(st):
    """SEM status bits, as packed in telemetry W8[31:16] and CMD_SEM ACK addr[31:16]."""
    return {
        "sem_init": st & 1,
        "sem_observation": (st >> 1) & 1,
        "sem_correction": (st >> 2) & 1,
        "sem_classification": (st >> 3) & 1,
        "sem_injection": (st >> 4) & 1,
        "sem_essential": (st >> 5) & 1,
        "sem_uncorrectable": (st >> 6) & 1,
        "sem_idle": (st >> 7) & 1,
        "sem_heartbeat": (st >> 8) & 1,
        "sem_icap_request": (st >> 9) & 1,
        "sem_icap_grant": (st >> 10) & 1,
    }


def parse_udp_datagram(data):
    """Return ('telem', dict) | ('ack', dict) | ('data', bytes) | None."""
    if not data:
        return None
    if is_mph3(data):
        magic, cmd, flags, seq, addr, length = unpack_hdr(data)
        if cmd == CMD_TELEM and len(data) >= 80:
            return ("telem", decode_udp_telem(
                (magic, cmd, flags, seq, addr, length), data[16:]))
        if cmd in (CMD_WR_ACK, CMD_XPOSE_ACK, CMD_RD_ACK, CMD_FFT2D_ACK,
                    CMD_CS_COEFF_ACK, CMD_CS_PARAM_ACK, CMD_SEM_ACK, CMD_NAK):
            return ("ack", {
                "cmd": cmd, "name": CMD_NAMES.get(cmd, "0x%02X" % cmd),
                "seq": seq, "addr": addr, "length": length,
            })
        return None
    return ("data", data)


def judge_telem(d):
    """UI-facing health check. Skips idle-stuck noise (no new eth frames / same heart)."""
    problems = []
    if d.get("source") == "udp" and d.get("sig") not in (None, PAY_MAGIC):
        problems.append("payload signature 0x%08x != TLM1" % d["sig"])
    if "calib" in d and not d["calib"]:
        problems.append("MIG 未校准")
    if d.get("source") == "udp" and "learned" in d and not d["learned"]:
        problems.append("尚未学到上位机 MAC/IP")
    if d.get("wr_err_sticky"):
        problems.append("写通道错误 wr_err_sticky")
    if d.get("swap_error"):
        problems.append("转置引擎错误")
    if d.get("rd_error"):
        problems.append("读通道错误")
    if d.get("uart_ovf"):
        problems.append("UART 溢出 cnt=%s" % d.get("uart_ovf_cnt", "?"))
    if d.get("o_status") is not None and d["o_status"] != 0xF:
        problems.append("MIG o_status=0x%X != 0xF" % d["o_status"])
    if d.get("rx_err_frames"):
        problems.append("以太网错帧 %d" % d["rx_err_frames"])
    if d.get("wr_busy") and d.get("wr_wvalid") and not d.get("wr_wready"):
        problems.append("写停住 wvalid=1 wready=0 wr=%s" % d.get("wr_bytes"))
    if d.get("wr_busy") and d.get("m_wvalid") and not d.get("m_wready"):
        problems.append("mux/MIG 写停住")
    if d.get("rd_busy") and d.get("rd_arvalid") and not d.get("rd_arready"):
        problems.append("读停住 arvalid=1 arready=0")
    if d.get("swap_busy") and d.get("sw_wvalid") and not d.get("sw_wready"):
        problems.append("转置停住")
    return (len(problems) == 0), problems


def compare_transposed(back, golden=None):
    if golden is None:
        golden = expected_transposed()
    if len(back) != len(golden):
        return False, "长度 %d / %d" % (len(back), len(golden))
    if back == golden:
        return True, "256x256 转置比对通过"
    mism = 0
    first = None
    for i in range(0, len(back), SAMPLE_B):
        if back[i:i + SAMPLE_B] != golden[i:i + SAMPLE_B]:
            mism += 1
            if first is None:
                first = i // SAMPLE_B
    r, c = divmod(first, MATRIX) if first is not None else (0, 0)
    return False, "错 %d / %d 样点，首错 (%d,%d)" % (mism, MATRIX * MATRIX, r, c)


def pack_sample_i16(re, im):
    """DDR sample: sign-extended I16/Q16 in I32/Q32 little-endian."""
    re = int(re)
    im = int(im)
    if re < -32768:
        re = -32768
    if re > 32767:
        re = 32767
    if im < -32768:
        im = -32768
    if im > 32767:
        im = 32767
    return struct.pack("<ii", re, im)


def unpack_sample_i16(buf):
    re32, im32 = struct.unpack_from("<ii", buf)
    re = struct.unpack("<h", struct.pack("<H", re32 & 0xFFFF))[0]
    im = struct.unpack("<h", struct.pack("<H", im32 & 0xFFFF))[0]
    return re, im


def build_fft_matrix():
    """Sparse 16-bit impulses — same set as tb_fft2d_cs."""
    chunks = []
    for r in range(MATRIX):
        for c in range(MATRIX):
            re, im = 0, 0
            if r == 3 and c == 5:
                re = 7
            elif r == 10 and c == 20:
                re, im = 4, 2
            elif r == 200 and c == 100:
                re = -3
            chunks.append(pack_sample_i16(re, im))
    return b"".join(chunks)


def _twiddles_q15():
    import math
    w = []
    for k in range(MATRIX):
        ang = -2.0 * math.pi * k / float(MATRIX)
        re = int(round(math.cos(ang) * 32767))
        im = int(round(math.sin(ang) * 32767))
        w.append((re, im))
    return w


def _sat16(x):
    if x > 32767:
        return 32767
    if x < -32768:
        return -32768
    return int(x)


def _sx_width(x, bits):
    """Two's-complement wrap to `bits` (matches Verilog signed reg overflow)."""
    mask = (1 << bits) - 1
    x = int(x) & mask
    if x >= (1 << (bits - 1)):
        x -= (1 << bits)
    return x


def _sx16(x):
    return _sx_width(x, 16)


def _sx20(x):
    return _sx_width(x, 20)


def _sx24(x):
    return _sx_width(x, 24)


def fft256_int(re_in, im_in, inv=False):
    """Match fft256_core: DIT, Q15 twiddle, 24-bit t_re, sat16 add, inv >>>8."""
    n = MATRIX
    re = [0] * n
    im = [0] * n
    for i in range(n):
        rev = int("{:08b}".format(i)[::-1], 2)
        re[rev] = int(re_in[i])
        im[rev] = int(im_in[i])
    wtab = _twiddles_q15()
    for stage in range(8):
        le = 2 << stage
        le2 = 1 << stage
        for j in range(le2):
            wr, wi = wtab[j << (7 - stage)]
            if inv:
                wi = -wi
            i = j
            while i < n:
                ia = i
                ib = i + le2
                br, bi = re[ib], im[ib]
                p0 = br * wr
                p1 = bi * wi
                p2 = br * wi
                p3 = bi * wr
                # fft256_core: t_re/t_im are signed [23:0]
                tr = _sx24((p0 - p1 + 16384) >> 15)
                ti = _sx24((p2 + p3 + 16384) >> 15)
                ar, ai = re[ia], im[ia]
                re[ia] = _sat16(ar + tr)
                im[ia] = _sat16(ai + ti)
                re[ib] = _sat16(ar - tr)
                im[ib] = _sat16(ai - ti)
                i += le
    if inv:
        re = [int(x) >> 8 for x in re]
        im = [int(x) >> 8 for x in im]
    return re, im


_CS_ATAN = (8192, 4836, 2555, 1297, 651, 326, 163, 81,
            41, 20, 10, 5, 3, 1, 1, 0)
_CS_K = 19898


def cs_phase_apply(x_re, x_im, n, a, b, c, en=1, signed_n=False):
    """Bit-accurate y = x * exp(j*(a*n^2+b*n+c)), matching cs_phase_gen.v.

    CORDIC state (cx,cy) is kept as signed 20-bit like the RTL regs; w_re/w_im
    use the same sat20 clamp before the final Q15 multiply.
    """
    if not en:
        return int(x_re), int(x_im)
    if not (a or b or c):
        # RTL en=0 bypass when all coeffs are 0
        return int(x_re), int(x_im)
    if signed_n:
        nn = n - 256 if n >= 128 else n
        n2 = (nn * nn) & 0xFFFF
        pa = _sx16((a * n2) & 0xFFFF)
        pb = _sx16((b * (nn & 0xFFFF)) & 0xFFFF)
    else:
        pa = _sx16((a * (n * n)) & 0xFFFF)
        pb = _sx16((b * n) & 0xFFFF)
    phi = _sx16(pa + pb + c)
    if phi >= 16384:
        cx, cy, cz = 0, _CS_K, _sx16(phi - 16384)
    elif phi <= -16384:
        cx, cy, cz = 0, -_CS_K, _sx16(phi + 16384)
    else:
        cx, cy, cz = _CS_K, 0, phi
    for i in range(16):
        x_sh = cx >> i
        y_sh = cy >> i
        if cz >= 0:
            cx = _sx20(cx - y_sh)
            cy = _sx20(cy + x_sh)
            cz = _sx16(cz - _CS_ATAN[i])
        else:
            cx = _sx20(cx + y_sh)
            cy = _sx20(cy - x_sh)
            cz = _sx16(cz + _CS_ATAN[i])
    # sat20 in cs_phase_gen.v
    w_re = _sat16(cx)
    w_im = _sat16(cy)
    y_re = _sat16((int(x_re) * w_re - int(x_im) * w_im + 16384) >> 15)
    y_im = _sat16((int(x_re) * w_im + int(x_im) * w_re + 16384) >> 15)
    return y_re, y_im


def _abc(triple):
    if not triple:
        return (0, 0, 0)
    return (int(triple[0]), int(triple[1]), int(triple[2]))


def _comp_along_row(re, im, abc):
    a, b, c = abc
    if not (a or b or c):
        return
    for r in range(MATRIX):
        for n in range(MATRIX):
            re[r][n], im[r][n] = cs_phase_apply(re[r][n], im[r][n], n, a, b, c)


def _comp_along_col(re, im, abc):
    a, b, c = abc
    if not (a or b or c):
        return
    for col in range(MATRIX):
        for n in range(MATRIX):
            re[n][col], im[n][col] = cs_phase_apply(
                re[n][col], im[n][col], n, a, b, c)


def pack_cs_coeff(sel, a, b, c, seq=1):
    addr = (a & 0xFFFF) | ((b & 0xFFFF) << 16)
    return pack_hdr(CMD_CS_COEFF, seq, addr, c & 0xFFFF, flags=sel & 3)


def pack_cs_table(sel, fa, a, b, c, seq=1):
    """One per-fa (a,b,c) write. flags[2]=1, flags[1:0]=table (H1/H2/Haz)."""
    addr = (fa & 0xFF) | ((a & 0xFFFF) << 16)
    length = (b & 0xFFFF) | ((c & 0xFFFF) << 16)
    return pack_hdr(CMD_CS_COEFF, seq, addr, length,
                    flags=(sel & 3) | CS_FLAG_TABLE)


def _f32_u32(x):
    import struct as _st
    return _st.unpack("<I", _st.pack("<f", float(x)))[0]


def pack_cs_param(idx, f0, f1=0.0, seq=1):
    """Write two float32 scalars starting at idx (addr=f0 bits, length=f1 bits)."""
    return pack_hdr(CMD_CS_PARAM, seq, _f32_u32(f0), _f32_u32(f1),
                    flags=idx & 0x0F)


def pack_cs_param_commit(seq=1):
    """Trigger on-board H1/H2/Haz table build from latched scalars."""
    return pack_hdr(CMD_CS_PARAM, seq, 0, 0, flags=CS_PARAM_COMMIT)


def fft2d_cs_int(re_mat, im_mat, mode=FFT_MODE_CS, hcs=None, hr=None, ha=None):
    """CS order on a 256x256 list-of-lists. mode 0/1/2 as on the board.

    hcs/hr/ha are (a,b,c) triples in the same 16-bit angle units as the FPGA
    (2^16 = 2*pi). Applied only in CS mode, after the matching F pass.
    """
    re = [row[:] for row in re_mat]
    im = [row[:] for row in im_mat]
    hcs, hr, ha = _abc(hcs), _abc(hr), _abc(ha)

    def along_col(inv):
        for c in range(MATRIX):
            vr = [re[r][c] for r in range(MATRIX)]
            vi = [im[r][c] for r in range(MATRIX)]
            vr, vi = fft256_int(vr, vi, inv)
            for r in range(MATRIX):
                re[r][c] = vr[r]
                im[r][c] = vi[r]

    def along_row(inv):
        for r in range(MATRIX):
            vr, vi = fft256_int(re[r], im[r], inv)
            re[r] = vr
            im[r] = vi

    if mode == FFT_MODE_FFT:
        along_col(False)
        along_row(False)
    elif mode == FFT_MODE_IFFT:
        along_row(True)
        along_col(True)
    else:
        # mode 0/3 host four-pass (impulse). FPGA mode 0 is imaging.
        along_col(False)
        _comp_along_col(re, im, hcs)
        along_row(False)
        _comp_along_row(re, im, hr)
        along_row(True)
        _comp_along_row(re, im, ha)
        along_col(True)
    return re, im


def unpack_matrix_i16(buf):
    re = [[0] * MATRIX for _ in range(MATRIX)]
    im = [[0] * MATRIX for _ in range(MATRIX)]
    for r in range(MATRIX):
        for c in range(MATRIX):
            off = (r * MATRIX + c) * SAMPLE_B
            re[r][c], im[r][c] = unpack_sample_i16(buf[off:off + SAMPLE_B])
    return re, im


def pack_matrix_i16(re_mat, im_mat):
    chunks = []
    for r in range(MATRIX):
        for c in range(MATRIX):
            chunks.append(pack_sample_i16(re_mat[r][c], im_mat[r][c]))
    return b"".join(chunks)


def compare_c16(back, golden, tol=2):
    if len(back) != len(golden):
        return False, "长度 %d / %d" % (len(back), len(golden))
    miss = 0
    first = None
    peak = 0
    for i in range(0, len(back), SAMPLE_B):
        br, bi = unpack_sample_i16(back[i:i + SAMPLE_B])
        gr, gi = unpack_sample_i16(golden[i:i + SAMPLE_B])
        if abs(br) > peak:
            peak = abs(br)
        if abs(bi) > peak:
            peak = abs(bi)
        if abs(br - gr) > tol or abs(bi - gi) > tol:
            miss += 1
            if first is None:
                first = i // SAMPLE_B
    if miss == 0:
        return True, "256x256 复数比对通过  tol=%d peak=%d" % (tol, peak)
    r, c = divmod(first, MATRIX)
    return False, "错 %d / %d 样点，首错 (%d,%d)  tol=%d" % (
        miss, MATRIX * MATRIX, r, c, tol)
