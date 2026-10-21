// Ported from the A7 project (code_v/eth/udp_eth_tx_gen_udp_frame.v).
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
//接受eth_rx的模块过滤过来的数据，然后用这个模块用于产生以太网的帧头、ip表头、udp表头，
module udp_eth_tx_gen_udp_frame #(
    parameter BOARD_MAC_ADDR  =  48'h00_11_22_33_44_55,//板子的mac可以由我们指定，而点电脑的以太网卡的MAC地址是确定的
    parameter BOARD_IP_ADDR   = {8'd192,8'd168,8'd0,8'd4}
)
(
        input              clk         ,
        input              rst         ,
        input      [128:0] status      ,
        input              fil_data_vld,
        input      [7:0]   fil_data    ,
        output reg         po_data_vld ,
        output reg [7:0]   po_data
    );
wire  frame_flag;
reg [15:0]data_cnt;
wire [15:0]pkg_len;
wire [15:0]ip_len;
wire [15:0]udp_len;
wire [47:0]pc_mac_addr;
wire [31:0]pc_ip_addr ;
assign {pkg_len,ip_len,udp_len,pc_mac_addr,pc_ip_addr}=status[127:0];
//data_cnt
always @(posedge clk or posedge rst) begin
    if(rst)
           data_cnt<='d0;
    else if(fil_data_vld)
                 data_cnt<=data_cnt+1'b1;
    else 
            data_cnt<='d0;  
end
//frame_flag
assign frame_flag=fil_data_vld==1'b1&&data_cnt<=pkg_len-1-4;//去掉后面的4byte的验证数据
//根据计数值将前面的数据替代，给出相应的数值
always @(posedge clk or posedge rst) begin
    if(rst)
       po_data<='d0;
    else if(frame_flag)begin
              case (data_cnt)
                  0,1,2,
                  3,4,5,6:  po_data<=8'h55;    //前导码
                        7:  po_data<=8'hd5;    //帧起始界定符
                  8:        po_data<=pc_mac_addr[47:40];//PC_MAC
                  9:        po_data<=pc_mac_addr[39:32];
                  10:       po_data<=pc_mac_addr[31:24];
                  11:       po_data<=pc_mac_addr[23:16];
                  12:       po_data<=pc_mac_addr[15:8];
                  13:       po_data<=pc_mac_addr[7:0]  ;

                  14 :      po_data<=BOARD_MAC_ADDR[47:40];//BOARD_MAC_ADDR
                  15 :      po_data<=BOARD_MAC_ADDR[39:32];
                  16:       po_data<=BOARD_MAC_ADDR[31:24];
                  17:       po_data<=BOARD_MAC_ADDR[23:16];
                  18:       po_data<=BOARD_MAC_ADDR[15:8];
                  19:       po_data<=BOARD_MAC_ADDR[7:0];   

                  20:       po_data<=8'h08;   //以太网上层协议
                  21:       po_data<=8'h00;   //未知TOS

                  22:       po_data<=8'h45; //网络层协议ip4，一共有5个字节的帧头
                  23:       po_data<=8'h00; //8bit服务类型

                  24:       po_data<=ip_len[15:8];//ip4数据的长度
                  25:       po_data<=ip_len[7:0] ;
                  
                  26:       po_data<=8'h00; //标识都取零
                  27:       po_data<=8'h00; 

                  28:       po_data<=8'h40;//高3bit为是否分片，为否 后面的片偏移为0
                  29:       po_data<=8'h00;//片偏移的低位为0

                  30:       po_data<=8'h40;//TTL生存时间
                  31:       po_data<=8'h11;//标识udp协议

                  32:       po_data<=8'h00;//ip的首部校验和先设置为0
                  33:       po_data<=8'h00;

                  34:       po_data<=BOARD_IP_ADDR[31:24];//板子的ip地址
                  35:       po_data<=BOARD_IP_ADDR[23:16];
                  36:       po_data<=BOARD_IP_ADDR[15:8];  
                  37:       po_data<=BOARD_IP_ADDR[7:0];

                  38:       po_data<=pc_ip_addr[31:24];//电脑的ip地址      
                  39:       po_data<=pc_ip_addr[23:16];
                  40:       po_data<=pc_ip_addr[15:8];  
                  41:       po_data<=pc_ip_addr[7:0];
                 
                  42:       po_data<=8'h1f;  //udp的源端口号
                  43:       po_data<=8'h90;

                  44:       po_data<=8'h1f;  //udp目的端口号
                  45:       po_data<=8'h90;  
                  
                  46:       po_data<=udp_len[15:8]; //udp的长度
                  47:       po_data<=udp_len[7 :0];

                  48:       po_data<=8'h00;      //udp的校验位
                  49:       po_data<=8'h00;         
                  default:po_data<=fil_data; 
              endcase     
    end
    else po_data<='d0;
end
//po_data_vld
always @(posedge clk or posedge rst) begin
    if(rst)
        po_data_vld<=1'b0;
    else if(frame_flag)
       po_data_vld<=fil_data_vld;
    else 
        po_data_vld<=1'b0;
end
endmodule
