`timescale 1ns/1ps

(* keep_hierarchy = "yes" *)
module pfft_rom_wp4r #(
    parameter integer ENTRIES = 4,
    parameter integer SHIFT = 7,
    parameter MEM_FILE = "pfft_s2_rom_wp4r.mem"
)(
    input  wire clk,
    input  wire [9:0] exponent,
    output reg  signed [fft_wg_pkg::TW_W-1:0] coeff_re,
    output reg  signed [fft_wg_pkg::TW_W-1:0] coeff_im
);
import fft_wg_pkg::*;
localparam integer AW = (ENTRIES <= 1) ? 1 : $clog2(ENTRIES);
reg [2*TW_W-1:0] rom [0:ENTRIES-1];
wire [AW-1:0] index = exponent >> SHIFT;
`ifdef XILINX_SIMULATOR
initial $readmemh({"rtl/paper_fft12/common/", MEM_FILE}, rom);
`else
initial $readmemh(MEM_FILE, rom);
`endif
always @(posedge clk)
    {coeff_re, coeff_im} <= rom[index];
endmodule


(* keep_hierarchy = "yes" *)
module pfft_hom_rom_wp4r #(
    parameter integer STAGE = 2
)(
    input  wire clk,
    input  wire [9:0] exponent,
    output wire signed [fft_wg_pkg::TW_W-1:0] coeff_re,
    output wire signed [fft_wg_pkg::TW_W-1:0] coeff_im
);
generate
if (STAGE == 2) begin : g_rom
    pfft_rom_wp4r #(.ENTRIES(4), .SHIFT(7), .MEM_FILE("pfft_s2_rom_wp4r.mem")) u (
        clk, exponent, coeff_re, coeff_im);
end else if (STAGE == 3) begin : g_rom
    pfft_rom_wp4r #(.ENTRIES(8), .SHIFT(6), .MEM_FILE("pfft_s3_rom_wp4r.mem")) u (
        clk, exponent, coeff_re, coeff_im);
end else if (STAGE == 4) begin : g_rom
    pfft_rom_wp4r #(.ENTRIES(16), .SHIFT(5), .MEM_FILE("pfft_s4_rom_wp4r.mem")) u (
        clk, exponent, coeff_re, coeff_im);
end else if (STAGE == 5) begin : g_rom
    pfft_rom_wp4r #(.ENTRIES(32), .SHIFT(4), .MEM_FILE("pfft_s5_rom_wp4r.mem")) u (
        clk, exponent, coeff_re, coeff_im);
end else if (STAGE == 6) begin : g_rom
    pfft_rom_wp4r #(.ENTRIES(64), .SHIFT(3), .MEM_FILE("pfft_s6_rom_wp4r.mem")) u (
        clk, exponent, coeff_re, coeff_im);
end else if (STAGE == 7) begin : g_rom
    pfft_rom_wp4r #(.ENTRIES(128), .SHIFT(2), .MEM_FILE("pfft_s7_rom_wp4r.mem")) u (
        clk, exponent, coeff_re, coeff_im);
end else begin : g_invalid
    initial $fatal(1, "homogeneous PFFT ROM only exists for stages 2 through 7");
end
endgenerate
endmodule


(* keep_hierarchy = "yes" *)
module pfft_sdf_control_wp4r #(
    parameter integer DEPTH = 128,
    parameter integer AW = (DEPTH <= 1) ? 1 : $clog2(DEPTH)
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    output reg  input_valid_q,
    output wire rd_en,
    output wire [AW-1:0] rd_addr,
    output wire work_valid,
    output wire work_second,
    output wire work_primed,
    output wire [AW-1:0] work_addr,
    output reg  pending_wr_en,
    output reg  [AW-1:0] pending_wr_addr,
    output wire forward_active,
    output wire emit_raw,
    output wire raw_is_last,
    output reg  [7:0] out_count
);
localparam integer PHASE_W = (2*DEPTH <= 2) ? 1 : $clog2(2*DEPTH);
localparam integer SYNC_READ = (DEPTH == 128);
(* DONT_TOUCH = "yes" *) reg [PHASE_W-1:0] phase;
reg primed;
reg request_valid, request_second, request_primed;
reg [AW-1:0] request_address;
wire phase_second = (phase >= DEPTH);
wire [AW-1:0] current_address = phase_second ? phase - DEPTH : phase;
wire [PHASE_W-1:0] phase_next = input_valid_q ?
    ((phase == (2*DEPTH-1)) ? {PHASE_W{1'b0}} : phase + 1'b1) : phase;

assign rd_en = input_valid_q;
assign rd_addr = current_address;
assign work_valid = SYNC_READ ? request_valid : input_valid_q;
assign work_second = SYNC_READ ? request_second : phase_second;
assign work_primed = SYNC_READ ? request_primed : primed;
assign work_addr = SYNC_READ ? request_address : current_address;
assign emit_raw = work_valid && (work_second || work_primed);
assign raw_is_last = (out_count == 8'd255);
assign forward_active = work_valid && pending_wr_en &&
                        (work_addr == pending_wr_addr);

always @(posedge clk) begin
    if (rst) begin
        input_valid_q <= 1'b0;
        phase <= {PHASE_W{1'b0}};
        primed <= 1'b0;
        request_valid <= 1'b0;
        request_second <= 1'b0;
        request_primed <= 1'b0;
        request_address <= {AW{1'b0}};
        pending_wr_en <= 1'b0;
        pending_wr_addr <= {AW{1'b0}};
        out_count <= 8'd0;
    end else begin
        input_valid_q <= in_valid;
        request_valid <= SYNC_READ ? input_valid_q : 1'b0;
        if (SYNC_READ && input_valid_q) begin
            request_second <= phase_second;
            request_primed <= primed;
            request_address <= current_address;
        end
        phase <= phase_next;
        if (input_valid_q && (phase == (2*DEPTH-1)))
            primed <= 1'b1;
        if (emit_raw)
            out_count <= out_count + 1'b1;
        pending_wr_en <= work_valid;
        pending_wr_addr <= work_addr;
    end
end
endmodule


(* keep_hierarchy = "yes" *)
module pfft_sdf4_wp4r #(
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
    output reg  [3:0] out_valid,
    output reg  [3:0] out_last,
    output wire [8*WO-1:0] out_data
);
import fft_wg_pkg::*;
localparam integer MW = W + 1;
localparam integer DW = 2*MW;
localparam integer AW = (DEPTH <= 1) ? 1 : $clog2(DEPTH);
localparam integer SYNC_READ = (DEPTH == 128);

wire ctl_ivq, ctl_rd_en, ctl_wv, ctl_ws, ctl_wp, ctl_pwe, ctl_fwd, emit_raw, raw_is_last;
wire [AW-1:0] ctl_rd_addr, ctl_wa, ctl_pwa;
wire [7:0] out_count;
pfft_sdf_control_wp4r #(.DEPTH(DEPTH), .AW(AW)) u_control (
    .clk(clk), .rst(rst), .in_valid(in_valid), .input_valid_q(ctl_ivq),
    .rd_en(ctl_rd_en), .rd_addr(ctl_rd_addr),
    .work_valid(ctl_wv), .work_second(ctl_ws), .work_primed(ctl_wp),
    .work_addr(ctl_wa), .pending_wr_en(ctl_pwe), .pending_wr_addr(ctl_pwa),
    .forward_active(ctl_fwd), .emit_raw(emit_raw), .raw_is_last(raw_is_last),
    .out_count(out_count));
wire [7:0] out_count_next = out_count + emit_raw;

wire [4*DW-1:0] mem_read_data;
wire [4*DW-1:0] mem_write_data;
wp4r_hier_fft_memory_v5 #(.WIDTH(4*DW), .DEPTH(DEPTH), .ADDR_W(AW)) u_memory (
    .clk(clk), .rd_en(ctl_rd_en), .rd_addr(ctl_rd_addr), .rd_data(mem_read_data),
    .wr_en(ctl_wv), .wr_addr(ctl_wa), .wr_data(mem_write_data));

wire [9:0] group_phi_now, group_phi_next;
wp4r_hier_pfft_exchange_phi_wg #(.STAGE(STAGE)) u_phi_now (
    .index({out_count, 2'd0}), .exponent(group_phi_now));
wp4r_hier_pfft_exchange_phi_wg #(.STAGE(STAGE)) u_phi_next (
    .index({out_count_next, 2'd0}), .exponent(group_phi_next));

wire signed [TW_W-1:0] tw_re [0:3];
wire signed [TW_W-1:0] tw_im [0:3];
generate
if (STAGE >= 2 && STAGE <= 7) begin : g_shared_rom
    wire signed [TW_W-1:0] coeff_re, coeff_im;
    pfft_hom_rom_wp4r #(.STAGE(STAGE)) u_rom (
        .clk(clk), .exponent(group_phi_next), .coeff_re(coeff_re), .coeff_im(coeff_im));
    for (genvar rl = 0; rl < 4; rl = rl + 1) begin : g_broadcast
        assign tw_re[rl] = coeff_re;
        assign tw_im[rl] = coeff_im;
    end
end else begin : g_no_shared_rom
    for (genvar rl = 0; rl < 4; rl = rl + 1) begin : g_zero
        assign tw_re[rl] = {TW_W{1'b0}};
        assign tw_im[rl] = {TW_W{1'b0}};
    end
end
endgenerate

wire [3:0] lane_valid;
wire signed [WO-1:0] rot_re_input [0:3];
wire signed [WO-1:0] rot_im_input [0:3];
reg signed [WO-1:0] lane_re [0:3];
reg signed [WO-1:0] lane_im [0:3];
reg ml1, ml2, ml3;
integer oi;
always @(posedge clk) begin
    if (rst) begin
        out_valid <= 4'b0;
        out_last <= 4'b0;
        ml1 <= 1'b0;
        ml2 <= 1'b0;
        ml3 <= 1'b0;
        for (oi = 0; oi < 4; oi = oi + 1) begin
            lane_re[oi] <= {WO{1'b0}};
            lane_im[oi] <= {WO{1'b0}};
        end
    end else begin
        ml1 <= emit_raw && raw_is_last;
        ml2 <= ml1;
        ml3 <= ml2;
        for (oi = 0; oi < 4; oi = oi + 1) begin
            out_valid[oi] <= lane_valid[oi];
            out_last[oi] <= lane_valid[oi] ? ml3 : 1'b0;
            if (lane_valid[oi]) begin
                lane_re[oi] <= rot_re_input[oi];
                lane_im[oi] <= rot_im_input[oi];
            end
        end
    end
end

generate
for (genvar lane = 0; lane < 4; lane = lane + 1) begin : g_lane
    localparam integer TRIVIAL_ONLY = (STAGE == 1) || ((STAGE == 8) && (lane < 2));
    localparam [1:0] LANE_INDEX = lane;
    reg signed [W-1:0] in_re_q, in_im_q, req_re, req_im;
    always @(posedge clk) begin
        if (in_valid) begin
            in_re_q <= in_data[(8-2*lane)*W-1 -: W];
            in_im_q <= in_data[(7-2*lane)*W-1 -: W];
        end
        if (SYNC_READ && ctl_ivq) begin
            req_re <= in_re_q;
            req_im <= in_im_q;
        end
    end
    wire signed [W-1:0] work_re = SYNC_READ ? req_re : in_re_q;
    wire signed [W-1:0] work_im = SYNC_READ ? req_im : in_im_q;
    wire signed [MW-1:0] memory_re = mem_read_data[(lane+1)*DW-1 -: MW];
    wire signed [MW-1:0] memory_im = mem_read_data[lane*DW+MW-1 -: MW];
    wire signed [MW-1:0] work_ext_re = work_re;
    wire signed [MW-1:0] work_ext_im = work_im;
    wire signed [MW-1:0] sum_re = memory_re + work_ext_re;
    wire signed [MW-1:0] sum_im = memory_im + work_ext_im;
    wire signed [MW-1:0] dif_re = memory_re - work_ext_re;
    wire signed [MW-1:0] dif_im = memory_im - work_ext_im;
    wire signed [MW-1:0] store_re = ctl_ws ? dif_re : work_ext_re;
    wire signed [MW-1:0] store_im = ctl_ws ? dif_im : work_ext_im;
    assign mem_write_data[(lane+1)*DW-1 -: DW] = {store_re, store_im};
    wire signed [MW-1:0] emit_re = ctl_ws ? sum_re : memory_re;
    wire signed [MW-1:0] emit_im = ctl_ws ? sum_im : memory_im;
    wire signed [WO-1:0] emit_wo_re = emit_re;
    wire signed [WO-1:0] emit_wo_im = emit_im;
    wire signed [WO-1:0] rotated_re, rotated_im;

    if (!TRIVIAL_ONLY) begin : g_multiply
        wire signed [TW_W-1:0] private_re, private_im;
        wire [9:0] lane_phi_next;
        if (STAGE == 8) begin : g_private_rom
            wp4r_hier_pfft_exchange_phi_wg #(.STAGE(STAGE)) u_phi_next (
                .index({out_count_next, LANE_INDEX}), .exponent(lane_phi_next));
            pfft_rom_wp4r #(.ENTRIES(256), .SHIFT(1),
                            .MEM_FILE("pfft_s8_rom_wp4r.mem")) u_rom (
                .clk(clk), .exponent(lane_phi_next),
                .coeff_re(private_re), .coeff_im(private_im));
        end else begin : g_broadcast_rom
            assign private_re = tw_re[lane];
            assign private_im = tw_im[lane];
            assign lane_phi_next = 10'd0;
        end
        wp4r_hier_fft_complex_mul_wg_pipe #(.WI(MW), .WO(WO)) u_rotator (
            .clk(clk), .rst(rst), .valid_in(emit_raw),
            .in_re(emit_re), .in_im(emit_im),
            .coeff_re(private_re), .coeff_im(private_im),
            .valid_out(lane_valid[lane]), .out_re(rotated_re), .out_im(rotated_im));
        wire _unused = lane_phi_next[0];
    end else begin : g_trivial
        wire [9:0] lane_phi_now;
        if (STAGE == 8) begin : g_lane_phi
            wp4r_hier_pfft_exchange_phi_wg #(.STAGE(STAGE)) u_phi_now (
                .index({out_count, LANE_INDEX}), .exponent(lane_phi_now));
        end else begin : g_group_phi
            assign lane_phi_now = group_phi_now;
        end
        wire signed [WO-1:0] rot_re, rot_im;
        wp4r_hier_fft_complex_rotate_trivial_wg #(.W(WO)) u_rotator (
            .in_re(emit_wo_re), .in_im(emit_wo_im), .exponent(lane_phi_now),
            .out_re(rot_re), .out_im(rot_im));
        reg v1, v2, v3;
        reg signed [WO-1:0] r1, i1, r2, i2, r3, i3;
        always @(posedge clk) begin
            if (rst) begin
                v1 <= 1'b0;
                v2 <= 1'b0;
                v3 <= 1'b0;
                r1 <= {WO{1'b0}};
                i1 <= {WO{1'b0}};
                r2 <= {WO{1'b0}};
                i2 <= {WO{1'b0}};
                r3 <= {WO{1'b0}};
                i3 <= {WO{1'b0}};
            end else begin
                v1 <= emit_raw;
                if (emit_raw) begin
                    r1 <= rot_re;
                    i1 <= rot_im;
                end
                v2 <= v1;
                r2 <= r1;
                i2 <= i1;
                v3 <= v2;
                r3 <= r2;
                i3 <= i2;
            end
        end
        assign lane_valid[lane] = v3;
        assign rotated_re = r3;
        assign rotated_im = i3;
        wire _unused_tw = ^{tw_re[lane], tw_im[lane]};
    end
    assign rot_re_input[lane] = rotated_re;
    assign rot_im_input[lane] = rotated_im;
end
endgenerate

generate
for (genvar lane = 0; lane < 4; lane = lane + 1) begin : g_outputs
    assign out_data[(8-2*lane)*WO-1 -: WO] = lane_re[lane];
    assign out_data[(7-2*lane)*WO-1 -: WO] = lane_im[lane];
end
endgenerate
wire _unused = ^{in_last, ctl_wp, ctl_pwe, ctl_pwa, ctl_fwd};
endmodule


(* keep_hierarchy = "yes" *)
module pfft_stage9_replica_wp4r #(
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
import fft_wg_pkg::*;
wire [39:0] rom_addr;
wire signed [TW_W-1:0] c1_re, c1_im, c3_re, c3_im;
pfft_rom_wp4r #(.ENTRIES(256), .SHIFT(0),
                .MEM_FILE("pfft_s9_rom_wp4r.mem")) u_rom1 (
    .clk(clk), .exponent(rom_addr[29:20]), .coeff_re(c1_re), .coeff_im(c1_im));
pfft_rom_wp4r #(.ENTRIES(256), .SHIFT(0),
                .MEM_FILE("pfft_s9j_rom_wp4r.mem")) u_rom3 (
    .clk(clk), .exponent(rom_addr[9:0]), .coeff_re(c3_re), .coeff_im(c3_im));
wire [8*TW_W-1:0] coeffs = {{(2*TW_W){1'b0}}, c1_re, c1_im,
                            {(2*TW_W){1'b0}}, c3_re, c3_im};
pfft_stage9_exchange_wg #(.W(W), .WO(WO)) u_stage9 (
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
    .in_data(in_data), .out_valid(out_valid), .out_last(out_last),
    .out_data(out_data), .addr(rom_addr), .coeffs_in(coeffs));
endmodule


(* keep_hierarchy = "yes" *)
module pfft_stage10_replica_wp4r #(
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
reg [7:0] beat_count;
wire frame_last = (beat_count == 8'd255);
always @(posedge clk) begin
    if (rst)
        beat_count <= 8'd0;
    else if (in_valid)
        beat_count <= beat_count + 1'b1;
end
pfft_stage10_wg #(.W(W), .WO(WO)) u_stage10 (
    .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(frame_last),
    .in0_re(in_data[8*W-1 -: W]), .in0_im(in_data[7*W-1 -: W]),
    .in1_re(in_data[6*W-1 -: W]), .in1_im(in_data[5*W-1 -: W]),
    .in2_re(in_data[4*W-1 -: W]), .in2_im(in_data[3*W-1 -: W]),
    .in3_re(in_data[2*W-1 -: W]), .in3_im(in_data[W-1:0]),
    .out_valid(out_valid), .out_last(out_last),
    .out0_re(out_data[8*WO-1 -: WO]), .out0_im(out_data[7*WO-1 -: WO]),
    .out1_re(out_data[6*WO-1 -: WO]), .out1_im(out_data[5*WO-1 -: WO]),
    .out2_re(out_data[4*WO-1 -: WO]), .out2_im(out_data[3*WO-1 -: WO]),
    .out3_re(out_data[2*WO-1 -: WO]), .out3_im(out_data[WO-1:0]));
wire _unused = in_last;
endmodule


(* keep_hierarchy = "yes" *)
module pfft_real_tmr_stage_wg #(
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
    if (STAGE <= 8) begin : g_sdf
        (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
        pfft_sdf4_wp4r #(.STAGE(STAGE), .DEPTH(DEPTH), .W(W), .WO(WO)) u_copy (
            .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
            .in_data(in_data), .out_valid(rep_valid[r]), .out_last(rep_last[r]),
            .out_data(rep_data[r]));
    end else if (STAGE == 9) begin : g_s9
        wire v, l;
        (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
        pfft_stage9_replica_wp4r #(.W(W), .WO(WO)) u_copy (
            .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
            .in_data(in_data), .out_valid(v), .out_last(l), .out_data(rep_data[r]));
        assign rep_valid[r] = {4{v}};
        assign rep_last[r] = {4{l}};
    end else begin : g_s10
        wire v, l;
        (* DONT_TOUCH = "yes", keep_hierarchy = "yes" *)
        pfft_stage10_replica_wp4r #(.W(W), .WO(WO)) u_copy (
            .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
            .in_data(in_data), .out_valid(v), .out_last(l), .out_data(rep_data[r]));
        assign rep_valid[r] = {4{v}};
        assign rep_last[r] = {4{l}};
    end
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
