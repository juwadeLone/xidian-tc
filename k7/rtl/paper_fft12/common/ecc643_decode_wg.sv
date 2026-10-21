`timescale 1ns/1ps

(* keep_hierarchy = "yes" *)
module ecc643_encode_wp4r #(
    parameter integer W = 16
)(
    input  wire [8*W-1:0] in_data,
    output wire signed [W+1:0] c4_re,
    output wire signed [W+1:0] c4_im,
    output wire signed [W+1:0] c5_re,
    output wire signed [W+1:0] c5_im
);
wire signed [W+1:0] f0r = $signed(in_data[8*W-1 -: W]);
wire signed [W+1:0] f0i = $signed(in_data[7*W-1 -: W]);
wire signed [W+1:0] f1r = $signed(in_data[6*W-1 -: W]);
wire signed [W+1:0] f1i = $signed(in_data[5*W-1 -: W]);
wire signed [W+1:0] f2r = $signed(in_data[4*W-1 -: W]);
wire signed [W+1:0] f2i = $signed(in_data[3*W-1 -: W]);
wire signed [W+1:0] f3r = $signed(in_data[2*W-1 -: W]);
wire signed [W+1:0] f3i = $signed(in_data[W-1:0]);
assign c4_re = (f0r + f1r) + (f2r + f3r);
assign c4_im = (f0i + f1i) + (f2i + f3i);
assign c5_re = (f0r - f1i) + (-f2r + f3i);
assign c5_im = (f0i + f1r) + (-f2i - f3r);
endmodule


(* keep_hierarchy = "yes" *)
module ecc643_flags_wp4r (
    input  wire clk,
    input  wire rst,
    input  wire valid,
    input  wire det_in,
    input  wire found,
    input  wire [2:0] loc_in,
    input  wire sum_in,
    output reg  det,
    output reg  cor,
    output reg  unc,
    output reg  [2:0] loc,
    output reg  beat_sum
);
always @(posedge clk) begin
    if (rst) begin
        det <= 1'b0;
        cor <= 1'b0;
        unc <= 1'b0;
        loc <= 3'd0;
        beat_sum <= 1'b0;
    end else begin
        det <= valid && det_in;
        cor <= valid && found;
        unc <= valid && det_in && !found;
        loc <= (valid && found) ? loc_in : 3'd0;
        beat_sum <= valid && sum_in;
    end
end
endmodule


(* keep_hierarchy = "yes" *)
module ecc643_decode_wg #(
    parameter integer W = 16,
    parameter integer WO = 18,
    parameter integer FBW = W + 20,
    parameter integer KW = W + 22,
    parameter integer RW = W + 23
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire in_sum,
    input  wire signed [FBW-1:0] f0_re, f0_im,
    input  wire signed [FBW-1:0] f1_re, f1_im,
    input  wire signed [FBW-1:0] f2_re, f2_im,
    input  wire signed [FBW-1:0] f3_re, f3_im,
    input  wire signed [KW-1:0] c4_re, c4_im,
    input  wire signed [KW-1:0] c5_re, c5_im,
    output reg  out_valid,
    output reg  out_last,
    output reg  [8*WO-1:0] out_data,
    output wire det,
    output wire cor,
    output wire unc,
    output wire [2:0] loc,
    output wire beat_sum,
    output reg  signed [RW-1:0] sy0r,
    output reg  signed [RW-1:0] sy0i,
    output reg  signed [RW-1:0] sy1r,
    output reg  signed [RW-1:0] sy1i
);
import fft_wg_pkg::*;
reg res_valid, res_last, res_sum;
reg signed [FBW-1:0] q0r, q0i, q1r, q1i, q2r, q2i, q3r, q3i;

wire signed [RW-1:0] x0r = f0_re;
wire signed [RW-1:0] x0i = f0_im;
wire signed [RW-1:0] x1r = f1_re;
wire signed [RW-1:0] x1i = f1_im;
wire signed [RW-1:0] x2r = f2_re;
wire signed [RW-1:0] x2i = f2_im;
wire signed [RW-1:0] x3r = f3_re;
wire signed [RW-1:0] x3i = f3_im;
wire signed [RW-1:0] x4r = c4_re;
wire signed [RW-1:0] x4i = c4_im;
wire signed [RW-1:0] x5r = c5_re;
wire signed [RW-1:0] x5i = c5_im;
wire signed [RW-1:0] sy0r_w = x4r - ((x0r + x1r) + (x2r + x3r));
wire signed [RW-1:0] sy0i_w = x4i - ((x0i + x1i) + (x2i + x3i));
wire signed [RW-1:0] sy1r_w = x5r - ((x0r - x1i) + (-x2r + x3i));
wire signed [RW-1:0] sy1i_w = x5i - ((x0i + x1r) + (-x2i - x3r));

wire s0nz = (sy0r != 0) || (sy0i != 0);
wire s1nz = (sy1r != 0) || (sy1i != 0);
wire det_w = s0nz || s1nz;
wire [5:0] fl;
assign fl[0] = s0nz && (sy1r == sy0r) && (sy1i == sy0i);
assign fl[1] = s0nz && (sy1r == -sy0i) && (sy1i == sy0r);
assign fl[2] = s0nz && (sy1r == -sy0r) && (sy1i == -sy0i);
assign fl[3] = s0nz && (sy1r == sy0i) && (sy1i == -sy0r);
assign fl[4] = s0nz && !s1nz;
assign fl[5] = !s0nz && s1nz;
wire [2:0] fl_count = fl[0] + fl[1] + fl[2] + fl[3] + fl[4] + fl[5];
wire found = det_w && (fl_count == 3'd1);
wire [2:0] loc_w = fl[0] ? 3'd0 : fl[1] ? 3'd1 : fl[2] ? 3'd2 :
                   fl[3] ? 3'd3 : fl[4] ? 3'd4 : 3'd5;

wire signed [RW-1:0] w0r = q0r;
wire signed [RW-1:0] w0i = q0i;
wire signed [RW-1:0] w1r = q1r;
wire signed [RW-1:0] w1i = q1i;
wire signed [RW-1:0] w2r = q2r;
wire signed [RW-1:0] w2i = q2i;
wire signed [RW-1:0] w3r = q3r;
wire signed [RW-1:0] w3i = q3i;
wire signed [RW-1:0] corr0r = (found && (loc_w == 3'd0)) ? (w0r + sy0r) : w0r;
wire signed [RW-1:0] corr0i = (found && (loc_w == 3'd0)) ? (w0i + sy0i) : w0i;
wire signed [RW-1:0] corr1r = (found && (loc_w == 3'd1)) ? (w1r + sy0r) : w1r;
wire signed [RW-1:0] corr1i = (found && (loc_w == 3'd1)) ? (w1i + sy0i) : w1i;
wire signed [RW-1:0] corr2r = (found && (loc_w == 3'd2)) ? (w2r + sy0r) : w2r;
wire signed [RW-1:0] corr2i = (found && (loc_w == 3'd2)) ? (w2i + sy0i) : w2i;
wire signed [RW-1:0] corr3r = (found && (loc_w == 3'd3)) ? (w3r + sy0r) : w3r;
wire signed [RW-1:0] corr3i = (found && (loc_w == 3'd3)) ? (w3i + sy0i) : w3i;
wire signed [WO-1:0] o0r = corr0r >>> TW_FRAC;
wire signed [WO-1:0] o0i = corr0i >>> TW_FRAC;
wire signed [WO-1:0] o1r = corr1r >>> TW_FRAC;
wire signed [WO-1:0] o1i = corr1i >>> TW_FRAC;
wire signed [WO-1:0] o2r = corr2r >>> TW_FRAC;
wire signed [WO-1:0] o2i = corr2i >>> TW_FRAC;
wire signed [WO-1:0] o3r = corr3r >>> TW_FRAC;
wire signed [WO-1:0] o3i = corr3i >>> TW_FRAC;

always @(posedge clk) begin
    if (rst) begin
        res_valid <= 1'b0;
        res_last <= 1'b0;
        res_sum <= 1'b0;
        out_valid <= 1'b0;
        out_last <= 1'b0;
        out_data <= {(8*WO){1'b0}};
        q0r <= {FBW{1'b0}};
        q0i <= {FBW{1'b0}};
        q1r <= {FBW{1'b0}};
        q1i <= {FBW{1'b0}};
        q2r <= {FBW{1'b0}};
        q2i <= {FBW{1'b0}};
        q3r <= {FBW{1'b0}};
        q3i <= {FBW{1'b0}};
        sy0r <= {RW{1'b0}};
        sy0i <= {RW{1'b0}};
        sy1r <= {RW{1'b0}};
        sy1i <= {RW{1'b0}};
    end else begin
        res_valid <= in_valid;
        res_last <= in_last;
        res_sum <= in_sum;
        if (in_valid) begin
            q0r <= f0_re;
            q0i <= f0_im;
            q1r <= f1_re;
            q1i <= f1_im;
            q2r <= f2_re;
            q2i <= f2_im;
            q3r <= f3_re;
            q3i <= f3_im;
            sy0r <= sy0r_w;
            sy0i <= sy0i_w;
            sy1r <= sy1r_w;
            sy1i <= sy1i_w;
        end
        out_valid <= res_valid;
        out_last <= res_valid ? res_last : 1'b0;
        if (res_valid)
            out_data <= {o0r, o0i, o1r, o1i, o2r, o2i, o3r, o3i};
    end
end

ecc643_flags_wp4r u_flags (
    .clk(clk), .rst(rst), .valid(res_valid), .det_in(det_w), .found(found),
    .loc_in(loc_w), .sum_in(res_sum), .det(det), .cor(cor), .unc(unc),
    .loc(loc), .beat_sum(beat_sum));
endmodule
