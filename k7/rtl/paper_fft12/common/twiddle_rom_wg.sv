`timescale 1ns/1ps

// One twiddle bank: a 1024 x 43 SECDED codeword ROM in block RAM with up to
// two synchronous read ports. Coefficient on cycle t = table[address presented
// on cycle t-1]. Table content: sim/gen_twiddle_rom_wg.py.
(* keep_hierarchy = "yes" *)
module twiddle_bank_wg #(
    parameter integer PORTS = 2
)(
    input  wire clk,
    input  wire [9:0] addr_a,
    input  wire [9:0] addr_b,                    // ignored when PORTS == 1
    output wire signed [fft_wg_pkg::TW_W-1:0] coeff_a_re,
    output wire signed [fft_wg_pkg::TW_W-1:0] coeff_a_im,
    output wire signed [fft_wg_pkg::TW_W-1:0] coeff_b_re,
    output wire signed [fft_wg_pkg::TW_W-1:0] coeff_b_im,
    output wire det_a,
    output wire cor_a,
    output wire det_b,
    output wire cor_b
);
import fft_wg_pkg::*;

`ifdef XILINX_SIMULATOR
localparam MEM_FILE = "rtl/paper_fft12/common/twiddle_rom_1024_secded.mem";
`else
localparam MEM_FILE = "twiddle_rom_1024_secded.mem";
`endif

(* rom_style = "block" *) reg [42:0] rom [0:1023];
initial $readmemh(MEM_FILE, rom);

reg [42:0] code_a;
always @(posedge clk)
    code_a <= rom[addr_a];

wire [35:0] data_a;
secded_decode_w #(.DATA_W(36), .PARITY(6)) dec_a (
    .codeword(code_a), .data(data_a), .detected(det_a), .corrected(cor_a));
assign coeff_a_re = data_a[2*TW_W-1:TW_W];
assign coeff_a_im = data_a[TW_W-1:0];

generate
if (PORTS == 2) begin : g_b
    reg [42:0] code_b;
    always @(posedge clk)
        code_b <= rom[addr_b];

    wire [35:0] data_b;
    secded_decode_w #(.DATA_W(36), .PARITY(6)) dec_b (
        .codeword(code_b), .data(data_b), .detected(det_b), .corrected(cor_b));
    assign coeff_b_re = data_b[2*TW_W-1:TW_W];
    assign coeff_b_im = data_b[TW_W-1:0];
end else begin : g_no_b
    assign coeff_b_re = {TW_W{1'b0}};
    assign coeff_b_im = {TW_W{1'b0}};
    assign det_b = 1'b0;
    assign cor_b = 1'b0;
end
endgenerate
endmodule
