`timescale 1ns/1ps

(* keep_hierarchy = "yes" *)
module fft_complex_mul_full_wp4r #(
    parameter integer WI = fft_wg_pkg::S1_W + 1,
    parameter integer FW = WI + fft_wg_pkg::TW_W + 1
)(
    input  wire clk,
    input  wire rst,
    input  wire [2:0] ce,
    input  wire signed [WI-1:0] in_re,
    input  wire signed [WI-1:0] in_im,
    input  wire signed [fft_wg_pkg::TW_W-1:0] coeff_re,
    input  wire signed [fft_wg_pkg::TW_W-1:0] coeff_im,
    output reg  signed [FW-1:0] out_re,
    output reg  signed [FW-1:0] out_im
);
import fft_wg_pkg::*;
reg signed [WI-1:0] re_s1, im_s1;
reg signed [TW_W-1:0] cr_s1, ci_s1;
always @(posedge clk) begin
    if (ce[0]) begin
        re_s1 <= in_re;
        im_s1 <= in_im;
        cr_s1 <= coeff_re;
        ci_s1 <= coeff_im;
    end
end

reg signed [WI+TW_W-1:0] rr_s2, ii_s2, ri_s2, ir_s2;
always @(posedge clk) begin
    if (ce[1]) begin
        rr_s2 <= re_s1 * cr_s1;
        ii_s2 <= im_s1 * ci_s1;
        ri_s2 <= re_s1 * ci_s1;
        ir_s2 <= im_s1 * cr_s1;
    end
end

wire signed [FW-1:0] acc_re = rr_s2 - ii_s2;
wire signed [FW-1:0] acc_im = ri_s2 + ir_s2;
always @(posedge clk) begin
    if (rst) begin
        out_re <= {FW{1'b0}};
        out_im <= {FW{1'b0}};
    end else if (ce[2]) begin
        out_re <= acc_re;
        out_im <= acc_im;
    end
end
endmodule
