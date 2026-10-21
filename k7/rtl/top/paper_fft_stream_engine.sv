`timescale 1ns/1ps

// Paper-FFT stream DUT for the K7 SEM shell (instantiated by k7_paper_core).
//
// Data path is UDP-only; DDR/MIG is not touched:
//   udp_eth_rx payload stream (128b) -> repack -> in_buf[MAX_FRAMES*256 x IN_BEAT_W]
//   -> kernel (256 beats/frame, gap-free, in_last on beat 255)
//   -> out_buf -> repack -> 128b -> u_tx_payload FIFO -> udp_tx_engine.
//
// Byte format on the wire is the packed little-endian beat stream:
//   beat = {in0_re, in0_im, in1_re, in1_im, in2_re, in2_im, in3_re, in3_im}
//   MSB first inside the beat word; wire byte 0 = beat bits [7:0].
// Beat widths come from IN_W/OUT_W (component bits):
//   35/35 -> 280-bit beats, 8960 B/frame (paper_fft 18-bit-twiddle build)
//   16/27 -> 128-bit in / 216-bit out, 4096 B in / 6912 B out per frame
//             (paper_fft12 growing-width build)
//
// WR command (ing_*):   ing_len must equal FRAMES*IN_FRAME_BYTES,
//                       1..MAX_FRAMES frames.
//                       Each WR reloads in_buf from beat 0 (fresh stimulus).
// RUN command (run_*):  streams the ingested frames through the kernel.
// RD command (out_*):   out_len must equal target_frames*OUT_FRAME_BYTES;
//                       stalls on out_commit so an early RD waits for the
//                       kernel.
// Telemetry:            per-run counters + last event, comb-read 96-bit rows
//                       through the shell's seed_rb port.
module paper_fft_stream_engine #(
    parameter integer PAPER_ARCH      = 3,   // s0 s1 s2 s3 p0 p1 p2 s2h p2h p0x s2hr p2hr
    parameter integer ARITH_THRESHOLD = 0,   // 0 -> kernel default (16/3/3)
    parameter integer MAX_FRAMES      = 16,
    parameter integer IN_W            = 35,  // input component width
    parameter integer OUT_W           = 35   // output component width
) (
    input  wire         clk,
    input  wire         rst,
    input  wire         clear,

    // ---- WR ingest (UDP payload -> in_buf; the shell has no udp_wr_master) ----
    input  wire         ing_start,       // wr_start
    input  wire [31:0]  ing_len,         // op_length (bytes)
    input  wire [127:0] ing_data,        // wr_tdata
    input  wire         ing_valid,       // wr_tvalid
    output wire         ing_ready,       // -> wr_tready
    output wire         ing_busy,        // -> wr_busy
    output reg          ing_done,        // -> wr_done
    output reg          ing_error,       // -> wr_error

    // ---- RUN (driven by the app FSM, same as any other DUT) ----
    input  wire         run_start,       // dut_start
    output wire         run_busy,        // -> dut_busy
    output reg          run_done,        // -> dut_done
    output reg          run_error,       // -> dut_error

    // ---- RD stream out (out_buf -> UDP TX FIFO; no udp_rd_master here) ----
    input  wire         out_start,       // rd_start
    input  wire [31:0]  out_len,         // op_length (bytes)
    output reg  [127:0] out_data,        // -> rd_tdata
    output reg          out_valid,       // -> rd_tvalid
    input  wire         out_ready,       // <- rd_tready
    output wire         out_busy,        // -> rd_axi_busy
    output reg          out_done,        // -> rd_axi_done
    output reg          out_error,       // -> rd_axi_error

    // ---- telemetry read port (wired to the shell seed_rb port) ----
    input  wire [4:0]   rb_idx,
    output reg  [95:0]  rb_data,

    // ---- debug nibble for telem_uart's dbg_app slot ----
    output wire [2:0]   fsm_state          // {od_active, rstate}
);
    localparam integer FRAME_BEATS = 256;
    localparam integer IN_BEAT_W   = 8 * IN_W;
    localparam integer OUT_BEAT_W  = 8 * OUT_W;
    localparam integer WORD_W      = 128;
    localparam integer IN_FRAME_BYTES  = FRAME_BEATS * IN_W;   // 8 comp/beat
    localparam integer OUT_FRAME_BYTES = FRAME_BEATS * OUT_W;
    localparam integer BUF_DEPTH   = MAX_FRAMES * FRAME_BEATS;
    localparam integer PTR_W       = (BUF_DEPTH <= 2) ? 1 : $clog2(BUF_DEPTH);
    localparam integer ACC_W       = 544;
    localparam [31:0]  MAX_BYTES   = MAX_FRAMES * IN_FRAME_BYTES;
    localparam [31:0]  RUN_WATCHDOG = 32'd8_000_000;  // ~64 ms at 125 MHz

    // ----------------------------------------------------------------- RAMs
    (* ram_style = "block" *) reg [IN_BEAT_W-1:0]  in_buf  [0:BUF_DEPTH-1];
    (* ram_style = "block" *) reg [OUT_BEAT_W-1:0] out_buf [0:BUF_DEPTH-1];

    // -------------------------------------------------------- ingest (WR)
    // 128-bit little-endian word stream -> 280-bit beats -> in_buf.
    // acc holds pending stream bits (LSB = earliest wire bit); a push appends
    // the 128-bit word above the pending bits, a pop drains 280 bits.
    reg [ACC_W-1:0] acc;
    reg [9:0]       acc_n;
    reg [PTR_W-1:0] ing_wptr;
    reg [31:0]      ing_len_q, ing_bytes;
    reg [15:0]      frames_committed, target_frames;
    reg             ing_active;

    wire        do_pop   = ing_active && (acc_n >= IN_BEAT_W);
    wire        do_push  = ing_valid && ing_ready;
    // Pops run every cycle the accumulator holds a beat, so pushes only ever
    // land below BEAT_W -- the bound is a hard guard, never the constraint.
    // When beat == wire word the pop leaves the accumulator empty, so a
    // same-cycle push can refill without stalling the stream.
    assign ing_ready = ing_active && (acc_n <= ACC_W - WORD_W) &&
                       (!do_pop || (IN_BEAT_W == WORD_W));
    assign ing_busy  = ing_active;

    always @(posedge clk) begin
        if (rst || clear) begin
            acc <= '0; acc_n <= '0;
            ing_wptr <= '0; ing_len_q <= '0; ing_bytes <= '0;
            frames_committed <= '0; target_frames <= '0;
            ing_active <= 1'b0; ing_done <= 1'b0; ing_error <= 1'b0;
        end else begin
            ing_done <= 1'b0;
            if (ing_start) begin
                if (ing_active || run_busy ||
                    ing_len == 0 || (ing_len % IN_FRAME_BYTES) != 0 ||
                    ing_len > MAX_BYTES) begin
                    ing_error <= 1'b1;
                    ing_done  <= 1'b1;
                end else begin
                    ing_active <= 1'b1;
                    ing_len_q  <= ing_len;
                    ing_bytes  <= '0;
                    ing_wptr   <= '0;
                    acc        <= '0;
                    acc_n      <= '0;
                    frames_committed <= '0;
                    ing_error  <= 1'b0;
                end
            end
            if (do_pop) begin
                in_buf[ing_wptr] <= acc[IN_BEAT_W-1:0];
                if (do_push) begin
                    acc   <= (acc >> IN_BEAT_W) |
                             ({{(ACC_W-WORD_W){1'b0}}, ing_data}
                              << (acc_n - IN_BEAT_W));
                    acc_n <= acc_n - IN_BEAT_W + WORD_W;
                    ing_bytes <= ing_bytes + WORD_W/8;
                end else begin
                    acc   <= acc >> IN_BEAT_W;
                    acc_n <= acc_n - IN_BEAT_W;
                end
                ing_wptr <= ing_wptr + 1'b1;
                if (ing_wptr[7:0] == 8'd255)
                    frames_committed <= frames_committed + 1'b1;
            end else if (do_push) begin
                acc   <= acc | ({{(ACC_W-WORD_W){1'b0}}, ing_data} << acc_n);
                acc_n <= acc_n + WORD_W;
                ing_bytes <= ing_bytes + WORD_W/8;
            end
            // Frame-aligned lengths drain the accumulator exactly; sampling
            // one cycle after the last pop also lets frames_committed settle.
            if (ing_active && ing_bytes >= ing_len_q && acc_n == 10'd0) begin
                ing_active    <= 1'b0;
                ing_done      <= 1'b1;
                target_frames <= frames_committed;
            end else if (ing_active && ing_bytes >= ing_len_q &&
                         acc_n < IN_BEAT_W && acc_n != 10'd0) begin
                // A residue that is not a whole beat means the producer sent
                // more or fewer bytes than the header promised -- NAK it
                // rather than hanging S_WAIT_WR.
                ing_active <= 1'b0;
                ing_error  <= 1'b1;
                ing_done   <= 1'b1;
            end
        end
    end

    // ------------------------------------------------------------- kernel
    wire                     d_in_valid;
    wire                     d_in_last;
    wire [IN_BEAT_W-1:0]     d_in_data;
    wire                     d_out_valid;
    wire                     d_out_last;
    wire signed [OUT_W-1:0]  o0r, o0i, o1r, o1i, o2r, o2i, o3r, o3i;
    wire [9:0]               k_win_second, k_det, k_cor, k_unc;   // one bit per kernel stage
    wire [29:0]              k_loc;                               // 3 bits per stage
    reg                      kernel_rst;

    localparam integer TH_S1 = (ARITH_THRESHOLD != 0) ? ARITH_THRESHOLD : 16;
    localparam integer TH_S3 = (ARITH_THRESHOLD != 0) ? ARITH_THRESHOLD : 3;
    localparam integer TH_P1 = (ARITH_THRESHOLD != 0) ? ARITH_THRESHOLD : 3;

    generate
        if (PAPER_ARCH == 1) begin : g_k
            wire [7:0]  ws8, det8, cor8, unc8;
            wire [23:0] loc24;
            top_s1_gao_subfft_ecc #(.ARITH_THRESHOLD(TH_S1)) u_kernel (
                .clk(clk), .rst(kernel_rst), .in_valid(d_in_valid),
                .in_last(d_in_last),
                .in0_re(d_in_data[8*IN_W-1 -: IN_W]), .in0_im(d_in_data[7*IN_W-1 -: IN_W]),
                .in1_re(d_in_data[6*IN_W-1 -: IN_W]), .in1_im(d_in_data[5*IN_W-1 -: IN_W]),
                .in2_re(d_in_data[4*IN_W-1 -: IN_W]), .in2_im(d_in_data[3*IN_W-1 -: IN_W]),
                .in3_re(d_in_data[2*IN_W-1 -: IN_W]), .in3_im(d_in_data[IN_W-1 -: IN_W]),
                .out_valid(d_out_valid), .out_last(d_out_last),
                .out0_re(o0r), .out0_im(o0i), .out1_re(o1r), .out1_im(o1i),
                .out2_re(o2r), .out2_im(o2i), .out3_re(o3r), .out3_im(o3i),
                .win_second(ws8), .up_detected(det8),
                .up_corrected(cor8), .up_uncorrectable(unc8),
                .up_error_location(loc24));
            assign k_win_second = {2'b00, ws8};
            assign k_det = {2'b00, det8};
            assign k_cor = {2'b00, cor8};
            assign k_unc = {2'b00, unc8};
            assign k_loc = {6'd0, loc24};
        end else if (PAPER_ARCH == 3) begin : g_k
            wire [9:0]  ws10, det10, cor10, unc10;
            wire [29:0] loc30;
            top_s3_subfft_ecc u_kernel (
                .clk(clk), .rst(kernel_rst), .in_valid(d_in_valid),
                .in_last(d_in_last),
                .in0_re(d_in_data[8*IN_W-1 -: IN_W]), .in0_im(d_in_data[7*IN_W-1 -: IN_W]),
                .in1_re(d_in_data[6*IN_W-1 -: IN_W]), .in1_im(d_in_data[5*IN_W-1 -: IN_W]),
                .in2_re(d_in_data[4*IN_W-1 -: IN_W]), .in2_im(d_in_data[3*IN_W-1 -: IN_W]),
                .in3_re(d_in_data[2*IN_W-1 -: IN_W]), .in3_im(d_in_data[IN_W-1 -: IN_W]),
                .out_valid(d_out_valid), .out_last(d_out_last),
                .out0_re(o0r), .out0_im(o0i), .out1_re(o1r), .out1_im(o1i),
                .out2_re(o2r), .out2_im(o2i), .out3_re(o3r), .out3_im(o3i),
                .win_second(ws10), .up_detected(det10),
                .up_corrected(cor10), .up_uncorrectable(unc10),
                .up_error_location(loc30));
            assign k_win_second = ws10;
            assign k_det = det10;
            assign k_cor = cor10;
            assign k_unc = unc10;
            assign k_loc = loc30;
        end else if (PAPER_ARCH == 5) begin : g_k
            wire [9:0]  ws10, det10, cor10, unc10;
            wire [29:0] loc30;
            top_p1_pfft_ecc #(.ARITH_THRESHOLD(TH_P1)) u_kernel (
                .clk(clk), .rst(kernel_rst), .in_valid(d_in_valid),
                .in_last(d_in_last),
                .in0_re(d_in_data[8*IN_W-1 -: IN_W]), .in0_im(d_in_data[7*IN_W-1 -: IN_W]),
                .in1_re(d_in_data[6*IN_W-1 -: IN_W]), .in1_im(d_in_data[5*IN_W-1 -: IN_W]),
                .in2_re(d_in_data[4*IN_W-1 -: IN_W]), .in2_im(d_in_data[3*IN_W-1 -: IN_W]),
                .in3_re(d_in_data[2*IN_W-1 -: IN_W]), .in3_im(d_in_data[IN_W-1 -: IN_W]),
                .out_valid(d_out_valid), .out_last(d_out_last),
                .out0_re(o0r), .out0_im(o0i), .out1_re(o1r), .out1_im(o1i),
                .out2_re(o2r), .out2_im(o2i), .out3_re(o3r), .out3_im(o3i),
                .win_second(ws10), .up_detected(det10),
                .up_corrected(cor10), .up_uncorrectable(unc10),
                .up_error_location(loc30));
            assign k_win_second = ws10;
            assign k_det = det10;
            assign k_cor = cor10;
            assign k_unc = unc10;
            assign k_loc = loc30;
        end else if (PAPER_ARCH == 0) begin : g_k
            top_s0_subfft_unprotected u_kernel (
                .clk(clk), .rst(kernel_rst), .in_valid(d_in_valid),
                .in_last(d_in_last),
                .in0_re(d_in_data[8*IN_W-1 -: IN_W]), .in0_im(d_in_data[7*IN_W-1 -: IN_W]),
                .in1_re(d_in_data[6*IN_W-1 -: IN_W]), .in1_im(d_in_data[5*IN_W-1 -: IN_W]),
                .in2_re(d_in_data[4*IN_W-1 -: IN_W]), .in2_im(d_in_data[3*IN_W-1 -: IN_W]),
                .in3_re(d_in_data[2*IN_W-1 -: IN_W]), .in3_im(d_in_data[IN_W-1 -: IN_W]),
                .out_valid(d_out_valid), .out_last(d_out_last),
                .out0_re(o0r), .out0_im(o0i), .out1_re(o1r), .out1_im(o1i),
                .out2_re(o2r), .out2_im(o2i), .out3_re(o3r), .out3_im(o3i));
            assign k_win_second = 10'd0;
            assign k_det = 10'd0; assign k_cor = 10'd0;
            assign k_unc = 10'd0; assign k_loc = 30'd0;
        end else if (PAPER_ARCH == 2) begin : g_k
            wire [9:0] stage_mismatch;
            top_s2_subfft_tmr u_kernel (
                .clk(clk), .rst(kernel_rst), .in_valid(d_in_valid),
                .in_last(d_in_last),
                .in0_re(d_in_data[8*IN_W-1 -: IN_W]), .in0_im(d_in_data[7*IN_W-1 -: IN_W]),
                .in1_re(d_in_data[6*IN_W-1 -: IN_W]), .in1_im(d_in_data[5*IN_W-1 -: IN_W]),
                .in2_re(d_in_data[4*IN_W-1 -: IN_W]), .in2_im(d_in_data[3*IN_W-1 -: IN_W]),
                .in3_re(d_in_data[2*IN_W-1 -: IN_W]), .in3_im(d_in_data[IN_W-1 -: IN_W]),
                .out_valid(d_out_valid), .out_last(d_out_last),
                .out0_re(o0r), .out0_im(o0i), .out1_re(o1r), .out1_im(o1i),
                .out2_re(o2r), .out2_im(o2i), .out3_re(o3r), .out3_im(o3i),
                .stage_mismatch(stage_mismatch));
            assign k_win_second = 10'd0;
            assign k_det = stage_mismatch; assign k_cor = stage_mismatch;
            assign k_unc = 10'd0; assign k_loc = 30'd0;
        end else if (PAPER_ARCH == 7) begin : g_k
            wire [9:0] stage_mismatch;
            top_s2h_subfft_tmr u_kernel (
                .clk(clk), .rst(kernel_rst), .in_valid(d_in_valid),
                .in_last(d_in_last),
                .in0_re(d_in_data[8*IN_W-1 -: IN_W]), .in0_im(d_in_data[7*IN_W-1 -: IN_W]),
                .in1_re(d_in_data[6*IN_W-1 -: IN_W]), .in1_im(d_in_data[5*IN_W-1 -: IN_W]),
                .in2_re(d_in_data[4*IN_W-1 -: IN_W]), .in2_im(d_in_data[3*IN_W-1 -: IN_W]),
                .in3_re(d_in_data[2*IN_W-1 -: IN_W]), .in3_im(d_in_data[IN_W-1 -: IN_W]),
                .out_valid(d_out_valid), .out_last(d_out_last),
                .out0_re(o0r), .out0_im(o0i), .out1_re(o1r), .out1_im(o1i),
                .out2_re(o2r), .out2_im(o2i), .out3_re(o3r), .out3_im(o3i),
                .stage_mismatch(stage_mismatch));
            assign k_win_second = 10'd0;
            assign k_det = stage_mismatch; assign k_cor = stage_mismatch;
            assign k_unc = 10'd0; assign k_loc = 30'd0;
        end else if (PAPER_ARCH == 8) begin : g_k
            wire [9:0] stage_mismatch;
            top_p2h_pfft_tmr u_kernel (
                .clk(clk), .rst(kernel_rst), .in_valid(d_in_valid),
                .in_last(d_in_last),
                .in0_re(d_in_data[8*IN_W-1 -: IN_W]), .in0_im(d_in_data[7*IN_W-1 -: IN_W]),
                .in1_re(d_in_data[6*IN_W-1 -: IN_W]), .in1_im(d_in_data[5*IN_W-1 -: IN_W]),
                .in2_re(d_in_data[4*IN_W-1 -: IN_W]), .in2_im(d_in_data[3*IN_W-1 -: IN_W]),
                .in3_re(d_in_data[2*IN_W-1 -: IN_W]), .in3_im(d_in_data[IN_W-1 -: IN_W]),
                .out_valid(d_out_valid), .out_last(d_out_last),
                .out0_re(o0r), .out0_im(o0i), .out1_re(o1r), .out1_im(o1i),
                .out2_re(o2r), .out2_im(o2i), .out3_re(o3r), .out3_im(o3i),
                .stage_mismatch(stage_mismatch));
            assign k_win_second = 10'd0;
            assign k_det = stage_mismatch; assign k_cor = stage_mismatch;
            assign k_unc = 10'd0; assign k_loc = 30'd0;
        end else if (PAPER_ARCH == 10) begin : g_k
            wire [9:0] stage_mismatch;
            top_s2hr_subfft_hybrid u_kernel (
                .clk(clk), .rst(kernel_rst), .in_valid(d_in_valid),
                .in_last(d_in_last),
                .in0_re(d_in_data[8*IN_W-1 -: IN_W]), .in0_im(d_in_data[7*IN_W-1 -: IN_W]),
                .in1_re(d_in_data[6*IN_W-1 -: IN_W]), .in1_im(d_in_data[5*IN_W-1 -: IN_W]),
                .in2_re(d_in_data[4*IN_W-1 -: IN_W]), .in2_im(d_in_data[3*IN_W-1 -: IN_W]),
                .in3_re(d_in_data[2*IN_W-1 -: IN_W]), .in3_im(d_in_data[IN_W-1 -: IN_W]),
                .out_valid(d_out_valid), .out_last(d_out_last),
                .out0_re(o0r), .out0_im(o0i), .out1_re(o1r), .out1_im(o1i),
                .out2_re(o2r), .out2_im(o2i), .out3_re(o3r), .out3_im(o3i),
                .stage_mismatch(stage_mismatch));
            assign k_win_second = 10'd0;
            assign k_det = stage_mismatch; assign k_cor = stage_mismatch;
            assign k_unc = 10'd0; assign k_loc = 30'd0;
        end else if (PAPER_ARCH == 11) begin : g_k
            wire [9:0] stage_mismatch;
            top_p2hr_pfft_hybrid u_kernel (
                .clk(clk), .rst(kernel_rst), .in_valid(d_in_valid),
                .in_last(d_in_last),
                .in0_re(d_in_data[8*IN_W-1 -: IN_W]), .in0_im(d_in_data[7*IN_W-1 -: IN_W]),
                .in1_re(d_in_data[6*IN_W-1 -: IN_W]), .in1_im(d_in_data[5*IN_W-1 -: IN_W]),
                .in2_re(d_in_data[4*IN_W-1 -: IN_W]), .in2_im(d_in_data[3*IN_W-1 -: IN_W]),
                .in3_re(d_in_data[2*IN_W-1 -: IN_W]), .in3_im(d_in_data[IN_W-1 -: IN_W]),
                .out_valid(d_out_valid), .out_last(d_out_last),
                .out0_re(o0r), .out0_im(o0i), .out1_re(o1r), .out1_im(o1i),
                .out2_re(o2r), .out2_im(o2i), .out3_re(o3r), .out3_im(o3i),
                .stage_mismatch(stage_mismatch));
            assign k_win_second = 10'd0;
            assign k_det = stage_mismatch; assign k_cor = stage_mismatch;
            assign k_unc = 10'd0; assign k_loc = 30'd0;
        end else if (PAPER_ARCH == 4) begin : g_k
            top_p0_pfft_no_framebuf_v1 u_kernel (
                .clk(clk), .rst(kernel_rst), .in_valid(d_in_valid),
                .in_last(d_in_last),
                .in0_re(d_in_data[8*IN_W-1 -: IN_W]), .in0_im(d_in_data[7*IN_W-1 -: IN_W]),
                .in1_re(d_in_data[6*IN_W-1 -: IN_W]), .in1_im(d_in_data[5*IN_W-1 -: IN_W]),
                .in2_re(d_in_data[4*IN_W-1 -: IN_W]), .in2_im(d_in_data[3*IN_W-1 -: IN_W]),
                .in3_re(d_in_data[2*IN_W-1 -: IN_W]), .in3_im(d_in_data[IN_W-1 -: IN_W]),
                .out_valid(d_out_valid), .out_last(d_out_last),
                .out0_re(o0r), .out0_im(o0i), .out1_re(o1r), .out1_im(o1i),
                .out2_re(o2r), .out2_im(o2i), .out3_re(o3r), .out3_im(o3i));
            assign k_win_second = 10'd0;
            assign k_det = 10'd0; assign k_cor = 10'd0;
            assign k_unc = 10'd0; assign k_loc = 30'd0;
        end else if (PAPER_ARCH == 9) begin : g_k
            top_p0x_pfft_exchange u_kernel (
                .clk(clk), .rst(kernel_rst), .in_valid(d_in_valid),
                .in_last(d_in_last),
                .in0_re(d_in_data[8*IN_W-1 -: IN_W]), .in0_im(d_in_data[7*IN_W-1 -: IN_W]),
                .in1_re(d_in_data[6*IN_W-1 -: IN_W]), .in1_im(d_in_data[5*IN_W-1 -: IN_W]),
                .in2_re(d_in_data[4*IN_W-1 -: IN_W]), .in2_im(d_in_data[3*IN_W-1 -: IN_W]),
                .in3_re(d_in_data[2*IN_W-1 -: IN_W]), .in3_im(d_in_data[IN_W-1 -: IN_W]),
                .out_valid(d_out_valid), .out_last(d_out_last),
                .out0_re(o0r), .out0_im(o0i), .out1_re(o1r), .out1_im(o1i),
                .out2_re(o2r), .out2_im(o2i), .out3_re(o3r), .out3_im(o3i));
            assign k_win_second = 10'd0;
            assign k_det = 10'd0; assign k_cor = 10'd0;
            assign k_unc = 10'd0; assign k_loc = 30'd0;
        end else begin : g_k
            wire [9:0] stage_mismatch;
            top_p2_pfft_tmr_no_framebuf_v1 u_kernel (
                .clk(clk), .rst(kernel_rst), .in_valid(d_in_valid),
                .in_last(d_in_last),
                .in0_re(d_in_data[8*IN_W-1 -: IN_W]), .in0_im(d_in_data[7*IN_W-1 -: IN_W]),
                .in1_re(d_in_data[6*IN_W-1 -: IN_W]), .in1_im(d_in_data[5*IN_W-1 -: IN_W]),
                .in2_re(d_in_data[4*IN_W-1 -: IN_W]), .in2_im(d_in_data[3*IN_W-1 -: IN_W]),
                .in3_re(d_in_data[2*IN_W-1 -: IN_W]), .in3_im(d_in_data[IN_W-1 -: IN_W]),
                .out_valid(d_out_valid), .out_last(d_out_last),
                .out0_re(o0r), .out0_im(o0i), .out1_re(o1r), .out1_im(o1i),
                .out2_re(o2r), .out2_im(o2i), .out3_re(o3r), .out3_im(o3i),
                .stage_mismatch(stage_mismatch));
            assign k_win_second = 10'd0;
            assign k_det = stage_mismatch; assign k_cor = stage_mismatch;
            assign k_unc = 10'd0; assign k_loc = 30'd0;
        end
    endgenerate

    // ---------------------------------------------------------- run control
    // Per RUN: release the kernel reset, wait REL_CYCLES, then stream
    // target_frames frames from in_buf gap-free while capturing out_buf.
    localparam [1:0] RS_IDLE = 2'd0, RS_REL = 2'd1, RS_FEED = 2'd2,
                     RS_TAIL = 2'd3;
    localparam integer REL_CYCLES = 8;

    reg [1:0]        rstate;
    reg [3:0]        rel_cnt;
    reg [15:0]       run_frame;
    reg [7:0]        feed_idx;
    reg [PTR_W-1:0]  wr_ptr_out;
    reg [15:0]       out_frames;
    reg [31:0]       out_commit;
    reg [31:0]       watchdog;
    // Declared here (not with the drain block) because the run FSM below
    // refuses run_start while a drain is in flight.
    reg              od_active;

    always @(posedge clk)
        kernel_rst <= rst || (rstate == RS_IDLE);
    assign run_busy   = (rstate != RS_IDLE);

    // These kernels are valid-gated streaming FFTs: an SDF stage emits only
    // in the second half of its 2*DEPTH window (or a primed first half), so
    // pipeline latency is ~one frame and the LAST frame's tail never leaves
    // the die unless more in_valid beats arrive.  Feed therefore continues
    // past target_frames -- same 256-beat/in_last cadence, data irrelevant
    // (it replays in_buf) -- until out_commit reaches the wanted count.
    // Beats beyond it are the flush stream's own outputs and are dropped.
    wire [31:0] needed_beats = {16'd0, target_frames} * FRAME_BEATS;
    // Round the flush up to a frame boundary: the kernel input stream must
    // never be truncated mid-frame (and the run FSM exits at the same
    // commit condition regardless).
    wire        feed_issue = (rstate == RS_FEED || rstate == RS_TAIL) &&
                             (out_commit < needed_beats || feed_idx != 0);
    wire [PTR_W-1:0] in_rd_addr = run_frame * FRAME_BEATS + feed_idx;

    reg [IN_BEAT_W-1:0] d_in_data_r;
    reg                 d_in_valid_r, d_in_last_r;
    assign d_in_valid = d_in_valid_r;
    assign d_in_last  = d_in_last_r;
    assign d_in_data  = d_in_data_r;

    wire [OUT_BEAT_W-1:0] out_beat =
        {o0r, o0i, o1r, o1i, o2r, o2i, o3r, o3i};

    always @(posedge clk) begin
        if (rst || clear) begin
            rstate <= RS_IDLE; rel_cnt <= '0;
            run_frame <= '0; feed_idx <= '0;
            d_in_data_r <= '0; d_in_valid_r <= 1'b0; d_in_last_r <= 1'b0;
            wr_ptr_out <= '0;
            out_commit <= '0; watchdog <= '0;
            run_done <= 1'b0; run_error <= 1'b0;
        end else begin
            run_done <= 1'b0;
            d_in_valid_r <= 1'b0; d_in_last_r <= 1'b0;
            if (feed_issue) begin
                d_in_data_r  <= in_buf[in_rd_addr];
                d_in_valid_r <= 1'b1;
                d_in_last_r  <= (feed_idx == 8'd255);
                if (feed_idx == 8'd255) begin
                    feed_idx  <= '0;
                    run_frame <= run_frame + 1'b1;
                end else begin
                    feed_idx <= feed_idx + 1'b1;
                end
            end
            if (d_out_valid && out_commit < needed_beats) begin
                out_buf[wr_ptr_out] <= out_beat;
                wr_ptr_out <= wr_ptr_out + 1'b1;
                out_commit <= out_commit + 1'b1;
            end
            case (rstate)
                RS_IDLE: begin
                    watchdog <= '0;
                    if (run_start) begin
                        // od_active: a drain in flight reads out_commit, which
                        // this run would zero -- refuse instead of deadlocking.
                        if (target_frames == 0 || od_active) begin
                            run_error <= 1'b1;
                            run_done  <= 1'b1;
                        end else begin
                            rstate     <= RS_REL;
                            rel_cnt    <= '0;
                            run_frame  <= '0; feed_idx <= '0;
                            wr_ptr_out <= '0;
                            out_commit <= '0;
                            run_error  <= 1'b0;
                        end
                    end
                end
                RS_REL: begin
                    if (rel_cnt == REL_CYCLES - 1)
                        rstate <= RS_FEED;
                    else
                        rel_cnt <= rel_cnt + 1'b1;
                end
                RS_FEED: begin
                    watchdog <= watchdog + 1'b1;
                    if (run_frame == target_frames)
                        rstate <= RS_TAIL;
                    if (watchdog == RUN_WATCHDOG) begin
                        run_error <= 1'b1;
                        run_done  <= 1'b1;
                        rstate    <= RS_IDLE;
                    end
                end
                RS_TAIL: begin
                    watchdog <= watchdog + 1'b1;
                    if (watchdog == RUN_WATCHDOG) begin
                        run_error <= 1'b1;
                        run_done  <= 1'b1;
                        rstate    <= RS_IDLE;
                    end else if (out_commit == needed_beats &&
                                 feed_idx == '0) begin
                        // Wait for the flush frame boundary so the kernel
                        // input stream is never truncated mid-frame.
                        rstate   <= RS_IDLE;
                        run_done <= 1'b1;
                    end
                end
                default: rstate <= RS_IDLE;
            endcase
        end
    end

    // -------------------------------------------------- telemetry (sticky
    // per RUN: counters reset on run_start, last event snapshot kept)
    reg [31:0] det_cnt, cor_cnt, unc_cnt;
    reg [7:0]  last_stage, last_loc, last_class, last_win;
    reg [15:0] last_frame, last_beat;
    reg [31:0] err_flags;

    // Only count while data is actually in the kernel: during RS_REL the
    // pipeline is still flushing reset state and the flags mean nothing.
    wire telem_active = (rstate == RS_FEED) || (rstate == RS_TAIL);
    integer ti;
    always @(posedge clk) begin
        if (rst || clear) begin
            det_cnt <= '0; cor_cnt <= '0; unc_cnt <= '0;
            last_stage <= '0; last_loc <= '0; last_class <= '0;
            last_frame <= '0; last_beat <= '0; err_flags <= '0;
        end else if (run_start && rstate == RS_IDLE) begin
            det_cnt <= '0; cor_cnt <= '0; unc_cnt <= '0;
            last_stage <= '0; last_loc <= '0; last_class <= '0;
            last_win <= '0;
            last_frame <= '0; last_beat <= '0; err_flags <= '0;
        end else begin
            if (ing_error) err_flags[0] <= 1'b1;
            if (out_error) err_flags[1] <= 1'b1;
            if (run_error) err_flags[2] <= 1'b1;
            for (ti = 0; ti < 10; ti = ti + 1) begin
                if (telem_active && k_det[ti])  det_cnt <= det_cnt + 1'b1;
                if (telem_active && k_cor[ti])  cor_cnt <= cor_cnt + 1'b1;
                if (telem_active && k_unc[ti])  unc_cnt <= unc_cnt + 1'b1;
                if (telem_active && (k_det[ti] || k_unc[ti])) begin
                    last_stage <= ti[7:0];
                    last_loc   <= {5'd0, k_loc[3*ti +: 3]};
                    last_class <= k_unc[ti] ? 8'd2 : 8'd1;
                    last_win   <= {7'd0, k_win_second[ti]};
                    last_frame <= out_frames;
                    last_beat  <= {8'd0, feed_idx};
                end
            end
        end
    end

    always @* begin
        case (rb_idx)
            5'd0: rb_data = {32'd0, det_cnt, cor_cnt};
            5'd1: rb_data = {32'd0, unc_cnt, out_commit};
            5'd2: rb_data = {32'd0, last_win, last_frame, last_beat,
                             last_stage, last_class, last_loc};
            5'd3: rb_data = {64'd0, target_frames, frames_committed};
            5'd4: rb_data = {64'd0, err_flags};
            5'd5: rb_data = {85'd0, rstate, ing_active, 4'd0,
                             PAPER_ARCH[3:0]};
            default: rb_data = 96'd0;
        endcase
    end

    // ----------------------------------------------------------- RD drain
    // out_len must equal target_frames*OUT_FRAME_BYTES. Beats are only read
    // once they
    // are committed, so an early RD stalls on out_commit rather than racing.
    // The BRAM read is registered (od_beat/od_beat_v two-stage pull) so the
    // buffer stays block RAM.  od_active itself is declared with the run
    // FSM state above -- xvlog rejects use-before-declaration.
    reg [31:0] od_len, od_bytes;
    reg [PTR_W-1:0] od_ptr;
    reg [ACC_W-1:0] oacc;
    reg [9:0]   oacc_n;
    reg         od_beat_v;
    reg [OUT_BEAT_W-1:0] od_beat;

    assign out_busy = od_active;
    // od_ptr zero-extends to 32 bits: out_commit can reach BUF_DEPTH when a
    // run fills the whole buffer, so the compare must not be truncated.
    wire od_pull = od_active && !od_beat_v &&
                   (oacc_n <= ACC_W - OUT_BEAT_W) &&
                   (od_ptr < out_commit);

    always @(posedge clk) begin
        if (rst || clear) begin
            od_active <= 1'b0; od_len <= '0; od_bytes <= '0;
            od_ptr <= '0; oacc <= '0; oacc_n <= '0;
            od_beat_v <= 1'b0; od_beat <= '0;
            out_data <= '0; out_valid <= 1'b0;
            out_done <= 1'b0; out_error <= 1'b0;
        end else begin
            out_done <= 1'b0;
            if (out_start) begin
                // Idle-state RD checks the beats actually exist; an early RD
                // issued while a run is still going stalls on out_commit by
                // design.  Without this a no-run or failed-run RD would pull
                // forever on a commit count that can never be reached.
                if (od_active || out_len == 0 ||
                    out_len != {16'd0, target_frames} * OUT_FRAME_BYTES ||
                    (rstate == RS_IDLE &&
                     {16'd0, target_frames} * FRAME_BEATS > out_commit)) begin
                    out_error <= 1'b1;
                    out_done  <= 1'b1;
                end else begin
                    od_active <= 1'b1;
                    od_len    <= out_len;
                    od_bytes  <= '0;
                    od_ptr    <= '0;
                    oacc      <= '0;
                    oacc_n    <= '0;
                    od_beat_v <= 1'b0;
                    out_error <= 1'b0;
                end
            end
            if (od_active) begin
                // stage 1: issue BRAM read; stage 2: append beat; emit: drain
                // one 128-bit word per cycle when a full word is pending.
                if (od_pull) begin
                    od_beat   <= out_buf[od_ptr];
                    od_beat_v <= 1'b1;
                    od_ptr    <= od_ptr + 1'b1;
                end else if (od_beat_v) begin
                    oacc      <= oacc |
                                 ({{(ACC_W-OUT_BEAT_W){1'b0}}, od_beat} << oacc_n);
                    oacc_n    <= oacc_n + OUT_BEAT_W;
                    od_beat_v <= 1'b0;
                end else if (oacc_n >= WORD_W && !out_valid) begin
                    out_data  <= oacc[WORD_W-1:0];
                    out_valid <= 1'b1;
                    oacc      <= oacc >> WORD_W;
                    oacc_n    <= oacc_n - WORD_W;
                end
                if (out_valid && out_ready) begin
                    out_valid <= 1'b0;
                    od_bytes  <= od_bytes + WORD_W/8;
                end
                if (od_bytes >= od_len && od_len != 0) begin
                    od_active <= 1'b0;
                    out_valid <= 1'b0;
                    out_done  <= 1'b1;
                end
            end
        end
    end

    // ------------------------------------------------------- frame count
    always @(posedge clk) begin
        if (rst || clear)
            out_frames <= '0;
        else if (run_start && rstate == RS_IDLE)
            out_frames <= '0;
        else if (d_out_valid && d_out_last && out_commit < needed_beats)
            out_frames <= out_frames + 1'b1;
    end

    assign fsm_state = {od_active, rstate};
endmodule
