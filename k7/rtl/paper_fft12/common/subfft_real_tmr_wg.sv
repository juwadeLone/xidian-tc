`timescale 1ns/1ps

// WP4-R S-family real-TMR stage modules, shared by S0 (s3) and S1 (s2).
// Per stage three complete replicas; per-lane-word voting on data, valid and
// last; one stage mismatch flag per stage.  ROMs hold only the factors the
// stage actually uses (task 3.2); no SECDED in the TMR region.

// Synchronous per-lane twiddle ROM, address presented one beat early (same
// timing as pfft_rom_wp4r).  No SECDED, no rom_style: Vivado infers the
// mapping.
(* keep_hierarchy = "yes" *)
module subfft_rom_wp4r #(
    parameter integer ENTRIES = 128,
    parameter MEM_FILE = "subfft_s1_rom_wp4r.mem"
)(
    input  wire clk,
    input  wire [9:0] addr,
    output reg  signed [fft_wg_pkg::TW_W-1:0] coeff_re,
    output reg  signed [fft_wg_pkg::TW_W-1:0] coeff_im
);
import fft_wg_pkg::*;
localparam integer AW = (ENTRIES <= 1) ? 1 : $clog2(ENTRIES);
reg [2*TW_W-1:0] rom [0:ENTRIES-1];
`ifdef XILINX_SIMULATOR
initial $readmemh({"rtl/paper_fft12/common/", MEM_FILE}, rom);
`else
initial $readmemh(MEM_FILE, rom);
`endif
always @(posedge clk)
    {coeff_re, coeff_im} <= rom[addr[AW-1:0]];
endmodule


// One SubFFT channel of stage 8: DEPTH=1 SDF butterfly (trivial rotation
// only, factor is always 1), then the cross twiddle for lanes 1..3 (own ROM
// per channel; lane 0 does not rotate).  W in / WO out; the cross multiply
// keeps WO bits.
(* keep_hierarchy = "yes" *)
module subfft_stage8_channel_wp4r #(
    parameter integer LANE = 0,
    parameter integer W = fft_wg_pkg::S8_W,
    parameter integer WO = fft_wg_pkg::S8_WO
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire signed [W-1:0] in_re,
    input  wire signed [W-1:0] in_im,
    output wire out_valid,
    output wire out_last,
    output wire signed [WO-1:0] out_re,
    output wire signed [WO-1:0] out_im
);
import fft_wg_pkg::*;
localparam integer MW = W + 1;

// DEPTH=1 SDF control: phase toggles per input beat.
(* DONT_TOUCH = "yes" *) reg phase;
reg primed;
wire phase_next = !in_valid ? phase : ~phase;
always @(posedge clk) begin
    if (rst) begin
        phase <= 1'b0;
        primed <= 1'b0;
    end else begin
        phase <= phase_next;
        if (in_valid && phase)
            primed <= 1'b1;
    end
end
wire phase_second = phase;
wire emit_raw = in_valid && (phase_second || primed);

// 1-deep delay storage (no SECDED in the TMR region).
wire [2*MW-1:0] mem_rd_data;
wire [2*MW-1:0] mem_wr_data;
wp4r_hier_fft_memory_v5 #(.WIDTH(2*MW), .DEPTH(1), .ADDR_W(1)) u_memory (
    .clk(clk), .rd_en(in_valid), .rd_addr(1'b0), .rd_data(mem_rd_data),
    .wr_en(in_valid), .wr_addr(1'b0), .wr_data(mem_wr_data));
wire signed [MW-1:0] memory_re = mem_rd_data[2*MW-1 -: MW];
wire signed [MW-1:0] memory_im = mem_rd_data[MW-1 -: MW];
wire signed [MW-1:0] sum_re = memory_re + in_re;
wire signed [MW-1:0] sum_im = memory_im + in_im;
wire signed [MW-1:0] dif_re = memory_re - in_re;
wire signed [MW-1:0] dif_im = memory_im - in_im;
assign mem_wr_data = {phase_second ? dif_re : {{(MW-W){in_re[W-1]}}, in_re},
                      phase_second ? dif_im : {{(MW-W){in_im[W-1]}}, in_im}};
wire signed [MW-1:0] emit_re = phase_second ? sum_re : memory_re;
wire signed [MW-1:0] emit_im = phase_second ? sum_im : memory_im;

// Trivial rotation (stage-8 channel factor is always 1).
wire signed [WO-1:0] emit_wo_re = emit_re;
wire signed [WO-1:0] emit_wo_im = emit_im;
wire signed [WO-1:0] rot_re, rot_im;
wp4r_hier_fft_complex_rotate_trivial_wg #(.W(WO)) u_rotator (
    .in_re(emit_wo_re), .in_im(emit_wo_im), .exponent(10'd0),
    .out_re(rot_re), .out_im(rot_im));

(* DONT_TOUCH = "yes" *) reg [7:0] beat_count;
always @(posedge clk) begin
    if (rst)
        beat_count <= 8'd0;
    else if (emit_raw)
        beat_count <= beat_count + 1'b1;
end
wire [7:0] beat_count_next = emit_raw ? (beat_count + 8'd1) : beat_count;

// Cross ROM address one beat early: beat j (emitted now, out_count == j) is
// captured by the multiplier next beat, so the ROM reads bitrev8(j + 1) now.
function automatic [7:0] bitrev8(input [7:0] value);
    integer i;
    begin
        for (i = 0; i < 8; i = i + 1)
            bitrev8[i] = value[7-i];
    end
endfunction
wire [9:0] cross_addr = {2'b00, bitrev8(beat_count_next)};
wire signed [TW_W-1:0] xw_re, xw_im;
generate
if (LANE == 0) begin : g_no_cross
    assign xw_re = {TW_W{1'b0}};
    assign xw_im = {TW_W{1'b0}};
end else if (LANE == 1) begin : g_cross_rom
    subfft_rom_wp4r #(.ENTRIES(256), .MEM_FILE("subfft_x1_rom_wp4r.mem")) u_rom (
        .clk(clk), .addr(cross_addr), .coeff_re(xw_re), .coeff_im(xw_im));
end else if (LANE == 2) begin : g_cross_rom
    subfft_rom_wp4r #(.ENTRIES(256), .MEM_FILE("subfft_x2_rom_wp4r.mem")) u_rom (
        .clk(clk), .addr(cross_addr), .coeff_re(xw_re), .coeff_im(xw_im));
end else begin : g_cross_rom
    subfft_rom_wp4r #(.ENTRIES(256), .MEM_FILE("subfft_x3_rom_wp4r.mem")) u_rom (
        .clk(clk), .addr(cross_addr), .coeff_re(xw_re), .coeff_im(xw_im));
end
endgenerate

wire cross_valid;
wire signed [WO-1:0] rotated_re, rotated_im;
reg ml1, ml2, ml3;
generate
if (LANE == 0) begin : g_bypass
    reg bv1, bv2, bv3;
    reg signed [WO-1:0] br1, bi1, br2, bi2, br3, bi3;
    always @(posedge clk) begin
        if (rst) begin
            bv1 <= 1'b0; bv2 <= 1'b0; bv3 <= 1'b0;
            br1 <= {WO{1'b0}}; bi1 <= {WO{1'b0}};
            br2 <= {WO{1'b0}}; bi2 <= {WO{1'b0}};
            br3 <= {WO{1'b0}}; bi3 <= {WO{1'b0}};
        end else begin
            bv1 <= emit_raw;
            if (emit_raw) begin br1 <= rot_re; bi1 <= rot_im; end
            bv2 <= bv1; br2 <= br1; bi2 <= bi1;
            bv3 <= bv2; br3 <= br2; bi3 <= bi2;
        end
    end
    assign cross_valid = bv3;
    assign rotated_re = br3;
    assign rotated_im = bi3;
end else begin : g_cross_mul
    wp4r_hier_fft_complex_mul_wg_pipe #(.WI(WO), .WO(WO)) u_cross (
        .clk(clk), .rst(rst), .valid_in(emit_raw),
        .in_re(rot_re), .in_im(rot_im), .coeff_re(xw_re), .coeff_im(xw_im),
        .valid_out(cross_valid), .out_re(rotated_re), .out_im(rotated_im));
end
endgenerate

always @(posedge clk) begin
    if (rst) begin
        ml1 <= 1'b0;
        ml2 <= 1'b0;
        ml3 <= 1'b0;
    end else begin
        ml1 <= emit_raw && (beat_count == 8'd255);
        ml2 <= ml1;
        ml3 <= ml2;
    end
end
assign out_valid = cross_valid;
assign out_last = ml3;
assign out_re = rotated_re;
assign out_im = rotated_im;
endmodule


// Stage-8 replica: four independent channels.
(* keep_hierarchy = "yes" *)
module subfft_stage8_replica_wp4r #(
    parameter integer W = fft_wg_pkg::S8_W,
    parameter integer WO = fft_wg_pkg::S8_WO
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire [8*W-1:0] in_data,
    output wire out_valid,
    output wire out_last,
    output wire [8*WO-1:0] out_data
);
wire [3:0] ch_valid, ch_last;
generate
for (genvar lane = 0; lane < 4; lane = lane + 1) begin : g_ch
    (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
    subfft_stage8_channel_wp4r #(.LANE(lane), .W(W), .WO(WO)) u_channel (
        .clk(clk), .rst(rst), .in_valid(in_valid),
        .in_re(in_data[(8-2*lane)*W-1 -: W]),
        .in_im(in_data[(7-2*lane)*W-1 -: W]),
        .out_valid(ch_valid[lane]), .out_last(ch_last[lane]),
        .out_re(out_data[(8-2*lane)*WO-1 -: WO]),
        .out_im(out_data[(7-2*lane)*WO-1 -: WO]));
end
endgenerate
assign out_valid = ch_valid[0];
assign out_last = ch_last[0];
wire _unused = ^{in_last, ch_valid[3:1], ch_last[3:1]};
endmodule


// Stage-9 replica: merge layer with -j on the d13 word; own beat counter
// for last.
(* keep_hierarchy = "yes" *)
module subfft_stage9_replica_wp4r #(
    parameter integer W = fft_wg_pkg::S9_W,
    parameter integer WO = fft_wg_pkg::S9_WO
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire [8*W-1:0] in_data,
    output wire out_valid,
    output wire out_last,
    output wire [8*WO-1:0] out_data
);
wire core_valid, core_last;
(* DONT_TOUCH = "yes" *)
wp4r_hier_subfft_stage9_wg #(.W(W), .WO(WO)) u_core (
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
    .in0_re(in_data[8*W-1 -: W]), .in0_im(in_data[7*W-1 -: W]),
    .in1_re(in_data[6*W-1 -: W]), .in1_im(in_data[5*W-1 -: W]),
    .in2_re(in_data[4*W-1 -: W]), .in2_im(in_data[3*W-1 -: W]),
    .in3_re(in_data[2*W-1 -: W]), .in3_im(in_data[W-1:0]),
    .out_valid(core_valid), .out_last(core_last),
    .out0_re(out_data[8*WO-1 -: WO]), .out0_im(out_data[7*WO-1 -: WO]),
    .out1_re(out_data[6*WO-1 -: WO]), .out1_im(out_data[5*WO-1 -: WO]),
    .out2_re(out_data[4*WO-1 -: WO]), .out2_im(out_data[3*WO-1 -: WO]),
    .out3_re(out_data[2*WO-1 -: WO]), .out3_im(out_data[WO-1:0]));
(* DONT_TOUCH = "yes" *) reg [7:0] beat_count;
always @(posedge clk) begin
    if (rst)
        beat_count <= 8'd0;
    else if (core_valid)
        beat_count <= beat_count + 1'b1;
end
assign out_valid = core_valid;
assign out_last = core_valid && (beat_count == 8'd255);
wire _unused = core_last;
endmodule


// Stage-10 replica: merge layer; own beat counter for last.
(* keep_hierarchy = "yes" *)
module subfft_stage10_replica_wp4r #(
    parameter integer W = fft_wg_pkg::S10_W,
    parameter integer WO = fft_wg_pkg::S10_WO
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire [8*W-1:0] in_data,
    output wire out_valid,
    output wire out_last,
    output wire [8*WO-1:0] out_data
);
wire core_valid, core_last;
(* DONT_TOUCH = "yes" *)
wp4r_hier_subfft_stage10_wg #(.W(W), .WO(WO)) u_core (
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
    .in0_re(in_data[8*W-1 -: W]), .in0_im(in_data[7*W-1 -: W]),
    .in1_re(in_data[6*W-1 -: W]), .in1_im(in_data[5*W-1 -: W]),
    .in2_re(in_data[4*W-1 -: W]), .in2_im(in_data[3*W-1 -: W]),
    .in3_re(in_data[2*W-1 -: W]), .in3_im(in_data[W-1:0]),
    .out_valid(core_valid), .out_last(core_last),
    .out0_re(out_data[8*WO-1 -: WO]), .out0_im(out_data[7*WO-1 -: WO]),
    .out1_re(out_data[6*WO-1 -: WO]), .out1_im(out_data[5*WO-1 -: WO]),
    .out2_re(out_data[4*WO-1 -: WO]), .out2_im(out_data[3*WO-1 -: WO]),
    .out3_re(out_data[2*WO-1 -: WO]), .out3_im(out_data[WO-1:0]));
(* DONT_TOUCH = "yes" *) reg [7:0] beat_count;
always @(posedge clk) begin
    if (rst)
        beat_count <= 8'd0;
    else if (core_valid)
        beat_count <= beat_count + 1'b1;
end
assign out_valid = core_valid;
assign out_last = core_valid && (beat_count == 8'd255);
wire _unused = core_last;
endmodule


// S-family real-TMR stage: three complete replicas, per-lane-word data
// votes, valid/last votes, one mismatch flag.
(* keep_hierarchy = "yes" *)
module subfft_real_tmr_stage_wg #(
    parameter integer STAGE = 8,
    parameter integer DEPTH = 1,
    parameter integer W = fft_wg_pkg::S8_W,
    parameter integer WO = fft_wg_pkg::S8_WO
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire [8*W-1:0] in_data,
    output wire out_valid,
    output wire out_last,
    output wire [8*WO-1:0] out_data,
    output wire stage_mismatch
);
import fft_wg_pkg::*;
wire [3:0] rep_valid [0:2];
wire [3:0] rep_last [0:2];
wire [8*WO-1:0] rep_data [0:2];
generate
for (genvar r = 0; r < 3; r = r + 1) begin : g_replica
    wire v, l;
    if (STAGE <= 7) begin : g_sdf
        (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
        subfft_sdf_replica_wp4r #(.STAGE(STAGE), .DEPTH(DEPTH), .W(W), .WO(WO)) u_copy (
            .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
            .in_data(in_data), .out_valid(v), .out_last(l),
            .out_data(rep_data[r]));
    end else if (STAGE == 8) begin : g_s8
        (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
        subfft_stage8_replica_wp4r #(.W(W), .WO(WO)) u_copy (
            .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
            .in_data(in_data), .out_valid(v), .out_last(l),
            .out_data(rep_data[r]));
    end else if (STAGE == 9) begin : g_s9
        (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
        subfft_stage9_replica_wp4r #(.W(W), .WO(WO)) u_copy (
            .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
            .in_data(in_data), .out_valid(v), .out_last(l),
            .out_data(rep_data[r]));
    end else begin : g_s10
        (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
        subfft_stage10_replica_wp4r #(.W(W), .WO(WO)) u_copy (
            .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
            .in_data(in_data), .out_valid(v), .out_last(l),
            .out_data(rep_data[r]));
    end
    assign rep_valid[r] = {4{v}};
    assign rep_last[r] = {4{l}};
end
endgenerate

wire [3:0] voted_valid, voted_last;
wire valid_diff, last_diff;
wp4r_hier_tmr_vote_w #(.W(4)) u_valid_vote (
    .a(rep_valid[0]), .b(rep_valid[1]), .c(rep_valid[2]),
    .y(voted_valid), .mismatch(valid_diff));
wp4r_hier_tmr_vote_w #(.W(4)) u_last_vote (
    .a(rep_last[0]), .b(rep_last[1]), .c(rep_last[2]),
    .y(voted_last), .mismatch(last_diff));
wire [3:0] data_diff;
generate
for (genvar lane = 0; lane < 4; lane = lane + 1) begin : g_vote
    wp4r_hier_tmr_vote_w #(.W(2*WO)) u_data_vote (
        .a(rep_data[0][(8-2*lane)*WO-1 -: 2*WO]),
        .b(rep_data[1][(8-2*lane)*WO-1 -: 2*WO]),
        .c(rep_data[2][(8-2*lane)*WO-1 -: 2*WO]),
        .y(out_data[(8-2*lane)*WO-1 -: 2*WO]),
        .mismatch(data_diff[lane]));
end
endgenerate
tmr_stage_flag_wg #(.POINTS(6)) u_flag (
    .clk(clk), .rst(rst), .voted_valid(voted_valid[0]),
    .mismatch_points({data_diff, valid_diff, last_diff}), .flag(stage_mismatch));
assign out_valid = voted_valid[0];
assign out_last = voted_last[0];
endmodule

// Stage 1-7 replica: four independent SDF channels (S1).
(* keep_hierarchy = "yes" *)
module subfft_sdf_replica_wp4r #(
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
    output wire [8*WO-1:0] out_data
);
wire [3:0] ch_valid, ch_last;
generate
for (genvar lane = 0; lane < 4; lane = lane + 1) begin : g_ch
    (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
    subfft_sdf_channel_wp4r #(.STAGE(STAGE), .DEPTH(DEPTH), .W(W), .WO(WO),
        .MEM_FILE(STAGE == 1 ? "subfft_s1_rom_wp4r.mem" :
                  STAGE == 2 ? "subfft_s2_rom_wp4r.mem" :
                  STAGE == 3 ? "subfft_s3_rom_wp4r.mem" :
                  STAGE == 4 ? "subfft_s4_rom_wp4r.mem" :
                  STAGE == 5 ? "subfft_s5_rom_wp4r.mem" :
                  STAGE == 6 ? "subfft_s6_rom_wp4r.mem" : "")) u_channel (
        .clk(clk), .rst(rst), .in_valid(in_valid),
        .in_re(in_data[(8-2*lane)*W-1 -: W]),
        .in_im(in_data[(7-2*lane)*W-1 -: W]),
        .out_valid(ch_valid[lane]), .out_last(ch_last[lane]),
        .out_re(out_data[(8-2*lane)*WO-1 -: WO]),
        .out_im(out_data[(7-2*lane)*WO-1 -: WO]));
end
endgenerate
assign out_valid = ch_valid[0];
assign out_last = ch_last[0];
wire _unused = ^{in_last, ch_valid[3:1], ch_last[3:1]};
endmodule
