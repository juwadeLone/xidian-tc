`timescale 1ns/1ps

// WP4-R S1 (repository directory s2): one SubFFT SDF channel for stages 1-7
// of a real-TMR replica: own control (phase counter), own delay storage (no
// SECDED in the TMR region), butterfly, rotator and own ROM.  Stage 7 is
// trivial-rotation only.  Datapath and timing follow
// s0/r2sdf_lane_subfft_wg; the ROM holds only this stage's factors.

(* keep_hierarchy = "yes" *)
module subfft_sdf_channel_wp4r #(
    parameter integer STAGE = 1,
    parameter integer DEPTH = 128,
    parameter integer W = fft_wg_pkg::S1_W,
    parameter integer WO = fft_wg_pkg::S1_WO,
    parameter MEM_FILE = ""
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire signed [W-1:0] in_re,
    input  wire signed [W-1:0] in_im,
    output wire out_valid,
    output wire out_last,
    output wire signed [WO-1:0] out_re,
    output wire signed [WO-1:0] out_im
);
import fft_wg_pkg::*;
localparam integer MW = W + 1;
localparam integer DW = 2*MW;
localparam integer AW = (DEPTH <= 1) ? 1 : $clog2(DEPTH);
localparam integer SYNC_READ = (DEPTH == 128);
localparam integer TRIVIAL = (STAGE == 7);

wire ctl_ivq, ctl_rd_en, ctl_wv, ctl_ws, ctl_wp, ctl_pwe, ctl_fwd, emit_raw, raw_is_last;
wire [AW-1:0] ctl_rd_addr, ctl_wa, ctl_pwa;
wire [7:0] out_count;
(* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
pfft_sdf_control_wp4r #(.DEPTH(DEPTH), .AW(AW)) u_control (
    .clk(clk), .rst(rst), .in_valid(in_valid), .input_valid_q(ctl_ivq),
    .rd_en(ctl_rd_en), .rd_addr(ctl_rd_addr),
    .work_valid(ctl_wv), .work_second(ctl_ws), .work_primed(ctl_wp),
    .work_addr(ctl_wa), .pending_wr_en(ctl_pwe), .pending_wr_addr(ctl_pwa),
    .forward_active(ctl_fwd), .emit_raw(emit_raw), .raw_is_last(raw_is_last),
    .out_count(out_count));
wire [7:0] out_count_next = out_count + emit_raw;

reg signed [W-1:0] in_re_q, in_im_q, req_re, req_im;
always @(posedge clk) begin
    if (in_valid) begin
        in_re_q <= in_re;
        in_im_q <= in_im;
    end
    if (SYNC_READ && ctl_ivq) begin
        req_re <= in_re_q;
        req_im <= in_im_q;
    end
end
wire signed [W-1:0] work_re = SYNC_READ ? req_re : in_re_q;
wire signed [W-1:0] work_im = SYNC_READ ? req_im : in_im_q;

// Delay storage (no SECDED): direct write on the work beat, as in the
// reference lane.
wire [DW-1:0] mem_rd_data;
wire [DW-1:0] mem_wr_data;
(* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
wp4r_hier_fft_memory_v5 #(.WIDTH(DW), .DEPTH(DEPTH), .ADDR_W(AW)) u_memory (
    .clk(clk), .rd_en(ctl_rd_en), .rd_addr(ctl_rd_addr), .rd_data(mem_rd_data),
    .wr_en(ctl_wv), .wr_addr(ctl_wa), .wr_data(mem_wr_data));

wire signed [MW-1:0] memory_re = mem_rd_data[DW-1 -: MW];
wire signed [MW-1:0] memory_im = mem_rd_data[MW-1 -: MW];
wire signed [MW-1:0] work_ext_re = work_re;
wire signed [MW-1:0] work_ext_im = work_im;
wire signed [MW-1:0] sum_re = memory_re + work_ext_re;
wire signed [MW-1:0] sum_im = memory_im + work_ext_im;
wire signed [MW-1:0] dif_re = memory_re - work_ext_re;
wire signed [MW-1:0] dif_im = memory_im - work_ext_im;
wire signed [MW-1:0] store_re = ctl_ws ? dif_re : work_ext_re;
wire signed [MW-1:0] store_im = ctl_ws ? dif_im : work_ext_im;
assign mem_wr_data = {store_re, store_im};
wire signed [MW-1:0] emit_re = ctl_ws ? sum_re : memory_re;
wire signed [MW-1:0] emit_im = ctl_ws ? sum_im : memory_im;

wire emit_lower = emit_raw && !ctl_ws;
wire emit_upper = emit_raw && ctl_ws;

reg bv1, bv2, bv3, bl1, bl2, bl3, ml1, ml2, ml3;
reg signed [WO-1:0] br1, bi1, br2, bi2, br3, bi3;
wire mul_valid_out;
wire signed [WO-1:0] mul_out_re, mul_out_im;

generate
if (!TRIVIAL) begin : g_multiply
    wire [9:0] rom_index_next = {{(10-AW){1'b0}}, out_count_next[AW-1:0]};
    wire signed [TW_W-1:0] tw_re, tw_im;
    subfft_rom_wp4r #(.ENTRIES(DEPTH), .MEM_FILE(MEM_FILE)) u_rom (
        .clk(clk), .addr(rom_index_next), .coeff_re(tw_re), .coeff_im(tw_im));
    wp4r_hier_fft_complex_mul_wg_pipe #(.WI(MW), .WO(WO)) u_rotator (
        .clk(clk), .rst(rst), .valid_in(emit_lower),
        .in_re(emit_re), .in_im(emit_im), .coeff_re(tw_re), .coeff_im(tw_im),
        .valid_out(mul_valid_out), .out_re(mul_out_re), .out_im(mul_out_im));
end else begin : g_trivial
    wire signed [WO-1:0] emit_wo_re = emit_re;
    wire signed [WO-1:0] emit_wo_im = emit_im;
    wire [9:0] exp_now = out_count[0] ? 10'd256 : 10'd0;
    wire signed [WO-1:0] rot_re, rot_im;
    wp4r_hier_fft_complex_rotate_trivial_wg #(.W(WO)) u_rotator (
        .in_re(emit_wo_re), .in_im(emit_wo_im), .exponent(exp_now),
        .out_re(rot_re), .out_im(rot_im));
    reg tv1, tv2, tv3;
    reg signed [WO-1:0] t1r, t1i, t2r, t2i, t3r, t3i;
    always @(posedge clk) begin
        if (rst) begin
            tv1 <= 1'b0; tv2 <= 1'b0; tv3 <= 1'b0;
            t1r <= {WO{1'b0}}; t1i <= {WO{1'b0}};
            t2r <= {WO{1'b0}}; t2i <= {WO{1'b0}};
            t3r <= {WO{1'b0}}; t3i <= {WO{1'b0}};
        end else begin
            tv1 <= emit_lower;
            if (emit_lower) begin t1r <= rot_re; t1i <= rot_im; end
            tv2 <= tv1; t2r <= t1r; t2i <= t1i;
            tv3 <= tv2; t3r <= t2r; t3i <= t2i;
        end
    end
    assign mul_valid_out = tv3;
    assign mul_out_re = t3r;
    assign mul_out_im = t3i;
end
endgenerate

// Upper (sum) beats bypass the rotator with the same 3-beat latency.
always @(posedge clk) begin
    if (rst) begin
        bv1 <= 1'b0; bv2 <= 1'b0; bv3 <= 1'b0;
        bl1 <= 1'b0; bl2 <= 1'b0; bl3 <= 1'b0;
        ml1 <= 1'b0; ml2 <= 1'b0; ml3 <= 1'b0;
        br1 <= {WO{1'b0}}; bi1 <= {WO{1'b0}};
        br2 <= {WO{1'b0}}; bi2 <= {WO{1'b0}};
        br3 <= {WO{1'b0}}; bi3 <= {WO{1'b0}};
    end else begin
        bv1 <= emit_upper;
        bl1 <= emit_upper && raw_is_last;
        if (emit_upper) begin
            br1 <= emit_re;
            bi1 <= emit_im;
        end
        bv2 <= bv1; bl2 <= bl1; br2 <= br1; bi2 <= bi1;
        bv3 <= bv2; bl3 <= bl2; br3 <= br2; bi3 <= bi2;
        ml1 <= emit_lower && raw_is_last;
        ml2 <= ml1;
        ml3 <= ml2;
    end
end

reg ov, ol;
reg signed [WO-1:0] o_re, o_im;
wire ch_valid = mul_valid_out | bv3;
always @(posedge clk) begin
    if (rst) begin
        ov <= 1'b0;
        ol <= 1'b0;
        o_re <= {WO{1'b0}};
        o_im <= {WO{1'b0}};
    end else begin
        ov <= ch_valid;
        ol <= ch_valid ? (mul_valid_out ? ml3 : bl3) : 1'b0;
        if (ch_valid) begin
            o_re <= mul_valid_out ? mul_out_re : br3;
            o_im <= mul_valid_out ? mul_out_im : bi3;
        end
    end
end
assign out_valid = ov;
assign out_last = ol;
assign out_re = o_re;
assign out_im = o_im;
wire _unused = ^{ctl_wp, ctl_pwe, ctl_pwa, ctl_fwd};
endmodule
