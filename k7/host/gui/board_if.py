"""Board-facing host ports: telemetry, send-data, post-transpose receive.

Reserved interfaces the GUI (or another script) should call:

    host.send_data(payload, addr=0)             # write DDR
    host.do_transpose(addr=0)                   # in-place transpose
    host.recv_after_transpose(length=MATRIX_B)  # read back
"""
from __future__ import print_function

import socket
import threading
import time

from .protocol import (
    ACK_CS_COEFF, ACK_CS_PARAM, ACK_FFT2D, ACK_RD, ACK_SEM, ACK_WR, ACK_XPOSE,
    BOARD_IP, BOARD_MAC,
    CMD_CS_COEFF, CMD_CS_PARAM, CMD_CS_RB_ACK, CMD_FFT2D, CMD_NAMES, CMD_NAK,
    CMD_RD, CMD_SEM, CMD_TELEM, CMD_WR, CMD_XPOSE, CS_FLAG_TABLE,
    CS_PARAM_COMMIT, CS_PARAM_N,
    CS_SEL_CLR, CS_SEL_HA, CS_SEL_HCS, CS_SEL_HR,
    CS_TAB_H1, CS_TAB_H2, CS_TAB_HAZ, MATRIX_B,
    NAK,
    SEM_DUMP_PAGE_SHIFT, SEM_FLAG_DUMP, SEM_FLAG_FECC, SEM_PAY_MAX,
    UDP_CHUNK, UDP_PORT, decode_fecc_record, decode_sem_stat16,
    decode_uart_telem, pack_cs_param,
    pack_hdr, parse_udp_datagram, unpack_hdr,
)

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

try:
    from scapy.all import Ether, IP, UDP, conf, sendp
    _HAS_SCAPY = True
except Exception:
    _HAS_SCAPY = False


def list_com_ports():
    if list_ports is None:
        return []
    return [(p.device, p.description or p.device) for p in list_ports.comports()]


def find_ch340():
    for dev, desc in list_com_ports():
        if "CH340" in desc:
            return dev
    return None


def pick_wired_iface():
    if not _HAS_SCAPY:
        return None, None, None, "no scapy"
    ifaces = list(conf.ifaces.values()) if hasattr(conf.ifaces, "values") else list(conf.ifaces)
    cands = []
    for i in ifaces:
        ip = getattr(i, "ip", None)
        mac = getattr(i, "mac", None)
        nm = str(getattr(i, "name", ""))
        if not ip or not mac or ip.startswith("127.") or "WLAN" in nm or "Wi-Fi" in nm:
            continue
        virt = mac.lower().startswith("00:ff:")
        score = (1 if virt else 0, 0 if nm in ("以太网", "Ethernet") else 1)
        cands.append((score, i.network_name, ip, mac, nm))
    if not cands:
        return None, None, None, "no wired NIC"
    cands.sort()
    _, iface, ip, mac, nm = cands[0]
    return iface, ip, mac, nm


class BoardHost(object):
    """One UART + one UDP:8080 socket shared by telem / send / recv."""

    def __init__(self, log=None):
        self._log = log or (lambda m: None)
        self._ser = None
        self._sock = None
        self._stop = threading.Event()
        self._threads = []
        self._lock = threading.Lock()
        self._seq = 1
        self.last_telem = {}
        self.last_ack = None
        self.last_sem_status = {}
        self.last_faults = []
        self.rx_buf = bytearray()
        self._collecting = False
        self._on_telem = None
        self._on_ack = None
        self._on_rx = None
        self.iface = self.src_ip = self.src_mac = None
        self.iface_name = ""

    def log(self, msg):
        self._log(msg)

    def set_callbacks(self, on_telem=None, on_ack=None, on_rx=None):
        self._on_telem = on_telem
        self._on_ack = on_ack
        self._on_rx = on_rx

    # ---- connect ----
    def open_uart(self, com, baud=115200):
        if serial is None:
            raise RuntimeError("需要 pyserial")
        if self._ser:
            self.close_uart()
        self._ser = serial.Serial(com, baud, timeout=0.2)
        self._ser.reset_input_buffer()
        self.log("UART 打开 %s @%d" % (com, baud))

    def close_uart(self):
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
            self.log("UART 已关闭")

    def open_udp(self, bind_ip="0.0.0.0", port=UDP_PORT):
        if self._sock:
            self.close_udp()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        sock.bind((bind_ip, port))
        sock.settimeout(0.2)
        self._sock = sock
        self.log("UDP 监听 %s:%d" % (bind_ip, port))

    def close_udp(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
            self.log("UDP 已关闭")

    def start(self, com=None, baud=115200, udp=True):
        self._stop.clear()
        if com:
            self.open_uart(com, baud)
            t = threading.Thread(target=self._uart_loop, name="uart-rx")
            t.daemon = True
            t.start()
            self._threads.append(t)
        if udp:
            self.open_udp()
            t = threading.Thread(target=self._udp_loop, name="udp-rx")
            t.daemon = True
            t.start()
            self._threads.append(t)
        if _HAS_SCAPY:
            self.iface, self.src_ip, self.src_mac, self.iface_name = pick_wired_iface()
            if self.iface:
                self.log("网口 %s ip=%s mac=%s" % (self.iface_name, self.src_ip, self.src_mac))
            else:
                self.log("未找到有线网口: %s" % self.iface_name)

    def stop(self):
        self._stop.set()
        self.close_uart()
        self.close_udp()
        self._threads = []

    # ---- UART command ----
    def _uart_loop(self):
        while not self._stop.is_set() and self._ser:
            try:
                buf = self._ser.read(16)
            except Exception:
                break
            if len(buf) != 16:
                continue
            try:
                magic, cmd, flags, seq, addr, length = unpack_hdr(buf)
            except Exception:
                continue
            if magic != 0x3348504D:
                continue
            payload = b""
            if cmd in (ACK_SEM, CMD_CS_RB_ACK) and 0 < length <= SEM_PAY_MAX:
                # SEM ACK is header + length monitor bytes; CS_RB ACK is
                # header + length row bytes.  Read the tail here so the
                # next 16-byte read still lands on a frame boundary.
                extra = self._ser.read(length)
                if len(extra) != length:
                    self.log("SEM ACK 载荷短读 seq=%d 期望%d 实到%d" % (
                        seq, length, len(extra)))
                    continue
                payload = extra
            try:
                self._handle_uart_frame(cmd, flags, seq, addr, length, payload)
            except Exception as e:
                self.log("UART 帧处理异常: %s" % e)

    def _handle_uart_frame(self, cmd, flags, seq, addr, length, payload=b""):
        if cmd == CMD_TELEM:
            d = decode_uart_telem(flags, seq, addr, length)
            with self._lock:
                self.last_telem.update(d)
            if self._on_telem:
                self._on_telem(d)
        elif cmd in (ACK_WR, ACK_XPOSE, ACK_RD, ACK_FFT2D, ACK_CS_COEFF,
                      ACK_CS_PARAM, ACK_SEM, CMD_CS_RB_ACK, NAK, CMD_NAK):
            ack = {"cmd": cmd, "name": CMD_NAMES.get(cmd, "?"),
                   "seq": seq, "addr": addr, "length": length,
                   "payload": payload}
            with self._lock:
                self.last_ack = ack
            if self._on_ack:
                self._on_ack(ack)
            self.log("UART %s seq=%d addr=%d len=%d" % (
                ack["name"], seq, addr, length))

    def uart_cmd(self, cmd, addr=0, length=0, expect=None, timeout=8.0, flags=0):
        if not self._ser:
            raise RuntimeError("UART 未连接")
        with self._lock:
            seq = self._seq
            self._seq = (self._seq + 1) & 0xFFFF
            if self._seq == 0:
                self._seq = 1
        self._ser.write(pack_hdr(cmd, seq, addr, length, flags=flags))
        self._ser.flush()
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self._lock:
                ack = self.last_ack
            if ack and ack.get("seq") == seq:
                if ack["cmd"] == NAK or ack["cmd"] == CMD_NAK:
                    raise RuntimeError("NAK seq=%d" % seq)
                if expect is not None and ack["cmd"] != expect:
                    raise RuntimeError("ACK 不符 got %s" % ack["name"])
                return ack
            time.sleep(0.02)
        raise RuntimeError("UART ACK 超时 cmd=0x%02x" % cmd)

    # ---- SEM monitor (PG036 ASCII over CMD_SEM) ----
    def uart_cmd_sem(self, ascii_cmd, timeout_ms=2000, flags=0, timeout=None):
        """Send one PG036 monitor command; return the bridge's reply bytes.

        The board answers within timeout_ms or returns whatever it collected.
        Replies longer than SEM_PAY_MAX are truncated by the bridge; use
        dump_monitor_pages() to read the 64-byte unsolicited buffer.
        """
        if not self._ser:
            raise RuntimeError("UART 未连接")
        if isinstance(ascii_cmd, str):
            ascii_cmd = ascii_cmd.encode("ascii")
        if ascii_cmd and not ascii_cmd.endswith(b"\r"):
            ascii_cmd += b"\r"
        if len(ascii_cmd) > SEM_PAY_MAX:
            raise ValueError("SEM payload > %d" % SEM_PAY_MAX)
        wait = timeout if timeout is not None else (timeout_ms / 1000.0 + 2.0)
        with self._lock:
            seq = self._seq
            self._seq = (self._seq + 1) & 0xFFFF
            if self._seq == 0:
                self._seq = 1
            self.last_ack = None
        self._ser.write(pack_hdr(CMD_SEM, seq, timeout_ms, len(ascii_cmd),
                                 flags=flags) + ascii_cmd)
        self._ser.flush()
        t0 = time.time()
        while time.time() - t0 < wait:
            with self._lock:
                ack = self.last_ack
            if ack and ack.get("seq") == seq:
                if ack["cmd"] in (NAK, CMD_NAK):
                    raise RuntimeError("SEM NAK seq=%d" % seq)
                self.last_sem_status = decode_sem_stat16(
                    (int(ack.get("addr") or 0) >> 16) & 0xFFFF)
                return ack.get("payload") or b""
            time.sleep(0.02)
        raise RuntimeError("SEM ACK 超时 seq=%d" % seq)

    def read_fecc_record(self):
        """FRAME_ECCE2 latch, straight from the bridge (no telemetry wait)."""
        return decode_fecc_record(
            self.uart_cmd_sem(b"", timeout_ms=200, flags=SEM_FLAG_FECC))

    def dump_monitor_pages(self, n_pages=4, settle_s=0.4):
        """Read the 64-byte unsol buffer as 16-byte pages (flags[0]=1, [3:1]=page)."""
        if settle_s:
            time.sleep(settle_s)
        pages = []
        for p in range(int(n_pages)):
            flags = SEM_FLAG_DUMP | ((p & 7) << SEM_DUMP_PAGE_SHIFT)
            pages.append(self.uart_cmd_sem(b"", timeout_ms=200, flags=flags))
        return pages

    # ---- UDP ingest ----
    def _udp_loop(self):
        while not self._stop.is_set() and self._sock:
            try:
                data, _addr = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except Exception:
                break
            kind = parse_udp_datagram(data)
            if kind is None:
                continue
            tag, payload = kind
            if tag == "telem":
                with self._lock:
                    self.last_telem = payload
                if self._on_telem:
                    self._on_telem(payload)
            elif tag == "ack":
                with self._lock:
                    self.last_ack = payload
                if self._on_ack:
                    self._on_ack(payload)
            elif tag == "data" and self._collecting:
                with self._lock:
                    self.rx_buf.extend(payload)
                    n = len(self.rx_buf)
                if self._on_rx:
                    self._on_rx(n)

    # ---- reserved TX ----
    def send_data(self, payload, addr=0, chunk=UDP_CHUNK, on_progress=None):
        """Write `payload` into DDR at `addr` (UART ARM_WR + L2 UDP fill)."""
        if not payload:
            raise ValueError("空载荷")
        if len(payload) % 16:
            raise ValueError("载荷必须 16 字节对齐，当前 %d" % len(payload))
        if not _HAS_SCAPY or not self.iface:
            raise RuntimeError("发送需要 scapy 和有线网口")
        self.log("send_data %dB @0x%x chunk=%d" % (len(payload), addr, chunk))
        try:
            self.uart_cmd(CMD_WR, addr, len(payload), expect=ACK_WR, timeout=5.0)
        except RuntimeError as e:
            self.log("ARM_WR: %s （若已布防则继续填数）" % e)
        eth = Ether(dst=BOARD_MAC, src=self.src_mac)
        ip = IP(src=self.src_ip, dst=BOARD_IP)
        n_pkt = 0
        for off in range(0, len(payload), chunk):
            sendp(eth / ip / UDP(sport=UDP_PORT, dport=UDP_PORT) / payload[off:off + chunk],
                  iface=self.iface, verbose=0)
            n_pkt += 1
            time.sleep(0.005)
            if on_progress and (n_pkt % 32 == 0):
                on_progress(min(off + chunk, len(payload)), len(payload))
        if on_progress:
            on_progress(len(payload), len(payload))
        self.log("send_data 已发 %d 帧" % n_pkt)
        return n_pkt

    # ---- reserved transpose ----
    def do_transpose(self, addr=0):
        self.log("do_transpose @0x%x" % addr)
        return self.uart_cmd(CMD_XPOSE, addr, 0, expect=ACK_XPOSE, timeout=30.0)

    def do_fft2d(self, addr=0, mode=0, timeout=90.0):
        self.log("do_fft2d mode=%d @0x%x" % (mode, addr))
        return self.uart_cmd(CMD_FFT2D, addr, 0, expect=ACK_FFT2D,
                             timeout=timeout, flags=mode & 3)

    def set_cs_coeff(self, hcs=(0, 0, 0), hr=(0, 0, 0), ha=(0, 0, 0)):
        """Download 9 int16 (a,b,c) triples. Zeros clear that function."""
        triples = ((CS_SEL_HCS, hcs), (CS_SEL_HR, hr), (CS_SEL_HA, ha))
        if not any(any(t) for _s, t in triples):
            return self.uart_cmd(CMD_CS_COEFF, 0, 0, expect=ACK_CS_COEFF,
                                 flags=CS_SEL_CLR)
        last = None
        for sel, abc in triples:
            a, b, c = int(abc[0]), int(abc[1]), int(abc[2])
            addr = (a & 0xFFFF) | ((b & 0xFFFF) << 16)
            last = self.uart_cmd(CMD_CS_COEFF, addr, c & 0xFFFF,
                                 expect=ACK_CS_COEFF, flags=sel)
        return last

    def set_cs_tables(self, tables, on_progress=None):
        """Download 3x256 (a,b,c) tables. tables has abc_h1/h2/haz lists."""
        last = None
        keys = (("abc_h1", CS_TAB_H1), ("abc_h2", CS_TAB_H2), ("abc_haz", CS_TAB_HAZ))
        total = 3 * 256
        n = 0
        for key, sel in keys:
            rows = tables[key]
            for fa, abc in enumerate(rows):
                a, b, c = int(abc[0]), int(abc[1]), int(abc[2])
                addr = (fa & 0xFF) | ((a & 0xFFFF) << 16)
                length = (b & 0xFFFF) | ((c & 0xFFFF) << 16)
                last = self.uart_cmd(CMD_CS_COEFF, addr, length,
                                     expect=ACK_CS_COEFF,
                                     flags=(sel & 3) | CS_FLAG_TABLE)
                n += 1
                if on_progress and (n % 32 == 0 or n == total):
                    on_progress(n, total)
        self.log("set_cs_tables 已下发 %d 条" % n)
        return last

    def set_cs_params(self, scalars, on_progress=None):
        """Write 7 float32 scalars then commit on-board H1/H2/Haz build."""
        sc = [float(x) for x in scalars]
        if len(sc) < CS_PARAM_N:
            sc = sc + [0.0] * (CS_PARAM_N - len(sc))
        pairs = (
            (0, sc[0], sc[1]),
            (2, sc[2], sc[3]),
            (4, sc[4], sc[5]),
            (6, sc[6], 0.0),
        )
        last = None
        n = 0
        total = 5
        for idx, f0, f1 in pairs:
            hdr = pack_cs_param(idx, f0, f1)
            _m, _c, flags, _s, addr, length = unpack_hdr(hdr)
            last = self.uart_cmd(CMD_CS_PARAM, addr, length,
                                 expect=ACK_CS_PARAM, flags=flags)
            n += 1
            if on_progress:
                on_progress(n, total)
        last = self.uart_cmd(CMD_CS_PARAM, 0, 0, expect=ACK_CS_PARAM,
                             flags=CS_PARAM_COMMIT, timeout=30.0)
        if on_progress:
            on_progress(total, total)
        self.log("set_cs_params 已下发 %d 标量并建表" % CS_PARAM_N)
        return last

    # ---- reserved RX ----
    def recv_after_transpose(self, length=MATRIX_B, timeout=15.0, on_progress=None):
        """Issue RD_BACK and collect UDP payload until `length` bytes."""
        if not self._sock:
            raise RuntimeError("UDP 未打开，无法收数")
        with self._lock:
            self.rx_buf = bytearray()
            self._collecting = True
        self.log("recv_after_transpose 期望 %dB" % length)
        try:
            self.uart_cmd(CMD_RD, 0, length, expect=ACK_RD, timeout=3.0)
        except RuntimeError as e:
            self.log("RD_BACK 命令: %s （板卡可能已开始回读）" % e)
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self._lock:
                n = len(self.rx_buf)
            if on_progress:
                on_progress(n, length)
            if n >= length:
                break
            time.sleep(0.02)
        with self._lock:
            self._collecting = False
            data = bytes(self.rx_buf[:length])
        self.log("recv_after_transpose 收到 %d / %d" % (len(data), length))
        return data
