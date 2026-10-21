`timescale 1ns/1ps

(* keep_hierarchy = "yes" *)
module subfft_stage8_cross_wg #(parameter integer W=fft_wg_pkg::S8_WO)(
    input wire clk, rst, in_valid, in_last,
    input wire signed [W-1:0] in0_re, in0_im, in1_re, in1_im,
    input wire signed [W-1:0] in2_re, in2_im, in3_re, in3_im,
    input wire signed [fft_wg_pkg::TW_W-1:0] t1_re, t1_im, t2_re, t2_im, t3_re, t3_im,
    output wire [9:0] exp1, exp2, exp3,
    output wire out_valid, out_last,
    output wire signed [W-1:0] out0_re, out0_im, out1_re, out1_im,
    output wire signed [W-1:0] out2_re, out2_im, out3_re, out3_im
);
reg [7:0] beat_count;
function automatic [7:0] bit_reverse8(input [7:0] value);
integer i; begin for(i=0;i<8;i=i+1) bit_reverse8[i]=value[7-i]; end
endfunction
wire [7:0] beat_count_next = rst ? 8'd0 : (in_valid ? beat_count + 1'b1 : beat_count);
wire [7:0] k_next = bit_reverse8(beat_count_next);
assign exp1 = {2'b00, k_next};
assign exp2 = ({2'b00, k_next} << 1);
assign exp3 = ({2'b00, k_next} + ({2'b00, k_next} << 1));
wire mul_v1, mul_v2, mul_v3;
wire signed [W-1:0] c1_re, c1_im, c2_re, c2_im, c3_re, c3_im;
fft_complex_mul_wg_pipe #(.WI(W), .WO(W)) m1(
    .clk(clk), .rst(rst), .valid_in(in_valid), .in_re(in1_re), .in_im(in1_im),
    .coeff_re(t1_re), .coeff_im(t1_im), .valid_out(mul_v1), .out_re(c1_re), .out_im(c1_im));
fft_complex_mul_wg_pipe #(.WI(W), .WO(W)) m2(
    .clk(clk), .rst(rst), .valid_in(in_valid), .in_re(in2_re), .in_im(in2_im),
    .coeff_re(t2_re), .coeff_im(t2_im), .valid_out(mul_v2), .out_re(c2_re), .out_im(c2_im));
fft_complex_mul_wg_pipe #(.WI(W), .WO(W)) m3(
    .clk(clk), .rst(rst), .valid_in(in_valid), .in_re(in3_re), .in_im(in3_im),
    .coeff_re(t3_re), .coeff_im(t3_im), .valid_out(mul_v3), .out_re(c3_re), .out_im(c3_im));

// Lane0 and last-flag: exact 3-stage mirror of the CMUL pipe.
reg bv1, bv2, bv3, bl1, bl2, bl3;
reg signed [W-1:0] b0r1, b0i1, b0r2, b0i2, b0r3, b0i3;
always @(posedge clk) begin
    if (rst) begin
        beat_count <= 8'd0;
        bv1 <= 1'b0; bv2 <= 1'b0; bv3 <= 1'b0;
        bl1 <= 1'b0; bl2 <= 1'b0; bl3 <= 1'b0;
        b0r1 <= {W{1'b0}}; b0i1 <= {W{1'b0}};
        b0r2 <= {W{1'b0}}; b0i2 <= {W{1'b0}};
        b0r3 <= {W{1'b0}}; b0i3 <= {W{1'b0}};
    end else begin
        beat_count <= beat_count_next;
        bv1 <= in_valid; bl1 <= in_valid && in_last;
        if (in_valid) begin b0r1 <= in0_re; b0i1 <= in0_im; end
        bv2 <= bv1; bl2 <= bl1; b0r2 <= b0r1; b0i2 <= b0i1;
        bv3 <= bv2; bl3 <= bl2; b0r3 <= b0r2; b0i3 <= b0i2;
    end
end
assign out_valid = mul_v1;
assign out_last = bl3;
assign out0_re = b0r3; assign out0_im = b0i3;
assign out1_re = c1_re; assign out1_im = c1_im;
assign out2_re = c2_re; assign out2_im = c2_im;
assign out3_re = c3_re; assign out3_im = c3_im;
wire _unused_mul = mul_v2 ^ mul_v3 ^ bv3;
endmodule
