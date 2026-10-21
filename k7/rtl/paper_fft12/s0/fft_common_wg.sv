`timescale 1ns/1ps

// Growing-width variant (paper_fft12): shared arithmetic/memory primitives.
// Widths come from fft_wg_pkg; no stage shifts its result.
//
// Multiply contract: WI-bit signed x TW_W-bit coeff -> product WI+TW_W-1 bits,
// complex difference/sum WI+TW_W+1 bits, >>> TW_FRAC -> low WO bits kept.

module fft_memory_v5 #(
    parameter integer WIDTH = 42,
    parameter integer DEPTH = 128,
    parameter integer ADDR_W = (DEPTH <= 1) ? 1 : $clog2(DEPTH)
)(
    input  wire                   clk,
    input  wire                   rd_en,
    input  wire [ADDR_W-1:0]      rd_addr,
    output wire [WIDTH-1:0]       rd_data,
    input  wire                   wr_en,
    input  wire [ADDR_W-1:0]      wr_addr,
    input  wire [WIDTH-1:0]       wr_data
);
generate
if (DEPTH == 128) begin : g_sync_distributed
    (* ram_style = "distributed" *) reg [WIDTH-1:0] mem [0:DEPTH-1];
    reg [WIDTH-1:0] read_register;
    always @(posedge clk) begin
        if (rd_en)
            read_register <= mem[rd_addr];
        if (wr_en)
            mem[wr_addr] <= wr_data;
    end
    assign rd_data = read_register;
end else begin : g_async_distributed
    (* ram_style = "distributed" *) reg [WIDTH-1:0] mem [0:DEPTH-1];
    assign rd_data = mem[rd_addr];
    always @(posedge clk) begin
        if (wr_en)
            mem[wr_addr] <= wr_data;
    end
    wire _unused_rd_en = rd_en;
end
endgenerate
endmodule


// 3-stage pipelined complex multiply by a twiddle, WI-bit in, WO-bit out.
// Latency = 3 cycles from valid_in to valid_out.
module fft_complex_mul_wg_pipe #(
    parameter integer WI = fft_wg_pkg::S1_W + 1,
    parameter integer WO = fft_wg_pkg::S1_WO
)(
    input  wire                  clk,
    input  wire                  rst,
    input  wire                  valid_in,
    input  wire signed [WI-1:0]  in_re,
    input  wire signed [WI-1:0]  in_im,
    input  wire signed [fft_wg_pkg::TW_W-1:0] coeff_re,
    input  wire signed [fft_wg_pkg::TW_W-1:0] coeff_im,
    output reg                   valid_out,
    output reg  signed [WO-1:0]  out_re,
    output reg  signed [WO-1:0]  out_im
);
import fft_wg_pkg::*;

reg valid_s1;
reg signed [WI-1:0]   re_s1, im_s1;
reg signed [TW_W-1:0] cr_s1, ci_s1;
always @(posedge clk) begin
    if (rst) begin
        valid_s1 <= 1'b0;
    end else begin
        valid_s1 <= valid_in;
        if (valid_in) begin
            re_s1 <= in_re;
            im_s1 <= in_im;
            cr_s1 <= coeff_re;
            ci_s1 <= coeff_im;
        end
    end
end

reg valid_s2;
reg signed [WI+TW_W-2:0] rr_s2, ii_s2, ri_s2, ir_s2;
always @(posedge clk) begin
    if (rst) begin
        valid_s2 <= 1'b0;
    end else begin
        valid_s2 <= valid_s1;
        if (valid_s1) begin
            rr_s2 <= re_s1 * cr_s1;
            ii_s2 <= im_s1 * ci_s1;
            ri_s2 <= re_s1 * ci_s1;
            ir_s2 <= im_s1 * cr_s1;
        end
    end
end

wire signed [WI+TW_W:0] acc_re = rr_s2 - ii_s2;
wire signed [WI+TW_W:0] acc_im = ri_s2 + ir_s2;
always @(posedge clk) begin
    if (rst) begin
        valid_out <= 1'b0;
        out_re <= {WO{1'b0}};
        out_im <= {WO{1'b0}};
    end else begin
        valid_out <= valid_s2;
        if (valid_s2) begin
            out_re <= acc_re >>> TW_FRAC;
            out_im <= acc_im >>> TW_FRAC;
        end
    end
end
endmodule


// Exact rotations for the four trivial 1024-point twiddles.
module fft_complex_rotate_trivial_wg #(
    parameter integer W = fft_wg_pkg::S1_WO
)(
    input  wire signed [W-1:0] in_re,
    input  wire signed [W-1:0] in_im,
    input  wire        [9:0]   exponent,
    output reg  signed [W-1:0] out_re,
    output reg  signed [W-1:0] out_im
);
always @* begin
    case (exponent)
        10'd0:   begin out_re =  in_re; out_im =  in_im; end
        10'd256: begin out_re =  in_im; out_im = -in_re; end
        10'd512: begin out_re = -in_re; out_im = -in_im; end
        10'd768: begin out_re = -in_im; out_im =  in_re; end
        default: begin out_re =  in_re; out_im =  in_im; end
    endcase
end
endmodule


// Canonical DIF Stage 9 for SubFFT merge: butterfly -> WO = W+1 outputs.
module subfft_stage9_wg #(
    parameter integer W = fft_wg_pkg::S9_W,
    parameter integer WO = fft_wg_pkg::S9_WO
)(
    input wire clk, input wire rst, input wire in_valid, input wire in_last,
    input wire signed [W-1:0] in0_re, in0_im, in1_re, in1_im,
    input wire signed [W-1:0] in2_re, in2_im, in3_re, in3_im,
    output reg out_valid, output reg out_last,
    output reg signed [WO-1:0] out0_re, out0_im, out1_re, out1_im,
    output reg signed [WO-1:0] out2_re, out2_im, out3_re, out3_im
);
// +1 bit butterflies
wire signed [W:0] s02r = in0_re + in2_re, s02i = in0_im + in2_im;
wire signed [W:0] d02r = in0_re - in2_re, d02i = in0_im - in2_im;
wire signed [W:0] s13r = in1_re + in3_re, s13i = in1_im + in3_im;
wire signed [W:0] d13r = in1_re - in3_re, d13i = in1_im - in3_im;
reg v1, l1;
reg signed [WO-1:0] a0r, a0i, a1r, a1i, a2r, a2i, a3r, a3i;
always @(posedge clk) begin
    if (rst) begin
        v1 <= 1'b0; l1 <= 1'b0;
        out_valid <= 1'b0; out_last <= 1'b0;
    end else begin
        // Butterfly capture; a3 gets the -j rotation.
        v1 <= in_valid;
        l1 <= in_last;
        if (in_valid) begin
            a0r <= s02r; a0i <= s02i;
            a2r <= d02r; a2i <= d02i;
            a1r <= s13r; a1i <= s13i;
            a3r <= d13i; a3i <= -d13r;
        end
        out_valid <= v1;
        out_last <= l1;
        if (v1) begin
            out0_re <= a0r; out0_im <= a0i;
            out1_re <= a1r; out1_im <= a1i;
            out2_re <= a2r; out2_im <= a2i;
            out3_re <= a3r; out3_im <= a3i;
        end
    end
end
endmodule


// Canonical DIF Stage 10 for SubFFT merge: W in -> WO = W+1 out.
module subfft_stage10_wg #(
    parameter integer W = fft_wg_pkg::S10_W,
    parameter integer WO = fft_wg_pkg::S10_WO
)(
    input wire clk, input wire rst, input wire in_valid, input wire in_last,
    input wire signed [W-1:0] in0_re, in0_im, in1_re, in1_im,
    input wire signed [W-1:0] in2_re, in2_im, in3_re, in3_im,
    output reg out_valid, output reg out_last,
    output reg signed [WO-1:0] out0_re, out0_im, out1_re, out1_im,
    output reg signed [WO-1:0] out2_re, out2_im, out3_re, out3_im
);
wire signed [W:0] s01r=in0_re+in1_re,s01i=in0_im+in1_im,d01r=in0_re-in1_re,d01i=in0_im-in1_im;
wire signed [W:0] s23r=in2_re+in3_re,s23i=in2_im+in3_im,d23r=in2_re-in3_re,d23i=in2_im-in3_im;
reg v1,l1; reg signed [WO-1:0] q0r,q0i,q1r,q1i,q2r,q2i,q3r,q3i;
always @(posedge clk) begin
    if(rst) begin v1<=0;l1<=0;out_valid<=0;out_last<=0;end
    else begin
        v1<=in_valid;l1<=in_last;out_valid<=v1;out_last<=l1;
        if(in_valid) begin
            q0r<=s01r; q0i<=s01i;
            q2r<=d01r; q2i<=d01i;
            q1r<=s23r; q1i<=s23i;
            q3r<=d23r; q3i<=d23i;
        end
        if(v1) begin out0_re<=q0r;out0_im<=q0i;out1_re<=q1r;out1_im<=q1i;out2_re<=q2r;out2_im<=q2i;out3_re<=q3r;out3_im<=q3i;end
    end
end
endmodule
