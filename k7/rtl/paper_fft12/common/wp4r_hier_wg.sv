`timescale 1ns/1ps

// WP4-R batch 2 (task 11.2): hard hierarchy wrappers. Each wrapper carries
// keep_hierarchy, instantiates exactly one pre-existing module and passes
// parameters and ports through unchanged, so the post-route utilization
// report gives every functional unit its own row. The inner instance is
// always named u. fft_memory_v5 / fft_complex_mul_wg_pipe /
// fft_complex_rotate_trivial_wg resolve to the p0/ or s0/ family variant,
// whichever is compiled for the architecture; both have identical
// parameters and ports.

(* keep_hierarchy = "yes" *)
module wp4r_hier_fft_memory_v5 #(
    parameter integer WIDTH = 42,
    parameter integer DEPTH = 128,
    parameter integer ADDR_W = (DEPTH <= 1) ? 1 : $clog2(DEPTH)
)(
    input  wire              clk,
    input  wire              rd_en,
    input  wire [ADDR_W-1:0] rd_addr,
    output wire [WIDTH-1:0]  rd_data,
    input  wire              wr_en,
    input  wire [ADDR_W-1:0] wr_addr,
    input  wire [WIDTH-1:0]  wr_data
);
fft_memory_v5 #(.WIDTH(WIDTH), .DEPTH(DEPTH), .ADDR_W(ADDR_W)) u (
    .clk(clk), .rd_en(rd_en), .rd_addr(rd_addr), .rd_data(rd_data),
    .wr_en(wr_en), .wr_addr(wr_addr), .wr_data(wr_data));
endmodule


(* keep_hierarchy = "yes" *)
module wp4r_hier_secded_encode_w #(
    parameter integer DATA_W = 44,
    parameter integer PARITY = 6,
    parameter integer CODE_W = DATA_W + PARITY + 1
)(
    input  wire [DATA_W-1:0] data,
    output wire [CODE_W-1:0] codeword
);
secded_encode_w #(.DATA_W(DATA_W), .PARITY(PARITY), .CODE_W(CODE_W)) u (
    .data(data), .codeword(codeword));
endmodule


(* keep_hierarchy = "yes" *)
module wp4r_hier_secded_decode_w #(
    parameter integer DATA_W = 44,
    parameter integer PARITY = 6,
    parameter integer CODE_W = DATA_W + PARITY + 1
)(
    input  wire [CODE_W-1:0] codeword,
    output wire [DATA_W-1:0] data,
    output wire              detected,
    output wire              corrected
);
secded_decode_w #(.DATA_W(DATA_W), .PARITY(PARITY), .CODE_W(CODE_W)) u (
    .codeword(codeword), .data(data), .detected(detected),
    .corrected(corrected));
endmodule


(* keep_hierarchy = "yes" *)
module wp4r_hier_tmr_vote_w #(
    parameter integer W = 1
)(
    input  wire [W-1:0] a,
    input  wire [W-1:0] b,
    input  wire [W-1:0] c,
    output wire [W-1:0] y,
    output wire         mismatch
);
tmr_vote_w #(.W(W)) u (.a(a), .b(b), .c(c), .y(y), .mismatch(mismatch));
endmodule


(* keep_hierarchy = "yes" *)
module wp4r_hier_fft_complex_mul_wg_pipe #(
    parameter integer WI = fft_wg_pkg::S1_W + 1,
    parameter integer WO = fft_wg_pkg::S1_WO
)(
    input  wire                             clk,
    input  wire                             rst,
    input  wire                             valid_in,
    input  wire signed [WI-1:0]             in_re,
    input  wire signed [WI-1:0]             in_im,
    input  wire signed [fft_wg_pkg::TW_W-1:0] coeff_re,
    input  wire signed [fft_wg_pkg::TW_W-1:0] coeff_im,
    output wire                             valid_out,
    output wire signed [WO-1:0]             out_re,
    output wire signed [WO-1:0]             out_im
);
fft_complex_mul_wg_pipe #(.WI(WI), .WO(WO)) u (
    .clk(clk), .rst(rst), .valid_in(valid_in), .in_re(in_re), .in_im(in_im),
    .coeff_re(coeff_re), .coeff_im(coeff_im),
    .valid_out(valid_out), .out_re(out_re), .out_im(out_im));
endmodule


(* keep_hierarchy = "yes" *)
module wp4r_hier_fft_complex_rotate_trivial_wg #(
    parameter integer W = fft_wg_pkg::S1_WO
)(
    input  wire signed [W-1:0] in_re,
    input  wire signed [W-1:0] in_im,
    input  wire        [9:0]   exponent,
    output wire signed [W-1:0] out_re,
    output wire signed [W-1:0] out_im
);
fft_complex_rotate_trivial_wg #(.W(W)) u (
    .in_re(in_re), .in_im(in_im), .exponent(exponent),
    .out_re(out_re), .out_im(out_im));
endmodule


(* keep_hierarchy = "yes" *)
module wp4r_hier_pfft_exchange_phi_wg #(
    parameter integer STAGE = 1
)(
    input  wire [9:0] index,
    output wire [9:0] exponent
);
pfft_exchange_phi_wg #(.STAGE(STAGE)) u (.index(index), .exponent(exponent));
endmodule


(* keep_hierarchy = "yes" *)
module wp4r_hier_subfft_stage9_wg #(
    parameter integer W = fft_wg_pkg::S9_W,
    parameter integer WO = fft_wg_pkg::S9_WO
)(
    input  wire              clk,
    input  wire              rst,
    input  wire              in_valid,
    input  wire              in_last,
    input  wire signed [W-1:0]  in0_re,
    input  wire signed [W-1:0]  in0_im,
    input  wire signed [W-1:0]  in1_re,
    input  wire signed [W-1:0]  in1_im,
    input  wire signed [W-1:0]  in2_re,
    input  wire signed [W-1:0]  in2_im,
    input  wire signed [W-1:0]  in3_re,
    input  wire signed [W-1:0]  in3_im,
    output wire                 out_valid,
    output wire                 out_last,
    output wire signed [WO-1:0] out0_re,
    output wire signed [WO-1:0] out0_im,
    output wire signed [WO-1:0] out1_re,
    output wire signed [WO-1:0] out1_im,
    output wire signed [WO-1:0] out2_re,
    output wire signed [WO-1:0] out2_im,
    output wire signed [WO-1:0] out3_re,
    output wire signed [WO-1:0] out3_im
);
subfft_stage9_wg #(.W(W), .WO(WO)) u (
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
    .in0_re(in0_re), .in0_im(in0_im), .in1_re(in1_re), .in1_im(in1_im),
    .in2_re(in2_re), .in2_im(in2_im), .in3_re(in3_re), .in3_im(in3_im),
    .out_valid(out_valid), .out_last(out_last),
    .out0_re(out0_re), .out0_im(out0_im), .out1_re(out1_re), .out1_im(out1_im),
    .out2_re(out2_re), .out2_im(out2_im), .out3_re(out3_re), .out3_im(out3_im));
endmodule


(* keep_hierarchy = "yes" *)
module wp4r_hier_subfft_stage10_wg #(
    parameter integer W = fft_wg_pkg::S10_W,
    parameter integer WO = fft_wg_pkg::S10_WO
)(
    input  wire              clk,
    input  wire              rst,
    input  wire              in_valid,
    input  wire              in_last,
    input  wire signed [W-1:0]  in0_re,
    input  wire signed [W-1:0]  in0_im,
    input  wire signed [W-1:0]  in1_re,
    input  wire signed [W-1:0]  in1_im,
    input  wire signed [W-1:0]  in2_re,
    input  wire signed [W-1:0]  in2_im,
    input  wire signed [W-1:0]  in3_re,
    input  wire signed [W-1:0]  in3_im,
    output wire                 out_valid,
    output wire                 out_last,
    output wire signed [WO-1:0] out0_re,
    output wire signed [WO-1:0] out0_im,
    output wire signed [WO-1:0] out1_re,
    output wire signed [WO-1:0] out1_im,
    output wire signed [WO-1:0] out2_re,
    output wire signed [WO-1:0] out2_im,
    output wire signed [WO-1:0] out3_re,
    output wire signed [WO-1:0] out3_im
);
subfft_stage10_wg #(.W(W), .WO(WO)) u (
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
    .in0_re(in0_re), .in0_im(in0_im), .in1_re(in1_re), .in1_im(in1_im),
    .in2_re(in2_re), .in2_im(in2_im), .in3_re(in3_re), .in3_im(in3_im),
    .out_valid(out_valid), .out_last(out_last),
    .out0_re(out0_re), .out0_im(out0_im), .out1_re(out1_re), .out1_im(out1_im),
    .out2_re(out2_re), .out2_im(out2_im), .out3_re(out3_re), .out3_im(out3_im));
endmodule
