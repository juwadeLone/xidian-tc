`timescale 1ns/1ps

(* keep_hierarchy = "yes" *)
module pfft_tmr_stage_wg #(
    parameter integer STAGE=1, DEPTH=128,
    parameter integer W=fft_wg_pkg::S1_W, WO=fft_wg_pkg::S1_WO,
    parameter integer SHARED_MEM=1, SCHED=1
)(
    input wire clk, rst,
    input wire [3:0] in_valid, in_last,
    input wire [8*W-1:0] in_data,
    output wire [3:0] out_valid, out_last,
    output wire [8*WO-1:0] out_data,
    output wire stage_mismatch
);
import fft_wg_pkg::*;
generate
if (SCHED != 1) begin : g_unsupported_sched
    initial $fatal(1, "only exchanged schedule supported");
end
endgenerate
wire [3:0] rep_valid [0:2], rep_last [0:2];
wire [8*WO-1:0] rep_data [0:2];
wire [8*TW_W-1:0] coeffs;
wire data_diff, valid_diff, last_diff, rom_diff;
localparam integer DW=2*(W+1), MAW=(DEPTH<=1 ? 1 : $clog2(DEPTH));
wire [3:0] mem_rd_en [0:2], mem_wr_en [0:2];
wire [4*MAW-1:0] mem_rd_addr [0:2], mem_wr_addr [0:2];
wire [4*DW-1:0] mem_wr_data [0:2];
wire [4*DW-1:0] shared_read_data;
wire [19:0] mem_diff;
wire [39:0] rep_addr [0:2], voted_addr;
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
for(genvar r=0;r<3;r=r+1) begin : g_replica
    (* DONT_TOUCH="yes", keep_hierarchy="yes" *)
    pfft_stage_exchange_wg #(.STAGE(STAGE), .DEPTH(DEPTH), .W(W), .WO(WO),
                             .EXT_MEM(SHARED_MEM && STAGE<=8), .EXT_TW(1)) u_copy(
        .clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last), .in_data(in_data),
        .out_valid(rep_valid[r]), .out_last(rep_last[r]), .out_data(rep_data[r]),
        .coeffs_in(coeffs), .rom_addr(rep_addr[r]),
        .mem_rd_data(shared_read_data), .mem_rd_en(mem_rd_en[r]), .mem_wr_en(mem_wr_en[r]),
        .mem_rd_addr(mem_rd_addr[r]), .mem_wr_addr(mem_wr_addr[r]), .mem_wr_data(mem_wr_data[r]));
end
endgenerate
tmr_vote_w #(.W(40)) u_rom_vote(rep_addr[0],rep_addr[1],rep_addr[2],voted_addr,rom_diff);
pfft_exchange_twiddles_wg #(.STAGE(STAGE)) u_tw(.clk(clk), .addr(voted_addr), .coeffs(coeffs));
tmr_vote_w #(.W(4)) u_valid_vote(rep_valid[0],rep_valid[1],rep_valid[2],out_valid,valid_diff);
tmr_vote_w #(.W(4)) u_last_vote(rep_last[0],rep_last[1],rep_last[2],out_last,last_diff);
tmr_vote_w #(.W(8*WO)) u_data_vote(rep_data[0],rep_data[1],rep_data[2],out_data,data_diff);
tmr_stage_flag_wg #(.POINTS(24)) u_flag(clk,rst,out_valid[0],{data_diff,valid_diff,last_diff,rom_diff,mem_diff},stage_mismatch);
endmodule

(* keep_hierarchy = "yes" *)
module pfft_tmr_pipeline_wg #(parameter integer SHARED_MEM=1, SCHED=1)(
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
    pfft_tmr_stage_wg #(.STAGE(s), .DEPTH(DEPTH), .W(W), .WO(WO),
                        .SHARED_MEM(SHARED_MEM), .SCHED(SCHED)) u_stage(
        clk,rst,vin,lin,din,vout,lout,dout,stage_mismatch[s-1]);
end endgenerate
assign out_data=g_stage[10].dout;
assign out_valid=g_stage[10].vout[0]; assign out_last=g_stage[10].lout[0];
endmodule
