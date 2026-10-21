`timescale 1ns/1ps

// WP10 P1h (repository directory p2hr): PFFT hybrid TMR.  Stages 1-7
// triplicate everything except the delay storage, which is one shared
// SECDED memory per stage (one address holds all four lane words,
// tmr_secded_shared_mem_wp10 with WORDS=4); stages 8-10 keep the
// unmodified real-TMR stage (pfft_real_tmr_stage_wg, shared with P0/P1).
// Per-stage per-lane-word voting with one mismatch flag per stage; the six
// storage-entry votes inside the shared memory merge into that flag.

// ---------------------------------------------------------------------------
// Stage 1-7 replica: identical datapath and timing to pfft_sdf4_wp4r, but
// the packed four-lane delay memory is external.  The six storage-facing
// signals of WP10 task 2.3 are driven out and the decoded/fanned-out word
// comes back on mem_rd_data.  Write timing is the late write + forward
// contract (pending_wr_en/pending_wr_addr/forward_active), matching
// ecc643_secded_delay6_wg.
(* keep_hierarchy = "yes" *)
module p1h_sdf4_wp10 #(
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
    output reg  [3:0] out_valid,
    output reg  [3:0] out_last,
    output wire [8*WO-1:0] out_data,
    output wire mem_rd_en,
    output wire [AW-1:0] mem_rd_addr,
    output wire mem_pending_wr_en,
    output wire [AW-1:0] mem_pending_wr_addr,
    output wire mem_forward_active,
    output wire [4*DW-1:0] mem_wr_data,
    input  wire [4*DW-1:0] mem_rd_data
);
import fft_wg_pkg::*;
localparam integer MW = W + 1;
localparam integer SYNC_READ = (DEPTH == 128);

wire ctl_ivq, ctl_rd_en, ctl_wv, ctl_ws, ctl_wp, ctl_pwe, ctl_fwd, emit_raw, raw_is_last;
wire [AW-1:0] ctl_rd_addr, ctl_wa, ctl_pwa;
wire [7:0] out_count;
pfft_sdf_control_wp4r #(.DEPTH(DEPTH), .AW(AW)) u_control (
    .clk(clk), .rst(rst), .in_valid(in_valid), .input_valid_q(ctl_ivq),
    .rd_en(ctl_rd_en), .rd_addr(ctl_rd_addr),
    .work_valid(ctl_wv), .work_second(ctl_ws), .work_primed(ctl_wp),
    .work_addr(ctl_wa), .pending_wr_en(ctl_pwe), .pending_wr_addr(ctl_pwa),
    .forward_active(ctl_fwd), .emit_raw(emit_raw), .raw_is_last(raw_is_last),
    .out_count(out_count));
wire [7:0] out_count_next = out_count + emit_raw;

assign mem_rd_en = ctl_rd_en;
assign mem_rd_addr = ctl_rd_addr;
assign mem_pending_wr_en = ctl_pwe;
assign mem_pending_wr_addr = ctl_pwa;
assign mem_forward_active = ctl_fwd;

wire [9:0] group_phi_now, group_phi_next;
wp4r_hier_pfft_exchange_phi_wg #(.STAGE(STAGE)) u_phi_now (
    .index({out_count, 2'd0}), .exponent(group_phi_now));
wp4r_hier_pfft_exchange_phi_wg #(.STAGE(STAGE)) u_phi_next (
    .index({out_count_next, 2'd0}), .exponent(group_phi_next));

wire signed [TW_W-1:0] tw_re [0:3];
wire signed [TW_W-1:0] tw_im [0:3];
generate
if (STAGE >= 2 && STAGE <= 7) begin : g_shared_rom
    wire signed [TW_W-1:0] coeff_re, coeff_im;
    pfft_hom_rom_wp4r #(.STAGE(STAGE)) u_rom (
        .clk(clk), .exponent(group_phi_next), .coeff_re(coeff_re), .coeff_im(coeff_im));
    for (genvar rl = 0; rl < 4; rl = rl + 1) begin : g_broadcast
        assign tw_re[rl] = coeff_re;
        assign tw_im[rl] = coeff_im;
    end
end else begin : g_no_shared_rom
    for (genvar rl = 0; rl < 4; rl = rl + 1) begin : g_zero
        assign tw_re[rl] = {TW_W{1'b0}};
        assign tw_im[rl] = {TW_W{1'b0}};
    end
end
endgenerate

wire [3:0] lane_valid;
wire signed [WO-1:0] rot_re_input [0:3];
wire signed [WO-1:0] rot_im_input [0:3];
reg signed [WO-1:0] lane_re [0:3];
reg signed [WO-1:0] lane_im [0:3];
reg ml1, ml2, ml3;
integer oi;
always @(posedge clk) begin
    if (rst) begin
        out_valid <= 4'b0;
        out_last <= 4'b0;
        ml1 <= 1'b0;
        ml2 <= 1'b0;
        ml3 <= 1'b0;
        for (oi = 0; oi < 4; oi = oi + 1) begin
            lane_re[oi] <= {WO{1'b0}};
            lane_im[oi] <= {WO{1'b0}};
        end
    end else begin
        ml1 <= emit_raw && raw_is_last;
        ml2 <= ml1;
        ml3 <= ml2;
        for (oi = 0; oi < 4; oi = oi + 1) begin
            out_valid[oi] <= lane_valid[oi];
            out_last[oi] <= lane_valid[oi] ? ml3 : 1'b0;
            if (lane_valid[oi]) begin
                lane_re[oi] <= rot_re_input[oi];
                lane_im[oi] <= rot_im_input[oi];
            end
        end
    end
end

generate
for (genvar lane = 0; lane < 4; lane = lane + 1) begin : g_lane
    localparam integer TRIVIAL_ONLY = (STAGE == 1);
    localparam [1:0] LANE_INDEX = lane;
    reg signed [W-1:0] in_re_q, in_im_q, req_re, req_im;
    always @(posedge clk) begin
        if (in_valid) begin
            in_re_q <= in_data[(8-2*lane)*W-1 -: W];
            in_im_q <= in_data[(7-2*lane)*W-1 -: W];
        end
        if (SYNC_READ && ctl_ivq) begin
            req_re <= in_re_q;
            req_im <= in_im_q;
        end
    end
    wire signed [W-1:0] work_re = SYNC_READ ? req_re : in_re_q;
    wire signed [W-1:0] work_im = SYNC_READ ? req_im : in_im_q;
    wire signed [MW-1:0] memory_re = mem_rd_data[(lane+1)*DW-1 -: MW];
    wire signed [MW-1:0] memory_im = mem_rd_data[lane*DW+MW-1 -: MW];
    wire signed [MW-1:0] work_ext_re = work_re;
    wire signed [MW-1:0] work_ext_im = work_im;
    wire signed [MW-1:0] sum_re = memory_re + work_ext_re;
    wire signed [MW-1:0] sum_im = memory_im + work_ext_im;
    wire signed [MW-1:0] dif_re = memory_re - work_ext_re;
    wire signed [MW-1:0] dif_im = memory_im - work_ext_im;
    wire signed [MW-1:0] store_re = ctl_ws ? dif_re : work_ext_re;
    wire signed [MW-1:0] store_im = ctl_ws ? dif_im : work_ext_im;
    assign mem_wr_data[(lane+1)*DW-1 -: DW] = {store_re, store_im};
    wire signed [MW-1:0] emit_re = ctl_ws ? sum_re : memory_re;
    wire signed [MW-1:0] emit_im = ctl_ws ? sum_im : memory_im;
    wire signed [WO-1:0] emit_wo_re = emit_re;
    wire signed [WO-1:0] emit_wo_im = emit_im;
    wire signed [WO-1:0] rotated_re, rotated_im;

    if (!TRIVIAL_ONLY) begin : g_multiply
        wire signed [TW_W-1:0] private_re, private_im;
        wire [9:0] lane_phi_next;
        assign private_re = tw_re[lane];
        assign private_im = tw_im[lane];
        assign lane_phi_next = 10'd0;
        wp4r_hier_fft_complex_mul_wg_pipe #(.WI(MW), .WO(WO)) u_rotator (
            .clk(clk), .rst(rst), .valid_in(emit_raw),
            .in_re(emit_re), .in_im(emit_im),
            .coeff_re(private_re), .coeff_im(private_im),
            .valid_out(lane_valid[lane]), .out_re(rotated_re), .out_im(rotated_im));
        wire _unused = lane_phi_next[0];
    end else begin : g_trivial
        wire signed [WO-1:0] rot_re, rot_im;
        wp4r_hier_fft_complex_rotate_trivial_wg #(.W(WO)) u_rotator (
            .in_re(emit_wo_re), .in_im(emit_wo_im), .exponent(group_phi_now),
            .out_re(rot_re), .out_im(rot_im));
        reg v1, v2, v3;
        reg signed [WO-1:0] r1, i1, r2, i2, r3, i3;
        always @(posedge clk) begin
            if (rst) begin
                v1 <= 1'b0;
                v2 <= 1'b0;
                v3 <= 1'b0;
                r1 <= {WO{1'b0}};
                i1 <= {WO{1'b0}};
                r2 <= {WO{1'b0}};
                i2 <= {WO{1'b0}};
                r3 <= {WO{1'b0}};
                i3 <= {WO{1'b0}};
            end else begin
                v1 <= emit_raw;
                if (emit_raw) begin
                    r1 <= rot_re;
                    i1 <= rot_im;
                end
                v2 <= v1;
                r2 <= r1;
                i2 <= i1;
                v3 <= v2;
                r3 <= r2;
                i3 <= i2;
            end
        end
        assign lane_valid[lane] = v3;
        assign rotated_re = r3;
        assign rotated_im = i3;
        wire _unused_tw = ^{tw_re[lane], tw_im[lane]};
    end
    assign rot_re_input[lane] = rotated_re;
    assign rot_im_input[lane] = rotated_im;
end
endgenerate

generate
for (genvar lane = 0; lane < 4; lane = lane + 1) begin : g_outputs
    assign out_data[(8-2*lane)*WO-1 -: WO] = lane_re[lane];
    assign out_data[(7-2*lane)*WO-1 -: WO] = lane_im[lane];
end
endgenerate
wire _unused = ^{in_last, ctl_wp, ctl_wv, ctl_wa};
endmodule


// ---------------------------------------------------------------------------
// Hybrid stage: three replicas of everything but the delay storage plus one
// shared SECDED memory holding all four lane words at one address.  All six
// storage signals are 2-of-3 voted inside tmr_secded_shared_mem_wp10; its
// vote_mismatch joins the stage's TMR mismatch the same way the output
// voters do.
(* keep_hierarchy = "yes" *)
module p1h_hybrid_stage_wp10 #(
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
wire rep_rd_en [0:2];
wire [AW-1:0] rep_rd_addr [0:2];
wire rep_pwe [0:2];
wire [AW-1:0] rep_pwa [0:2];
wire rep_fwd [0:2];
wire [4*DW-1:0] rep_wr_data [0:2];
wire [4*DW-1:0] sh_rd_data;
wire mem_mm;
generate
for (genvar r = 0; r < 3; r = r + 1) begin : g_replica
    (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
    p1h_sdf4_wp10 #(.STAGE(STAGE), .DEPTH(DEPTH), .W(W), .WO(WO),
                    .AW(AW), .DW(DW)) u_copy (
        .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
        .in_data(in_data), .out_valid(rep_valid[r]), .out_last(rep_last[r]),
        .out_data(rep_data[r]),
        .mem_rd_en(rep_rd_en[r]), .mem_rd_addr(rep_rd_addr[r]),
        .mem_pending_wr_en(rep_pwe[r]), .mem_pending_wr_addr(rep_pwa[r]),
        .mem_forward_active(rep_fwd[r]),
        .mem_wr_data(rep_wr_data[r]), .mem_rd_data(sh_rd_data));
end
endgenerate
(* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
tmr_secded_shared_mem_wp10 #(.WORDS(4), .DATA_W(DW), .DEPTH(DEPTH),
                             .AW(AW)) u_shmem (
    .clk(clk), .rst(rst),
    .a_rd_en(rep_rd_en[0]), .a_rd_addr(rep_rd_addr[0]),
    .a_pending_wr_en(rep_pwe[0]), .a_pending_wr_addr(rep_pwa[0]),
    .a_forward_active(rep_fwd[0]), .a_wr_data(rep_wr_data[0]),
    .b_rd_en(rep_rd_en[1]), .b_rd_addr(rep_rd_addr[1]),
    .b_pending_wr_en(rep_pwe[1]), .b_pending_wr_addr(rep_pwa[1]),
    .b_forward_active(rep_fwd[1]), .b_wr_data(rep_wr_data[1]),
    .c_rd_en(rep_rd_en[2]), .c_rd_addr(rep_rd_addr[2]),
    .c_pending_wr_en(rep_pwe[2]), .c_pending_wr_addr(rep_pwa[2]),
    .c_forward_active(rep_fwd[2]), .c_wr_data(rep_wr_data[2]),
    .rd_data(sh_rd_data), .vote_mismatch(mem_mm));

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
tmr_stage_flag_wg #(.POINTS(7)) u_flag (
    .clk(clk), .rst(rst), .voted_valid(voted_valid[0]),
    .mismatch_points({mem_mm, data_diff, valid_diff, last_diff}),
    .flag(stage_mismatch));
assign out_valid = voted_valid[0];
assign out_last = voted_last[0];
endmodule


// ---------------------------------------------------------------------------
(* keep_hierarchy = "yes" *)
module p1h_pfft_hybrid_pipeline_wp10 (
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
        p1h_hybrid_stage_wp10 #(.STAGE(s), .DEPTH(DEPTH), .W(W), .WO(WO))
        u_stage (
            .clk(clk), .rst(rst), .in_valid(vin), .in_last(lin),
            .in_data(din), .out_valid(vout), .out_last(lout),
            .out_data(dout), .stage_mismatch(stage_mismatch[s-1]));
    end else begin : g_tmr
        pfft_real_tmr_stage_wg #(.STAGE(s), .DEPTH(DEPTH), .W(W), .WO(WO))
        u_stage (
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


module top_p2hr_pfft_hybrid (
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
p1h_pfft_hybrid_pipeline_wp10 u_pipeline (
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
    .in_data({in0_re, in0_im, in1_re, in1_im, in2_re, in2_im, in3_re, in3_im}),
    .out_valid(out_valid), .out_last(out_last),
    .out_data({out0_re, out0_im, out1_re, out1_im, out2_re, out2_im, out3_re, out3_im}),
    .stage_mismatch(stage_mismatch));
endmodule
