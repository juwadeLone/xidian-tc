`timescale 1ns/1ps

(* keep_hierarchy = "yes" *)
module p1_ecc_lane_wp4r #(
    parameter integer STAGE = 1,
    parameter integer DEPTH = 128,
    parameter integer W = 16,
    parameter integer WO = 18,
    parameter integer AW = (DEPTH <= 1) ? 1 : $clog2(DEPTH),
    parameter integer DW = 2*(W+1),
    parameter integer BW = W + 20
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire input_valid_q,
    input  wire signed [W-1:0] in_re,
    input  wire signed [W-1:0] in_im,
    input  wire work_second,
    input  wire emit_raw,
    input  wire [2:0] rot_ce,
    input  wire [9:0] trivial_exponent,
    input  wire signed [fft_wg_pkg::TW_W-1:0] tw_re,
    input  wire signed [fft_wg_pkg::TW_W-1:0] tw_im,
    input  wire [DW-1:0] mem_rd_data,
    output wire [DW-1:0] mem_wr_data,
    output wire signed [W:0] difference_re,
    output wire signed [W:0] difference_im,
    output wire signed [BW-1:0] boundary_re,
    output wire signed [BW-1:0] boundary_im
);
import fft_wg_pkg::*;
localparam integer MW = W + 1;
localparam integer SYNC_READ = (DEPTH == 128);
reg signed [W-1:0] in_re_q, in_im_q, req_re, req_im;
always @(posedge clk) begin
    if (in_valid) begin
        in_re_q <= in_re;
        in_im_q <= in_im;
    end
    if (SYNC_READ && input_valid_q) begin
        req_re <= in_re_q;
        req_im <= in_im_q;
    end
end
wire signed [W-1:0] work_re = SYNC_READ ? req_re : in_re_q;
wire signed [W-1:0] work_im = SYNC_READ ? req_im : in_im_q;
wire signed [MW-1:0] work_ext_re = work_re;
wire signed [MW-1:0] work_ext_im = work_im;
wire signed [MW-1:0] memory_re = mem_rd_data[DW-1 -: MW];
wire signed [MW-1:0] memory_im = mem_rd_data[MW-1:0];
wire signed [MW-1:0] sum_re = memory_re + work_ext_re;
wire signed [MW-1:0] sum_im = memory_im + work_ext_im;
assign difference_re = memory_re - work_ext_re;
assign difference_im = memory_im - work_ext_im;
wire signed [MW-1:0] store_re = work_second ? difference_re : work_ext_re;
wire signed [MW-1:0] store_im = work_second ? difference_im : work_ext_im;
assign mem_wr_data = {store_re, store_im};
wire signed [MW-1:0] emit_re = work_second ? sum_re : memory_re;
wire signed [MW-1:0] emit_im = work_second ? sum_im : memory_im;
wire signed [WO-1:0] emit_wo_re = emit_re;
wire signed [WO-1:0] emit_wo_im = emit_im;

generate
if (STAGE == 1) begin : g_trivial
    wire signed [WO-1:0] rot_re, rot_im;
    wp4r_hier_fft_complex_rotate_trivial_wg #(.W(WO)) u_rotator (
        .in_re(emit_wo_re), .in_im(emit_wo_im), .exponent(trivial_exponent),
        .out_re(rot_re), .out_im(rot_im));
    wire signed [BW-1:0] fmt_re = rot_re <<< TW_FRAC;
    wire signed [BW-1:0] fmt_im = rot_im <<< TW_FRAC;
    reg signed [BW-1:0] b1r, b1i, b2r, b2i, b3r, b3i;
    always @(posedge clk) begin
        if (rot_ce[0]) begin b1r <= fmt_re; b1i <= fmt_im; end
        if (rot_ce[1]) begin b2r <= b1r; b2i <= b1i; end
        if (rot_ce[2]) begin b3r <= b2r; b3i <= b2i; end
    end
    assign boundary_re = b3r;
    assign boundary_im = b3i;
    wire _unused = ^{tw_re, tw_im, emit_raw};
end else begin : g_multiply
    fft_complex_mul_full_wp4r #(.WI(MW), .FW(BW)) u_rotator (
        .clk(clk), .rst(rst), .ce(rot_ce),
        .in_re(emit_re), .in_im(emit_im), .coeff_re(tw_re), .coeff_im(tw_im),
        .out_re(boundary_re), .out_im(boundary_im));
    wire _unused = ^{trivial_exponent, emit_wo_re, emit_wo_im};
end
endgenerate
endmodule


(* keep_hierarchy = "yes" *)
module p1_ecc_stage_wp4r #(
    parameter integer STAGE = 1,
    parameter integer DEPTH = 128,
    parameter integer W = fft_wg_pkg::S1_W,
    parameter integer WO = fft_wg_pkg::S1_WO
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire [8*W-1:0] in_data,
    output wire out_valid,
    output wire out_last,
    output wire [8*WO-1:0] out_data,
    output wire det,
    output wire cor,
    output wire unc,
    output wire [2:0] loc,
    output wire beat_sum
);
import fft_wg_pkg::*;
localparam integer CWI = W + 2;
localparam integer CWO = WO + 2;
localparam integer FBW = W + 20;
localparam integer KW = W + 22;
localparam integer DF = 2*(W+1);
localparam integer DC = 2*(W+3);
localparam integer AW = (DEPTH <= 1) ? 1 : $clog2(DEPTH);

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
wire [9:0] group_phi_now, group_phi_next;
wp4r_hier_pfft_exchange_phi_wg #(.STAGE(STAGE)) u_phi_now (
    .index({out_count, 2'd0}), .exponent(group_phi_now));
wp4r_hier_pfft_exchange_phi_wg #(.STAGE(STAGE)) u_phi_next (
    .index({out_count_next, 2'd0}), .exponent(group_phi_next));

wire signed [TW_W-1:0] tw_re, tw_im;
generate
if (STAGE >= 2 && STAGE <= 7) begin : g_rom
    pfft_hom_rom_wp4r #(.STAGE(STAGE)) u_rom (
        .clk(clk), .exponent(group_phi_next), .coeff_re(tw_re), .coeff_im(tw_im));
end else begin : g_no_rom
    assign tw_re = {TW_W{1'b0}};
    assign tw_im = {TW_W{1'b0}};
end
endgenerate

wire signed [CWI-1:0] enc4r, enc4i, enc5r, enc5i;
ecc643_encode_wp4r #(.W(W)) u_encode (
    .in_data(in_data), .c4_re(enc4r), .c4_im(enc4i),
    .c5_re(enc5r), .c5_im(enc5i));

wire [4*DF-1:0] func_wr_data, func_rd_data;
wire [2*DC-1:0] check_wr_data, check_rd_data;
ecc643_secded_delay6_wg #(.W(W), .DEPTH(DEPTH), .AW(AW)) u_memory (
    .clk(clk), .rd_en(ctl_rd_en), .rd_addr(ctl_rd_addr),
    .pending_wr_en(ctl_pwe), .pending_wr_addr(ctl_pwa),
    .func_wr_data(func_wr_data), .check_wr_data(check_wr_data),
    .forward_active(ctl_fwd), .func_rd_data(func_rd_data),
    .check_rd_data(check_rd_data), .read_code());

reg rv1, rv2, rv3, rl1, rl2, rl3, rs1, rs2, rs3;
always @(posedge clk) begin
    if (rst) begin
        rv1 <= 1'b0;
        rv2 <= 1'b0;
        rv3 <= 1'b0;
        rl1 <= 1'b0;
        rl2 <= 1'b0;
        rl3 <= 1'b0;
        rs1 <= 1'b0;
        rs2 <= 1'b0;
        rs3 <= 1'b0;
    end else begin
        rv1 <= emit_raw;
        rv2 <= rv1;
        rv3 <= rv2;
        rl1 <= emit_raw && raw_is_last;
        rl2 <= rl1;
        rl3 <= rl2;
        rs1 <= emit_raw && ctl_ws;
        rs2 <= rs1;
        rs3 <= rs2;
    end
end
wire [2:0] rot_ce = {rv2, rv1, emit_raw};

wire signed [FBW-1:0] bnd_re [0:3];
wire signed [FBW-1:0] bnd_im [0:3];
wire signed [KW-1:0] bnd_c4r, bnd_c4i, bnd_c5r, bnd_c5i;
generate
for (genvar lane = 0; lane < 6; lane = lane + 1) begin : g_lane
    localparam integer LW = (lane < 4) ? W : CWI;
    localparam integer LWO = (lane < 4) ? WO : CWO;
    localparam integer LDW = 2*(LW+1);
    localparam integer LBW = LW + 20;
    wire signed [LW-1:0] lane_in_re, lane_in_im;
    wire [LDW-1:0] lane_mem_rd, lane_mem_wr;
    wire signed [LBW-1:0] lane_bnd_re, lane_bnd_im;
    if (lane < 4) begin : g_func
        assign lane_in_re = in_data[(8-2*lane)*W-1 -: W];
        assign lane_in_im = in_data[(7-2*lane)*W-1 -: W];
        assign lane_mem_rd = func_rd_data[(lane+1)*DF-1 -: DF];
        assign func_wr_data[(lane+1)*DF-1 -: DF] = lane_mem_wr;
        assign bnd_re[lane] = lane_bnd_re;
        assign bnd_im[lane] = lane_bnd_im;
    end else begin : g_check
        assign lane_in_re = (lane == 4) ? enc4r : enc5r;
        assign lane_in_im = (lane == 4) ? enc4i : enc5i;
        assign lane_mem_rd = check_rd_data[(lane-3)*DC-1 -: DC];
        assign check_wr_data[(lane-3)*DC-1 -: DC] = lane_mem_wr;
        if (lane == 4) begin : g_c4
            assign bnd_c4r = lane_bnd_re;
            assign bnd_c4i = lane_bnd_im;
        end else begin : g_c5
            assign bnd_c5r = lane_bnd_re;
            assign bnd_c5i = lane_bnd_im;
        end
    end
    p1_ecc_lane_wp4r #(.STAGE(STAGE), .DEPTH(DEPTH), .W(LW), .WO(LWO),
                        .AW(AW), .DW(LDW), .BW(LBW)) u_lane (
        .clk(clk), .rst(rst), .in_valid(in_valid), .input_valid_q(ctl_ivq),
        .in_re(lane_in_re), .in_im(lane_in_im),
        .work_second(ctl_ws), .emit_raw(emit_raw), .rot_ce(rot_ce),
        .trivial_exponent(group_phi_now), .tw_re(tw_re), .tw_im(tw_im),
        .mem_rd_data(lane_mem_rd), .mem_wr_data(lane_mem_wr),
        .difference_re(), .difference_im(),
        .boundary_re(lane_bnd_re), .boundary_im(lane_bnd_im));
end
endgenerate

ecc643_decode_wg #(.W(W), .WO(WO), .FBW(FBW), .KW(KW)) u_decode (
    .clk(clk), .rst(rst), .in_valid(rv3), .in_last(rl3), .in_sum(rs3),
    .f0_re(bnd_re[0]), .f0_im(bnd_im[0]),
    .f1_re(bnd_re[1]), .f1_im(bnd_im[1]),
    .f2_re(bnd_re[2]), .f2_im(bnd_im[2]),
    .f3_re(bnd_re[3]), .f3_im(bnd_im[3]),
    .c4_re(bnd_c4r), .c4_im(bnd_c4i),
    .c5_re(bnd_c5r), .c5_im(bnd_c5i),
    .out_valid(out_valid), .out_last(out_last), .out_data(out_data),
    .det(det), .cor(cor), .unc(unc), .loc(loc), .beat_sum(beat_sum),
    .sy0r(), .sy0i(), .sy1r(), .sy1i());
wire _unused = ^{in_last, ctl_wv, ctl_wp, ctl_wa, group_phi_next[0]};
endmodule


(* keep_hierarchy = "yes" *)
module p1_pfft_ecc_pipeline_wp4r (
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire [8*fft_wg_pkg::IN_W-1:0] in_data,
    output wire out_valid,
    output wire out_last,
    output wire [8*fft_wg_pkg::OUT_W-1:0] out_data,
    output wire [9:0] win_second,
    output wire [9:0] up_detected,
    output wire [9:0] up_corrected,
    output wire [9:0] up_uncorrectable,
    output wire [29:0] up_error_location,
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
    if (s <= 7) begin : g_ecc
        p1_ecc_stage_wp4r #(.STAGE(s), .DEPTH(DEPTH), .W(W), .WO(WO)) u_stage (
            .clk(clk), .rst(rst), .in_valid(vin), .in_last(lin), .in_data(din),
            .out_valid(vout), .out_last(lout), .out_data(dout),
            .det(up_detected[s-1]), .cor(up_corrected[s-1]),
            .unc(up_uncorrectable[s-1]), .loc(up_error_location[3*s-1 -: 3]),
            .beat_sum(win_second[s-1]));
        assign stage_mismatch[s-1] = 1'b0;
    end else begin : g_tmr
        wire mismatch;
        pfft_real_tmr_stage_wg #(.STAGE(s), .DEPTH(DEPTH), .W(W), .WO(WO)) u_stage (
            .clk(clk), .rst(rst), .in_valid(vin), .in_last(lin), .in_data(din),
            .out_valid(vout), .out_last(lout), .out_data(dout),
            .stage_mismatch(mismatch));
        assign stage_mismatch[s-1] = mismatch;
        assign up_detected[s-1] = mismatch;
        assign up_corrected[s-1] = mismatch;
        assign up_uncorrectable[s-1] = 1'b0;
        assign up_error_location[3*s-1 -: 3] = 3'd0;
        assign win_second[s-1] = 1'b0;
    end
end
endgenerate
assign out_valid = g_stage[10].vout;
assign out_last = g_stage[10].lout;
assign out_data = g_stage[10].dout;
endmodule
