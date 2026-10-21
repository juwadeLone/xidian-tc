`timescale 1ns/1ps

// Partial-growth Gao (7,4,3) path ECC for SubFFT (S1, paper_fft12).
// Same topology as s1/top_s1_kernel.sv: paths {0,1,3} parity, {2,4,5,6} data;
// every path is a complete 8-stage 256-pt lane; Hamming syndromes decoded at
// the stage-8 boundary; stages 9/10 are complete-stage TMR.
// pg differences: data paths grow 16->22 (cap 22), parity paths carry
// 3-term sums so they run +2 bits (18->24, cap 24).

(* keep_hierarchy = "yes" *)
module r2sdf_lane_subfft_wg #(
    parameter integer DEPTH = 128,
    parameter integer STAGE = 1,
    parameter integer W = 16,
    parameter integer HOLD = 0,
    parameter integer WO = (HOLD ? W : W + 1)
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire signed [W-1:0] in_re,
    input  wire signed [W-1:0] in_im,
    output reg  out_valid,
    output reg  out_last,
    output reg  signed [WO-1:0] out_re,
    output reg  signed [WO-1:0] out_im,
    output wire stage_ovf
);
localparam integer PHASE_W = (2*DEPTH <= 2) ? 1 : $clog2(2*DEPTH);
localparam integer ADDR_W = (DEPTH <= 1) ? 1 : $clog2(DEPTH);
localparam integer STRIDE = 256 / (2*DEPTH);
localparam integer SYNC_READ = (DEPTH == 128);
localparam integer TRIVIAL_ONLY = (DEPTH <= 2);
localparam integer USE_PIPE = (TRIVIAL_ONLY == 0);

reg [PHASE_W-1:0] phase;
reg primed;
reg [7:0] out_count;
wire phase_second = (phase >= DEPTH);
wire [ADDR_W-1:0] current_address = phase_second ? (phase - DEPTH) : phase;

reg request_valid, request_second, request_primed;
reg [ADDR_W-1:0] request_address;
reg signed [W-1:0] request_re, request_im;
wire work_valid = SYNC_READ ? request_valid : in_valid;
wire work_second = SYNC_READ ? request_second : phase_second;
wire work_primed = SYNC_READ ? request_primed : primed;
wire [ADDR_W-1:0] work_address = SYNC_READ ? request_address : current_address;
wire signed [W-1:0] work_in_re = SYNC_READ ? request_re : in_re;
wire signed [W-1:0] work_in_im = SYNC_READ ? request_im : in_im;

wire [2*WO-1:0] memory_read_word;
wire signed [WO-1:0] memory_re = memory_read_word[2*WO-1 -: WO];
wire signed [WO-1:0] memory_im = memory_read_word[WO-1 -: WO];
wire signed [W:0]   sum_re  = memory_re + work_in_re;
wire signed [W:0]   sum_im  = memory_im + work_in_im;
wire signed [W:0]   diff_re = memory_re - work_in_re;
wire signed [W:0]   diff_im = memory_im - work_in_im;
wire signed [WO-1:0] upper_re = HOLD ? (sum_re  >>> 1) : sum_re;
wire signed [WO-1:0] upper_im = HOLD ? (sum_im  >>> 1) : sum_im;
wire signed [WO-1:0] lower_re = HOLD ? (diff_re >>> 1) : diff_re;
wire signed [WO-1:0] lower_im = HOLD ? (diff_im >>> 1) : diff_im;
wire signed [WO-1:0] work_ext_re = work_in_re;
wire signed [WO-1:0] work_ext_im = work_in_im;
wire [9:0] twiddle_exponent = (work_address * STRIDE) << 2;
wire signed [WO-1:0] store_re = work_second ? lower_re : work_ext_re;
wire signed [WO-1:0] store_im = work_second ? lower_im : work_ext_im;
wire candidate_valid = work_second || ((!work_second) && work_primed);
wire emit_raw = work_valid && candidate_valid;
wire raw_is_last = (out_count == 8'd255);

wire signed [WO-1:0] rot_re, rot_im;
wire mul_valid_out;
wire signed [WO-1:0] mul_out_re, mul_out_im;

generate
if (!USE_PIPE) begin : g_triv
    fft_complex_rotate_trivial_wg #(.W(WO)) u_rot(
        .in_re(memory_re), .in_im(memory_im), .exponent(twiddle_exponent),
        .out_re(rot_re), .out_im(rot_im)
    );
    assign mul_valid_out = 1'b0;
    assign mul_out_re = {(WO){1'b0}};
    assign mul_out_im = {(WO){1'b0}};
    localparam signed [WO-1:0] NEG_MIN = {1'b1, {(WO-1){1'b0}}};
    assign stage_ovf = emit_raw && (!work_second) &&
        (((twiddle_exponent == 10'd256) && (memory_re == NEG_MIN)) ||
         ((twiddle_exponent == 10'd512) && ((memory_re == NEG_MIN) || (memory_im == NEG_MIN))) ||
         ((twiddle_exponent == 10'd768) && (memory_im == NEG_MIN)));
end else begin : g_pipe
    assign rot_re = {(WO){1'b0}};
    assign rot_im = {(WO){1'b0}};
    wire ovf_m;
    fft_complex_mul_wg_pipe #(.W(WO)) u_mul(
        .clk(clk), .rst(rst),
        .valid_in(emit_raw && (!work_second)),
        .in_re(memory_re), .in_im(memory_im), .exponent(twiddle_exponent),
        .valid_out(mul_valid_out), .ovf(ovf_m),
        .out_re(mul_out_re), .out_im(mul_out_im)
    );
    assign stage_ovf = ovf_m;
end
endgenerate

// Bypass mirrors mul's 3 registers: s1, s2, valid_out.
reg bv1, bv2, bv3, bl1, bl2, bl3;
reg signed [WO-1:0] br1, br2, br3, bi1, bi2, bi3;
reg ml1, ml2, ml3;

wire signed [WO-1:0] triv_out_re = work_second ? upper_re : rot_re;
wire signed [WO-1:0] triv_out_im = work_second ? upper_im : rot_im;

fft_memory_v5 #(.WIDTH(2*WO), .DEPTH(DEPTH), .ADDR_W(ADDR_W)) u_memory(
    .clk(clk), .rd_en(in_valid), .rd_addr(current_address), .rd_data(memory_read_word),
    .wr_en(work_valid), .wr_addr(work_address), .wr_data({store_re, store_im})
);

always @(posedge clk) begin
    if (rst) begin
        phase <= {PHASE_W{1'b0}};
        primed <= 1'b0;
        out_count <= 8'd0;
        request_valid <= 1'b0;
        request_second <= 1'b0;
        request_primed <= 1'b0;
        request_address <= {ADDR_W{1'b0}};
        request_re <= {W{1'b0}};
        request_im <= {W{1'b0}};
        out_valid <= 1'b0;
        out_last <= 1'b0;
        out_re <= {(WO){1'b0}};
        out_im <= {(WO){1'b0}};
        bv1 <= 1'b0; bv2 <= 1'b0; bv3 <= 1'b0;
        bl1 <= 1'b0; bl2 <= 1'b0; bl3 <= 1'b0;
        ml1 <= 1'b0; ml2 <= 1'b0; ml3 <= 1'b0;
        br1 <= {(WO){1'b0}}; br2 <= {(WO){1'b0}}; br3 <= {(WO){1'b0}};
        bi1 <= {(WO){1'b0}}; bi2 <= {(WO){1'b0}}; bi3 <= {(WO){1'b0}};
    end else begin
        request_valid <= SYNC_READ ? in_valid : 1'b0;
        if (SYNC_READ && in_valid) begin
            request_second <= phase_second;
            request_primed <= primed;
            request_address <= current_address;
            request_re <= in_re;
            request_im <= in_im;
        end
        if (in_valid) begin
            if (phase == (2*DEPTH-1)) begin
                phase <= {PHASE_W{1'b0}};
                primed <= 1'b1;
            end else begin
                phase <= phase + 1'b1;
            end
        end
        if (emit_raw)
            out_count <= out_count + 1'b1;

        if (!USE_PIPE) begin
            out_valid <= 1'b0;
            out_last <= 1'b0;
            if (emit_raw) begin
                out_valid <= 1'b1;
                out_last <= raw_is_last;
                out_re <= triv_out_re;
                out_im <= triv_out_im;
            end
        end else begin
            // Exact mirror of fft_complex_mul_wg_pipe valid pipeline.
            bv1 <= emit_raw && work_second;
            bl1 <= emit_raw && work_second && raw_is_last;
            if (emit_raw && work_second) begin
                br1 <= upper_re;
                bi1 <= upper_im;
            end
            bv2 <= bv1; bl2 <= bl1; br2 <= br1; bi2 <= bi1;
            bv3 <= bv2; bl3 <= bl2; br3 <= br2; bi3 <= bi2;

            ml1 <= emit_raw && (!work_second) && raw_is_last;
            ml2 <= ml1;
            ml3 <= ml2;

            // Same NBA visibility as reading mul_valid_out / mul_out_*.
            out_valid <= mul_valid_out | bv3;
            out_last  <= mul_valid_out ? ml3 : bl3;
            out_re    <= mul_valid_out ? mul_out_re : br3;
            out_im    <= mul_valid_out ? mul_out_im : bi3;
        end
    end
end
endmodule


module vote_w #(
    parameter integer W = 22
)(
    input  wire [W-1:0] a, b, c,
    output wire [W-1:0] y
);
assign y = (a & b) | (a & c) | (b & c);
endmodule


// One complete 8-stage 256-pt lane with parameterized input width and growth
// cap: stage k input width = min(WIN+k-1, WMAX), held when it reaches WMAX.
(* keep_hierarchy = "yes" *)
module s1_gao_path_wg #(
    parameter integer WIN  = 16,
    parameter integer WMAX = 22
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire signed [WIN-1:0]  in_re,
    input  wire signed [WIN-1:0]  in_im,
    output wire out_valid,
    output wire out_last,
    output wire signed [WMAX-1:0] out_re,
    output wire signed [WMAX-1:0] out_im,
    output wire path_ovf
);
localparam integer NS = 8;
wire [NS:0] v, l;
wire signed [WMAX-1:0] r [0:NS];
wire signed [WMAX-1:0] i [0:NS];
wire [NS:0] ov;
assign ov[0] = 1'b0;
assign path_ovf = |ov[NS:1];
assign v[0] = in_valid;
assign r[0] = in_re;
assign i[0] = in_im;
genvar k;
generate for (k = 1; k <= NS; k = k + 1) begin : u_s
    localparam integer WK = ((WIN + k - 1) < WMAX) ? (WIN + k - 1) : WMAX;
    localparam integer HK = (WK >= WMAX) ? 1 : 0;
    (* keep_hierarchy = "yes" *)
    r2sdf_lane_subfft_wg #(.DEPTH(256 >> k), .STAGE(k), .W(WK), .HOLD(HK)) u (
        .clk(clk), .rst(rst), .in_valid(v[k-1]),
        .in_re(r[k-1][WK-1:0]), .in_im(i[k-1][WK-1:0]),
        .out_valid(v[k]), .out_last(l[k]),
        .out_re(r[k]), .out_im(i[k]), .stage_ovf(ov[k])
    );
end endgenerate
assign out_valid = v[NS];
assign out_last  = l[NS];
assign out_re    = r[NS];
assign out_im    = i[NS];
wire _unused_l = ^l[NS-1:1];
endmodule


(* keep_hierarchy = "yes" *)
module s1_tmr_subfft_stage9_wg (
    input  wire clk, input wire rst, input wire in_valid, input wire in_last,
    input  wire signed [21:0] in0_re, in0_im, in1_re, in1_im, in2_re, in2_im, in3_re, in3_im,
    output wire out_valid, output wire out_last,
    output wire signed [21:0] out0_re, out0_im, out1_re, out1_im, out2_re, out2_im, out3_re, out3_im
);
    wire [2:0] v, l;
    wire signed [21:0] r0[0:2], i0[0:2], r1[0:2], i1[0:2], r2[0:2], i2[0:2], r3[0:2], i3[0:2];
    genvar g;
    generate for (g = 0; g < 3; g = g + 1) begin : rep
        (* DONT_TOUCH = "yes", keep = "true", keep_hierarchy = "yes" *)
        subfft_stage9_wg #(.W(22), .HOLD(1)) u (
            clk, rst, in_valid, in_last,
            in0_re, in0_im, in1_re, in1_im, in2_re, in2_im, in3_re, in3_im,
            v[g], l[g], r0[g], i0[g], r1[g], i1[g], r2[g], i2[g], r3[g], i3[g]
        );
    end endgenerate
    vote_w #(.W(22)) a0(r0[0], r0[1], r0[2], out0_re); vote_w #(.W(22)) b0(i0[0], i0[1], i0[2], out0_im);
    vote_w #(.W(22)) a1(r1[0], r1[1], r1[2], out1_re); vote_w #(.W(22)) b1(i1[0], i1[1], i1[2], out1_im);
    vote_w #(.W(22)) a2(r2[0], r2[1], r2[2], out2_re); vote_w #(.W(22)) b2(i2[0], i2[1], i2[2], out2_im);
    vote_w #(.W(22)) a3(r3[0], r3[1], r3[2], out3_re); vote_w #(.W(22)) b3(i3[0], i3[1], i3[2], out3_im);
    assign out_valid = (v[0] & v[1]) | (v[0] & v[2]) | (v[1] & v[2]);
    assign out_last  = (l[0] & l[1]) | (l[0] & l[2]) | (l[1] & l[2]);
endmodule


(* keep_hierarchy = "yes" *)
module s1_tmr_subfft_stage10_wg (
    input  wire clk, input wire rst, input wire in_valid, input wire in_last,
    input  wire signed [21:0] in0_re, in0_im, in1_re, in1_im, in2_re, in2_im, in3_re, in3_im,
    output wire out_valid, output wire out_last,
    output wire signed [21:0] out0_re, out0_im, out1_re, out1_im, out2_re, out2_im, out3_re, out3_im
);
    wire [2:0] v, l;
    wire signed [21:0] r0[0:2], i0[0:2], r1[0:2], i1[0:2], r2[0:2], i2[0:2], r3[0:2], i3[0:2];
    genvar g;
    generate for (g = 0; g < 3; g = g + 1) begin : rep
        (* DONT_TOUCH = "yes", keep = "true", keep_hierarchy = "yes" *)
        subfft_stage10_wg #(.W(22), .HOLD(1)) u (
            clk, rst, in_valid, in_last,
            in0_re, in0_im, in1_re, in1_im, in2_re, in2_im, in3_re, in3_im,
            v[g], l[g], r0[g], i0[g], r1[g], i1[g], r2[g], i2[g], r3[g], i3[g]
        );
    end endgenerate
    vote_w #(.W(22)) a0(r0[0], r0[1], r0[2], out0_re); vote_w #(.W(22)) b0(i0[0], i0[1], i0[2], out0_im);
    vote_w #(.W(22)) a1(r1[0], r1[1], r1[2], out1_re); vote_w #(.W(22)) b1(i1[0], i1[1], i1[2], out1_im);
    vote_w #(.W(22)) a2(r2[0], r2[1], r2[2], out2_re); vote_w #(.W(22)) b2(i2[0], i2[1], i2[2], out2_im);
    vote_w #(.W(22)) a3(r3[0], r3[1], r3[2], out3_re); vote_w #(.W(22)) b3(i3[0], i3[1], i3[2], out3_im);
    assign out_valid = (v[0] & v[1]) | (v[0] & v[2]) | (v[1] & v[2]);
    assign out_last  = (l[0] & l[1]) | (l[0] & l[2]) | (l[1] & l[2]);
endmodule


