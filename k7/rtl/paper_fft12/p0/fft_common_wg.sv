`timescale 1ns/1ps

// Growing-width variant (paper_fft12): shared arithmetic/memory primitives
// for the P0 no-exchange pipeline. Widths come from fft_wg_pkg; no stage
// shifts its result. Same multiply contract as the s0 common file.

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
