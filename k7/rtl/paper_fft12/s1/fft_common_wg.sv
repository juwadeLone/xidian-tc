`timescale 1ns/1ps

// Width-growing variant (paper_fft12): shared arithmetic/memory primitives.
// 18-bit Q2.16 twiddles; data grows +1 bit per stage (no >>>1 scaling).
//
// Multiply contract: in signed W x 18-bit coeff -> product W+17 signed,
// complex accumulation W+19 signed, >>>16 -> low W bits kept
// (register wrap-around, identical to assigning the shifted value into a
// W-bit signed register).

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


module fft_complex_mul_wg #(
    parameter integer W = 13
)(
    input  wire signed [W-1:0]   in_re,
    input  wire signed [W-1:0]   in_im,
    input  wire        [9:0]     exponent,
    output wire signed [W-1:0]   out_re,
    output wire signed [W-1:0]   out_im
);
wire signed [17:0]    coeff_re, coeff_im;
wire signed [W+16:0]  product_rr = in_re * coeff_re;
wire signed [W+16:0]  product_ii = in_im * coeff_im;
wire signed [W+16:0]  product_ri = in_re * coeff_im;
wire signed [W+16:0]  product_ir = in_im * coeff_re;
wire signed [W+18:0]  wide_re = $signed(product_rr) - $signed(product_ii);
wire signed [W+18:0]  wide_im = $signed(product_ri) + $signed(product_ir);
wire signed [W+18:0]  shifted_re = wide_re >>> 16;
wire signed [W+18:0]  shifted_im = wide_im >>> 16;
twiddle_rom_1024 u_rom(.exponent(exponent), .coeff_re(coeff_re), .coeff_im(coeff_im));
assign out_re = shifted_re[W-1:0];
assign out_im = shifted_im[W-1:0];
endmodule


// 3-stage pipelined Q16 complex multiply, same structure/latency as
// fft_complex_mul_q28_pipe. Latency = 3 cycles from valid_in to valid_out.
module fft_complex_mul_wg_pipe #(
    parameter integer W = 13
)(
    input  wire                clk,
    input  wire                rst,
    input  wire                valid_in,
    input  wire signed [W-1:0] in_re,
    input  wire signed [W-1:0] in_im,
    input  wire        [9:0]   exponent,
    output reg                 valid_out,
    output reg                 ovf,
    output reg  signed [W-1:0] out_re,
    output reg  signed [W-1:0] out_im
);
wire signed [17:0] coeff_re, coeff_im;
twiddle_rom_1024 u_rom(.exponent(exponent), .coeff_re(coeff_re), .coeff_im(coeff_im));

reg valid_s1;
reg signed [W-1:0] re_s1, im_s1;
reg signed [17:0]  cr_s1, ci_s1;
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
reg signed [W+16:0] rr_s2, ii_s2, ri_s2, ir_s2;
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

// Truncation overflow: (rr-ii)>>>16 is W+17 bits before the W-bit assignment.
// If the dropped bits above bit W-1 are not all sign-extension, the stored
// result wrapped -- flag it so the boundary decoder can mask this lineage.
wire signed [W+16:0] dif_re_w = rr_s2 - ii_s2;
wire signed [W+16:0] dif_im_w = ri_s2 + ir_s2;
wire signed [W+16:0] shf_re_w = dif_re_w >>> 16;
wire signed [W+16:0] shf_im_w = dif_im_w >>> 16;
wire ovf_re_c = (shf_re_w[W+16:W] != {(W+1){shf_re_w[W-1]}});
wire ovf_im_c = (shf_im_w[W+16:W] != {(W+1){shf_im_w[W-1]}});

always @(posedge clk) begin
    if (rst) begin
        valid_out <= 1'b0;
        ovf <= 1'b0;
        out_re <= {W{1'b0}};
        out_im <= {W{1'b0}};
    end else begin
        valid_out <= valid_s2;
        ovf <= valid_s2 && (ovf_re_c || ovf_im_c);
        if (valid_s2) begin
            out_re <= (($signed(rr_s2) - $signed(ii_s2)) >>> 16);
            out_im <= (($signed(ri_s2) + $signed(ir_s2)) >>> 16);
        end
    end
end
endmodule


// Exact rotations for the four trivial 1024-point twiddles.
module fft_complex_rotate_trivial_wg #(
    parameter integer W = 13
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


// Canonical DIF Stage 9 for SubFFT merge: cross twiddles on lanes 1..3
// (18-bit pipe, W in -> W out), then butterfly +1 bit -> W+1 outputs.
module subfft_stage9_wg #(
    parameter integer W = 20,
    parameter integer HOLD = 0,
    parameter integer WO = (HOLD ? W : W + 1)
)(
    input wire clk, input wire rst, input wire in_valid, input wire in_last,
    input wire signed [W-1:0] in0_re, in0_im, in1_re, in1_im,
    input wire signed [W-1:0] in2_re, in2_im, in3_re, in3_im,
    output reg out_valid, output reg out_last,
    output reg signed [WO-1:0] out0_re, out0_im, out1_re, out1_im,
    output reg signed [WO-1:0] out2_re, out2_im, out3_re, out3_im
);
reg [7:0] beat_count;
function automatic [7:0] bit_reverse8(input [7:0] value);
integer i; begin for(i=0;i<8;i=i+1) bit_reverse8[i]=value[7-i]; end
endfunction
wire [7:0] k = bit_reverse8(beat_count);
wire [9:0] exp1 = {2'b00, k};
wire [9:0] exp2 = ({2'b00, k} << 1);
wire [9:0] exp3 = ({2'b00, k} + ({2'b00, k} << 1));

wire mul_v1, mul_v2, mul_v3;
wire signed [W-1:0] c1_re, c1_im, c2_re, c2_im, c3_re, c3_im;
fft_complex_mul_wg_pipe #(.W(W)) m1(
    .clk(clk), .rst(rst), .valid_in(in_valid),
    .in_re(in1_re), .in_im(in1_im), .exponent(exp1),
    .valid_out(mul_v1), .out_re(c1_re), .out_im(c1_im)
);
fft_complex_mul_wg_pipe #(.W(W)) m2(
    .clk(clk), .rst(rst), .valid_in(in_valid),
    .in_re(in2_re), .in_im(in2_im), .exponent(exp2),
    .valid_out(mul_v2), .out_re(c2_re), .out_im(c2_im)
);
fft_complex_mul_wg_pipe #(.W(W)) m3(
    .clk(clk), .rst(rst), .valid_in(in_valid),
    .in_re(in3_re), .in_im(in3_im), .exponent(exp3),
    .valid_out(mul_v3), .out_re(c3_re), .out_im(c3_im)
);

// Lane0 and last-flag: exact 3-stage mirror of the CMUL pipe.
reg bv1, bv2, bv3, bl1, bl2, bl3;
reg signed [W-1:0] b0r1, b0i1, b0r2, b0i2, b0r3, b0i3;
wire pipe_valid = mul_v1;
wire signed [W-1:0] c0_re = b0r3;
wire signed [W-1:0] c0_im = b0i3;

// +1 bit butterflies
wire signed [W:0] s02r = c0_re + c2_re, s02i = c0_im + c2_im;
wire signed [W:0] d02r = c0_re - c2_re, d02i = c0_im - c2_im;
wire signed [W:0] s13r = c1_re + c3_re, s13i = c1_im + c3_im;
wire signed [W:0] d13r = c1_re - c3_re, d13i = c1_im - c3_im;

reg v1, l1;
reg signed [WO-1:0] a0r, a0i, a1r, a1i, a2r, a2i, a3r, a3i;
always @(posedge clk) begin
    if (rst) begin
        beat_count <= 8'd0;
        bv1 <= 1'b0; bv2 <= 1'b0; bv3 <= 1'b0;
        bl1 <= 1'b0; bl2 <= 1'b0; bl3 <= 1'b0;
        b0r1 <= {W{1'b0}}; b0i1 <= {W{1'b0}};
        b0r2 <= {W{1'b0}}; b0i2 <= {W{1'b0}};
        b0r3 <= {W{1'b0}}; b0i3 <= {W{1'b0}};
        v1 <= 1'b0; l1 <= 1'b0;
        out_valid <= 1'b0; out_last <= 1'b0;
    end else begin
        if (in_valid)
            beat_count <= beat_count + 1'b1;

        bv1 <= in_valid;
        bl1 <= in_valid && in_last;
        if (in_valid) begin
            b0r1 <= in0_re;
            b0i1 <= in0_im;
        end
        bv2 <= bv1; bl2 <= bl1; b0r2 <= b0r1; b0i2 <= b0i1;
        bv3 <= bv2; bl3 <= bl2; b0r3 <= b0r2; b0i3 <= b0i2;

        // Butterfly capture: growing stages keep +1 bit; held stages emit
        // (x +/- y)>>>1; a3 gets the -j rotation (negate-then-shift).
        v1 <= pipe_valid;
        l1 <= bl3;
        if (pipe_valid) begin
            a0r <= HOLD ? (s02r >>> 1) : s02r;
            a0i <= HOLD ? (s02i >>> 1) : s02i;
            a2r <= HOLD ? (d02r >>> 1) : d02r;
            a2i <= HOLD ? (d02i >>> 1) : d02i;
            a1r <= HOLD ? (s13r >>> 1) : s13r;
            a1i <= HOLD ? (s13i >>> 1) : s13i;
            a3r <= HOLD ? (d13i >>> 1) : d13i;
            a3i <= HOLD ? (-(d13r >>> 1)) : (-d13r);
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
wire _unused_mul = mul_v2 ^ mul_v3 ^ bv3;
endmodule


// Canonical DIF Stage 10 for SubFFT merge: W in -> WO out (WO=W+1 growing,
// W held with (x +/- y)>>>1 truncation).
module subfft_stage10_wg #(
    parameter integer W = 21,
    parameter integer HOLD = 0,
    parameter integer WO = (HOLD ? W : W + 1)
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
            q0r<=HOLD?(s01r>>>1):s01r; q0i<=HOLD?(s01i>>>1):s01i;
            q2r<=HOLD?(d01r>>>1):d01r; q2i<=HOLD?(d01i>>>1):d01i;
            q1r<=HOLD?(s23r>>>1):s23r; q1i<=HOLD?(s23i>>>1):s23i;
            q3r<=HOLD?(d23r>>>1):d23r; q3i<=HOLD?(d23i>>>1):d23i;
        end
        if(v1) begin out0_re<=q0r;out0_im<=q0i;out1_re<=q1r;out1_im<=q1i;out2_re<=q2r;out2_im<=q2i;out3_re<=q3r;out3_im<=q3i;end
    end
end
endmodule
