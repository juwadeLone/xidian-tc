`timescale 1ns/1ps

// Growing-width canonical P-SDF (no-exchange) datapath for paper_fft12.
// Same control structure as r2sdf_lane_pfft_no_exchange_v3; differences:
//   * input width W, output width WO (fft_wg_pkg; stage 1 is W+2, every
//     other stage W+1); no stage shifts
//   * growing stage: butterflies at W+1 bits, sign-extended into WO
//   * feedback memory word = 2*MW = 2*(W+1): first-half input (W bits)
//     sign-extended to MW, lower stored UNROTATED as an MW-bit diff; rotated
//     on the emit path through a 3-cycle pipe (trivial lanes use a matched
//     3-register delay) -- faithful port of the v3 emit-side rotation
//   * canonical lower exponent ((4*a + l) << (STAGE-1)) unchanged

(* keep_hierarchy = "yes" *) module r2sdf_lane_pfft_no_exchange_wg #(
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
localparam integer TRIVIAL_ONLY =
    ((STAGE == 7) && (LANE == 0)) ||
    ((STAGE == 8) && ((LANE == 0) || (LANE == 2)));
// All stages 1--8 use 3-cycle ROR emit path so trivial/non-trivial lanes stay
// aligned (faithful to r2sdf_lane_pfft_no_exchange_v3 USE_PIPE=1).
localparam [9:0] LANE_INDEX = LANE;
localparam integer MW = W + 1;

reg [PHASE_W-1:0] phase;
reg primed;
reg [7:0] out_count;
wire phase_second = (phase >= DEPTH);
wire [ADDR_W-1:0] current_address = phase_second ? phase - DEPTH : phase;
wire [PHASE_W-1:0] phase_next = rst ? {PHASE_W{1'b0}} :
    (in_valid ? ((phase == (2*DEPTH-1)) ? {PHASE_W{1'b0}} : phase + 1'b1) : phase);
wire phase_second_next = (phase_next >= DEPTH);
wire [ADDR_W-1:0] current_address_next = phase_second_next ? phase_next - DEPTH : phase_next;

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

// Canonical DIF lower exponent: ((4*a + l) * 2^(STAGE-1)) mod 1024.
// Applied on first-half read of previously stored unrotated lower.
wire [9:0] work_address_extended = {{(10-ADDR_W){1'b0}},work_address};
wire [9:0] canonical_offset = (work_address_extended << 2) + LANE_INDEX;
wire [9:0] lower_exponent = canonical_offset << (STAGE-1);
wire [9:0] work_address_next_ext = {{(10-ADDR_W){1'b0}},work_address_next};
wire [9:0] tw_addr_next = ((work_address_next_ext << 2) + LANE_INDEX) << (STAGE-1);
assign tw_addr = TRIVIAL_ONLY ? 10'd0 : tw_addr_next;

wire candidate_valid = work_second || ((!work_second) && work_primed);
wire emit_raw = work_valid && candidate_valid;
wire raw_is_last = (out_count == 8'd255);
wire signed [MW-1:0] work_ext_re = work_in_re;
wire signed [MW-1:0] work_ext_im = work_in_im;
wire signed [MW-1:0] store_re = work_second ? lower_re : work_ext_re;
wire signed [MW-1:0] store_im = work_second ? lower_im : work_ext_im;

wire signed [WO-1:0] rot_re, rot_im;
wire mul_valid_out;
wire signed [WO-1:0] mul_out_re, mul_out_im;

generate
if (!TRIVIAL_ONLY) begin : g_pipe
    assign rot_re = {(WO){1'b0}};
    assign rot_im = {(WO){1'b0}};
    fft_complex_mul_wg_pipe #(.WI(MW), .WO(WO)) u_mul(
        .clk(clk), .rst(rst),
        .valid_in(emit_raw && (!work_second)),
        .in_re(memory_re), .in_im(memory_im), .coeff_re(tw_re), .coeff_im(tw_im),
        .valid_out(mul_valid_out), .out_re(mul_out_re), .out_im(mul_out_im)
    );
end else begin : g_triv_pipe
    // Trivial twiddle, but still 3-cycle delay to match sibling CMUL lanes.
    wire signed [WO-1:0] triv_re, triv_im;
    wire _unused_tw = ^{tw_re, tw_im};
    fft_complex_rotate_trivial_wg #(.W(WO)) u_rot(
        .in_re(memory_wo_re), .in_im(memory_wo_im), .exponent(lower_exponent),
        .out_re(triv_re), .out_im(triv_im)
    );
    assign rot_re = triv_re;
    assign rot_im = triv_im;
    reg v1, v2, v3;
    reg signed [WO-1:0] r1, i1, r2, i2, r3, i3;
    always @(posedge clk) begin
        if (rst) begin
            v1 <= 1'b0; v2 <= 1'b0; v3 <= 1'b0;
            r1 <= {(WO){1'b0}}; i1 <= {(WO){1'b0}};
            r2 <= {(WO){1'b0}}; i2 <= {(WO){1'b0}};
            r3 <= {(WO){1'b0}}; i3 <= {(WO){1'b0}};
        end else begin
            v1 <= emit_raw && (!work_second);
            if (emit_raw && (!work_second)) begin
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

reg bv1, bv2, bv3, bl1, bl2, bl3;
reg signed [WO-1:0] br1, br2, br3, bi1, bi2, bi3;
reg ml1, ml2, ml3;

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

        out_valid <= mul_valid_out | bv3;
        out_last  <= mul_valid_out ? ml3 : bl3;
        out_re    <= mul_valid_out ? mul_out_re : br3;
        out_im    <= mul_valid_out ? mul_out_im : bi3;
    end
end
endmodule


(* keep_hierarchy = "yes" *) module pfft_stage4_sdf_no_exchange_wg #(
    parameter integer DEPTH = 128,
    parameter integer STAGE = 1,
    parameter integer W = fft_wg_pkg::S1_W,
    parameter integer WO = fft_wg_pkg::S1_WO
)(
    input wire clk, input wire rst, input wire in_valid, input wire in_last,
    input wire signed [W-1:0] in0_re, in0_im, in1_re, in1_im,
    input wire signed [W-1:0] in2_re, in2_im, in3_re, in3_im,
    output wire out_valid, output wire out_last,
    output wire signed [WO-1:0] out0_re, out0_im, out1_re, out1_im,
    output wire signed [WO-1:0] out2_re, out2_im, out3_re, out3_im
);
import fft_wg_pkg::*;
wire [3:0] lane_valid, lane_last;
wire [9:0] ta0, ta1, ta2, ta3;
wire signed [TW_W-1:0] tr0, ti0, tr1, ti1, tr2, ti2, tr3, ti3;
r2sdf_lane_pfft_no_exchange_wg #(.DEPTH(DEPTH),.STAGE(STAGE),.LANE(0),.W(W),.WO(WO)) l0(
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_re(in0_re), .in_im(in0_im),
    .out_valid(lane_valid[0]), .out_last(lane_last[0]), .out_re(out0_re), .out_im(out0_im),
    .tw_addr(ta0), .tw_re(tr0), .tw_im(ti0));
r2sdf_lane_pfft_no_exchange_wg #(.DEPTH(DEPTH),.STAGE(STAGE),.LANE(1),.W(W),.WO(WO)) l1(
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_re(in1_re), .in_im(in1_im),
    .out_valid(lane_valid[1]), .out_last(lane_last[1]), .out_re(out1_re), .out_im(out1_im),
    .tw_addr(ta1), .tw_re(tr1), .tw_im(ti1));
r2sdf_lane_pfft_no_exchange_wg #(.DEPTH(DEPTH),.STAGE(STAGE),.LANE(2),.W(W),.WO(WO)) l2(
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_re(in2_re), .in_im(in2_im),
    .out_valid(lane_valid[2]), .out_last(lane_last[2]), .out_re(out2_re), .out_im(out2_im),
    .tw_addr(ta2), .tw_re(tr2), .tw_im(ti2));
r2sdf_lane_pfft_no_exchange_wg #(.DEPTH(DEPTH),.STAGE(STAGE),.LANE(3),.W(W),.WO(WO)) l3(
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_re(in3_re), .in_im(in3_im),
    .out_valid(lane_valid[3]), .out_last(lane_last[3]), .out_re(out3_re), .out_im(out3_im),
    .tw_addr(ta3), .tw_re(tr3), .tw_im(ti3));
// Twiddle banks: one BRAM bank per pair of lanes that read different exponents.
// Stages 1-6 read one exponent per lane; stage 7 lane 0 and stage 8 lanes 0,2 are trivial.
generate
if (STAGE <= 6) begin : g_tw
    twiddle_bank_wg #(.PORTS(2)) u_bank_01 (
        .clk(clk), .addr_a(ta0), .addr_b(ta1),
        .coeff_a_re(tr0), .coeff_a_im(ti0), .coeff_b_re(tr1), .coeff_b_im(ti1),
        .det_a(), .cor_a(), .det_b(), .cor_b());
    twiddle_bank_wg #(.PORTS(2)) u_bank_23 (
        .clk(clk), .addr_a(ta2), .addr_b(ta3),
        .coeff_a_re(tr2), .coeff_a_im(ti2), .coeff_b_re(tr3), .coeff_b_im(ti3),
        .det_a(), .cor_a(), .det_b(), .cor_b());
end else if (STAGE == 7) begin : g_tw
    assign tr0 = {TW_W{1'b0}};
    assign ti0 = {TW_W{1'b0}};
    twiddle_bank_wg #(.PORTS(2)) u_bank_12 (
        .clk(clk), .addr_a(ta1), .addr_b(ta2),
        .coeff_a_re(tr1), .coeff_a_im(ti1), .coeff_b_re(tr2), .coeff_b_im(ti2),
        .det_a(), .cor_a(), .det_b(), .cor_b());
    twiddle_bank_wg #(.PORTS(1)) u_bank_3 (
        .clk(clk), .addr_a(ta3), .addr_b(10'd0),
        .coeff_a_re(tr3), .coeff_a_im(ti3), .coeff_b_re(), .coeff_b_im(),
        .det_a(), .cor_a(), .det_b(), .cor_b());
end else begin : g_tw
    assign tr0 = {TW_W{1'b0}};
    assign ti0 = {TW_W{1'b0}};
    assign tr2 = {TW_W{1'b0}};
    assign ti2 = {TW_W{1'b0}};
    twiddle_bank_wg #(.PORTS(2)) u_bank_13 (
        .clk(clk), .addr_a(ta1), .addr_b(ta3),
        .coeff_a_re(tr1), .coeff_a_im(ti1), .coeff_b_re(tr3), .coeff_b_im(ti3),
        .det_a(), .cor_a(), .det_b(), .cor_b());
end
endgenerate
assign out_valid = lane_valid[0];
assign out_last = lane_last[0];
wire _unused = in_last ^ lane_last[1] ^ lane_last[2] ^ lane_last[3]
             ^ lane_valid[1] ^ lane_valid[2] ^ lane_valid[3];
endmodule


// Canonical DIF Stage 9 (no twiddle resource): butterflies (lane0,lane2)
// and (lane1,lane3); second lower result rotates by -j. W in -> W+1 out.
(* keep_hierarchy = "yes" *) module pfft_stage9_no_exchange_wg #(
    parameter integer W = fft_wg_pkg::S9_W,
    parameter integer WO = fft_wg_pkg::S9_WO
)(
    input wire clk, input wire rst, input wire in_valid, input wire in_last,
    input wire signed [W-1:0] in0_re, in0_im, in1_re, in1_im,
    input wire signed [W-1:0] in2_re, in2_im, in3_re, in3_im,
    output reg out_valid, output reg out_last,
    output reg signed [WO-1:0] out0_re, out0_im, out1_re, out1_im,
    output reg signed [WO-1:0] out2_re, out2_im, out3_re, out3_im
);
wire signed [W:0] sum02_re = in0_re + in2_re;
wire signed [W:0] sum02_im = in0_im + in2_im;
wire signed [W:0] diff02_re = in0_re - in2_re;
wire signed [W:0] diff02_im = in0_im - in2_im;
wire signed [W:0] sum13_re = in1_re + in3_re;
wire signed [W:0] sum13_im = in1_im + in3_im;
wire signed [W:0] diff13_re = in1_re - in3_re;
wire signed [W:0] diff13_im = in1_im - in3_im;
wire signed [WO-1:0] upper0_re = sum02_re;
wire signed [WO-1:0] upper0_im = sum02_im;
wire signed [WO-1:0] upper1_re = sum13_re;
wire signed [WO-1:0] upper1_im = sum13_im;
wire signed [WO-1:0] lower2_re = diff02_re;
wire signed [WO-1:0] lower2_im = diff02_im;
wire signed [W:0] lower3_pre_re = diff13_re;
wire signed [W:0] lower3_pre_im = diff13_im;
wire signed [WO-1:0] lower3_re = lower3_pre_im;
wire signed [WO-1:0] lower3_im = -lower3_pre_re;

reg valid_pipe, last_pipe;
reg signed [WO-1:0] q0_re,q0_im,q1_re,q1_im,q2_re,q2_im,q3_re,q3_im;
always @(posedge clk) begin
    if (rst) begin
        valid_pipe <= 1'b0;
        last_pipe <= 1'b0;
        out_valid <= 1'b0;
        out_last <= 1'b0;
        q0_re <= {(WO){1'b0}}; q0_im <= {(WO){1'b0}};
        q1_re <= {(WO){1'b0}}; q1_im <= {(WO){1'b0}};
        q2_re <= {(WO){1'b0}}; q2_im <= {(WO){1'b0}};
        q3_re <= {(WO){1'b0}}; q3_im <= {(WO){1'b0}};
        out0_re <= {(WO){1'b0}}; out0_im <= {(WO){1'b0}};
        out1_re <= {(WO){1'b0}}; out1_im <= {(WO){1'b0}};
        out2_re <= {(WO){1'b0}}; out2_im <= {(WO){1'b0}};
        out3_re <= {(WO){1'b0}}; out3_im <= {(WO){1'b0}};
    end else begin
        valid_pipe <= in_valid;
        last_pipe <= in_last;
        out_valid <= valid_pipe;
        out_last <= last_pipe;
        if (in_valid) begin
            q0_re <= upper0_re; q0_im <= upper0_im;
            q1_re <= upper1_re; q1_im <= upper1_im;
            q2_re <= lower2_re; q2_im <= lower2_im;
            q3_re <= lower3_re; q3_im <= lower3_im;
        end
        if (valid_pipe) begin
            out0_re <= q0_re; out0_im <= q0_im;
            out1_re <= q1_re; out1_im <= q1_im;
            out2_re <= q2_re; out2_im <= q2_im;
            out3_re <= q3_re; out3_im <= q3_im;
        end
    end
end
endmodule


// Canonical DIF Stage 10 (no twiddle resource): butterflies
// (lane0,lane1) and (lane2,lane3). W in -> W+1 out.
(* keep_hierarchy = "yes" *) module pfft_stage10_wg #(
    parameter integer W = fft_wg_pkg::S10_W,
    parameter integer WO = fft_wg_pkg::S10_WO
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
 if(rst) begin v1<=0;l1<=0;out_valid<=0;out_last<=0;end else begin
  v1<=in_valid;l1<=in_last;out_valid<=v1;out_last<=l1;
  if(in_valid) begin
   q0r<=s01r;q0i<=s01i;
   q1r<=d01r;q1i<=d01i;
   q2r<=s23r;q2i<=s23i;
   q3r<=d23r;q3i<=d23i;
  end
  if(v1) begin out0_re<=q0r;out0_im<=q0i;out1_re<=q1r;out1_im<=q1i;out2_re<=q2r;out2_im<=q2i;out3_re<=q3r;out3_im<=q3i;end
 end
end
endmodule


(* keep_hierarchy = "yes" *) module p0_pfft_no_exchange_pipeline_wg(
 input wire clk,input wire rst,input wire in_valid,input wire in_last,
 input wire signed [fft_wg_pkg::IN_W-1:0] in0_re,in0_im,in1_re,in1_im,in2_re,in2_im,in3_re,in3_im,
 output wire out_valid,output wire out_last,
 output wire signed [fft_wg_pkg::OUT_W-1:0] out0_re,out0_im,out1_re,out1_im,out2_re,out2_im,out3_re,out3_im
);
import fft_wg_pkg::*;
wire [10:0] v,l;
wire signed [S1_WO-1:0] s1r[0:3],s1i[0:3];
wire signed [S2_WO-1:0] s2r[0:3],s2i[0:3];
wire signed [S3_WO-1:0] s3r[0:3],s3i[0:3];
wire signed [S4_WO-1:0] s4r[0:3],s4i[0:3];
wire signed [S5_WO-1:0] s5r[0:3],s5i[0:3];
wire signed [S6_WO-1:0] s6r[0:3],s6i[0:3];
wire signed [S7_WO-1:0] s7r[0:3],s7i[0:3];
wire signed [S8_WO-1:0] s8r[0:3],s8i[0:3];
wire signed [S9_WO-1:0] s9r[0:3],s9i[0:3];
wire signed [S10_WO-1:0] s10r[0:3],s10i[0:3];
assign v[0]=in_valid;assign l[0]=in_last;
pfft_stage4_sdf_no_exchange_wg #(.DEPTH(128),.STAGE(1),.W(S1_W),.WO(S1_WO)) s1(clk,rst,v[0],l[0],in0_re,in0_im,in1_re,in1_im,in2_re,in2_im,in3_re,in3_im,v[1],l[1],s1r[0],s1i[0],s1r[1],s1i[1],s1r[2],s1i[2],s1r[3],s1i[3]);
pfft_stage4_sdf_no_exchange_wg #(.DEPTH(64), .STAGE(2),.W(S2_W),.WO(S2_WO)) s2(clk,rst,v[1],l[1],s1r[0],s1i[0],s1r[1],s1i[1],s1r[2],s1i[2],s1r[3],s1i[3],v[2],l[2],s2r[0],s2i[0],s2r[1],s2i[1],s2r[2],s2i[2],s2r[3],s2i[3]);
pfft_stage4_sdf_no_exchange_wg #(.DEPTH(32), .STAGE(3),.W(S3_W),.WO(S3_WO)) s3(clk,rst,v[2],l[2],s2r[0],s2i[0],s2r[1],s2i[1],s2r[2],s2i[2],s2r[3],s2i[3],v[3],l[3],s3r[0],s3i[0],s3r[1],s3i[1],s3r[2],s3i[2],s3r[3],s3i[3]);
pfft_stage4_sdf_no_exchange_wg #(.DEPTH(16), .STAGE(4),.W(S4_W),.WO(S4_WO)) s4(clk,rst,v[3],l[3],s3r[0],s3i[0],s3r[1],s3i[1],s3r[2],s3i[2],s3r[3],s3i[3],v[4],l[4],s4r[0],s4i[0],s4r[1],s4i[1],s4r[2],s4i[2],s4r[3],s4i[3]);
pfft_stage4_sdf_no_exchange_wg #(.DEPTH(8),  .STAGE(5),.W(S5_W),.WO(S5_WO)) s5(clk,rst,v[4],l[4],s4r[0],s4i[0],s4r[1],s4i[1],s4r[2],s4i[2],s4r[3],s4i[3],v[5],l[5],s5r[0],s5i[0],s5r[1],s5i[1],s5r[2],s5i[2],s5r[3],s5i[3]);
pfft_stage4_sdf_no_exchange_wg #(.DEPTH(4),  .STAGE(6),.W(S6_W),.WO(S6_WO)) s6(clk,rst,v[5],l[5],s5r[0],s5i[0],s5r[1],s5i[1],s5r[2],s5i[2],s5r[3],s5i[3],v[6],l[6],s6r[0],s6i[0],s6r[1],s6i[1],s6r[2],s6i[2],s6r[3],s6i[3]);
pfft_stage4_sdf_no_exchange_wg #(.DEPTH(2),  .STAGE(7),.W(S7_W),.WO(S7_WO)) s7(clk,rst,v[6],l[6],s6r[0],s6i[0],s6r[1],s6i[1],s6r[2],s6i[2],s6r[3],s6i[3],v[7],l[7],s7r[0],s7i[0],s7r[1],s7i[1],s7r[2],s7i[2],s7r[3],s7i[3]);
pfft_stage4_sdf_no_exchange_wg #(.DEPTH(1),  .STAGE(8),.W(S8_W),.WO(S8_WO)) s8(clk,rst,v[7],l[7],s7r[0],s7i[0],s7r[1],s7i[1],s7r[2],s7i[2],s7r[3],s7i[3],v[8],l[8],s8r[0],s8i[0],s8r[1],s8i[1],s8r[2],s8i[2],s8r[3],s8i[3]);
pfft_stage9_no_exchange_wg #(.W(S9_W),.WO(S9_WO)) s9(clk,rst,v[8],l[8],s8r[0],s8i[0],s8r[1],s8i[1],s8r[2],s8i[2],s8r[3],s8i[3],v[9],l[9],s9r[0],s9i[0],s9r[1],s9i[1],s9r[2],s9i[2],s9r[3],s9i[3]);
pfft_stage10_wg #(.W(S10_W),.WO(S10_WO)) s10(clk,rst,v[9],l[9],s9r[0],s9i[0],s9r[1],s9i[1],s9r[2],s9i[2],s9r[3],s9i[3],v[10],l[10],s10r[0],s10i[0],s10r[1],s10i[1],s10r[2],s10i[2],s10r[3],s10i[3]);
assign out_valid=v[10];assign out_last=l[10];
assign out0_re=s10r[0];assign out0_im=s10i[0];assign out1_re=s10r[1];assign out1_im=s10i[1];
assign out2_re=s10r[2];assign out2_im=s10i[2];assign out3_re=s10r[3];assign out3_im=s10i[3];
wire [10:0] _unused_v = v;
endmodule


// Fair-compare top: same compute pipeline as P0, no terminal frame buffer.
module top_p0_pfft_no_framebuf_v1(
 input wire clk,input wire rst,input wire in_valid,input wire in_last,
 input wire signed[fft_wg_pkg::IN_W-1:0]in0_re,in0_im,in1_re,in1_im,in2_re,in2_im,in3_re,in3_im,
 output wire out_valid,output wire out_last,
 output wire signed[fft_wg_pkg::OUT_W-1:0]out0_re,out0_im,out1_re,out1_im,out2_re,out2_im,out3_re,out3_im
);
p0_pfft_no_exchange_pipeline_wg u_pipeline(
 clk,rst,in_valid,in_last,
 in0_re,in0_im,in1_re,in1_im,in2_re,in2_im,in3_re,in3_im,
 out_valid,out_last,
 out0_re,out0_im,out1_re,out1_im,out2_re,out2_im,out3_re,out3_im
);
endmodule
