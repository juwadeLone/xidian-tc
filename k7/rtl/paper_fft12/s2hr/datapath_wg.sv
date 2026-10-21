`timescale 1ns/1ps

// WP10 S1h (repository directory s2hr): SubFFT hybrid TMR.  Stages 1-7
// triplicate everything except the delay storage, which is one shared
// SECDED memory per lane (tmr_secded_shared_mem_wp10); stages 8-10 keep the
// unmodified real-TMR stage (subfft_real_tmr_stage_wg, shared with S0/S1).
// Per-stage per-lane-word voting with one mismatch flag per stage; the six
// storage-entry votes inside each shared memory merge into that flag.

// ---------------------------------------------------------------------------
// Stage 1-7 channel: identical datapath and timing to
// subfft_sdf_channel_wp4r, but the delay storage is external.  The six
// storage-facing signals of WP10 task 2.3 are driven out and the
// decoded/fanned-out word comes back on mem_rd_data.  Write timing is the
// late write + forward contract (pending_wr_en/pending_wr_addr/
// forward_active), matching s0_secded_delay_wg.
(* keep_hierarchy = "yes" *)
module s1h_sdf_channel_wp10 #(
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
    output wire signed [WO-1:0] out_im,
    output wire mem_rd_en,
    output wire [((DEPTH<=1)?1:$clog2(DEPTH))-1:0] mem_rd_addr,
    output wire mem_pending_wr_en,
    output wire [((DEPTH<=1)?1:$clog2(DEPTH))-1:0] mem_pending_wr_addr,
    output wire mem_forward_active,
    output wire [2*(W+1)-1:0] mem_wr_data,
    input  wire [2*(W+1)-1:0] mem_rd_data
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

// Storage-facing signals for the shared memory: read on the input beat,
// write one beat after the work beat (pending), forward when the work beat
// hits the pending address.
assign mem_rd_en = ctl_rd_en;
assign mem_rd_addr = ctl_rd_addr;
assign mem_pending_wr_en = ctl_pwe;
assign mem_pending_wr_addr = ctl_pwa;
assign mem_forward_active = ctl_fwd;

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
wire _unused = ^{ctl_wv, ctl_wp, ctl_wa};
endmodule


// ---------------------------------------------------------------------------
// Stage 1-7 replica: four independent SDF channels, each with its own
// control/butterfly/rotator/ROM; the four delay memories stay outside, at
// the stage, shared across the three replicas.
(* keep_hierarchy = "yes" *)
module s1h_sdf_replica_wp10 #(
    parameter integer STAGE = 1,
    parameter integer DEPTH = 128,
    parameter integer W = fft_wg_pkg::S1_W,
    parameter integer WO = fft_wg_pkg::S1_WO,
    parameter integer AW = (DEPTH <= 1) ? 1 : $clog2(DEPTH),
    parameter integer DW = 2*(W+1)
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire [8*W-1:0] in_data,
    output wire out_valid,
    output wire out_last,
    output wire [8*WO-1:0] out_data,
    output wire [3:0]       mem_rd_en,
    output wire [4*AW-1:0]  mem_rd_addr,
    output wire [3:0]       mem_pending_wr_en,
    output wire [4*AW-1:0]  mem_pending_wr_addr,
    output wire [3:0]       mem_forward_active,
    output wire [4*DW-1:0]  mem_wr_data,
    input  wire [4*DW-1:0]  mem_rd_data
);
wire [3:0] ch_valid, ch_last;
generate
for (genvar lane = 0; lane < 4; lane = lane + 1) begin : g_ch
    (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
    s1h_sdf_channel_wp10 #(.STAGE(STAGE), .DEPTH(DEPTH), .W(W), .WO(WO),
        .MEM_FILE(STAGE == 1 ? "subfft_s1_rom_wp4r.mem" :
                  STAGE == 2 ? "subfft_s2_rom_wp4r.mem" :
                  STAGE == 3 ? "subfft_s3_rom_wp4r.mem" :
                  STAGE == 4 ? "subfft_s4_rom_wp4r.mem" :
                  STAGE == 5 ? "subfft_s5_rom_wp4r.mem" :
                  STAGE == 6 ? "subfft_s6_rom_wp4r.mem" : "")) u_channel (
        .clk(clk), .rst(rst), .in_valid(in_valid),
        .in_re(in_data[(8-2*lane)*W-1 -: W]),
        .in_im(in_data[(7-2*lane)*W-1 -: W]),
        .out_valid(ch_valid[lane]), .out_last(ch_last[lane]),
        .out_re(out_data[(8-2*lane)*WO-1 -: WO]),
        .out_im(out_data[(7-2*lane)*WO-1 -: WO]),
        .mem_rd_en(mem_rd_en[lane]),
        .mem_rd_addr(mem_rd_addr[(lane+1)*AW-1 -: AW]),
        .mem_pending_wr_en(mem_pending_wr_en[lane]),
        .mem_pending_wr_addr(mem_pending_wr_addr[(lane+1)*AW-1 -: AW]),
        .mem_forward_active(mem_forward_active[lane]),
        .mem_wr_data(mem_wr_data[(lane+1)*DW-1 -: DW]),
        .mem_rd_data(mem_rd_data[(lane+1)*DW-1 -: DW]));
end
endgenerate
assign out_valid = ch_valid[0];
assign out_last = ch_last[0];
wire _unused = ^{in_last, ch_valid[3:1], ch_last[3:1]};
endmodule


// ---------------------------------------------------------------------------
// Hybrid stage: three replicas of everything but the delay storage plus one
// shared SECDED memory per lane.  All six storage signals are 2-of-3 voted
// inside tmr_secded_shared_mem_wp10; the four vote_mismatch flags join the
// stage's TMR mismatch the same way the output voters do.
(* keep_hierarchy = "yes" *)
module s1h_hybrid_stage_wp10 #(
    parameter integer STAGE = 1,
    parameter integer DEPTH = 128,
    parameter integer W = fft_wg_pkg::S1_W,
    parameter integer WO = fft_wg_pkg::S1_WO,
    parameter integer AW = (DEPTH <= 1) ? 1 : $clog2(DEPTH),
    parameter integer DW = 2*(W+1)
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire [8*W-1:0] in_data,
    output wire out_valid,
    output wire out_last,
    output wire [8*WO-1:0] out_data,
    output wire stage_mismatch
);
import fft_wg_pkg::*;
wire [3:0] rep_valid [0:2];
wire [3:0] rep_last [0:2];
wire [8*WO-1:0] rep_data [0:2];
wire [3:0] rep_rd_en [0:2];
wire [4*AW-1:0] rep_rd_addr [0:2];
wire [3:0] rep_pwe [0:2];
wire [4*AW-1:0] rep_pwa [0:2];
wire [3:0] rep_fwd [0:2];
wire [4*DW-1:0] rep_wr_data [0:2];
wire [4*DW-1:0] sh_rd_data;
wire [3:0] mem_mm;
generate
for (genvar r = 0; r < 3; r = r + 1) begin : g_replica
    wire v, l;
    (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
    s1h_sdf_replica_wp10 #(.STAGE(STAGE), .DEPTH(DEPTH), .W(W), .WO(WO),
                           .AW(AW), .DW(DW)) u_copy (
        .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
        .in_data(in_data), .out_valid(v), .out_last(l),
        .out_data(rep_data[r]),
        .mem_rd_en(rep_rd_en[r]), .mem_rd_addr(rep_rd_addr[r]),
        .mem_pending_wr_en(rep_pwe[r]), .mem_pending_wr_addr(rep_pwa[r]),
        .mem_forward_active(rep_fwd[r]),
        .mem_wr_data(rep_wr_data[r]), .mem_rd_data(sh_rd_data));
    assign rep_valid[r] = {4{v}};
    assign rep_last[r] = {4{l}};
end
for (genvar lane = 0; lane < 4; lane = lane + 1) begin : g_shmem
    (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
    tmr_secded_shared_mem_wp10 #(.WORDS(1), .DATA_W(DW), .DEPTH(DEPTH),
                                 .AW(AW)) u_shmem (
        .clk(clk), .rst(rst),
        .a_rd_en(rep_rd_en[0][lane]),
        .a_rd_addr(rep_rd_addr[0][(lane+1)*AW-1 -: AW]),
        .a_pending_wr_en(rep_pwe[0][lane]),
        .a_pending_wr_addr(rep_pwa[0][(lane+1)*AW-1 -: AW]),
        .a_forward_active(rep_fwd[0][lane]),
        .a_wr_data(rep_wr_data[0][(lane+1)*DW-1 -: DW]),
        .b_rd_en(rep_rd_en[1][lane]),
        .b_rd_addr(rep_rd_addr[1][(lane+1)*AW-1 -: AW]),
        .b_pending_wr_en(rep_pwe[1][lane]),
        .b_pending_wr_addr(rep_pwa[1][(lane+1)*AW-1 -: AW]),
        .b_forward_active(rep_fwd[1][lane]),
        .b_wr_data(rep_wr_data[1][(lane+1)*DW-1 -: DW]),
        .c_rd_en(rep_rd_en[2][lane]),
        .c_rd_addr(rep_rd_addr[2][(lane+1)*AW-1 -: AW]),
        .c_pending_wr_en(rep_pwe[2][lane]),
        .c_pending_wr_addr(rep_pwa[2][(lane+1)*AW-1 -: AW]),
        .c_forward_active(rep_fwd[2][lane]),
        .c_wr_data(rep_wr_data[2][(lane+1)*DW-1 -: DW]),
        .rd_data(sh_rd_data[(lane+1)*DW-1 -: DW]),
        .vote_mismatch(mem_mm[lane]));
end
endgenerate

wire [3:0] voted_valid, voted_last;
wire valid_diff, last_diff;
wp4r_hier_tmr_vote_w #(.W(4)) u_valid_vote (
    .a(rep_valid[0]), .b(rep_valid[1]), .c(rep_valid[2]),
    .y(voted_valid), .mismatch(valid_diff));
wp4r_hier_tmr_vote_w #(.W(4)) u_last_vote (
    .a(rep_last[0]), .b(rep_last[1]), .c(rep_last[2]),
    .y(voted_last), .mismatch(last_diff));
wire [3:0] data_diff;
generate
for (genvar lane = 0; lane < 4; lane = lane + 1) begin : g_vote
    wp4r_hier_tmr_vote_w #(.W(2*WO)) u_data_vote (
        .a(rep_data[0][(8-2*lane)*WO-1 -: 2*WO]),
        .b(rep_data[1][(8-2*lane)*WO-1 -: 2*WO]),
        .c(rep_data[2][(8-2*lane)*WO-1 -: 2*WO]),
        .y(out_data[(8-2*lane)*WO-1 -: 2*WO]),
        .mismatch(data_diff[lane]));
end
endgenerate
tmr_stage_flag_wg #(.POINTS(10)) u_flag (
    .clk(clk), .rst(rst), .voted_valid(voted_valid[0]),
    .mismatch_points({mem_mm, data_diff, valid_diff, last_diff}),
    .flag(stage_mismatch));
assign out_valid = voted_valid[0];
assign out_last = voted_last[0];
endmodule


// ---------------------------------------------------------------------------
(* keep_hierarchy = "yes" *)
module s1h_subfft_hybrid_pipeline_wp10 (
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire [8*fft_wg_pkg::IN_W-1:0] in_data,
    output wire out_valid,
    output wire out_last,
    output wire [8*fft_wg_pkg::OUT_W-1:0] out_data,
    output wire [9:0] stage_mismatch
);
import fft_wg_pkg::*;
function automatic integer stage_w(input integer s);
    case (s)
        1: stage_w = S1_W;
        2: stage_w = S2_W;
        3: stage_w = S3_W;
        4: stage_w = S4_W;
        5: stage_w = S5_W;
        6: stage_w = S6_W;
        7: stage_w = S7_W;
        8: stage_w = S8_W;
        9: stage_w = S9_W;
        10: stage_w = S10_W;
        default: stage_w = IN_W;
    endcase
endfunction
function automatic integer stage_wo(input integer s);
    case (s)
        1: stage_wo = S1_WO;
        2: stage_wo = S2_WO;
        3: stage_wo = S3_WO;
        4: stage_wo = S4_WO;
        5: stage_wo = S5_WO;
        6: stage_wo = S6_WO;
        7: stage_wo = S7_WO;
        8: stage_wo = S8_WO;
        9: stage_wo = S9_WO;
        10: stage_wo = S10_WO;
        default: stage_wo = OUT_W;
    endcase
endfunction

generate
for (genvar s = 1; s <= 10; s = s + 1) begin : g_stage
    localparam integer W = stage_w(s);
    localparam integer WO = stage_wo(s);
    localparam integer DEPTH = (s <= 8) ? (128 >> (s-1)) : 1;
    wire vin, lin, vout, lout;
    wire [8*W-1:0] din;
    wire [8*WO-1:0] dout;
    if (s == 1) begin : g_first
        assign vin = in_valid;
        assign lin = in_last;
        assign din = in_data;
    end else begin : g_later
        assign vin = g_stage[s-1].vout;
        assign lin = g_stage[s-1].lout;
        assign din = g_stage[s-1].dout;
    end
    if (s <= 7) begin : g_hybrid
        s1h_hybrid_stage_wp10 #(.STAGE(s), .DEPTH(DEPTH), .W(W), .WO(WO))
        u_stage (
            .clk(clk), .rst(rst), .in_valid(vin), .in_last(lin),
            .in_data(din), .out_valid(vout), .out_last(lout),
            .out_data(dout), .stage_mismatch(stage_mismatch[s-1]));
    end else begin : g_tmr
        subfft_real_tmr_stage_wg #(.STAGE(s), .W(W), .WO(WO)) u_stage (
            .clk(clk), .rst(rst), .in_valid(vin), .in_last(lin),
            .in_data(din), .out_valid(vout), .out_last(lout),
            .out_data(dout), .stage_mismatch(stage_mismatch[s-1]));
    end
end
endgenerate
assign out_valid = g_stage[10].vout;
assign out_last = g_stage[10].lout;
assign out_data = g_stage[10].dout;
endmodule


module top_s2hr_subfft_hybrid (
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in0_re, in0_im,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in1_re, in1_im,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in2_re, in2_im,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in3_re, in3_im,
    output wire out_valid,
    output wire out_last,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out0_re, out0_im,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out1_re, out1_im,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out2_re, out2_im,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out3_re, out3_im,
    output wire [9:0] stage_mismatch
);
s1h_subfft_hybrid_pipeline_wp10 u_pipeline (
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
    .in_data({in0_re, in0_im, in1_re, in1_im, in2_re, in2_im, in3_re, in3_im}),
    .out_valid(out_valid), .out_last(out_last),
    .out_data({out0_re, out0_im, out1_re, out1_im, out2_re, out2_im, out3_re, out3_im}),
    .stage_mismatch(stage_mismatch));
endmodule
