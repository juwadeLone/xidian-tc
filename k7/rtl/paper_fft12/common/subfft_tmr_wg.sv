`timescale 1ns/1ps

(* keep_hierarchy = "yes" *)
module subfft_tmr_replica_wg #(
    parameter integer STAGE=1, DEPTH=128,
    parameter integer W=fft_wg_pkg::S1_W, WO=fft_wg_pkg::S1_WO,
    parameter integer AW=(STAGE==8 ? 30 : 10),
    parameter integer CW=(STAGE==8 ? 6 : 2)*fft_wg_pkg::TW_W,
    parameter integer EXT_MEM=0, DW=2*(W+1), MAW=(DEPTH<=1 ? 1 : $clog2(DEPTH))
)(
    input wire clk, rst,
    input wire [3:0] in_valid, in_last,
    input wire [8*W-1:0] in_data,
    input wire [CW-1:0] coeffs,
    output wire [AW-1:0] rom_addr,
    output wire [3:0] out_valid, out_last,
    output wire [8*WO-1:0] out_data,
    input wire [4*DW-1:0] mem_rd_data,
    output wire [3:0] mem_rd_en, mem_wr_en,
    output wire [4*MAW-1:0] mem_rd_addr, mem_wr_addr,
    output wire [4*DW-1:0] mem_wr_data
);
import fft_wg_pkg::*;
generate
if(STAGE<=8) begin : g_sdf
    wire [3:0] lane_valid, lane_last;
    wire [8*WO-1:0] lane_data;
    for(genvar lane=0;lane<4;lane=lane+1) begin : g_lane
        if(STAGE<=6 && lane==0) begin : g_addr
            r2sdf_lane_subfft_wg #(.DEPTH(DEPTH), .STAGE(STAGE), .W(W), .WO(WO), .EXT_MEM(EXT_MEM)) u_lane(
                .clk(clk), .rst(rst), .in_valid(in_valid[lane]),
                .in_re(in_data[(8-2*lane)*W-1 -: W]), .in_im(in_data[(7-2*lane)*W-1 -: W]),
                .out_valid(lane_valid[lane]), .out_last(lane_last[lane]),
                .out_re(lane_data[(8-2*lane)*WO-1 -: WO]), .out_im(lane_data[(7-2*lane)*WO-1 -: WO]),
                .tw_addr(rom_addr), .tw_re(coeffs[2*TW_W-1 -: TW_W]), .tw_im(coeffs[TW_W-1:0]),
                .mem_rd_data(mem_rd_data[(lane+1)*DW-1 -: DW]), .mem_rd_en(mem_rd_en[lane]),
                .mem_rd_addr(mem_rd_addr[(lane+1)*MAW-1 -: MAW]), .mem_wr_en(mem_wr_en[lane]),
                .mem_wr_addr(mem_wr_addr[(lane+1)*MAW-1 -: MAW]), .mem_wr_data(mem_wr_data[(lane+1)*DW-1 -: DW]));
        end else begin : g_no_addr
            r2sdf_lane_subfft_wg #(.DEPTH(DEPTH), .STAGE(STAGE), .W(W), .WO(WO), .EXT_MEM(EXT_MEM)) u_lane(
                .clk(clk), .rst(rst), .in_valid(in_valid[lane]),
                .in_re(in_data[(8-2*lane)*W-1 -: W]), .in_im(in_data[(7-2*lane)*W-1 -: W]),
                .out_valid(lane_valid[lane]), .out_last(lane_last[lane]),
                .out_re(lane_data[(8-2*lane)*WO-1 -: WO]), .out_im(lane_data[(7-2*lane)*WO-1 -: WO]),
                .tw_addr(), .tw_re((STAGE<=6) ? coeffs[2*TW_W-1 -: TW_W] : {TW_W{1'b0}}),
                .tw_im((STAGE<=6) ? coeffs[TW_W-1:0] : {TW_W{1'b0}}),
                .mem_rd_data(mem_rd_data[(lane+1)*DW-1 -: DW]), .mem_rd_en(mem_rd_en[lane]),
                .mem_rd_addr(mem_rd_addr[(lane+1)*MAW-1 -: MAW]), .mem_wr_en(mem_wr_en[lane]),
                .mem_wr_addr(mem_wr_addr[(lane+1)*MAW-1 -: MAW]), .mem_wr_data(mem_wr_data[(lane+1)*DW-1 -: DW]));
        end
    end
    if(STAGE==8) begin : g_cross
        wire cross_valid, cross_last;
        subfft_stage8_cross_wg #(.W(WO)) u_cross(
            .clk(clk), .rst(rst), .in_valid(lane_valid[0]), .in_last(lane_last[0]),
            .in0_re(lane_data[8*WO-1 -: WO]), .in0_im(lane_data[7*WO-1 -: WO]),
            .in1_re(lane_data[6*WO-1 -: WO]), .in1_im(lane_data[5*WO-1 -: WO]),
            .in2_re(lane_data[4*WO-1 -: WO]), .in2_im(lane_data[3*WO-1 -: WO]),
            .in3_re(lane_data[2*WO-1 -: WO]), .in3_im(lane_data[WO-1:0]),
            .t1_re(coeffs[6*TW_W-1 -: TW_W]), .t1_im(coeffs[5*TW_W-1 -: TW_W]),
            .t2_re(coeffs[4*TW_W-1 -: TW_W]), .t2_im(coeffs[3*TW_W-1 -: TW_W]),
            .t3_re(coeffs[2*TW_W-1 -: TW_W]), .t3_im(coeffs[TW_W-1:0]),
            .exp1(rom_addr[29:20]), .exp2(rom_addr[19:10]), .exp3(rom_addr[9:0]),
            .out_valid(cross_valid), .out_last(cross_last),
            .out0_re(out_data[8*WO-1 -: WO]), .out0_im(out_data[7*WO-1 -: WO]),
            .out1_re(out_data[6*WO-1 -: WO]), .out1_im(out_data[5*WO-1 -: WO]),
            .out2_re(out_data[4*WO-1 -: WO]), .out2_im(out_data[3*WO-1 -: WO]),
            .out3_re(out_data[2*WO-1 -: WO]), .out3_im(out_data[WO-1:0]));
        assign out_valid={4{cross_valid}}; assign out_last={4{cross_last}};
    end else begin : g_plain
        assign out_data=lane_data; assign out_valid=lane_valid; assign out_last=lane_last;
        if(STAGE==7) assign rom_addr={AW{1'b0}};
    end
end else begin : g_merge
    wire valid, last;
    if(STAGE==9) begin : g_s9
        subfft_stage9_wg #(.W(W), .WO(WO)) u_merge(
            .clk(clk), .rst(rst), .in_valid(in_valid[0]), .in_last(in_last[0]),
            .in0_re(in_data[8*W-1 -: W]), .in0_im(in_data[7*W-1 -: W]),
            .in1_re(in_data[6*W-1 -: W]), .in1_im(in_data[5*W-1 -: W]),
            .in2_re(in_data[4*W-1 -: W]), .in2_im(in_data[3*W-1 -: W]),
            .in3_re(in_data[2*W-1 -: W]), .in3_im(in_data[W-1:0]),
            .out_valid(valid), .out_last(last),
            .out0_re(out_data[8*WO-1 -: WO]), .out0_im(out_data[7*WO-1 -: WO]),
            .out1_re(out_data[6*WO-1 -: WO]), .out1_im(out_data[5*WO-1 -: WO]),
            .out2_re(out_data[4*WO-1 -: WO]), .out2_im(out_data[3*WO-1 -: WO]),
            .out3_re(out_data[2*WO-1 -: WO]), .out3_im(out_data[WO-1:0]));
    end else begin : g_s10
        subfft_stage10_wg #(.W(W), .WO(WO)) u_merge(
            .clk(clk), .rst(rst), .in_valid(in_valid[0]), .in_last(in_last[0]),
            .in0_re(in_data[8*W-1 -: W]), .in0_im(in_data[7*W-1 -: W]),
            .in1_re(in_data[6*W-1 -: W]), .in1_im(in_data[5*W-1 -: W]),
            .in2_re(in_data[4*W-1 -: W]), .in2_im(in_data[3*W-1 -: W]),
            .in3_re(in_data[2*W-1 -: W]), .in3_im(in_data[W-1:0]),
            .out_valid(valid), .out_last(last),
            .out0_re(out_data[8*WO-1 -: WO]), .out0_im(out_data[7*WO-1 -: WO]),
            .out1_re(out_data[6*WO-1 -: WO]), .out1_im(out_data[5*WO-1 -: WO]),
            .out2_re(out_data[4*WO-1 -: WO]), .out2_im(out_data[3*WO-1 -: WO]),
            .out3_re(out_data[2*WO-1 -: WO]), .out3_im(out_data[WO-1:0]));
    end
    assign out_valid={4{valid}}; assign out_last={4{last}}; assign rom_addr={AW{1'b0}};
    assign mem_rd_en=4'b0; assign mem_wr_en=4'b0;
    assign mem_rd_addr={4*MAW{1'b0}}; assign mem_wr_addr={4*MAW{1'b0}}; assign mem_wr_data={4*DW{1'b0}};
end
endgenerate
endmodule

(* keep_hierarchy = "yes" *)
module subfft_tmr_stage_wg #(
    parameter integer STAGE=1, DEPTH=128, SHARED_MEM=0,
    parameter integer W=fft_wg_pkg::S1_W, WO=fft_wg_pkg::S1_WO,
    parameter integer AW=(STAGE==8 ? 30 : 10),
    parameter integer CW=(STAGE==8 ? 6 : 2)*fft_wg_pkg::TW_W
)(
    input wire clk, rst,
    input wire [3:0] in_valid, in_last,
    input wire [8*W-1:0] in_data,
    output wire [3:0] out_valid, out_last,
    output wire [8*WO-1:0] out_data,
    output wire stage_mismatch
);
import fft_wg_pkg::*;
wire [3:0] rep_valid [0:2], rep_last [0:2];
wire [8*WO-1:0] rep_data [0:2];
wire [CW-1:0] coeffs;
wire data_diff, valid_diff, last_diff, rom_diff;
localparam integer DW=2*(W+1), MAW=(DEPTH<=1 ? 1 : $clog2(DEPTH));
wire [3:0] mem_rd_en [0:2], mem_wr_en [0:2];
wire [4*MAW-1:0] mem_rd_addr [0:2], mem_wr_addr [0:2];
wire [4*DW-1:0] mem_wr_data [0:2];
wire [4*DW-1:0] shared_read_data;
wire [19:0] mem_diff;
generate
if(SHARED_MEM && STAGE<=8) begin : g_shared_memory
    for(genvar lane=0;lane<4;lane=lane+1) begin : g_mem
        tmr_secded_delay_wg #(.DATA_W(DW), .DEPTH(DEPTH), .AW(MAW)) u_shared_memory(
            .clk(clk), .rd_en({mem_rd_en[2][lane],mem_rd_en[1][lane],mem_rd_en[0][lane]}),
            .wr_en({mem_wr_en[2][lane],mem_wr_en[1][lane],mem_wr_en[0][lane]}),
            .rd_addr0(mem_rd_addr[0][(lane+1)*MAW-1 -: MAW]), .rd_addr1(mem_rd_addr[1][(lane+1)*MAW-1 -: MAW]),
            .rd_addr2(mem_rd_addr[2][(lane+1)*MAW-1 -: MAW]),
            .wr_addr0(mem_wr_addr[0][(lane+1)*MAW-1 -: MAW]), .wr_addr1(mem_wr_addr[1][(lane+1)*MAW-1 -: MAW]),
            .wr_addr2(mem_wr_addr[2][(lane+1)*MAW-1 -: MAW]),
            .wr_data0(mem_wr_data[0][(lane+1)*DW-1 -: DW]), .wr_data1(mem_wr_data[1][(lane+1)*DW-1 -: DW]),
            .wr_data2(mem_wr_data[2][(lane+1)*DW-1 -: DW]),
            .rd_data(shared_read_data[(lane+1)*DW-1 -: DW]), .mismatch(mem_diff[(lane+1)*5-1 -: 5]));
    end
end else begin : g_private_memory
    assign shared_read_data={4*DW{1'b0}}; assign mem_diff=20'b0;
end
if(STAGE<=6 || STAGE==8) begin : g_rom
    wire [AW-1:0] rep_addr [0:2], voted_addr;
    for(genvar r=0;r<3;r=r+1) begin : g_replica
        if(SHARED_MEM) begin : g_shared
            (* DONT_TOUCH="yes", keep_hierarchy="yes" *)
            subfft_tmr_replica_wg #(.STAGE(STAGE), .DEPTH(DEPTH), .W(W), .WO(WO), .EXT_MEM(1)) u_copy(
                .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last), .in_data(in_data), .coeffs(coeffs),
                .rom_addr(rep_addr[r]), .out_valid(rep_valid[r]), .out_last(rep_last[r]), .out_data(rep_data[r]),
                .mem_rd_data(shared_read_data), .mem_rd_en(mem_rd_en[r]), .mem_wr_en(mem_wr_en[r]),
                .mem_rd_addr(mem_rd_addr[r]), .mem_wr_addr(mem_wr_addr[r]), .mem_wr_data(mem_wr_data[r]));
        end else begin : g_private
            (* DONT_TOUCH="yes", keep_hierarchy="yes" *)
            subfft_tmr_replica_wg #(.STAGE(STAGE), .DEPTH(DEPTH), .W(W), .WO(WO)) u_copy(
                .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last), .in_data(in_data), .coeffs(coeffs),
                .rom_addr(rep_addr[r]), .out_valid(rep_valid[r]), .out_last(rep_last[r]), .out_data(rep_data[r]),
                .mem_rd_data({4*DW{1'b0}}), .mem_rd_en(), .mem_wr_en(), .mem_rd_addr(), .mem_wr_addr(), .mem_wr_data());
        end
    end
    tmr_vote_w #(.W(AW)) u_rom_vote(rep_addr[0],rep_addr[1],rep_addr[2],voted_addr,rom_diff);
    if(STAGE==8) begin : g_cross
        twiddle_bank_wg #(.PORTS(2)) u_tw12(
            .clk(clk), .addr_a(voted_addr[29:20]), .addr_b(voted_addr[19:10]),
            .coeff_a_re(coeffs[6*TW_W-1 -: TW_W]), .coeff_a_im(coeffs[5*TW_W-1 -: TW_W]),
            .coeff_b_re(coeffs[4*TW_W-1 -: TW_W]), .coeff_b_im(coeffs[3*TW_W-1 -: TW_W]),
            .det_a(), .cor_a(), .det_b(), .cor_b());
        twiddle_bank_wg #(.PORTS(1)) u_tw3(
            .clk(clk), .addr_a(voted_addr[9:0]), .addr_b(10'd0),
            .coeff_a_re(coeffs[2*TW_W-1 -: TW_W]), .coeff_a_im(coeffs[TW_W-1:0]),
            .coeff_b_re(), .coeff_b_im(), .det_a(), .cor_a(), .det_b(), .cor_b());
    end else begin : g_stage
        twiddle_bank_wg #(.PORTS(1)) u_tw(
            .clk(clk), .addr_a(voted_addr), .addr_b(10'd0), .coeff_a_re(coeffs[2*TW_W-1 -: TW_W]),
            .coeff_a_im(coeffs[TW_W-1:0]), .coeff_b_re(), .coeff_b_im(), .det_a(), .cor_a(), .det_b(), .cor_b());
    end
end else begin : g_no_rom
    assign coeffs={CW{1'b0}}; assign rom_diff=1'b0;
    for(genvar r=0;r<3;r=r+1) begin : g_replica
        if(SHARED_MEM && STAGE<=8) begin : g_shared
            (* DONT_TOUCH="yes", keep_hierarchy="yes" *)
            subfft_tmr_replica_wg #(.STAGE(STAGE), .DEPTH(DEPTH), .W(W), .WO(WO), .EXT_MEM(1)) u_copy(
                .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last), .in_data(in_data), .coeffs(coeffs),
                .rom_addr(), .out_valid(rep_valid[r]), .out_last(rep_last[r]), .out_data(rep_data[r]),
                .mem_rd_data(shared_read_data), .mem_rd_en(mem_rd_en[r]), .mem_wr_en(mem_wr_en[r]),
                .mem_rd_addr(mem_rd_addr[r]), .mem_wr_addr(mem_wr_addr[r]), .mem_wr_data(mem_wr_data[r]));
        end else begin : g_private
            (* DONT_TOUCH="yes", keep_hierarchy="yes" *)
            subfft_tmr_replica_wg #(.STAGE(STAGE), .DEPTH(DEPTH), .W(W), .WO(WO)) u_copy(
                .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last), .in_data(in_data), .coeffs(coeffs),
                .rom_addr(), .out_valid(rep_valid[r]), .out_last(rep_last[r]), .out_data(rep_data[r]),
                .mem_rd_data({4*DW{1'b0}}), .mem_rd_en(), .mem_wr_en(), .mem_rd_addr(), .mem_wr_addr(), .mem_wr_data());
        end
    end
end
endgenerate
tmr_vote_w #(.W(4)) u_valid_vote(rep_valid[0],rep_valid[1],rep_valid[2],out_valid,valid_diff);
tmr_vote_w #(.W(4)) u_last_vote(rep_last[0],rep_last[1],rep_last[2],out_last,last_diff);
tmr_vote_w #(.W(8*WO)) u_data_vote(rep_data[0],rep_data[1],rep_data[2],out_data,data_diff);
tmr_stage_flag_wg #(.POINTS(24)) u_flag(clk,rst,out_valid[0],{data_diff,valid_diff,last_diff,rom_diff,mem_diff},stage_mismatch);
endmodule

(* keep_hierarchy = "yes" *)
module subfft_tmr_pipeline_wg #(parameter integer SHARED_MEM=0)(
    input wire clk, rst, in_valid, in_last,
    input wire [8*fft_wg_pkg::IN_W-1:0] in_data,
    output wire out_valid, out_last,
    output wire [8*fft_wg_pkg::OUT_W-1:0] out_data,
    output wire [9:0] stage_mismatch
);
import fft_wg_pkg::*;
function automatic integer stage_w(input integer s);
    case(s)
        1:stage_w=S1_W; 2:stage_w=S2_W; 3:stage_w=S3_W; 4:stage_w=S4_W; 5:stage_w=S5_W;
        6:stage_w=S6_W; 7:stage_w=S7_W; 8:stage_w=S8_W; 9:stage_w=S9_W; 10:stage_w=S10_W;
        default:stage_w=IN_W;
    endcase
endfunction
function automatic integer stage_wo(input integer s);
    case(s)
        1:stage_wo=S1_WO; 2:stage_wo=S2_WO; 3:stage_wo=S3_WO; 4:stage_wo=S4_WO; 5:stage_wo=S5_WO;
        6:stage_wo=S6_WO; 7:stage_wo=S7_WO; 8:stage_wo=S8_WO; 9:stage_wo=S9_WO; 10:stage_wo=S10_WO;
        default:stage_wo=OUT_W;
    endcase
endfunction
generate for(genvar s=1;s<=10;s=s+1) begin : g_stage
    localparam integer W=stage_w(s), WO=stage_wo(s), DEPTH=(s<=8 ? (128>>(s-1)) : 1);
    wire [3:0] vin, lin, vout, lout;
    wire [8*W-1:0] din;
    wire [8*WO-1:0] dout;
    if(s==1) begin : g_first
        assign vin={4{in_valid}}; assign lin={4{in_last}}; assign din=in_data;
    end else begin : g_later
        assign vin=g_stage[s-1].vout; assign lin=g_stage[s-1].lout; assign din=g_stage[s-1].dout;
    end
    subfft_tmr_stage_wg #(.STAGE(s), .DEPTH(DEPTH), .W(W), .WO(WO), .SHARED_MEM(SHARED_MEM)) u_stage(
        clk,rst,vin,lin,din,vout,lout,dout,stage_mismatch[s-1]);
end endgenerate
assign out_data=g_stage[10].dout;
assign out_valid=g_stage[10].vout[0]; assign out_last=g_stage[10].lout[0];
endmodule
