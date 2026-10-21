`timescale 1ns/1ps

(* keep_hierarchy = "yes" *)
module ecc643_secded_delay6_wg #(
    parameter integer W = 16,
    parameter integer DEPTH = 128,
    parameter integer AW = (DEPTH <= 1) ? 1 : $clog2(DEPTH),
    parameter integer DF = 2*(W+1),
    parameter integer DC = 2*(W+3),
    parameter integer CF = DF + 7,
    parameter integer CC = DC + 7,
    parameter integer MEM_W = 4*CF + 2*CC
)(
    input  wire clk,
    input  wire rd_en,
    input  wire [AW-1:0] rd_addr,
    input  wire pending_wr_en,
    input  wire [AW-1:0] pending_wr_addr,
    input  wire [4*DF-1:0] func_wr_data,
    input  wire [2*DC-1:0] check_wr_data,
    input  wire forward_active,
    output wire [4*DF-1:0] func_rd_data,
    output wire [2*DC-1:0] check_rd_data,
    output wire [MEM_W-1:0] read_code
);
reg [4*DF-1:0] pending_func;
reg [2*DC-1:0] pending_check;
always @(posedge clk) begin
    pending_func <= func_wr_data;
    pending_check <= check_wr_data;
end

wire [MEM_W-1:0] write_code;
generate
for (genvar lane = 0; lane < 4; lane = lane + 1) begin : g_func
    wire [DF-1:0] decoded;
    wp4r_hier_secded_encode_w #(.DATA_W(DF), .PARITY(6), .CODE_W(CF)) u_encode (
        .data(pending_func[(lane+1)*DF-1 -: DF]),
        .codeword(write_code[(lane+1)*CF-1 -: CF]));
    wp4r_hier_secded_decode_w #(.DATA_W(DF), .PARITY(6), .CODE_W(CF)) u_decode (
        .codeword(read_code[(lane+1)*CF-1 -: CF]), .data(decoded),
        .detected(), .corrected());
    assign func_rd_data[(lane+1)*DF-1 -: DF] =
        forward_active ? pending_func[(lane+1)*DF-1 -: DF] : decoded;
end
for (genvar lane = 0; lane < 2; lane = lane + 1) begin : g_check
    wire [DC-1:0] decoded;
    wp4r_hier_secded_encode_w #(.DATA_W(DC), .PARITY(6), .CODE_W(CC)) u_encode (
        .data(pending_check[(lane+1)*DC-1 -: DC]),
        .codeword(write_code[4*CF+(lane+1)*CC-1 -: CC]));
    wp4r_hier_secded_decode_w #(.DATA_W(DC), .PARITY(6), .CODE_W(CC)) u_decode (
        .codeword(read_code[4*CF+(lane+1)*CC-1 -: CC]), .data(decoded),
        .detected(), .corrected());
    assign check_rd_data[(lane+1)*DC-1 -: DC] =
        forward_active ? pending_check[(lane+1)*DC-1 -: DC] : decoded;
end
endgenerate

wp4r_hier_fft_memory_v5 #(.WIDTH(MEM_W), .DEPTH(DEPTH), .ADDR_W(AW)) u_mem (
    .clk(clk), .rd_en(rd_en), .rd_addr(rd_addr), .rd_data(read_code),
    .wr_en(pending_wr_en), .wr_addr(pending_wr_addr), .wr_data(write_code));
endmodule
