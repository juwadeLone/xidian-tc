// Ported from the A7 project (code_v/eth/udp_eth_tx_cal_check_sum.v).
// Verbatim apart from the include path and, where marked, ADDR_W 29->31
// for the K7's 2 GB address space (contracts/k7_ddr_axi_decision.md).
`timescale 1ns / 1ps
   // --------------------------------------------------------------------
   // Copyright (c) 2022 by MicroPhase Technologies Inc. 
   // --------------------------------------------------------------------
   //
   // Permission:
   //
   //   MicroPhase grants permission to use and modify this code for use
   //   in synthesis for all MicroPhase Development Boards.
   //   Other use of this code, including the selling 
   //   ,duplication, or modification of any portion is strictly prohibited.
   //
   // Disclaimer:
   //
   //   This VHDL/Verilog or C/C++ source code is intended as a design reference
   //   which illustrates how these types of functions can be implemented.
   //   It is the user's responsibility to verify their design for
   //   consistency and functionality through the use of formal
   //   verification methods.  MicroPhase provides no warranty regarding the use 
   //   or functionality of this code.
   //
   // --------------------------------------------------------------------
   //           
   //                     MicroPhase Technologies Inc
   //                     Shanghai, China
   //
   //                     web: http://www.microphase.cn/   
   //                     email: support@microphase.cn
   //
   // --------------------------------------------------------------------
   // --------------------------------------------------------------------
   //   
   // Major Functions:   
   //
   // --------------------------------------------------------------------
   // --------------------------------------------------------------------
   //
   //  Revision History    
   //  Date          By            Revision    Change Description
   //---------------------------------------------------------------------
   // 2022.07.15   Ao Guohua     1.0          Original
   //                                                      
   // --------------------------------------------------------------------
   // --------------------------------------------------------------------

//这个模块用于求ip表头的校验和、udp的校验和，在数据流的相应的位置去做替换
module udp_eth_tx_cal_check_sum(
        input             clk,
        input             rst,
        input   [128:0]   status,
        input             pi_data_vld,
        input    [7:0]    pi_data,
        output reg        po_data_vld,
        output reg[7:0]   po_data 
    );
reg  [15:0]data_cnt;
reg  [15:0]rd_data_cnt;
wire [15:0]pkg_len ;
wire [15:0]ip_len  ;
wire [15:0]udp_len ;
reg        ip_flag ;    //表明ip表头的数据
reg        ip_flag_d;  //对ip_flag_d延时一拍
reg        ip_shift_cnt;//用于ip表头移位的计数器
reg        ip_shift_vld;//表明移位完成16bit的数据有效
reg        ip_flag_nedge;
reg [15:0] ip_shift;    //将ip表头移位成16位的
reg [31:0] ip_check_sum;
reg [15:0] ip_crc;
reg        udp_flag;
reg        udp_flag_d;
reg        udp_flag_nedge;
wire       udp_flag_pedge;
reg        udp_shift_cnt;
reg        udp_shift_vld;
reg [15:0] udp_shift;
reg [31:0] udp_check_sum;
reg [16:0] udp_crc_temp ;
wire [15:0] udp_crc;
wire       full;
wire       empty;
reg        po_data_vld_temp;
wire[7:0]  po_data_temp;
wire       carry;
assign {pkg_len,ip_len,udp_len}=status[127:80];
//data_cnt
always @(posedge clk or posedge rst) begin
    if(rst)
        data_cnt<='d0;
    else if(pi_data_vld)
               data_cnt<=data_cnt+1'b1;
    else data_cnt<='d0;    
end
//ip_flag ip表头的标志
always @(posedge clk or posedge rst) begin
    if(rst)
        ip_flag<=1'b0;
    else if(data_cnt=='d21)
             ip_flag<=1'b1; 
    else if(data_cnt=='d41) 
             ip_flag<=1'b0;  
    else  ip_flag<=ip_flag;
end
//ip_flag_d 对ip_flag信号进行延时
always @(posedge clk or posedge rst) begin
    if(rst)
         ip_flag_d<=1'b0;
    else 
         ip_flag_d<=ip_flag;
end
//ip_flag_nedge
always @(posedge clk or posedge rst) begin
    if(rst)
        ip_flag_nedge<=1'b0;
    else if(ip_flag==1'b0&&ip_flag_d==1'b1)
               ip_flag_nedge<=1'b1;
    else  ip_flag_nedge<=1'b0; 
end
//ip_shift_cnt
always @(posedge clk or posedge rst) begin
    if(rst)
       ip_shift_cnt<='d0;
    else if(ip_flag)begin
            if(ip_shift_cnt=='d1)
                 ip_shift_cnt<='d0;
            else 
                 ip_shift_cnt<=ip_shift_cnt+1'b1;
    end 
    else 
            ip_shift_cnt<='d0;
end
//ip_shift_vld
always @(posedge clk or posedge rst) begin
    if(rst)
         ip_shift_vld<=1'b0;
    else if(ip_shift_cnt=='d1)
            ip_shift_vld<=1'b1;
    else 
          ip_shift_vld<=1'b0;   
end
//ip_shift
always @(posedge clk or posedge rst) begin
    if(rst)
         ip_shift<='d0;
    else if(ip_flag)
              ip_shift<={ip_shift[7:0],pi_data};
    else 
               ip_shift<='d0;
end
//ip_check_sum
always @(posedge clk or posedge rst) begin
    if(rst)
        ip_check_sum<='d0;
    else if(ip_shift_vld)
                 ip_check_sum<=ip_check_sum+ip_shift;
    else if(ip_flag_nedge)
                 ip_check_sum<='d0;
    else   ip_check_sum<=ip_check_sum;
end 
//ip_crc
always @(posedge clk or posedge rst) begin
    if(rst)
        ip_crc<='d0;
    else if(ip_flag_nedge)
           ip_crc<=~(ip_check_sum[15:0]+ip_check_sum[31:16]);
    else 
           ip_crc<=ip_crc;
end
//udp_flag
always @(posedge clk or posedge rst) begin
    if(rst)
       udp_flag<=1'b0;
    else if(data_cnt=='d33) 
             udp_flag<=1'b1;
    else if(udp_len[0])begin//udp的数据为奇数个时让udp_flag数据延长一个周期
            if(data_cnt==pkg_len-1-4+1)  //
                    udp_flag<=1'b0;
            else  udp_flag<=udp_flag;       
    end 
    else if(data_cnt==pkg_len-1-4)
                udp_flag<=1'b0;
    else  udp_flag<=udp_flag;         
end
//udp_flag_d
always @(posedge clk or posedge rst) begin
    if(rst)
        udp_flag_d<=1'b0;
    else 
        udp_flag_d<=udp_flag;   
end
//udp_shift_cnt
always @(posedge clk or posedge rst) begin
    if(rst)
       udp_shift_cnt<=1'b0;
    else if(udp_flag) 
            udp_shift_cnt<=udp_shift_cnt+1'b1; 
    else  
             udp_shift_cnt<=1'b0;
end
//udp_shift_vld
always @(posedge clk or posedge rst) begin
    if(rst)
        udp_shift_vld<=1'b0;
    else if(udp_shift_cnt==1'b1)
             udp_shift_vld<=1'b1;
    else  udp_shift_vld<=1'b0;  
end
//udp_shift
always @(posedge clk or posedge rst) begin
    if(rst)
       udp_shift<='d0;
    else if(udp_flag) 
            udp_shift<={udp_shift[7:0],pi_data};
    else udp_shift<=udp_shift;
end
//udp_flag_nedge  udp_flag信号的上升沿
always @(posedge clk or posedge rst) begin
    if(rst)
        udp_flag_nedge<=1'b0;
    else if(udp_flag==1'b0&&udp_flag_d==1'b1)
                udp_flag_nedge<=1'b1;
    else udp_flag_nedge<=1'b0;
end
//udp_flag_pedge
assign udp_flag_pedge=udp_flag==1'b1&&udp_flag_d==1'b0;
//udp_check_sum
always @(posedge clk or posedge rst) begin
    if(rst)
       udp_check_sum<='d0;
    else if(udp_flag_nedge==1'b1) 
            udp_check_sum<='d0; 
    else if(udp_flag_pedge==1'b1)
            udp_check_sum<=udp_len+16'h0011; 
    else  if(udp_shift_vld)  //当udp数据为偶数个时
            udp_check_sum<=udp_check_sum+udp_shift;
    else   udp_check_sum<=udp_check_sum;
end
//udp_crc
always @(posedge clk or posedge rst) begin
    if(rst)
        udp_crc_temp<='d0;
    else if(udp_flag_nedge==1'b1)
                udp_crc_temp<={1'b0,udp_check_sum[31:16]}+{1'b0,udp_check_sum[15:0]};
    else    udp_crc_temp<=udp_crc_temp;
end
assign  udp_crc=~{udp_crc_temp[15:0]+udp_crc_temp[16]};
//fifo用于储存数据
FWFT_sync_fifo #(
    .WRITE_DATA_WIDTH(8),
    .FIFO_WRITE_DEPTH(2048),
    .PROG_FULL_THRESH(1920)
) fifo_data (
    .clk(clk),
    .rst(rst),
    .din(pi_data),
    .wr_en(pi_data_vld),
    .rd_en(po_data_vld_temp),
    .dout(po_data_temp),
    .full(full),
    .empty(empty)
);
//po_data_vld_temp
always @(posedge clk or posedge rst) begin
    if(rst)
      po_data_vld_temp<=1'b0;
    else if(udp_flag_nedge==1'b1)
               po_data_vld_temp<=1'b1;
    else if(rd_data_cnt==pkg_len-1-4)
               po_data_vld_temp<=1'b0;
    else  po_data_vld_temp<=po_data_vld_temp;    
end
//rd_data_cnt
always @(posedge clk or posedge rst) begin
    if(rst)
        rd_data_cnt<='d0;
    else if(po_data_vld_temp) begin
            if(rd_data_cnt==pkg_len-1-4)
                 rd_data_cnt<='d0;
            else 
                  rd_data_cnt<=rd_data_cnt+1'b1;
    end         
    else rd_data_cnt<='d0;
end
//po_data_vld
always @(posedge clk or posedge rst) begin
    if(rst)
       po_data_vld<=1'b0;
    else 
       po_data_vld<=po_data_vld_temp;
end
//po_data
always @(posedge clk or posedge rst) begin
    if(rst)
       po_data<='d0;
    else begin
         case (rd_data_cnt)
            32: po_data<=ip_crc[15:8];
            33: po_data<=ip_crc[7:0]; 
            48: po_data<=udp_crc[15:8];   
            49: po_data<=udp_crc[7:0];    
            default:po_data<= po_data_temp;
         endcase
    end  
end
endmodule
