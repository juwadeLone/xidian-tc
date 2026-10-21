`timescale 1ns/1ps

// Growing-width SubFFT lane/core (paper_fft12). Identical control structure
// to r2sdf_lane_subfft_v5; differences:
//   * input component width W, output width WO (fft_wg_pkg; stage 1 is W+2,
//     every other stage W+1)
//   * butterfly sum/diff at W+1 bits, sign-extended into WO
//   * feedback memory word = 2*MW = 2*(W+1): first-half input (W bits)
//     sign-extended to MW, second-half diff (MW bits)
//   * twiddle multiply reads the MW-bit stored word, product >>> TW_FRAC,
//     low WO bits kept

(* keep_hierarchy = "yes" *)
module r2sdf_lane_subfft_wg #(
    parameter integer DEPTH = 128,
    parameter integer STAGE = 1,
    parameter integer W = fft_wg_pkg::S1_W,
    parameter integer WO = fft_wg_pkg::S1_WO,
    parameter integer EXT_MEM = 0
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
    output wire [9:0] tw_addr,
    input  wire signed [fft_wg_pkg::TW_W-1:0] tw_re,
    input  wire signed [fft_wg_pkg::TW_W-1:0] tw_im,
    output wire mem_rd_en,
    output wire [((DEPTH<=1)?1:$clog2(DEPTH))-1:0] mem_rd_addr,
    input wire [2*(W+1)-1:0] mem_rd_data,
    output wire mem_wr_en,
    output wire [((DEPTH<=1)?1:$clog2(DEPTH))-1:0] mem_wr_addr,
    output wire [2*(W+1)-1:0] mem_wr_data
);
localparam integer PHASE_W = (2*DEPTH <= 2) ? 1 : $clog2(2*DEPTH);
localparam integer ADDR_W = (DEPTH <= 1) ? 1 : $clog2(DEPTH);
localparam integer STRIDE = 256 / (2*DEPTH);
localparam integer SYNC_READ = (DEPTH == 128);
localparam integer TRIVIAL_ONLY = (DEPTH <= 2);
localparam integer USE_PIPE = (TRIVIAL_ONLY == 0);
localparam integer MW = W + 1;

reg [PHASE_W-1:0] phase;
reg primed;
reg [7:0] out_count;
wire phase_second = (phase >= DEPTH);
wire [ADDR_W-1:0] current_address = phase_second ? (phase - DEPTH) : phase;
wire [PHASE_W-1:0] phase_next = rst ? {PHASE_W{1'b0}} :
    (in_valid ? ((phase == (2*DEPTH-1)) ? {PHASE_W{1'b0}} : phase + 1'b1) : phase);
wire phase_second_next = (phase_next >= DEPTH);
wire [ADDR_W-1:0] current_address_next = phase_second_next ? (phase_next - DEPTH) : phase_next;

reg request_valid, request_second, request_primed;
reg [ADDR_W-1:0] request_address;
wire [ADDR_W-1:0] request_address_next = rst ? {ADDR_W{1'b0}} :
    ((SYNC_READ && in_valid) ? current_address : request_address);
reg signed [W-1:0] request_re, request_im;
wire work_valid = SYNC_READ ? request_valid : in_valid;
wire work_second = SYNC_READ ? request_second : phase_second;
wire work_primed = SYNC_READ ? request_primed : primed;
wire [ADDR_W-1:0] work_address = SYNC_READ ? request_address : current_address;
wire [ADDR_W-1:0] work_address_next = SYNC_READ ? request_address_next : current_address_next;
wire [9:0] tw_addr_next = (work_address_next * STRIDE) << 2;
assign tw_addr = TRIVIAL_ONLY ? 10'd0 : tw_addr_next;
wire signed [W-1:0] work_in_re = SYNC_READ ? request_re : in_re;
wire signed [W-1:0] work_in_im = SYNC_READ ? request_im : in_im;

wire [2*MW-1:0] memory_read_word;
wire signed [MW-1:0] memory_re = memory_read_word[2*MW-1 -: MW];
wire signed [MW-1:0] memory_im = memory_read_word[MW-1 -: MW];
wire signed [WO-1:0] memory_wo_re = memory_re;
wire signed [WO-1:0] memory_wo_im = memory_im;
wire signed [W:0]   sum_re  = memory_re + work_in_re;
wire signed [W:0]   sum_im  = memory_im + work_in_im;
wire signed [W:0]   diff_re = memory_re - work_in_re;
wire signed [W:0]   diff_im = memory_im - work_in_im;
wire signed [WO-1:0] upper_re = sum_re;
wire signed [WO-1:0] upper_im = sum_im;
wire signed [MW-1:0] lower_re = diff_re;
wire signed [MW-1:0] lower_im = diff_im;
wire signed [MW-1:0] work_ext_re = work_in_re;
wire signed [MW-1:0] work_ext_im = work_in_im;
wire [9:0] twiddle_exponent = (work_address * STRIDE) << 2;
wire signed [MW-1:0] store_re = work_second ? lower_re : work_ext_re;
wire signed [MW-1:0] store_im = work_second ? lower_im : work_ext_im;
wire candidate_valid = work_second || ((!work_second) && work_primed);
wire emit_raw = work_valid && candidate_valid;
wire raw_is_last = (out_count == 8'd255);

wire signed [WO-1:0] rot_re, rot_im;
wire mul_valid_out;
wire signed [WO-1:0] mul_out_re, mul_out_im;

generate
if (!USE_PIPE) begin : g_triv
    wire _unused_tw = ^{tw_re, tw_im};
    fft_complex_rotate_trivial_wg #(.W(WO)) u_rot(
        .in_re(memory_wo_re), .in_im(memory_wo_im), .exponent(twiddle_exponent),
        .out_re(rot_re), .out_im(rot_im)
    );
    assign mul_valid_out = 1'b0;
    assign mul_out_re = {(WO){1'b0}};
    assign mul_out_im = {(WO){1'b0}};
end else begin : g_pipe
    assign rot_re = {(WO){1'b0}};
    assign rot_im = {(WO){1'b0}};
    fft_complex_mul_wg_pipe #(.WI(MW), .WO(WO)) u_mul(
        .clk(clk), .rst(rst),
        .valid_in(emit_raw && (!work_second)),
        .in_re(memory_re), .in_im(memory_im), .coeff_re(tw_re), .coeff_im(tw_im),
        .valid_out(mul_valid_out), .out_re(mul_out_re), .out_im(mul_out_im)
    );
end
endgenerate

// Bypass mirrors mul's 3 registers: s1, s2, valid_out.
reg bv1, bv2, bv3, bl1, bl2, bl3;
reg signed [WO-1:0] br1, br2, br3, bi1, bi2, bi3;
reg ml1, ml2, ml3;

wire signed [WO-1:0] triv_out_re = work_second ? upper_re : rot_re;
wire signed [WO-1:0] triv_out_im = work_second ? upper_im : rot_im;

assign mem_rd_en = in_valid;
assign mem_rd_addr = current_address;
assign mem_wr_en = work_valid;
assign mem_wr_addr = work_address;
assign mem_wr_data = {store_re, store_im};
generate
if(EXT_MEM) begin : g_external_memory
    assign memory_read_word = mem_rd_data;
end else begin : g_internal_memory
    fft_memory_v5 #(.WIDTH(2*MW), .DEPTH(DEPTH), .ADDR_W(ADDR_W)) u_memory(
        .clk(clk), .rd_en(in_valid), .rd_addr(current_address), .rd_data(memory_read_word),
        .wr_en(work_valid), .wr_addr(work_address), .wr_data({store_re, store_im}));
end
endgenerate

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
        request_address <= request_address_next;
        if (SYNC_READ && in_valid) begin
            request_second <= phase_second;
            request_primed <= primed;
            request_re <= in_re;
            request_im <= in_im;
        end
        phase <= phase_next;
        if (in_valid && (phase == (2*DEPTH-1)))
            primed <= 1'b1;
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


(* keep_hierarchy = "yes" *)
module fft_256_subfft_lane_wg (
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in_re,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in_im,
    output wire out_valid,
    output wire out_last,
    output wire signed [fft_wg_pkg::S8_WO-1:0] out_re,
    output wire signed [fft_wg_pkg::S8_WO-1:0] out_im,
    output wire [59:0] tw_addr,        // stage s at [10*(s-1) +: 10], s = 1..6
    input  wire [215:0] tw_coeff       // stage s at [36*(s-1) +: 36] = {re, im}
);
    import fft_wg_pkg::*;
    // Classic R2SDF 256-pt: depths 128,64,32,16,8,4,2,1; stage s output
    // width S<s>_WO (see fft_wg_pkg).
    wire [8:0] v, l;
    wire signed [S1_W-1:0]  r0, i0;
    wire signed [S1_WO-1:0] r1, i1;
    wire signed [S2_WO-1:0] r2, i2;
    wire signed [S3_WO-1:0] r3, i3;
    wire signed [S4_WO-1:0] r4, i4;
    wire signed [S5_WO-1:0] r5, i5;
    wire signed [S6_WO-1:0] r6, i6;
    wire signed [S7_WO-1:0] r7, i7;
    wire signed [S8_WO-1:0] r8, i8;
    assign v[0] = in_valid;
    assign r0 = in_re;
    assign i0 = in_im;

    r2sdf_lane_subfft_wg #(.DEPTH(128), .STAGE(1), .W(S1_W), .WO(S1_WO)) u_s1(
        .clk(clk), .rst(rst), .in_valid(v[0]), .in_re(r0), .in_im(i0),
        .out_valid(v[1]), .out_last(l[1]), .out_re(r1), .out_im(i1),
        .tw_addr(tw_addr[9:0]), .tw_re(tw_coeff[35:18]), .tw_im(tw_coeff[17:0]));
    r2sdf_lane_subfft_wg #(.DEPTH(64),  .STAGE(2), .W(S2_W), .WO(S2_WO)) u_s2(
        .clk(clk), .rst(rst), .in_valid(v[1]), .in_re(r1), .in_im(i1),
        .out_valid(v[2]), .out_last(l[2]), .out_re(r2), .out_im(i2),
        .tw_addr(tw_addr[19:10]), .tw_re(tw_coeff[71:54]), .tw_im(tw_coeff[53:36]));
    r2sdf_lane_subfft_wg #(.DEPTH(32),  .STAGE(3), .W(S3_W), .WO(S3_WO)) u_s3(
        .clk(clk), .rst(rst), .in_valid(v[2]), .in_re(r2), .in_im(i2),
        .out_valid(v[3]), .out_last(l[3]), .out_re(r3), .out_im(i3),
        .tw_addr(tw_addr[29:20]), .tw_re(tw_coeff[107:90]), .tw_im(tw_coeff[89:72]));
    r2sdf_lane_subfft_wg #(.DEPTH(16),  .STAGE(4), .W(S4_W), .WO(S4_WO)) u_s4(
        .clk(clk), .rst(rst), .in_valid(v[3]), .in_re(r3), .in_im(i3),
        .out_valid(v[4]), .out_last(l[4]), .out_re(r4), .out_im(i4),
        .tw_addr(tw_addr[39:30]), .tw_re(tw_coeff[143:126]), .tw_im(tw_coeff[125:108]));
    r2sdf_lane_subfft_wg #(.DEPTH(8),   .STAGE(5), .W(S5_W), .WO(S5_WO)) u_s5(
        .clk(clk), .rst(rst), .in_valid(v[4]), .in_re(r4), .in_im(i4),
        .out_valid(v[5]), .out_last(l[5]), .out_re(r5), .out_im(i5),
        .tw_addr(tw_addr[49:40]), .tw_re(tw_coeff[179:162]), .tw_im(tw_coeff[161:144]));
    r2sdf_lane_subfft_wg #(.DEPTH(4),   .STAGE(6), .W(S6_W), .WO(S6_WO)) u_s6(
        .clk(clk), .rst(rst), .in_valid(v[5]), .in_re(r5), .in_im(i5),
        .out_valid(v[6]), .out_last(l[6]), .out_re(r6), .out_im(i6),
        .tw_addr(tw_addr[59:50]), .tw_re(tw_coeff[215:198]), .tw_im(tw_coeff[197:180]));
    r2sdf_lane_subfft_wg #(.DEPTH(2),   .STAGE(7), .W(S7_W), .WO(S7_WO)) u_s7(
        .clk(clk), .rst(rst), .in_valid(v[6]), .in_re(r6), .in_im(i6),
        .out_valid(v[7]), .out_last(l[7]), .out_re(r7), .out_im(i7),
        .tw_addr(), .tw_re({TW_W{1'b0}}), .tw_im({TW_W{1'b0}}));
    r2sdf_lane_subfft_wg #(.DEPTH(1),   .STAGE(8), .W(S8_W), .WO(S8_WO)) u_s8(
        .clk(clk), .rst(rst), .in_valid(v[7]), .in_re(r7), .in_im(i7),
        .out_valid(v[8]), .out_last(l[8]), .out_re(r8), .out_im(i8),
        .tw_addr(), .tw_re({TW_W{1'b0}}), .tw_im({TW_W{1'b0}}));

    assign out_valid = v[8];
    assign out_last  = l[8];
    assign out_re    = r8;
    assign out_im    = i8;
endmodule


// Six stage tables (one bank per stage), shared by the four lanes: every lane
// of a SubFFT stage uses the same exponent on the same cycle.
(* keep_hierarchy = "yes" *)
module subfft_lane_twiddle_wg (
    input  wire clk,
    input  wire [59:0] tw_addr,        // stage s at [10*(s-1) +: 10]
    output wire [215:0] tw_coeff       // stage s at [36*(s-1) +: 36] = {re, im}
);
    import fft_wg_pkg::*;
    genvar s;
    generate
    for (s = 1; s <= 6; s = s + 1) begin : g_stage
        wire signed [TW_W-1:0] c_re, c_im;
        twiddle_bank_wg #(.PORTS(1)) u_bank (
            .clk(clk), .addr_a(tw_addr[10*(s-1) +: 10]), .addr_b(10'd0),
            .coeff_a_re(c_re), .coeff_a_im(c_im), .coeff_b_re(), .coeff_b_im(),
            .det_a(), .cor_a(), .det_b(), .cor_b()
        );
        assign tw_coeff[36*(s-1) +: 36] = {c_re, c_im};
    end
    endgenerate
endmodule


(* keep_hierarchy = "yes" *)
module subfft_merge_l4_wg (
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire signed [fft_wg_pkg::S8_WO-1:0] in0_re, in0_im, in1_re, in1_im,
    input  wire signed [fft_wg_pkg::S8_WO-1:0] in2_re, in2_im, in3_re, in3_im,
    output wire out_valid,
    output wire out_last,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out0_re, out0_im, out1_re, out1_im,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out2_re, out2_im, out3_re, out3_im
);
    import fft_wg_pkg::*;
    wire v9, l9;
    wire signed [S9_WO-1:0] r9_0, i9_0, r9_1, i9_1, r9_2, i9_2, r9_3, i9_3;
    subfft_stage9_wg #(.W(S9_W), .WO(S9_WO)) u_s9 (
        .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
        .in0_re(in0_re), .in0_im(in0_im), .in1_re(in1_re), .in1_im(in1_im),
        .in2_re(in2_re), .in2_im(in2_im), .in3_re(in3_re), .in3_im(in3_im),
        .out_valid(v9), .out_last(l9),
        .out0_re(r9_0), .out0_im(i9_0), .out1_re(r9_1), .out1_im(i9_1),
        .out2_re(r9_2), .out2_im(i9_2), .out3_re(r9_3), .out3_im(i9_3)
    );
    subfft_stage10_wg #(.W(S10_W), .WO(S10_WO)) u_s10 (
        .clk(clk), .rst(rst), .in_valid(v9), .in_last(l9),
        .in0_re(r9_0), .in0_im(i9_0), .in1_re(r9_1), .in1_im(i9_1),
        .in2_re(r9_2), .in2_im(i9_2), .in3_re(r9_3), .in3_im(i9_3),
        .out_valid(out_valid), .out_last(out_last),
        .out0_re(out0_re), .out0_im(out0_im), .out1_re(out1_re), .out1_im(out1_im),
        .out2_re(out2_re), .out2_im(out2_im), .out3_re(out3_re), .out3_im(out3_im)
    );
endmodule


module subfft_functional_core_wg (
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in0_re, in0_im, in1_re, in1_im,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in2_re, in2_im, in3_re, in3_im,
    output wire out_valid,
    output wire out_last,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out0_re, out0_im, out1_re, out1_im,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out2_re, out2_im, out3_re, out3_im
);
    import fft_wg_pkg::*;
    wire [3:0] lane_v, lane_l;
    wire signed [S8_WO-1:0] m0_re, m0_im, m1_re, m1_im, m2_re, m2_im, m3_re, m3_im;
    wire [59:0] lane0_tw_addr;
    wire [215:0] tw_coeff;

    subfft_lane_twiddle_wg u_tw (.clk(clk), .tw_addr(lane0_tw_addr), .tw_coeff(tw_coeff));

    fft_256_subfft_lane_wg u_fft256_0 (
        .clk(clk), .rst(rst), .in_valid(in_valid),
        .in_re(in0_re), .in_im(in0_im),
        .out_valid(lane_v[0]), .out_last(lane_l[0]),
        .out_re(m0_re), .out_im(m0_im),
        .tw_addr(lane0_tw_addr), .tw_coeff(tw_coeff)
    );
    fft_256_subfft_lane_wg u_fft256_1 (
        .clk(clk), .rst(rst), .in_valid(in_valid),
        .in_re(in1_re), .in_im(in1_im),
        .out_valid(lane_v[1]), .out_last(lane_l[1]),
        .out_re(m1_re), .out_im(m1_im),
        .tw_addr(), .tw_coeff(tw_coeff)
    );
    fft_256_subfft_lane_wg u_fft256_2 (
        .clk(clk), .rst(rst), .in_valid(in_valid),
        .in_re(in2_re), .in_im(in2_im),
        .out_valid(lane_v[2]), .out_last(lane_l[2]),
        .out_re(m2_re), .out_im(m2_im),
        .tw_addr(), .tw_coeff(tw_coeff)
    );
    fft_256_subfft_lane_wg u_fft256_3 (
        .clk(clk), .rst(rst), .in_valid(in_valid),
        .in_re(in3_re), .in_im(in3_im),
        .out_valid(lane_v[3]), .out_last(lane_l[3]),
        .out_re(m3_re), .out_im(m3_im),
        .tw_addr(), .tw_coeff(tw_coeff)
    );

    wire merge_valid = lane_v[0];
    wire merge_last  = lane_l[0];
    wire _unused_lanes = ^{lane_v[3:1], lane_l[3:1], in_last};
    wire cross_valid, cross_last;
    wire signed [S8_WO-1:0] x0_re, x0_im, x1_re, x1_im, x2_re, x2_im, x3_re, x3_im;
    wire [9:0] exp1, exp2, exp3;
    wire signed [TW_W-1:0] t1_re, t1_im, t2_re, t2_im, t3_re, t3_im;
    twiddle_bank_wg #(.PORTS(2)) u_tw12 (
        .clk(clk), .addr_a(exp1), .addr_b(exp2),
        .coeff_a_re(t1_re), .coeff_a_im(t1_im), .coeff_b_re(t2_re), .coeff_b_im(t2_im),
        .det_a(), .cor_a(), .det_b(), .cor_b());
    twiddle_bank_wg #(.PORTS(1)) u_tw3 (
        .clk(clk), .addr_a(exp3), .addr_b(10'd0),
        .coeff_a_re(t3_re), .coeff_a_im(t3_im), .coeff_b_re(), .coeff_b_im(),
        .det_a(), .cor_a(), .det_b(), .cor_b());
    subfft_stage8_cross_wg u_s8x (
        .clk(clk), .rst(rst), .in_valid(merge_valid), .in_last(merge_last),
        .in0_re(m0_re), .in0_im(m0_im), .in1_re(m1_re), .in1_im(m1_im),
        .in2_re(m2_re), .in2_im(m2_im), .in3_re(m3_re), .in3_im(m3_im),
        .t1_re(t1_re), .t1_im(t1_im), .t2_re(t2_re), .t2_im(t2_im), .t3_re(t3_re), .t3_im(t3_im),
        .exp1(exp1), .exp2(exp2), .exp3(exp3), .out_valid(cross_valid), .out_last(cross_last),
        .out0_re(x0_re), .out0_im(x0_im), .out1_re(x1_re), .out1_im(x1_im),
        .out2_re(x2_re), .out2_im(x2_im), .out3_re(x3_re), .out3_im(x3_im));

    subfft_merge_l4_wg u_merge (
        .clk(clk), .rst(rst),
        .in_valid(cross_valid), .in_last(cross_last),
        .in0_re(x0_re), .in0_im(x0_im),
        .in1_re(x1_re), .in1_im(x1_im),
        .in2_re(x2_re), .in2_im(x2_im),
        .in3_re(x3_re), .in3_im(x3_im),
        .out_valid(out_valid), .out_last(out_last),
        .out0_re(out0_re), .out0_im(out0_im),
        .out1_re(out1_re), .out1_im(out1_im),
        .out2_re(out2_re), .out2_im(out2_im),
        .out3_re(out3_re), .out3_im(out3_im)
    );
endmodule


module top_s0_subfft_unprotected (
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in0_re, in0_im, in1_re, in1_im,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in2_re, in2_im, in3_re, in3_im,
    output wire out_valid,
    output wire out_last,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out0_re, out0_im, out1_re, out1_im,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out2_re, out2_im, out3_re, out3_im
);
    subfft_functional_core_wg u_func (
        .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
        .in0_re(in0_re), .in0_im(in0_im),
        .in1_re(in1_re), .in1_im(in1_im),
        .in2_re(in2_re), .in2_im(in2_im),
        .in3_re(in3_re), .in3_im(in3_im),
        .out_valid(out_valid), .out_last(out_last),
        .out0_re(out0_re), .out0_im(out0_im),
        .out1_re(out1_re), .out1_im(out1_im),
        .out2_re(out2_re), .out2_im(out2_im),
        .out3_re(out3_re), .out3_im(out3_im)
    );
endmodule
