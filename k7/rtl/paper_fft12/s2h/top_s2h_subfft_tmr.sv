`timescale 1ns/1ps
module top_s2h_subfft_tmr(
    input wire clk, rst, in_valid, in_last,
    input wire signed [fft_wg_pkg::IN_W-1:0] in0_re, in0_im, in1_re, in1_im,
    input wire signed [fft_wg_pkg::IN_W-1:0] in2_re, in2_im, in3_re, in3_im,
    output wire out_valid, out_last,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out0_re, out0_im, out1_re, out1_im,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out2_re, out2_im, out3_re, out3_im,
    output wire [9:0] stage_mismatch
);
subfft_tmr_pipeline_wg #(.SHARED_MEM(1)) u_pipeline(
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
    .in_data({in0_re,in0_im,in1_re,in1_im,in2_re,in2_im,in3_re,in3_im}),
    .out_valid(out_valid), .out_last(out_last),
    .out_data({out0_re,out0_im,out1_re,out1_im,out2_re,out2_im,out3_re,out3_im}),
    .stage_mismatch(stage_mismatch));
endmodule
