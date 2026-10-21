`timescale 1ns/1ps

module pfft_exchange_phi_wg #(
    parameter integer STAGE = 1
)(
    input  wire [9:0] index,
    output reg  [9:0] exponent
);
integer high_bit;
integer low_bit;
always @* begin
    exponent = 10'd0;
    for (high_bit = 1; high_bit < 10; high_bit = high_bit + 1) begin
        for (low_bit = 0; low_bit < high_bit; low_bit = low_bit + 1) begin
            if ((STAGE == (10 - low_bit - 1)) && index[high_bit] && index[low_bit])
                exponent = exponent + (10'd1 << (9 - high_bit + low_bit));
        end
    end
end
endmodule


(* keep_hierarchy = "yes" *) module r2sdf_lane_pfft_exchange_wg #(
    parameter integer DEPTH = 128,
    parameter integer STAGE = 1,
    parameter integer LANE = 0,
    parameter integer W = fft_wg_pkg::S1_W,
    parameter integer WO = fft_wg_pkg::S1_WO,
    parameter integer EXT_MEM = 0
)(
    input wire clk, input wire rst, input wire in_valid,
    input wire signed [W-1:0] in_re, in_im,
    output reg out_valid, output reg out_last,
    output reg signed [WO-1:0] out_re, out_im,
    output wire [9:0] tw_addr,
    input wire signed [fft_wg_pkg::TW_W-1:0] tw_re, tw_im,
    output wire mem_rd_en,
    output wire [((DEPTH<=1)?1:$clog2(DEPTH))-1:0] mem_rd_addr,
    input wire [2*(W+1)-1:0] mem_rd_data,
    output wire mem_wr_en,
    output wire [((DEPTH<=1)?1:$clog2(DEPTH))-1:0] mem_wr_addr,
    output wire [2*(W+1)-1:0] mem_wr_data
);
localparam integer PHASE_W = (2*DEPTH <= 2) ? 1 : $clog2(2*DEPTH);
localparam integer ADDR_W = (DEPTH <= 1) ? 1 : $clog2(DEPTH);
localparam integer SYNC_READ = (DEPTH == 128);
localparam integer TRIVIAL_ONLY = (STAGE == 1) || ((STAGE == 8) && (LANE < 2));
localparam [1:0] LANE_INDEX = LANE;
localparam integer MW = W + 1;

reg [PHASE_W-1:0] phase;
reg primed;
reg [7:0] out_count;
wire phase_second = (phase >= DEPTH);
wire [ADDR_W-1:0] current_address = phase_second ? phase - DEPTH : phase;
wire [PHASE_W-1:0] phase_next = rst ? {PHASE_W{1'b0}} :
    (in_valid ? ((phase == (2*DEPTH-1)) ? {PHASE_W{1'b0}} : phase + 1'b1) : phase);

reg request_valid, request_second, request_primed;
reg [ADDR_W-1:0] request_address;
wire [ADDR_W-1:0] request_address_next = rst ? {ADDR_W{1'b0}} :
    ((SYNC_READ && in_valid) ? current_address : request_address);
reg signed [W-1:0] request_re, request_im;
wire work_valid = SYNC_READ ? request_valid : in_valid;
wire work_second = SYNC_READ ? request_second : phase_second;
wire work_primed = SYNC_READ ? request_primed : primed;
wire [ADDR_W-1:0] work_address = SYNC_READ ? request_address : current_address;
wire signed [W-1:0] work_in_re = SYNC_READ ? request_re : in_re;
wire signed [W-1:0] work_in_im = SYNC_READ ? request_im : in_im;

wire [2*MW-1:0] memory_read_word;
wire signed [MW-1:0] memory_re = memory_read_word[2*MW-1 -: MW];
wire signed [MW-1:0] memory_im = memory_read_word[MW-1 -: MW];
wire signed [WO-1:0] memory_wo_re = memory_re;
wire signed [WO-1:0] memory_wo_im = memory_im;
wire signed [W:0]   sumw_re = memory_re + work_in_re;
wire signed [W:0]   sumw_im = memory_im + work_in_im;
wire signed [W:0]   difw_re = memory_re - work_in_re;
wire signed [W:0]   difw_im = memory_im - work_in_im;
wire signed [WO-1:0] upper_re = sumw_re;
wire signed [WO-1:0] upper_im = sumw_im;
wire signed [MW-1:0] lower_re = difw_re;
wire signed [MW-1:0] lower_im = difw_im;

wire candidate_valid = work_second || ((!work_second) && work_primed);
wire emit_raw = work_valid && candidate_valid;
wire raw_is_last = (out_count == 8'd255);
wire signed [MW-1:0] work_ext_re = work_in_re;
wire signed [MW-1:0] work_ext_im = work_in_im;
wire signed [MW-1:0] store_re = work_second ? lower_re : work_ext_re;
wire signed [MW-1:0] store_im = work_second ? lower_im : work_ext_im;

wire signed [MW-1:0] emit_mw_re = work_second ? sumw_re : memory_re;
wire signed [MW-1:0] emit_mw_im = work_second ? sumw_im : memory_im;
wire signed [WO-1:0] emit_wo_re = work_second ? upper_re : memory_wo_re;
wire signed [WO-1:0] emit_wo_im = work_second ? upper_im : memory_wo_im;
wire [7:0] out_count_next = out_count + emit_raw;

wire mul_valid_out;
wire signed [WO-1:0] mul_out_re, mul_out_im;

generate
if (!TRIVIAL_ONLY) begin : g_pipe
    wire [9:0] phi_next;
    pfft_exchange_phi_wg #(.STAGE(STAGE)) u_phi_next(
        .index({out_count_next, LANE_INDEX}), .exponent(phi_next));
    assign tw_addr = phi_next;
    fft_complex_mul_wg_pipe #(.WI(MW), .WO(WO)) u_mul(
        .clk(clk), .rst(rst),
        .valid_in(emit_raw),
        .in_re(emit_mw_re), .in_im(emit_mw_im), .coeff_re(tw_re), .coeff_im(tw_im),
        .valid_out(mul_valid_out), .out_re(mul_out_re), .out_im(mul_out_im)
    );
end else begin : g_triv_pipe
    wire [9:0] phi_current;
    pfft_exchange_phi_wg #(.STAGE(STAGE)) u_phi_now(
        .index({out_count, LANE_INDEX}), .exponent(phi_current));
    assign tw_addr = 10'd0;
    wire _unused_tw = ^{tw_re, tw_im, out_count_next[0], emit_mw_re[0], emit_mw_im[0]};
    wire signed [WO-1:0] triv_re, triv_im;
    fft_complex_rotate_trivial_wg #(.W(WO)) u_rot(
        .in_re(emit_wo_re), .in_im(emit_wo_im), .exponent(phi_current),
        .out_re(triv_re), .out_im(triv_im)
    );
    reg v1, v2, v3;
    reg signed [WO-1:0] r1, i1, r2, i2, r3, i3;
    always @(posedge clk) begin
        if (rst) begin
            v1 <= 1'b0; v2 <= 1'b0; v3 <= 1'b0;
            r1 <= {(WO){1'b0}}; i1 <= {(WO){1'b0}};
            r2 <= {(WO){1'b0}}; i2 <= {(WO){1'b0}};
            r3 <= {(WO){1'b0}}; i3 <= {(WO){1'b0}};
        end else begin
            v1 <= emit_raw;
            if (emit_raw) begin
                r1 <= triv_re;
                i1 <= triv_im;
            end
            v2 <= v1; r2 <= r1; i2 <= i1;
            v3 <= v2; r3 <= r2; i3 <= i2;
        end
    end
    assign mul_valid_out = v3;
    assign mul_out_re = r3;
    assign mul_out_im = i3;
end
endgenerate

assign mem_rd_en = in_valid;
assign mem_rd_addr = current_address;
assign mem_wr_en = work_valid;
assign mem_wr_addr = work_address;
assign mem_wr_data = {store_re, store_im};
generate
if(EXT_MEM) begin : g_external_memory
    assign memory_read_word = mem_rd_data;
end else begin : g_internal_memory
    fft_memory_v5 #(.WIDTH(2*MW),.DEPTH(DEPTH),.ADDR_W(ADDR_W)) u_memory(
        .clk(clk),.rd_en(in_valid),.rd_addr(current_address),.rd_data(memory_read_word),
        .wr_en(work_valid),.wr_addr(work_address),.wr_data({store_re,store_im})
    );
end
endgenerate

reg ml1, ml2, ml3;
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
        ml1 <= 1'b0; ml2 <= 1'b0; ml3 <= 1'b0;
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
        ml1 <= emit_raw && raw_is_last;
        ml2 <= ml1;
        ml3 <= ml2;
        out_valid <= mul_valid_out;
        out_last  <= ml3;
        out_re    <= mul_out_re;
        out_im    <= mul_out_im;
    end
end
endmodule


(* keep_hierarchy = "yes" *) module pfft_stage9_exchange_wg #(
    parameter integer W = fft_wg_pkg::S9_W,
    parameter integer WO = fft_wg_pkg::S9_WO
)(
    input wire clk, input wire rst, input wire in_valid, input wire in_last,
    input wire [8*W-1:0] in_data,
    output reg out_valid, output reg out_last,
    output wire [8*WO-1:0] out_data,
    output wire [39:0] addr,
    input wire [8*fft_wg_pkg::TW_W-1:0] coeffs_in
);
import fft_wg_pkg::*;
localparam integer MW = W + 1;
reg [7:0] beat_count;
wire [7:0] beat_count_next = beat_count + in_valid;
wire signed [W-1:0] in0_re = in_data[8*W-1 -: W], in0_im = in_data[7*W-1 -: W];
wire signed [W-1:0] in1_re = in_data[6*W-1 -: W], in1_im = in_data[5*W-1 -: W];
wire signed [W-1:0] in2_re = in_data[4*W-1 -: W], in2_im = in_data[3*W-1 -: W];
wire signed [W-1:0] in3_re = in_data[2*W-1 -: W], in3_im = in_data[W-1:0];
wire signed [W:0] s02r = in0_re + in2_re, s02i = in0_im + in2_im;
wire signed [W:0] d02r = in0_re - in2_re, d02i = in0_im - in2_im;
wire signed [W:0] s13r = in1_re + in3_re, s13i = in1_im + in3_im;
wire signed [W:0] d13r = in1_re - in3_re, d13i = in1_im - in3_im;
wire [9:0] e0, e1, e2, e3;
pfft_exchange_phi_wg #(.STAGE(9)) p0({beat_count, 2'd0}, e0);
pfft_exchange_phi_wg #(.STAGE(9)) p1({beat_count, 2'd1}, e1);
pfft_exchange_phi_wg #(.STAGE(9)) p2({beat_count, 2'd2}, e2);
pfft_exchange_phi_wg #(.STAGE(9)) p3({beat_count, 2'd3}, e3);
wire [9:0] n0, n1, n2, n3;
pfft_exchange_phi_wg #(.STAGE(9)) q0({beat_count_next, 2'd0}, n0);
pfft_exchange_phi_wg #(.STAGE(9)) q1({beat_count_next, 2'd1}, n1);
pfft_exchange_phi_wg #(.STAGE(9)) q2({beat_count_next, 2'd2}, n2);
pfft_exchange_phi_wg #(.STAGE(9)) q3({beat_count_next, 2'd3}, n3);
assign addr = {n0, n1, n2, n3};
wire signed [WO-1:0] t0r, t0i, t2r, t2i;
fft_complex_rotate_trivial_wg #(.W(WO)) rot0(s02r, s02i, e0, t0r, t0i);
fft_complex_rotate_trivial_wg #(.W(WO)) rot2(d02r, d02i, e2, t2r, t2i);
wire v1, v3;
wire signed [WO-1:0] m1r, m1i, m3r, m3i;
fft_complex_mul_wg_pipe #(.WI(MW), .WO(WO)) rot1(
    .clk(clk), .rst(rst), .valid_in(in_valid),
    .in_re(s13r), .in_im(s13i),
    .coeff_re(coeffs_in[6*TW_W-1 -: TW_W]), .coeff_im(coeffs_in[5*TW_W-1 -: TW_W]),
    .valid_out(v1), .out_re(m1r), .out_im(m1i));
fft_complex_mul_wg_pipe #(.WI(MW), .WO(WO)) rot3(
    .clk(clk), .rst(rst), .valid_in(in_valid),
    .in_re(d13r), .in_im(d13i),
    .coeff_re(coeffs_in[2*TW_W-1 -: TW_W]), .coeff_im(coeffs_in[TW_W-1:0]),
    .valid_out(v3), .out_re(m3r), .out_im(m3i));
reg bv1, bv2, bv3, bl1, bl2, bl3;
reg signed [WO-1:0] r0_1, i0_1, r0_2, i0_2, r0_3, i0_3;
reg signed [WO-1:0] r2_1, i2_1, r2_2, i2_2, r2_3, i2_3;
reg signed [WO-1:0] out0_re, out0_im, out1_re, out1_im;
reg signed [WO-1:0] out2_re, out2_im, out3_re, out3_im;
assign out_data[8*WO-1 -: WO] = out0_re; assign out_data[7*WO-1 -: WO] = out0_im;
assign out_data[6*WO-1 -: WO] = out1_re; assign out_data[5*WO-1 -: WO] = out1_im;
assign out_data[4*WO-1 -: WO] = out2_re; assign out_data[3*WO-1 -: WO] = out2_im;
assign out_data[2*WO-1 -: WO] = out3_re; assign out_data[WO-1:0]      = out3_im;
always @(posedge clk) begin
    if (rst) begin
        beat_count <= 8'd0; out_valid <= 1'b0; out_last <= 1'b0;
        out0_re <= {(WO){1'b0}}; out0_im <= {(WO){1'b0}};
        out1_re <= {(WO){1'b0}}; out1_im <= {(WO){1'b0}};
        out2_re <= {(WO){1'b0}}; out2_im <= {(WO){1'b0}};
        out3_re <= {(WO){1'b0}}; out3_im <= {(WO){1'b0}};
        bv1 <= 1'b0; bv2 <= 1'b0; bv3 <= 1'b0; bl1 <= 1'b0; bl2 <= 1'b0; bl3 <= 1'b0;
        r0_1 <= {(WO){1'b0}}; i0_1 <= {(WO){1'b0}}; r0_2 <= {(WO){1'b0}}; i0_2 <= {(WO){1'b0}};
        r0_3 <= {(WO){1'b0}}; i0_3 <= {(WO){1'b0}};
        r2_1 <= {(WO){1'b0}}; i2_1 <= {(WO){1'b0}}; r2_2 <= {(WO){1'b0}}; i2_2 <= {(WO){1'b0}};
        r2_3 <= {(WO){1'b0}}; i2_3 <= {(WO){1'b0}};
    end else begin
        if (in_valid) beat_count <= beat_count + 1'b1;
        bv1 <= in_valid; bl1 <= in_valid && in_last;
        if (in_valid) begin r0_1 <= t0r; i0_1 <= t0i; r2_1 <= t2r; i2_1 <= t2i; end
        bv2 <= bv1; bl2 <= bl1; r0_2 <= r0_1; i0_2 <= i0_1; r2_2 <= r2_1; i2_2 <= i2_1;
        bv3 <= bv2; bl3 <= bl2; r0_3 <= r0_2; i0_3 <= i0_2; r2_3 <= r2_2; i2_3 <= i2_2;
        out_valid <= v1; out_last <= v1 ? bl3 : 1'b0;
        if (v1) begin
            out0_re <= r0_3; out0_im <= i0_3;
            out1_re <= m1r;  out1_im <= m1i;
            out2_re <= r2_3; out2_im <= i2_3;
            out3_re <= m3r;  out3_im <= m3i;
        end
    end
end
wire _unused = ^{v3, in_last, e0[9], e2[9]};
endmodule


(* keep_hierarchy = "yes" *) module pfft_exchange_twiddles_wg #(
    parameter integer STAGE = 1
)(
    input  wire clk,
    input  wire [39:0] addr,
    output wire [8*fft_wg_pkg::TW_W-1:0] coeffs
);
import fft_wg_pkg::*;
generate
if (STAGE >= 2 && STAGE <= 7) begin : g_bcast
    wire signed [TW_W-1:0] c_re, c_im;
    twiddle_bank_wg #(.PORTS(1)) u_tw (
        .clk(clk), .addr_a(addr[39:30]), .addr_b(10'd0),
        .coeff_a_re(c_re), .coeff_a_im(c_im),
        .coeff_b_re(), .coeff_b_im(), .det_a(), .cor_a(), .det_b(), .cor_b());
    assign coeffs = {c_re, c_im, c_re, c_im, c_re, c_im, c_re, c_im};
end else if (STAGE == 8) begin : g_bcast
    wire signed [TW_W-1:0] c_re, c_im;
    twiddle_bank_wg #(.PORTS(1)) u_tw (
        .clk(clk), .addr_a(addr[19:10]), .addr_b(10'd0),
        .coeff_a_re(c_re), .coeff_a_im(c_im),
        .coeff_b_re(), .coeff_b_im(), .det_a(), .cor_a(), .det_b(), .cor_b());
    assign coeffs = {{(4*TW_W){1'b0}}, c_re, c_im, c_re, c_im};
end else if (STAGE == 9) begin : g_two
    wire signed [TW_W-1:0] ca_re, ca_im, cb_re, cb_im;
    twiddle_bank_wg #(.PORTS(2)) u_tw (
        .clk(clk), .addr_a(addr[29:20]), .addr_b(addr[9:0]),
        .coeff_a_re(ca_re), .coeff_a_im(ca_im),
        .coeff_b_re(cb_re), .coeff_b_im(cb_im),
        .det_a(), .cor_a(), .det_b(), .cor_b());
    assign coeffs = {{(2*TW_W){1'b0}}, ca_re, ca_im,
                     {(2*TW_W){1'b0}}, cb_re, cb_im};
end else begin : g_none
    assign coeffs = {(8*TW_W){1'b0}};
    wire _unused = ^{clk, addr};
end
endgenerate
endmodule


(* keep_hierarchy = "yes" *) module pfft_stage_exchange_wg #(
    parameter integer STAGE = 1,
    parameter integer DEPTH = 128,
    parameter integer W = fft_wg_pkg::S1_W,
    parameter integer WO = fft_wg_pkg::S1_WO,
    parameter integer EXT_MEM = 0,
    parameter integer EXT_TW = 0,
    parameter integer DW = 2*(W+1),
    parameter integer MAW = ((DEPTH<=1) ? 1 : $clog2(DEPTH))
)(
    input wire clk, input wire rst,
    input wire [3:0] in_valid, input wire [3:0] in_last,
    input wire [8*W-1:0] in_data,
    output wire [3:0] out_valid, output wire [3:0] out_last,
    output wire [8*WO-1:0] out_data,
    input wire [8*fft_wg_pkg::TW_W-1:0] coeffs_in,
    output wire [39:0] rom_addr,
    input wire [4*DW-1:0] mem_rd_data,
    output wire [3:0] mem_rd_en, output wire [3:0] mem_wr_en,
    output wire [4*MAW-1:0] mem_rd_addr, output wire [4*MAW-1:0] mem_wr_addr,
    output wire [4*DW-1:0] mem_wr_data
);
import fft_wg_pkg::*;
generate
if (STAGE <= 8) begin : g_lanes
    wire [8*TW_W-1:0] coeffs;
    if (EXT_TW) begin : g_ext_tw
        assign coeffs = coeffs_in;
    end else begin : g_int_tw
        wire _unused_ci = ^coeffs_in;
        pfft_exchange_twiddles_wg #(.STAGE(STAGE)) u_tw (
            .clk(clk), .addr(rom_addr), .coeffs(coeffs));
    end
    wire _unused_l = ^in_last;
    for (genvar lane = 0; lane < 4; lane = lane + 1) begin : g_lane
        r2sdf_lane_pfft_exchange_wg #(
            .DEPTH(DEPTH), .STAGE(STAGE), .LANE(lane), .W(W), .WO(WO),
            .EXT_MEM(EXT_MEM)) u_lane (
            .clk(clk), .rst(rst), .in_valid(in_valid[lane]),
            .in_re(in_data[(8-2*lane)*W-1 -: W]), .in_im(in_data[(7-2*lane)*W-1 -: W]),
            .out_valid(out_valid[lane]), .out_last(out_last[lane]),
            .out_re(out_data[(8-2*lane)*WO-1 -: WO]), .out_im(out_data[(7-2*lane)*WO-1 -: WO]),
            .tw_addr(rom_addr[(4-lane)*10-1 -: 10]),
            .tw_re(coeffs[(8-2*lane)*TW_W-1 -: TW_W]), .tw_im(coeffs[(7-2*lane)*TW_W-1 -: TW_W]),
            .mem_rd_data(mem_rd_data[(lane+1)*DW-1 -: DW]), .mem_rd_en(mem_rd_en[lane]),
            .mem_rd_addr(mem_rd_addr[(lane+1)*MAW-1 -: MAW]), .mem_wr_en(mem_wr_en[lane]),
            .mem_wr_addr(mem_wr_addr[(lane+1)*MAW-1 -: MAW]),
            .mem_wr_data(mem_wr_data[(lane+1)*DW-1 -: DW]));
    end
end else begin : g_tail
    wire tail_valid, tail_last;
    if (STAGE == 9) begin : g_s9
        wire [8*TW_W-1:0] coeffs;
        if (EXT_TW) begin : g_ext_tw
            assign coeffs = coeffs_in;
        end else begin : g_int_tw
            wire _unused_ci = ^coeffs_in;
            pfft_exchange_twiddles_wg #(.STAGE(STAGE)) u_tw (
                .clk(clk), .addr(rom_addr), .coeffs(coeffs));
        end
        wire _unused_m = ^mem_rd_data;
        pfft_stage9_exchange_wg #(.W(W), .WO(WO)) u_stage9 (
            .clk(clk), .rst(rst), .in_valid(in_valid[0]), .in_last(in_last[0]),
            .in_data(in_data), .out_valid(tail_valid), .out_last(tail_last),
            .out_data(out_data), .addr(rom_addr), .coeffs_in(coeffs));
    end else begin : g_s10
        assign rom_addr = 40'd0;
        wire _unused = ^{coeffs_in, mem_rd_data};
        pfft_stage10_wg #(.W(W), .WO(WO)) u_stage10 (
            .clk(clk), .rst(rst), .in_valid(in_valid[0]), .in_last(in_last[0]),
            .in0_re(in_data[8*W-1 -: W]), .in0_im(in_data[7*W-1 -: W]),
            .in1_re(in_data[6*W-1 -: W]), .in1_im(in_data[5*W-1 -: W]),
            .in2_re(in_data[4*W-1 -: W]), .in2_im(in_data[3*W-1 -: W]),
            .in3_re(in_data[2*W-1 -: W]), .in3_im(in_data[W-1:0]),
            .out_valid(tail_valid), .out_last(tail_last),
            .out0_re(out_data[8*WO-1 -: WO]), .out0_im(out_data[7*WO-1 -: WO]),
            .out1_re(out_data[6*WO-1 -: WO]), .out1_im(out_data[5*WO-1 -: WO]),
            .out2_re(out_data[4*WO-1 -: WO]), .out2_im(out_data[3*WO-1 -: WO]),
            .out3_re(out_data[2*WO-1 -: WO]), .out3_im(out_data[WO-1:0]));
    end
    assign out_valid = {4{tail_valid}};
    assign out_last  = {4{tail_last}};
    assign mem_rd_en = 4'b0;
    assign mem_wr_en = 4'b0;
    assign mem_rd_addr = {4*MAW{1'b0}};
    assign mem_wr_addr = {4*MAW{1'b0}};
    assign mem_wr_data = {4*DW{1'b0}};
end
endgenerate
endmodule
