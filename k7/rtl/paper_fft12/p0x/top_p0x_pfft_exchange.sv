`timescale 1ns/1ps

(* keep_hierarchy = "yes" *) module p0x_pfft_exchange_pipeline_wg(
 input wire clk,input wire rst,input wire in_valid,input wire in_last,
 input wire [8*fft_wg_pkg::IN_W-1:0] in_data,
 output wire out_valid,output wire out_last,
 output wire [8*fft_wg_pkg::OUT_W-1:0] out_data
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
    localparam integer DW=2*(W+1), MAW=(DEPTH<=1 ? 1 : $clog2(DEPTH));
    wire [3:0] vin, lin, vout, lout;
    wire [8*W-1:0] din;
    wire [8*WO-1:0] dout;
    if(s==1) begin : g_first
        assign vin={4{in_valid}}; assign lin={4{in_last}}; assign din=in_data;
    end else begin : g_later
        assign vin=g_stage[s-1].vout; assign lin=g_stage[s-1].lout; assign din=g_stage[s-1].dout;
    end
    pfft_stage_exchange_wg #(.STAGE(s), .DEPTH(DEPTH), .W(W), .WO(WO),
        .EXT_MEM(0), .EXT_TW(0), .DW(DW), .MAW(MAW)) u_stage(
        .clk(clk), .rst(rst), .in_valid(vin), .in_last(lin), .in_data(din),
        .out_valid(vout), .out_last(lout), .out_data(dout),
        .coeffs_in({(8*TW_W){1'b0}}), .rom_addr(),
        .mem_rd_data({(4*DW){1'b0}}), .mem_rd_en(), .mem_wr_en(),
        .mem_rd_addr(), .mem_wr_addr(), .mem_wr_data());
end endgenerate
assign out_data=g_stage[10].dout;
assign out_valid=g_stage[10].vout[0]; assign out_last=g_stage[10].lout[0];
endmodule


module top_p0x_pfft_exchange(
 input wire clk,input wire rst,input wire in_valid,input wire in_last,
 input wire signed[fft_wg_pkg::IN_W-1:0]in0_re,in0_im,in1_re,in1_im,in2_re,in2_im,in3_re,in3_im,
 output wire out_valid,output wire out_last,
 output wire signed[fft_wg_pkg::OUT_W-1:0]out0_re,out0_im,out1_re,out1_im,out2_re,out2_im,out3_re,out3_im
);
p0x_pfft_exchange_pipeline_wg u_pipeline(
 .clk(clk),.rst(rst),.in_valid(in_valid),.in_last(in_last),
 .in_data({in0_re,in0_im,in1_re,in1_im,in2_re,in2_im,in3_re,in3_im}),
 .out_valid(out_valid),.out_last(out_last),
 .out_data({out0_re,out0_im,out1_re,out1_im,out2_re,out2_im,out3_re,out3_im})
);
endmodule
