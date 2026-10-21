`timescale 1ns/1ps

// WP4-R S0 (repository directory s3): SubFFT with per-stage [6,4,3] ECC on
// stages 1-7 and real TMR on stages 8-10 (subfft_real_tmr_stage_wg, shared
// with S1).  Unlike P0 (p1), the six lanes of an ECC stage are fully
// independent: each has its own phase counter, its own SECDED delay memory
// (late write + forwarding), butterfly, rotator and ROM.  The stage-entry
// encoder feeds all six lanes; the shared decoder's beat alignment is the
// 6-of-4 majority of the six lane valids (last and sum likewise).

// ---------------------------------------------------------------------------
// Single-word SECDED delay memory, "write one beat late + forward" (task
// 3.4): write enable/address/data are registered one beat, then encoded and
// written; reading the address written on the previous beat forwards the
// pending word.  One SECDED codeword per lane memory.
(* keep_hierarchy = "yes" *)
module s0_secded_delay_wg #(
    parameter integer DATA_W = 34,
    parameter integer DEPTH = 128,
    parameter integer AW = (DEPTH <= 1) ? 1 : $clog2(DEPTH),
    parameter integer CODE_W = DATA_W + 7
)(
    input  wire              clk,
    input  wire              rd_en,
    input  wire [AW-1:0]     rd_addr,
    input  wire              pending_wr_en,
    input  wire [AW-1:0]     pending_wr_addr,
    input  wire [DATA_W-1:0] wr_data,
    input  wire              forward_active,
    output wire [DATA_W-1:0] rd_data,
    output wire [CODE_W-1:0] read_code
);
reg [DATA_W-1:0] pending_data;
always @(posedge clk)
    pending_data <= wr_data;

wire [CODE_W-1:0] write_code;
wp4r_hier_secded_encode_w #(.DATA_W(DATA_W), .PARITY(6), .CODE_W(CODE_W)) u_encode (
    .data(pending_data), .codeword(write_code));
wire [DATA_W-1:0] decoded;
wp4r_hier_secded_decode_w #(.DATA_W(DATA_W), .PARITY(6), .CODE_W(CODE_W)) u_decode (
    .codeword(read_code), .data(decoded), .detected(), .corrected());
assign rd_data = forward_active ? pending_data : decoded;

wp4r_hier_fft_memory_v5 #(.WIDTH(CODE_W), .DEPTH(DEPTH), .ADDR_W(AW)) u_mem (
    .clk(clk), .rd_en(rd_en), .rd_addr(rd_addr), .rd_data(read_code),
    .wr_en(pending_wr_en), .wr_addr(pending_wr_addr), .wr_data(write_code));
endmodule


// ---------------------------------------------------------------------------
// One ECC lane of a SubFFT stage (functional or check; check lanes run at
// W+2 / WO+2).  Boundary words are full-width products (BW = W+20 / W+22,
// 16 fractional bits); beats that do not go through the multiplier are
// shifted left by 16 instead.
(* keep_hierarchy = "yes" *)
module s0_ecc_lane_wp4r #(
    parameter integer STAGE = 1,
    parameter integer DEPTH = 128,
    parameter integer W = 16,
    parameter integer WO = 18,
    parameter integer BW = W + 20,
    parameter MEM_FILE = ""
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire signed [W-1:0] in_re,
    input  wire signed [W-1:0] in_im,
    output wire bnd_valid,
    output wire bnd_last,
    output wire bnd_sum,
    output wire signed [BW-1:0] boundary_re,
    output wire signed [BW-1:0] boundary_im
);
import fft_wg_pkg::*;
localparam integer MW = W + 1;
localparam integer DW = 2*MW;
localparam integer AW = (DEPTH <= 1) ? 1 : $clog2(DEPTH);
localparam integer SYNC_READ = (DEPTH == 128);
localparam integer TRIVIAL = (STAGE == 7);

wire ctl_ivq, ctl_rd_en, ctl_wv, ctl_ws, ctl_wp, ctl_pwe, ctl_fwd, emit_raw, raw_is_last;
wire [AW-1:0] ctl_rd_addr, ctl_wa, ctl_pwa;
wire [7:0] out_count;
(* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
pfft_sdf_control_wp4r #(.DEPTH(DEPTH), .AW(AW)) u_control (
    .clk(clk), .rst(rst), .in_valid(in_valid), .input_valid_q(ctl_ivq),
    .rd_en(ctl_rd_en), .rd_addr(ctl_rd_addr),
    .work_valid(ctl_wv), .work_second(ctl_ws), .work_primed(ctl_wp),
    .work_addr(ctl_wa), .pending_wr_en(ctl_pwe), .pending_wr_addr(ctl_pwa),
    .forward_active(ctl_fwd), .emit_raw(emit_raw), .raw_is_last(raw_is_last),
    .out_count(out_count));
wire [7:0] out_count_next = out_count + emit_raw;

reg signed [W-1:0] in_re_q, in_im_q, req_re, req_im;
always @(posedge clk) begin
    if (in_valid) begin
        in_re_q <= in_re;
        in_im_q <= in_im;
    end
    if (SYNC_READ && ctl_ivq) begin
        req_re <= in_re_q;
        req_im <= in_im_q;
    end
end
wire signed [W-1:0] work_re = SYNC_READ ? req_re : in_re_q;
wire signed [W-1:0] work_im = SYNC_READ ? req_im : in_im_q;

// SECDED delay storage, one codeword per lane.
wire [DW-1:0] mem_rd_data;
wire [DW-1:0] mem_wr_data;
(* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
s0_secded_delay_wg #(.DATA_W(DW), .DEPTH(DEPTH), .AW(AW)) u_memory (
    .clk(clk), .rd_en(ctl_rd_en), .rd_addr(ctl_rd_addr),
    .pending_wr_en(ctl_pwe), .pending_wr_addr(ctl_pwa),
    .wr_data(mem_wr_data), .forward_active(ctl_fwd),
    .rd_data(mem_rd_data), .read_code());

wire signed [MW-1:0] memory_re = mem_rd_data[DW-1 -: MW];
wire signed [MW-1:0] memory_im = mem_rd_data[MW-1 -: MW];
wire signed [MW-1:0] work_ext_re = work_re;
wire signed [MW-1:0] work_ext_im = work_im;
wire signed [MW-1:0] sum_re = memory_re + work_ext_re;
wire signed [MW-1:0] sum_im = memory_im + work_ext_im;
wire signed [MW-1:0] difference_re = memory_re - work_ext_re;
wire signed [MW-1:0] difference_im = memory_im - work_ext_im;
wire signed [MW-1:0] store_re = ctl_ws ? difference_re : work_ext_re;
wire signed [MW-1:0] store_im = ctl_ws ? difference_im : work_ext_im;
assign mem_wr_data = {store_re, store_im};
wire signed [MW-1:0] emit_re = ctl_ws ? sum_re : memory_re;
wire signed [MW-1:0] emit_im = ctl_ws ? sum_im : memory_im;

// Upper (sum) beats bypass the rotation: the boundary is the sum shifted
// left by TW_FRAC, tracked through a plain 3-beat pipe.  pv marks the path
// of the beat reaching the boundary.
wire signed [BW-1:0] emit_bw_re = emit_re;
wire signed [BW-1:0] emit_bw_im = emit_im;
wire signed [BW-1:0] upper_fmt_re = emit_bw_re <<< TW_FRAC;
wire signed [BW-1:0] upper_fmt_im = emit_bw_im <<< TW_FRAC;
reg signed [BW-1:0] u1r, u1i, u2r, u2i, u3r, u3i;
reg rv1, rv2, rv3, rl1, rl2, rl3, rs1, rs2, rs3, pv1, pv2, pv3;
always @(posedge clk) begin
    if (rst) begin
        rv1 <= 1'b0; rv2 <= 1'b0; rv3 <= 1'b0;
        rl1 <= 1'b0; rl2 <= 1'b0; rl3 <= 1'b0;
        rs1 <= 1'b0; rs2 <= 1'b0; rs3 <= 1'b0;
        pv1 <= 1'b0; pv2 <= 1'b0; pv3 <= 1'b0;
        u1r <= {BW{1'b0}}; u1i <= {BW{1'b0}};
        u2r <= {BW{1'b0}}; u2i <= {BW{1'b0}};
        u3r <= {BW{1'b0}}; u3i <= {BW{1'b0}};
    end else begin
        rv1 <= emit_raw;
        rv2 <= rv1;
        rv3 <= rv2;
        rl1 <= emit_raw && raw_is_last;
        rl2 <= rl1;
        rl3 <= rl2;
        rs1 <= emit_raw && ctl_ws;
        rs2 <= rs1;
        rs3 <= rs2;
        pv1 <= ctl_ws;
        pv2 <= pv1;
        pv3 <= pv2;
        u1r <= upper_fmt_re; u1i <= upper_fmt_im;
        u2r <= u1r;        u2i <= u1i;
        u3r <= u2r;        u3i <= u2i;
    end
end
wire [2:0] rot_ce = {rv2, rv1, emit_raw};

wire signed [BW-1:0] lower_bnd_re, lower_bnd_im;
generate
if (!TRIVIAL) begin : g_multiply
    // Per-lane ROM, one read per beat; the index is the work address of the
    // beat whose word the multiplier captures (out_count mod DEPTH), read
    // one beat early.
    wire [9:0] rom_index_next = {{(10-AW){1'b0}}, out_count_next[AW-1:0]};
    wire signed [TW_W-1:0] tw_re, tw_im;
    subfft_rom_wp4r #(.ENTRIES(DEPTH), .MEM_FILE(MEM_FILE)) u_rom (
        .clk(clk), .addr(rom_index_next), .coeff_re(tw_re), .coeff_im(tw_im));
    fft_complex_mul_full_wp4r #(.WI(MW), .FW(BW)) u_rotator (
        .clk(clk), .rst(rst), .ce(rot_ce),
        .in_re(emit_re), .in_im(emit_im), .coeff_re(tw_re), .coeff_im(tw_im),
        .out_re(lower_bnd_re), .out_im(lower_bnd_im));
end else begin : g_trivial
    wire signed [WO-1:0] emit_wo_re = emit_re;
    wire signed [WO-1:0] emit_wo_im = emit_im;
    wire [9:0] exp_now = out_count[0] ? 10'd256 : 10'd0;
    wire signed [WO-1:0] rot_re, rot_im;
    wp4r_hier_fft_complex_rotate_trivial_wg #(.W(WO)) u_rotator (
        .in_re(emit_wo_re), .in_im(emit_wo_im), .exponent(exp_now),
        .out_re(rot_re), .out_im(rot_im));
    reg signed [WO-1:0] t1r, t1i, t2r, t2i, t3r, t3i;
    always @(posedge clk) begin
        if (rst) begin
            t1r <= {WO{1'b0}}; t1i <= {WO{1'b0}};
            t2r <= {WO{1'b0}}; t2i <= {WO{1'b0}};
            t3r <= {WO{1'b0}}; t3i <= {WO{1'b0}};
        end else begin
            t1r <= rot_re; t1i <= rot_im;
            t2r <= t1r;    t2i <= t1i;
            t3r <= t2r;    t3i <= t2i;
        end
    end
    assign lower_bnd_re = {{(BW-WO-16){t3r[WO-1]}}, t3r, {16{1'b0}}};
    assign lower_bnd_im = {{(BW-WO-16){t3i[WO-1]}}, t3i, {16{1'b0}}};
end
endgenerate

assign boundary_re = pv3 ? u3r : lower_bnd_re;
assign boundary_im = pv3 ? u3i : lower_bnd_im;
assign bnd_valid = rv3;
assign bnd_last = rl3;
assign bnd_sum = rs3;
wire _unused = ^{ctl_wv, ctl_wp, ctl_wa, rot_ce};
endmodule


// ---------------------------------------------------------------------------
// ECC stage: entry encoder, six independent lanes, 6-of-4 beat alignment,
// shared syndrome decoder.
(* keep_hierarchy = "yes" *)
module s0_ecc_stage_wp4r #(
    parameter integer STAGE = 1,
    parameter integer DEPTH = 128,
    parameter integer W = fft_wg_pkg::S1_W,
    parameter integer WO = fft_wg_pkg::S1_WO
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire [8*W-1:0] in_data,
    output wire out_valid,
    output wire out_last,
    output wire [8*WO-1:0] out_data,
    output wire det,
    output wire cor,
    output wire unc,
    output wire [2:0] loc,
    output wire beat_sum
);
import fft_wg_pkg::*;
localparam integer CWI = W + 2;
localparam integer CWO = WO + 2;
localparam integer FBW = W + 20;
localparam integer KW = W + 22;

wire signed [CWI-1:0] enc4r, enc4i, enc5r, enc5i;
ecc643_encode_wp4r #(.W(W)) u_encode (
    .in_data(in_data), .c4_re(enc4r), .c4_im(enc4i),
    .c5_re(enc5r), .c5_im(enc5i));

wire [5:0] lane_v, lane_l, lane_s;
wire signed [FBW-1:0] bnd_re [0:3];
wire signed [FBW-1:0] bnd_im [0:3];
wire signed [KW-1:0] bnd_c4r, bnd_c4i, bnd_c5r, bnd_c5i;
generate
for (genvar lane = 0; lane < 6; lane = lane + 1) begin : g_lane
    localparam integer LW = (lane < 4) ? W : CWI;
    localparam integer LWO = (lane < 4) ? WO : CWO;
    localparam integer LBW = (lane < 4) ? FBW : KW;
    wire signed [LW-1:0] lane_in_re, lane_in_im;
    if (lane < 4) begin : g_func
        assign lane_in_re = in_data[(8-2*lane)*W-1 -: W];
        assign lane_in_im = in_data[(7-2*lane)*W-1 -: W];
    end else begin : g_check
        assign lane_in_re = (lane == 4) ? enc4r : enc5r;
        assign lane_in_im = (lane == 4) ? enc4i : enc5i;
    end
    wire signed [LBW-1:0] lane_bnd_re, lane_bnd_im;
    (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
    s0_ecc_lane_wp4r #(.STAGE(STAGE), .DEPTH(DEPTH), .W(LW), .WO(LWO),
                       .BW(LBW),
                       .MEM_FILE(STAGE <= 6 ?
                           (STAGE == 1 ? "subfft_s1_rom_wp4r.mem" :
                            STAGE == 2 ? "subfft_s2_rom_wp4r.mem" :
                            STAGE == 3 ? "subfft_s3_rom_wp4r.mem" :
                            STAGE == 4 ? "subfft_s4_rom_wp4r.mem" :
                            STAGE == 5 ? "subfft_s5_rom_wp4r.mem" :
                                         "subfft_s6_rom_wp4r.mem") : ""))
        u_lane (
        .clk(clk), .rst(rst), .in_valid(in_valid),
        .in_re(lane_in_re), .in_im(lane_in_im),
        .bnd_valid(lane_v[lane]), .bnd_last(lane_l[lane]),
        .bnd_sum(lane_s[lane]),
        .boundary_re(lane_bnd_re), .boundary_im(lane_bnd_im));
    if (lane < 4) begin : g_func_bnd
        assign bnd_re[lane] = lane_bnd_re;
        assign bnd_im[lane] = lane_bnd_im;
    end else if (lane == 4) begin : g_c4_bnd
        assign bnd_c4r = lane_bnd_re;
        assign bnd_c4i = lane_bnd_im;
    end else begin : g_c5_bnd
        assign bnd_c5r = lane_bnd_re;
        assign bnd_c5i = lane_bnd_im;
    end
end
endgenerate

// 6-of-4 majority beat alignment over the six lane valids (last, sum
// likewise): never a single lane's valid.
wire [2:0] cnt_v = lane_v[0] + lane_v[1] + lane_v[2] + lane_v[3] + lane_v[4] + lane_v[5];
wire [2:0] cnt_l = lane_l[0] + lane_l[1] + lane_l[2] + lane_l[3] + lane_l[4] + lane_l[5];
wire [2:0] cnt_s = lane_s[0] + lane_s[1] + lane_s[2] + lane_s[3] + lane_s[4] + lane_s[5];
wire voted_valid = (cnt_v >= 3'd4);
wire voted_last = (cnt_l >= 3'd4);
wire voted_sum = (cnt_s >= 3'd4);

ecc643_decode_wg #(.W(W), .WO(WO), .FBW(FBW), .KW(KW)) u_decode (
    .clk(clk), .rst(rst), .in_valid(voted_valid), .in_last(voted_last),
    .in_sum(voted_sum),
    .f0_re(bnd_re[0]), .f0_im(bnd_im[0]),
    .f1_re(bnd_re[1]), .f1_im(bnd_im[1]),
    .f2_re(bnd_re[2]), .f2_im(bnd_im[2]),
    .f3_re(bnd_re[3]), .f3_im(bnd_im[3]),
    .c4_re(bnd_c4r), .c4_im(bnd_c4i),
    .c5_re(bnd_c5r), .c5_im(bnd_c5i),
    .out_valid(out_valid), .out_last(out_last), .out_data(out_data),
    .det(det), .cor(cor), .unc(unc), .loc(loc), .beat_sum(beat_sum),
    .sy0r(), .sy0i(), .sy1r(), .sy1i());
wire _unused = in_last;
endmodule


// ---------------------------------------------------------------------------
(* keep_hierarchy = "yes" *)
module s0_subfft_ecc_pipeline_wp4r (
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire [8*fft_wg_pkg::IN_W-1:0] in_data,
    output wire out_valid,
    output wire out_last,
    output wire [8*fft_wg_pkg::OUT_W-1:0] out_data,
    output wire [9:0] win_second,
    output wire [9:0] up_detected,
    output wire [9:0] up_corrected,
    output wire [9:0] up_uncorrectable,
    output wire [29:0] up_error_location,
    output wire [9:0] stage_mismatch
);
import fft_wg_pkg::*;
function automatic integer stage_w(input integer s);
    case (s)
        1: stage_w = S1_W;
        2: stage_w = S2_W;
        3: stage_w = S3_W;
        4: stage_w = S4_W;
        5: stage_w = S5_W;
        6: stage_w = S6_W;
        7: stage_w = S7_W;
        8: stage_w = S8_W;
        9: stage_w = S9_W;
        10: stage_w = S10_W;
        default: stage_w = IN_W;
    endcase
endfunction
function automatic integer stage_wo(input integer s);
    case (s)
        1: stage_wo = S1_WO;
        2: stage_wo = S2_WO;
        3: stage_wo = S3_WO;
        4: stage_wo = S4_WO;
        5: stage_wo = S5_WO;
        6: stage_wo = S6_WO;
        7: stage_wo = S7_WO;
        8: stage_wo = S8_WO;
        9: stage_wo = S9_WO;
        10: stage_wo = S10_WO;
        default: stage_wo = OUT_W;
    endcase
endfunction

generate
for (genvar s = 1; s <= 10; s = s + 1) begin : g_stage
    localparam integer W = stage_w(s);
    localparam integer WO = stage_wo(s);
    localparam integer DEPTH = (s <= 8) ? (128 >> (s-1)) : 1;
    wire vin, lin, vout, lout;
    wire [8*W-1:0] din;
    wire [8*WO-1:0] dout;
    if (s == 1) begin : g_first
        assign vin = in_valid;
        assign lin = in_last;
        assign din = in_data;
    end else begin : g_later
        assign vin = g_stage[s-1].vout;
        assign lin = g_stage[s-1].lout;
        assign din = g_stage[s-1].dout;
    end
    if (s <= 7) begin : g_ecc
        s0_ecc_stage_wp4r #(.STAGE(s), .DEPTH(DEPTH), .W(W), .WO(WO)) u_stage (
            .clk(clk), .rst(rst), .in_valid(vin), .in_last(lin), .in_data(din),
            .out_valid(vout), .out_last(lout), .out_data(dout),
            .det(up_detected[s-1]), .cor(up_corrected[s-1]),
            .unc(up_uncorrectable[s-1]), .loc(up_error_location[3*s-1 -: 3]),
            .beat_sum(win_second[s-1]));
        assign stage_mismatch[s-1] = 1'b0;
    end else begin : g_tmr
        wire mismatch;
        subfft_real_tmr_stage_wg #(.STAGE(s), .W(W), .WO(WO)) u_stage (
            .clk(clk), .rst(rst), .in_valid(vin), .in_last(lin), .in_data(din),
            .out_valid(vout), .out_last(lout), .out_data(dout),
            .stage_mismatch(mismatch));
        assign stage_mismatch[s-1] = mismatch;
        assign up_detected[s-1] = mismatch;
        assign up_corrected[s-1] = mismatch;
        assign up_uncorrectable[s-1] = 1'b0;
        assign up_error_location[3*s-1 -: 3] = 3'd0;
        assign win_second[s-1] = 1'b0;
    end
end
endgenerate
assign out_valid = g_stage[10].vout;
assign out_last = g_stage[10].lout;
assign out_data = g_stage[10].dout;
endmodule


module top_s3_subfft_ecc (
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in0_re, in0_im,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in1_re, in1_im,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in2_re, in2_im,
    input  wire signed [fft_wg_pkg::IN_W-1:0] in3_re, in3_im,
    output wire out_valid,
    output wire out_last,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out0_re, out0_im,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out1_re, out1_im,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out2_re, out2_im,
    output wire signed [fft_wg_pkg::OUT_W-1:0] out3_re, out3_im,
    output wire [9:0] win_second,
    output wire [9:0] up_detected,
    output wire [9:0] up_corrected,
    output wire [9:0] up_uncorrectable,
    output wire [29:0] up_error_location
);
wire [9:0] stage_mismatch;
s0_subfft_ecc_pipeline_wp4r u_pipeline (
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
    .in_data({in0_re, in0_im, in1_re, in1_im, in2_re, in2_im, in3_re, in3_im}),
    .out_valid(out_valid), .out_last(out_last),
    .out_data({out0_re, out0_im, out1_re, out1_im, out2_re, out2_im, out3_re, out3_im}),
    .win_second(win_second), .up_detected(up_detected),
    .up_corrected(up_corrected), .up_uncorrectable(up_uncorrectable),
    .up_error_location(up_error_location), .stage_mismatch(stage_mismatch));
endmodule
