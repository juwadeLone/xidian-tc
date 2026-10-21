`timescale 1ns/1ps
`include "udp_cmd_hdr.vh"

// Paper-FFT K7 shell: the same UART command layer, UDP transport, telemetry
// and SEM injection channel as k7_udp_ddr_core, with the entire DDR data
// path deleted rather than tied off.
//
//   WR  udp_eth_rx payload (128b) -> paper_fft_stream_engine ingest -> in_buf
//   RUN CS_PARAM flags[7]         -> engine streams frames through the kernel
//   RD  engine out_buf -> u_tx_payload FIFO -> udp_tx_engine -> udp_eth_tx
//   CS_RB                        -> engine telemetry rows
//   SEM                          -> unchanged: cdc + monitor bridge + wrapper
//
// There is no AXI master, no interconnect, no MIG and no app wrapper in this
// image.  udp_wr_master/udp_rd_master/the width gears/the 3-to-1 mux simply
// do not exist here -- FFT data never touches DDR.
module k7_paper_core #(
    parameter integer DIV       = 1085,           // 100 MHz / 115200
    parameter integer HEART_DIV = 125_000_000,   // ~1 Hz at 125 MHz
    // Header sanity bound only; the engine enforces FRAMES*8960 itself.
    parameter [31:0]  MAX_BYTES = 32'h0800_0000,
    parameter integer PAPER_ARCH      = 3,   // 0..6: s0 s1 s2 s3 p0 p1 p2
    parameter integer ARITH_THRESHOLD = 0,   // 0 -> kernel default (16/4/8)
    parameter integer PAPER_MAX_FRAMES = 16,
    // paper_fft12 growing-width variant: 16-bit input beats (128b), 27-bit output (216b)
    parameter integer PAPER_IN_W  = 16,
    parameter integer PAPER_OUT_W = 27
) (
    input  wire clk,
    input  wire rst_n,
    // ICAPE2 clock for the SEM core: the 100 MHz sysclk pin buffered through
    // a BUFG in the top.  Kept separate from clk (125 MHz) -- PG036 requires
    // icap_clk <= 100 MHz, so the processing clock cannot drive it.
    input  wire icap_clk,
    input  wire icap_rst_n,
    input  wire uart_rx_i,
    output wire uart_tx_o,
    // "Processing domain out of reset".  There is no MIG calibration to wait
    // for; the top feeds proc_rst_n here so commands are accepted as soon as
    // the domain is released.
    input  wire calib_done,
    input  wire rx_clk, rx_rst,
    input  wire gmii_rx_dv,
    input  wire [7:0] gmii_rd,
    output wire gmii_tx_dv,
    output wire [7:0] gmii_td
);
    wire rst = ~rst_n;

    wire [7:0] rx_data;
    wire       rx_valid;
    uart_rx #(.DIV(DIV)) u_uart_rx (
        .clk(clk), .rst(rst), .rx(uart_rx_i),
        .data(rx_data), .valid(rx_valid)
    );

    wire        cmd_valid, cmd_ready, hdr_ok;
    wire        cmd_is_wr, cmd_is_rd, cmd_is_xpose;
    wire [7:0]  cmd, flags;
    wire [15:0] seq;
    wire [31:0] cmd_addr, cmd_length;
    wire [127:0] rx_payload;

    // ---- engine telemetry row read-back (CS_RB) ----
    wire [10:0]          cs_rb_idx;
    wire                 cs_rb_dbg;
    wire [95:0]          seed_rb_data_w;
    wire [127:0]         cs_rb_tx_payload = {32'd0, seed_rb_data_w};
    wire [127:0]         sem_resp_payload;
    wire [15:0] rx_ovf_cnt;
    wire [7:0]  if_tx_data;
    wire        if_tx_valid, if_tx_ready;
    wire        ack_busy;
    wire [7:0]  ack_cmd_last;
    // S_TX_PAY reads tx_payload live; ack_cmd_last (latched at ack_start)
    // picks the SEM bridge response while that ACK is on the wire.
    wire [127:0] if_tx_payload =
        (ack_cmd_last == `UDP_CMD_SEM_ACK) ? sem_resp_payload
                                          : cs_rb_tx_payload;

    wire        seq_ack_start;
    wire [7:0]  seq_ack_cmd, seq_ack_flags;
    wire [15:0] seq_ack_seq;
    wire [31:0] seq_ack_addr, seq_ack_length;

    wire        telem_start;
    wire [7:0]  telem_cmd, telem_flags;
    wire [15:0] telem_seq;
    wire [31:0] telem_addr, telem_length;

    // ---- SEM status (assembled before uart_cmd_if so the SEM_ACK mux below
    // can use it; the wires themselves are driven at the bottom of the file) ----
    wire        sem_busy;
    wire        sem_status_idle_c, sem_status_injection_c;
    wire        sem_stat_heartbeat, sem_stat_uncorrectable, sem_stat_essential;
    wire        sem_stat_classification, sem_stat_correction;
    wire        sem_stat_observation, sem_stat_initialization;
    wire        sem_icap_request, sem_icap_grant;
    wire        sem_icap_request_c, sem_icap_grant_c;
    wire [15:0] sem_stat16 = {5'd0, sem_icap_grant_c, sem_icap_request_c,
        sem_stat_heartbeat, sem_status_idle_c, sem_stat_uncorrectable,
        sem_stat_essential, sem_status_injection_c, sem_stat_classification,
        sem_stat_correction, sem_stat_observation, sem_stat_initialization};

    // A7 parity: a pending SEM command suppresses the telemetry poll ack so
    // the two cannot interleave on the single UART TX path.
    wire        ack_start = seq_ack_start | (telem_start & ~sem_busy);
    wire [7:0]  ack_cmd    = seq_ack_start ? seq_ack_cmd    : telem_cmd;
    wire [7:0]  ack_flags  = seq_ack_start ? seq_ack_flags  : telem_flags;
    wire [15:0] ack_seq    = seq_ack_start ? seq_ack_seq    : telem_seq;
    wire [31:0] ack_addr   = seq_ack_start ?
        ((seq_ack_cmd == `UDP_CMD_SEM_ACK) ? {sem_stat16, seq_ack_addr[15:0]}
                                          : seq_ack_addr)
        : telem_addr;
    wire [31:0] ack_length = seq_ack_start ? seq_ack_length : telem_length;

    uart_cmd_if #(.MAX_BYTES(MAX_BYTES)) u_uart_cmd_if (
        .clk(clk), .rst_n(rst_n),
        .rx_data(rx_data), .rx_valid(rx_valid),
        .cmd_valid(cmd_valid), .cmd_ready(cmd_ready),
        .cmd(cmd), .flags(flags), .seq(seq),
        .addr(cmd_addr), .length(cmd_length),
        .hdr_ok(hdr_ok),
        .cmd_is_wr(cmd_is_wr), .cmd_is_rd(cmd_is_rd), .cmd_is_xpose(cmd_is_xpose),
        .rx_payload(rx_payload),
        .ack_start(ack_start), .ack_cmd(ack_cmd), .ack_flags(ack_flags),
        .ack_seq(ack_seq), .ack_addr(ack_addr), .ack_length(ack_length),
        .tx_payload(if_tx_payload),
        .ack_busy(ack_busy),
        .rx_ovf_cnt(rx_ovf_cnt),
        .debug_rx_hold(),
        .ack_cmd_last(ack_cmd_last),
        .tx_data(if_tx_data), .tx_valid(if_tx_valid), .tx_ready(if_tx_ready)
    );

    wire [2:0] cmd_state;
    wire       wr_start, swap_start, rd_start, fft_start, cs_build_start, sem_start;
    wire [1:0] mux_sel;
    wire [31:0] op_length;
    wire wr_busy, wr_done, wr_error, rd_busy, rd_done, rd_error;
    // Unsupported compute commands fail explicitly, same as the DDR image:
    // cmd_seq waits on (done && !busy), so leaving them unanswered would hang
    // the command layer; they are refused instead.
    wire fft_busy, fft_done_pulse, fft_error;
    wire swap_busy_w, swap_done_pulse, swap_error_w;
    reg refused_fft, refused_swap;
    always @(posedge clk) begin
        if (!rst_n) begin
            refused_fft  <= 1'b0;
            refused_swap <= 1'b0;
        end else begin
            refused_fft  <= fft_start;
            refused_swap <= swap_start;
        end
    end
    assign fft_busy        = 1'b0;
    assign fft_done_pulse  = refused_fft;
    assign fft_error       = 1'b1;
    assign swap_busy_w     = 1'b0;
    assign swap_done_pulse = refused_swap;
    assign swap_error_w    = 1'b1;
    reg swap_done_l;
    always @(posedge clk) begin
        if (!rst_n)               swap_done_l <= 1'b0;
        else if (swap_start)      swap_done_l <= 1'b0;
        else if (swap_done_pulse) swap_done_l <= 1'b1;
    end
    reg fft_done_l;
    always @(posedge clk) begin
        if (!rst_n)             fft_done_l <= 1'b0;
        else if (fft_start)     fft_done_l <= 1'b0;
        else if (fft_done_pulse) fft_done_l <= 1'b1;
    end
    wire fft_done = fft_done_l;
    wire sem_done, sem_err;
    wire [7:0] sem_resp_len;
    wire [127:0] wr_tdata, rd_tdata;
    wire wr_tvalid, wr_tready, rd_tvalid, rd_tready;

    // ---- paper FFT DUT: UDP WR payload -> in_buf -> kernel -> out_buf ->
    // UDP RD stream.  No DDR anywhere on the data path.
    wire        eng_run_busy, eng_run_done, eng_run_error;
    wire        eng_out_busy, eng_out_done, eng_out_error;
    wire [2:0]  eng_fsm_state;

    paper_fft_stream_engine #(
        .PAPER_ARCH(PAPER_ARCH),
        .ARITH_THRESHOLD(ARITH_THRESHOLD),
        .MAX_FRAMES(PAPER_MAX_FRAMES),
        .IN_W(PAPER_IN_W),
        .OUT_W(PAPER_OUT_W)
    ) u_dut (
        .clk(clk), .rst(rst), .clear(!rst_n),
        .ing_start(wr_start), .ing_len(op_length),
        .ing_data(wr_tdata), .ing_valid(wr_tvalid),
        .ing_ready(wr_tready), .ing_busy(wr_busy),
        .ing_done(wr_done), .ing_error(wr_error),
        .run_start(cs_build_start), .run_busy(eng_run_busy),
        .run_done(eng_run_done), .run_error(eng_run_error),
        .out_start(rd_start), .out_len(op_length),
        .out_data(rd_tdata), .out_valid(rd_tvalid), .out_ready(rd_tready),
        .out_busy(eng_out_busy), .out_done(eng_out_done),
        .out_error(eng_out_error),
        .rb_idx(cs_rb_idx[4:0]), .rb_data(seed_rb_data_w),
        .fsm_state(eng_fsm_state)
    );

    // RUN bookkeeping.  The engine pulses done/error for a single cycle while
    // S_WAIT_B samples a level, so both are latched until cs_build_start arms
    // the next run -- the same gating the DDR image applies to the CS app.
    reg run_fin, run_err;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            run_fin <= 1'b0;
            run_err <= 1'b0;
        end else if (cs_build_start) begin
            run_fin <= 1'b0;
            run_err <= 1'b0;
        end else begin
            if (eng_run_done)  run_fin <= 1'b1;
            if (eng_run_error) run_err <= 1'b1;
        end
    end
    wire cs_app_done_l = run_fin && !cs_build_start;
    reg image_done_q;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) image_done_q <= 1'b0;
        else        image_done_q <= eng_run_done;
    end

    cmd_seq #(.ADDR_W(31)) u_cmd_seq (
        .clk(clk), .rst_n(rst_n), .calib_done(calib_done),
        .cmd_valid(cmd_valid), .cmd_ready(cmd_ready),
        .cmd(cmd), .flags(flags), .seq(seq),
        .addr(cmd_addr), .length(cmd_length),
        .hdr_ok(hdr_ok),
        .cmd_is_wr(cmd_is_wr), .cmd_is_rd(cmd_is_rd), .cmd_is_xpose(cmd_is_xpose),
        .ack_start(seq_ack_start), .ack_cmd(seq_ack_cmd),
        .ack_flags(seq_ack_flags), .ack_seq(seq_ack_seq),
        .ack_addr(seq_ack_addr), .ack_length(seq_ack_length),
        .ack_busy(ack_busy),
        // Stream ingest has no address: op_addr is left unconnected.
        .mux_sel(mux_sel), .op_addr(), .op_length(op_length), .wr_err_sticky(),
        .wr_start(wr_start), .wr_busy(wr_busy), .wr_done(wr_done), .wr_error(wr_error),
        .swap_start(swap_start), .swap_busy(swap_busy_w),
        .swap_done(swap_done_l), .swap_error(swap_error_w),
        .rd_start(rd_start), .rd_busy(rd_busy), .rd_done(rd_done), .rd_error(rd_error),
        .fft_mode(), .fft_start(fft_start),
        .fft_busy(fft_busy), .fft_done(fft_done), .fft_error(fft_error),
        .debug_state(cmd_state),
        .cs_a_hcs(), .cs_b_hcs(), .cs_c_hcs(),
        .cs_a_hr(), .cs_b_hr(), .cs_c_hr(),
        .cs_a_ha(), .cs_b_ha(), .cs_c_ha(),
        .cs_tab_we(), .cs_tab_sel(), .cs_tab_fa(),
        .cs_tab_a(), .cs_tab_b(), .cs_tab_c(),
        // Non-run CS_PARAM writes have nowhere to land in this image; the
        // command still ACKs, exactly like every board top that left
        // cs_param_we unconnected before this one wired it up.
        .cs_param_we(), .cs_param_idx(),
        .cs_param_w0(), .cs_param_w1(),
        .cs_build_start(cs_build_start),
        .cs_build_busy(eng_run_busy), .cs_build_done(cs_app_done_l),
        .cs_build_error(run_err),
        .sem_busy(sem_busy), .sem_start(sem_start),
        .sem_done(sem_done), .sem_err(sem_err), .sem_resp_len(sem_resp_len),
        .rx_payload(rx_payload),
        .cs_row_we(), .cs_row_sel(), .cs_row_idx(), .cs_row_data(),
        .cs_rb_sel(), .cs_rb_idx(cs_rb_idx), .cs_rb_dbg(cs_rb_dbg),
        // No seed-table dump exists: report not-ready so CS_DUMP NAKs in
        // S_IDLE instead of stalling in S_WAIT_D forever.
        .cs_dump_start(), .cs_dump_ready(1'b0), .cs_dump_done(1'b0)
    );

    localparam integer HEART_W = (HEART_DIV <= 2) ? 2 : $clog2(HEART_DIV);
    reg [HEART_W-1:0] heart_div;
    reg [7:0]         heart_flag;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            heart_div  <= {HEART_W{1'b0}};
            heart_flag <= 8'd0;
        end else if (heart_div == HEART_W'(HEART_DIV - 1)) begin
            heart_div  <= {HEART_W{1'b0}};
            heart_flag <= heart_flag + 8'd1;
        end else begin
            heart_div <= heart_div + 1'b1;
        end
    end

    assign rd_busy  = eng_out_busy | tx_busy;
    assign rd_error = eng_out_error;
    wire tx_busy;

    telem_uart u_telem_uart (
        .clk(clk), .rst_n(rst_n),
        .heart_flag(heart_flag),
        // {dut error, CS_PARAM flags[7], dut idle} -- the same contract the
        // DDR image's csa app exposed, so wait_idle's dbg_fsm0==1 check and
        // the swap_error abort both work unmodified on the host.
        .dbg_app({run_err, flags[7], ~eng_run_busy}),
        .calib_done(calib_done),
        .cmd_state(cmd_state),
        // There is no bus mux behind mux_sel in this image; report the
        // request as-is so the host can still see the last command class.
        .mux_sel(mux_sel),
        .wr_busy(wr_busy), .swap_busy(eng_run_busy), .rd_busy(rd_busy),
        .wr_error(wr_error), .swap_error(run_err), .rd_error(rd_error),
        .wr_beat(wr_tvalid & wr_tready), .rd_beat(rd_tvalid & rd_tready),
        // Same remap as the DDR image: the swap telemetry slots carry the
        // DUT's busy, error and image_done.
        .swap_done(image_done_q),
        .ack_busy(ack_busy),
        .cmd_ack_start(seq_ack_start),
        .telem_start(telem_start),
        .telem_cmd(telem_cmd),
        .telem_flags(telem_flags),
        .telem_seq(telem_seq),
        .telem_addr(telem_addr),
        .telem_length(telem_length)
    );

    wire       send_en, tx_done;
    wire [7:0] glue_data;
    uart_tx_glue u_glue (
        .clk(clk), .rst_n(rst_n),
        .s_tdata(if_tx_data), .s_tvalid(if_tx_valid), .s_tready(if_tx_ready),
        .send_en(send_en), .tx_data(glue_data), .tx_done(tx_done)
    );

    uart_tx #(.DIV(DIV)) u_uart_tx (
        .clk(clk), .rst(rst),
        .send(send_en), .data(glue_data),
        .tx(uart_tx_o), .busy(), .done(tx_done)
    );


    // A7 host protocol is unchanged: 192.168.0.4 / 00:11:22:33:44:55,
    // UDP 8080. The host sends raw Ethernet on its 192.168.137.1 NIC.
    wire [47:0] rx_mac;
    wire [31:0] rx_ip;
    udp_eth_rx u_eth_rx (
        .clk(rx_clk), .rst(rx_rst), .I_sys_clk(clk), .I_sys_rst_n(rst_n),
        .gmii_rx_dv(gmii_rx_dv), .gmii_rd(gmii_rd),
        .status(), .fil_data(), .fil_data_vld(),
        .pld_tdata(wr_tdata), .pld_tvalid(wr_tvalid), .pld_tready(wr_tready),
        .pc_mac(rx_mac), .pc_ip(rx_ip)
    );
    // Atomic peer snapshot across RX -> processing clock, not an unsynchronised bus.
    reg [79:0] peer_sent;
    wire peer_full, peer_valid;
    wire [79:0] peer_out;
    wire peer_write = !rx_rst && !peer_full && rx_mac != 0 && {rx_mac,rx_ip} != peer_sent;
    always @(posedge rx_clk) begin
        if (rx_rst) peer_sent <= 0;
        else if (peer_write) peer_sent <= {rx_mac,rx_ip};
    end
    FWFT_Async_fifo #(.WRITE_DATA_WIDTH(80), .READ_DATA_WIDTH(80),
        .FIFO_WRITE_DEPTH(32), .FIFO_MEMORY_TYPE("distributed")) u_peer_fifo (
        .rst(rx_rst), .wr_clk(rx_clk), .rd_clk(clk),
        .din({rx_mac,rx_ip}), .wr_en(peer_write), .full(peer_full),
        .rd_en(peer_valid), .dout(peer_out), .data_valid(peer_valid)
    );
    reg [79:0] peer;
    always @(posedge clk) begin
        // Default reply peer = lab host (10:ff:e0:3b:35:0a / 192.168.1.100).
        // Seed shots never send UDP, so without a nonzero reset value the
        // post-program RD replies would go to MAC 0 and vanish; any real
        // RX frame still re-learns the peer below.
        if (!rst_n) peer <= {48'h10FF_E03B_350A, 32'hC0A8_0164};
        else if (peer_valid) peer <= peer_out;
    end

    wire fifo_full, fifo_valid;
    wire [7:0] tx_byte;
    wire tx_ready;
    FWFT_sync_fifo #(.WRITE_DATA_WIDTH(128), .READ_DATA_WIDTH(8),
        .FIFO_WRITE_DEPTH(128)) u_tx_payload (
        .clk(clk), .rst(!rst_n), .din(rd_tdata),
        .wr_en(rd_tvalid && rd_tready), .full(fifo_full),
        .rd_en(tx_ready && fifo_valid), .dout(tx_byte), .data_valid(fifo_valid)
    );
    assign rd_tready = !fifo_full;
    wire [128:0] tx_status;
    wire tx_fil_valid;
    wire [7:0] tx_fil_data;
    udp_tx_engine u_tx_engine (
        .clk(clk), .rst(!rst_n), .start(rd_start), .length(op_length),
        .pc_mac(peer[79:32]), .pc_ip(peer[31:0]), .busy(tx_busy), .done(rd_done),
        .pld_tdata(tx_byte), .pld_tvalid(fifo_valid), .pld_tready(tx_ready),
        .status(tx_status), .fil_data_vld(tx_fil_valid), .fil_data(tx_fil_data)
    );
    udp_eth_tx u_eth_tx (
        .clk(clk), .rst(!rst_n), .status(tx_status),
        .fil_data_vld(tx_fil_valid), .fil_data(tx_fil_data),
        .po_data_vld(gmii_tx_dv), .po_data(gmii_td)
    );

    // ===================== SEM (PG036) CRAM injection channel ==============
    // Identical to k7_udp_ddr_core: sem_0 + ICAPE2 in sem_wrapper, ASCII
    // monitor commands relayed by sem_monitor_bridge, request/response
    // bundle crossing clk125 <-> icap_clk through sem_cmd_cdc's toggle
    // handshake.
    wire        status_heartbeat, status_initialization, status_observation;
    wire        status_correction, status_classification, status_essential;
    wire        status_uncorrectable, status_idle, status_injection;
    wire [7:0]  mon_rxdata, mon_txdata;
    wire        mon_rxread, mon_rxempty, mon_txwrite, mon_txfull;
    wire        s_sem_start, s_sem_done, s_sem_err;
    wire [7:0]  s_sem_flags, s_sem_resp_len;
    wire [31:0] s_sem_timeout, s_sem_len;
    wire [127:0] s_sem_payload, s_sem_resp;

    sem_cmd_cdc u_sem_cdc (
        .c_clk(clk), .c_rst_n(rst_n),
        .c_start(sem_start), .c_flags(flags),
        .c_timeout_ms(cmd_addr), .c_cmd_len(cmd_length),
        .c_cmd_payload(rx_payload),
        .c_done(sem_done), .c_err(sem_err), .c_resp_len(sem_resp_len),
        .c_resp_payload(sem_resp_payload),
        .s_clk(icap_clk), .s_rst_n(icap_rst_n),
        .s_start(s_sem_start), .s_flags(s_sem_flags),
        .s_timeout_ms(s_sem_timeout), .s_cmd_len(s_sem_len),
        .s_cmd_payload(s_sem_payload),
        .s_done(s_sem_done), .s_err(s_sem_err),
        .s_resp_len(s_sem_resp_len), .s_resp_payload(s_sem_resp)
    );

    sem_monitor_bridge #(.CLK_HZ(100_000_000)) u_sem_bridge (
        .clk(icap_clk), .rst_n(icap_rst_n),
        .start(s_sem_start), .flags(s_sem_flags), .timeout_ms(s_sem_timeout),
        .cmd_len(s_sem_len), .cmd_payload(s_sem_payload),
        .status_idle(status_idle), .status_injection(status_injection),
        .monitor_rxdata(mon_rxdata), .monitor_rxread(mon_rxread),
        .monitor_rxempty(mon_rxempty),
        .monitor_txdata(mon_txdata), .monitor_txwrite(mon_txwrite),
        .monitor_txfull(mon_txfull),
        .done(s_sem_done), .err(s_sem_err),
        .resp_len(s_sem_resp_len), .resp_payload(s_sem_resp)
    );

    sem_wrapper u_sem_wrapper (
        .icap_clk(icap_clk), .icap_resetn(icap_rst_n),
        .status_heartbeat(status_heartbeat),
        .status_initialization(status_initialization),
        .status_observation(status_observation),
        .status_correction(status_correction),
        .status_classification(status_classification),
        .status_essential(status_essential),
        .status_uncorrectable(status_uncorrectable),
        .status_idle(status_idle),
        .icap_request(sem_icap_request), .icap_grant(sem_icap_grant),
        .monitor_txdata(mon_txdata), .monitor_txwrite(mon_txwrite),
        .monitor_txfull(mon_txfull),
        .monitor_rxdata(mon_rxdata), .monitor_rxread(mon_rxread),
        .monitor_rxempty(mon_rxempty)
    );

    // SEM status is quasi-static; two-flop each bit into the command domain
    // for the SEM_ACK stat16 field.
    (* ASYNC_REG = "TRUE" *) reg [10:0] sem_stat_m, sem_stat_s;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sem_stat_m <= 11'd0;
            sem_stat_s <= 11'd0;
        end else begin
            sem_stat_m <= {status_heartbeat, status_idle, status_uncorrectable,
                           status_essential, status_injection,
                           status_classification, status_correction,
                           status_observation, status_initialization,
                           sem_icap_request, sem_icap_grant};
            sem_stat_s <= sem_stat_m;
        end
    end
    assign {sem_stat_heartbeat, sem_status_idle_c, sem_stat_uncorrectable,
            sem_stat_essential, sem_status_injection_c, sem_stat_classification,
            sem_stat_correction, sem_stat_observation,
            sem_stat_initialization, sem_icap_request_c, sem_icap_grant_c}
        = sem_stat_s;
endmodule
