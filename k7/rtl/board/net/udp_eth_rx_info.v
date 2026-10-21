// Ported from the A7 project (code_v/eth/udp_eth_rx_info.v).
// Verbatim apart from the include path and, where marked, ADDR_W 29->31
// for the K7's 2 GB address space (contracts/k7_ddr_axi_decision.md).
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
// 2022.07.19     Ao Guohua     1.0          Original
//                                                      
// --------------------------------------------------------------------
// --------------------------------------------------------------------
  //此模块判断协议是否为udp,把需要的信息储存在fifo中供后面的模块使用
  module udp_eth_rx_info
      #( parameter BOARD_MAC_ADDR = 48'h00_11_22_33_44_55 ,       //目的的MAC地址
         parameter BOARD_IP_ADDR = {8'd192, 8'd168, 8'd0, 8'd4}  //FPGA IP地址
       )
    (
          input       rst       ,
          input       clk       ,
          input       gmii_rx_dv,
          input [7:0] gmii_rd   ,
          input       crc_err   ,//crc校验是否有错
          input       crc_last  ,//校验结束同时也表明一帧结束
          input       buf_busy  ,//存储数据的模块是否忙碌
    
          output [128:0] status  ,//用于寄存一帧数据的长度以及是否错误
          input         rd_ack  ,//data_buferr对rd_req的应答信号
          output        rd_req   //向data_buffer发出读出数据的要求
      );
  reg [47:0]   pc_mac_addr ; //提取电脑的网口的mac地址
  reg [31:0]   pc_ip_addr  ; //提取点电脑网口的ip地址
  reg [47:0]   dst_mac_addr;
  reg [31:0]   dst_ip_addr;
  reg [15:0]   pkg_len;  //这帧数据的长度，包含前导码、帧起始界定符、crc32验证码
  reg [15:0]   ip_len;   //网络层ip的数据长度
  reg [15:0]   udp_len;  //传输层udp的数据长度
  reg          pkg_err; //这包数据mac地址、ip地址
  reg [15:0]   data_cnt ;//数据个数计数器
  reg          gmii_rx_dv_d; //用于把gmii_rx_dv延时一拍
  wire [128:0]  din  ;
  wire         full  ;
  wire         empty ;
  reg  [15:0]  protocol;
  //data_cnt
  always @(posedge clk or posedge rst) begin
      if(rst)
          data_cnt<='d0;
      else if(gmii_rx_dv)
               data_cnt<=data_cnt+1'b1;
      else data_cnt<='d0;
  end
  //gmii_rx_dv_d
  always @(posedge clk or posedge rst) begin
      if(rst)
         gmii_rx_dv_d<=1'b0;
      else 
         gmii_rx_dv_d<=gmii_rx_dv; 
  end
  //protocol
  always @(posedge clk or posedge rst) begin
      if(rst)
         protocol<='d0;
      else if(data_cnt>=20&&data_cnt<=21)
              protocol<={protocol[7:0],gmii_rd};
      else 
              protocol<=protocol; 
  end
  //pkg_len 
  always @(posedge clk or posedge rst) begin
      if(rst)
          pkg_len<='d0;
      else if(gmii_rx_dv==1'b0&&gmii_rx_dv_d==1'b1)
                pkg_len<=data_cnt;
      else  pkg_len<=pkg_len;       
  end
  //ip_len
  always @(posedge clk or posedge rst) begin
      if(rst)
          ip_len<='d0;
      else if(data_cnt>=24&&data_cnt<=25)
              ip_len<={ip_len[7:0],gmii_rd};
      else ip_len<=ip_len;     
  end
  //udp_len
  always @(posedge clk or posedge rst) begin
      if(rst)
         udp_len<='d0;
      else if(data_cnt>=46&&data_cnt<=47)
               udp_len<={udp_len[7:0],gmii_rd};
      else 
              udp_len<=udp_len;
  end
  //dst_mac_addr
  always @(posedge clk or posedge rst) begin
      if(rst)
         dst_mac_addr<='d0;
      else if(data_cnt>=8&&data_cnt<=13) 
              dst_mac_addr<={dst_mac_addr[39:0],gmii_rd};
      else dst_mac_addr<= dst_mac_addr;
  end
  //dst_ip_addr
  always @(posedge clk or posedge rst) begin
      if(rst)
          dst_ip_addr<='d0;
      else if(data_cnt>=38&&data_cnt<=41)
                  dst_ip_addr<={dst_ip_addr[23:0],gmii_rd};
      else  dst_ip_addr<=dst_ip_addr;
  end
  //pkg_err
  always @(negedge clk  or posedge rst) begin
     if(rst)
          pkg_err<=1'b0;
     else if(gmii_rx_dv==1'b0&&gmii_rx_dv_d==1'b1)begin
                   if((dst_mac_addr==BOARD_MAC_ADDR||dst_mac_addr==48'hff_ff_ff_ff_ff_ff) 
                      &&(dst_ip_addr==BOARD_IP_ADDR)&&(protocol==16'h0800))
                       pkg_err<=1'b0;
                   else 
                       pkg_err<=1'b1;    //mac地址不对，产生错误信号 
     end
     else pkg_err<=pkg_err;
  end
  //****************pc_mac_addr************************
  always@(posedge clk or posedge rst)begin
    if(rst)                         
          pc_mac_addr<='d0;                      
    else  if(data_cnt>='d14&&data_cnt<='d19)                             
          pc_mac_addr<={pc_mac_addr[39:0],gmii_rd};  
    else 
          pc_mac_addr<=pc_mac_addr;                       
  end
  //**************pc_ip_addr***************************
  always@(posedge clk or posedge rst)begin
    if(rst)                           
          pc_ip_addr<='d0;                       
    else if(data_cnt>='d34&&data_cnt<='d37)begin                     
              pc_ip_addr<={pc_ip_addr[23:0],gmii_rd};                      
     end                                 
    else  begin                          
              pc_ip_addr<=pc_ip_addr;                      
    end                                  
  end
  assign din={pkg_err|crc_err,pkg_len,ip_len,udp_len,pc_mac_addr,pc_ip_addr};
FWFT_sync_fifo #(
    .WRITE_DATA_WIDTH                  (129                        ),
    .FIFO_WRITE_DEPTH                  (256                      ),
    .PROG_FULL_THRESH                  (192                       )
)fifo_udp_info (
    .clk                               (clk                       ),// input wire clk
    .rst                               (rst                       ),// input wire srst
    .din                               (din                       ),// input wire [48 : 0] din
    .wr_en                             (crc_last                  ),// input wire wr_en
    .rd_en                             (rd_ack                    ),// input wire rd_en
    .dout                              (status                    ),// output wire [48 : 0] dout
    .full                              (full                      ),// output wire full
    .empty                             (empty                     ) // output wire empty
  );
  assign rd_req=buf_busy==1'b0&&empty==1'b0;
  endmodule