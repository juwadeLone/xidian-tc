`timescale 1ns/1ps

module top_p1_pfft_ecc #(
    parameter integer ARITH_THRESHOLD = 3,
    parameter integer EXACT_CHECK = 1
)(
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
    output wire [9:0] win_second,
    output wire [9:0] up_detected,
    output wire [9:0] up_corrected,
    output wire [9:0] up_uncorrectable,
    output wire [29:0] up_error_location
);
wire [9:0] stage_mismatch;
p1_pfft_ecc_pipeline_wp4r u_pipeline (
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
    .in_data({in0_re, in0_im, in1_re, in1_im, in2_re, in2_im, in3_re, in3_im}),
    .out_valid(out_valid), .out_last(out_last),
    .out_data({out0_re, out0_im, out1_re, out1_im, out2_re, out2_im, out3_re, out3_im}),
    .win_second(win_second), .up_detected(up_detected),
    .up_corrected(up_corrected), .up_uncorrectable(up_uncorrectable),
    .up_error_location(up_error_location), .stage_mismatch(stage_mismatch));
wire _unused = ^{ARITH_THRESHOLD[0], EXACT_CHECK[0]};
endmodule
