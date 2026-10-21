// Ported from the A7 project (code_v/eth/gmii_to_rgmii.v).
// Verbatim: the transmit side is ODDR only, with no delay to calibrate.
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
// 2022.07.26     Ao Guohua     1.0          Original
//                                                      
// --------------------------------------------------------------------
// --------------------------------------------------------------------
//将gmii接口转化为rgmii接口
  module gmii_to_rgmii(
      input                rst         , //复位信号
      input                idelay_clk  ,
      //gmii  接口    
      input                gmii_txc    ,
      input                txc_fwd_clk , //90 度相移，用于前传给 PHY
      input                gmii_tx_dv  ,
      input                gmii_tx_err ,
      input       [7:0]    gmii_td     ,
       //rgmii 接口
      output               rgmii_txc   ,
      output               rgmii_tx_ctl,
      output       [3:0]   rgmii_td    
      );
//  clk_phase_shift clk_phase_shift
//   (
//    // Clock out ports
//    .clk_out1(rgmii_txc),     // output clk_out1
//    // Status and control signals
//    .reset(rst), // input reset
//    .locked(),       // output locked
//   // Clock in ports
//    .clk_in1(gmii_txc));      // input clk_in1
    // Two changes from the A7 original, made one at a time and measured:
    //
    // 1. It had `assign rgmii_txc = gmii_txc;`, putting a global clock net
    //    straight onto an output pin.  7-series forwards a clock through an ODDR
    //    in the IOB instead.  On its own this did not make the link work.
    // 2. The forwarded clock now comes from txc_fwd_clk, which the top level
    //    supplies 90 degrees shifted.  RGMII wants the clock centred in the data
    //    eye, and the data ODDRs below stay on gmii_txc, so forwarding an
    //    in-phase clock leaves the PHY sampling on the data transitions.  The A7
    //    source carries a commented-out clk_phase_shift doing exactly this,
    //    which is what pointed here.
    ODDR #(
        .DDR_CLK_EDGE("SAME_EDGE"),
        .INIT(1'b0),
        .SRTYPE("SYNC")
    ) ODDR_txc (
        .Q(rgmii_txc),
        .C(txc_fwd_clk),
        .CE(1'b1),
        .D1(1'b1),
        .D2(1'b0),
        .R(1'b0),
        .S(1'b0)
    );

  //rgmii_rx_ctl
      ODDR #(
      .DDR_CLK_EDGE("SAME_EDGE"), // "OPPOSITE_EDGE" or "SAME_EDGE"
      .INIT(1'b0), // Initial value of Q: 1'b0 or 1'b1
      .SRTYPE("SYNC") // Set/Reset type: "SYNC" or "ASYNC"
      ) ODDR_inst0(
      .Q(rgmii_tx_ctl), // 1-bit DDR output
      .C(gmii_txc), // clock net, not the forwarded pin
      .CE(1'b1), // 1-bit clock enable input
      .D1(gmii_tx_dv), // 1-bit data input (positive edge)
      .D2(gmii_tx_err^gmii_tx_dv), // 1-bit data input (negative edge)
      .R(1'b0), // 1-bit reset
      .S(rst) // 1-bit set
      );
  //rgmii_rd
  generate
      genvar i;
      for (i = 0;i<4 ; i=i+1) begin
         ODDR #(
      .DDR_CLK_EDGE("SAME_EDGE"), // "OPPOSITE_EDGE" or "SAME_EDGE"
      .INIT(1'b0), // Initial value of Q: 1'b0 or 1'b1
      .SRTYPE("SYNC") // Set/Reset type: "SYNC" or "ASYNC"
      ) ODDR_inst1(
      .Q(rgmii_td[i]), // 1-bit DDR output
      .C(gmii_txc), // clock net, not the forwarded pin
      .CE(1'b1), // 1-bit clock enable input
      .D1(gmii_td[i]), // 1-bit data input (positive edge)
      .D2(gmii_td[i+4]), // 1-bit data input (negative edge)
      .R(1'b0), // 1-bit reset
      .S(rst) // 1-bit set
      ); 
          
      end
  endgenerate
  endmodule
