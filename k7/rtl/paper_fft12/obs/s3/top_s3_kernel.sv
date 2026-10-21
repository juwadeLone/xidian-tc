// S3 obs top (pg): thin wrapper exposing the engine kernel slot.
`timescale 1ns/1ps

module top_s3_subfft_ecc #(
    parameter integer ARITH_THRESHOLD = 3
)(
    input  wire clk, input wire rst, input wire in_valid, input wire in_last,
    input  wire signed [15:0] in0_re, in0_im, in1_re, in1_im, in2_re, in2_im, in3_re, in3_im,
    output wire out_valid, output wire out_last,
    output wire signed [21:0] out0_re, out0_im, out1_re, out1_im, out2_re, out2_im, out3_re, out3_im,
    output wire [7:0]  win_second,
    output wire [7:0]  up_detected,
    output wire [7:0]  up_corrected,
    output wire [7:0]  up_uncorrectable,
    output wire [23:0] up_error_location
);
    s3_subfft_ecc_core_wg #(.ARITH_THRESHOLD(ARITH_THRESHOLD)) u (
        clk, rst, in_valid, in_last,
        in0_re, in0_im, in1_re, in1_im, in2_re, in2_im, in3_re, in3_im,
        out_valid, out_last,
        out0_re, out0_im, out1_re, out1_im, out2_re, out2_im, out3_re, out3_im,
        win_second, up_detected, up_corrected, up_uncorrectable, up_error_location
    );
endmodule
