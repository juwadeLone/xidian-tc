// Ported from the A7 project (code_v/eth/udp_eth_tx.v).
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
//用于产生udp的数据帧
module udp_eth_tx  #(
    parameter BOARD_MAC_ADDR  =  48'h00_11_22_33_44_55,     //板子的mac可以由我们指定，而点电脑的以太网卡的MAC地址是确定的
    parameter BOARD_IP_ADDR   = {8'd192,8'd168,8'd0,8'd4}  //开发板的ip地址也可以由我们指定
)
(
     input         clk         ,
     input         rst         ,
     input  [128:0] status      ,
     input         fil_data_vld,
     input  [7:0]  fil_data    ,
     output        po_data_vld ,
     output [7:0]  po_data      
    );

wire         po_data_vld0 ;
wire [7:0]   po_data0     ;
wire         po_data_vld1 ;
wire [7:0]   po_data1     ;


udp_eth_tx_gen_udp_frame #(
    .BOARD_MAC_ADDR  (BOARD_MAC_ADDR ),  //板子的mac可以由我们指定，而点电脑的以太网卡的MAC地址是确定的
    .BOARD_IP_ADDR   (BOARD_IP_ADDR  )
) udp_eth_tx_gen_udp_frame0
(
        .clk         (clk         ),
        .rst         (rst         ),
        .status      (status      ),
        .fil_data_vld(fil_data_vld),
        .fil_data    (fil_data    ),
        .po_data_vld (po_data_vld0 ),
        .po_data     (po_data0     )
    );
udp_eth_tx_cal_check_sum  udp_eth_tx_cal_check_sum0(
        .clk        (clk   ),
        .rst        (rst   ),
        .status     (status),
        .pi_data_vld(po_data_vld0),
        .pi_data    (po_data0),
        .po_data_vld(po_data_vld1),
        .po_data    (po_data1    )
    );
eth_tx_crc  eth_tx_crc(
         .clk        (clk    ),
         .rst        (rst    ),
         .pi_data_vld(po_data_vld1),
         .pi_data    (po_data1),
         .po_data_vld(po_data_vld),
         .po_data    (po_data)
    );
endmodule
