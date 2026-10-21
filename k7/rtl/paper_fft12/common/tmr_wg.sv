`timescale 1ns/1ps

module tmr_vote_w #(parameter integer W=1)(
    input wire [W-1:0] a, b, c,
    output wire [W-1:0] y,
    output wire mismatch
);
assign y = (a & b) | (a & c) | (b & c);
assign mismatch = (|(a ^ b)) | (|(a ^ c));
endmodule

(* keep_hierarchy = "yes" *)
module tmr_stage_flag_wg #(parameter integer POINTS=1)(
    input wire clk, rst,
    input wire voted_valid,
    input wire [POINTS-1:0] mismatch_points,
    output reg flag
);
always @(posedge clk) begin
    if (rst) flag <= 1'b0;
    else flag <= voted_valid && (|mismatch_points);
end
endmodule
