// Ported from the A7 project (code_v/eth/rgmii_to_gmii.v).
//
// The A7 hardcoded IDELAY_VALUE(16), tuned for its own trace lengths.  Here the
// taps are loaded at runtime (IDELAY_TYPE VAR_LOAD, tap_value/tap_load) so the
// right value can be measured instead of assumed.
//
// Why runtime and not a parameter: with taps at 0 the frame count matched the
// host exactly -- DV timing was right -- yet only a third of the looped-back
// frames survived, because a frame count says nothing about whether the data
// bits inside it were sampled correctly.  Bit errors corrupt the FCS and the far
// end discards the frame silently.  Sweeping a parameter costs a four-minute
// rebuild per value; sweeping CNTVALUEIN costs a VIO write.
//
// IDELAYCTRL needs a 200 MHz reference on idelay_clk (REFCLK_FREQUENCY below),
// and every IDELAYE2 in the group must share the IODELAY_GROUP attribute.
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
//本模块用于把rgmii接口（时钟上升沿和下降沿都传输数据）转化为gmii接口（时钟上升沿传输数据）
module rgmii_to_gmii(
    input               idelay_clk  ,
    input       [4:0]   tap_value   , //IDELAY tap, measured not assumed
    input               tap_load    , //pulse to load tap_value
    input               rst         , //复位信号
    //rgmii 接口
    input               rgmii_rxc   ,
    input               rgmii_rx_ctl,
    input       [3:0]   rgmii_rd    ,
    //gmii  接口 
    output              gmii_rxc    ,
    output              gmii_rx_dv  ,
    output              gmii_rx_err  ,
    output      [7:0]   gmii_rd     
    );
wire           rgmii_rx_ctl_d;
wire   [3:0]   rgmii_rd_d    ;
wire           gmii_rx_xor   ;  //gmii_dv^gmii_error
assign gmii_rxc = rgmii_rxc;
assign gmii_rx_err=gmii_rx_dv^gmii_rx_xor;
//输入延时控制
// Specifies group name for associated IDELAYs/ODELAYs and IDELAYCTRL
(* IODELAY_GROUP = "rgmii_rx_delay" *) 
IDELAYCTRL  IDELAYCTRL_inst (
    .RDY(),                      // 1-bit output: Ready output
    .REFCLK(idelay_clk),         // 1-bit input: Reference clock input
    .RST(1'b0)                   // 1-bit input: Active high reset input
);
//rgmii_rx_ctl输入延时与双沿采样
(* IODELAY_GROUP = "rgmii_rx_delay" *) 
IDELAYE2 #(
      .CINVCTRL_SEL("FALSE"),          // Enable dynamic clock inversion (FALSE, TRUE)
      .DELAY_SRC("IDATAIN"),           // Delay input (IDATAIN, DATAIN)
      .HIGH_PERFORMANCE_MODE("FALSE"), // Reduced jitter ("TRUE"), Reduced power ("FALSE")
      .IDELAY_TYPE("VAR_LOAD"),        // runtime-loadable: the tap value is a
                                       // board measurement, not a constant
      .IDELAY_VALUE(0),                // ignored in VAR_LOAD; taps come from CNTVALUEIN
      .PIPE_SEL("FALSE"),              // Select pipelined mode, FALSE, TRUE
      .REFCLK_FREQUENCY(200.0),        // IDELAYCTRL clock input frequency in MHz (190.0-210.0, 290.0-310.0).
      .SIGNAL_PATTERN("CLOCK")          // DATA, CLOCK input signal
   )
IDELAYE2_inst (
              .CNTVALUEOUT     (),                  // 5-bit output: Counter value output
              .DATAOUT         (rgmii_rx_ctl_d),                  // 1-bit output: Delayed data output
              .C               (idelay_clk),        // VAR_LOAD makes LD synchronous
              .CE              (1'b0),              // 1-bit input: enable increment/decrement
              .CINVCTRL        (1'b0),              // 1-bit input: Dynamic clock inversion input
              .CNTVALUEIN      (tap_value),              // 5-bit input: Counter value input
              .DATAIN          (1'b0),              // 1-bit input: Internal delay data input
              .IDATAIN         (rgmii_rx_ctl),                  // 1-bit input: Data input from the I/O
              .INC             (1'b0),              // 1-bit input: Increment / Decrement tap delay
              .LD              (tap_load),          // load CNTVALUEIN
              .LDPIPEEN        (1'b0),              // 1-bit input: Enable PIPELINE register
              .REGRST          (1'b0)               // 1-bit input: Active-high reset tap-delay input
   );
// 控制信号，double data rate to single data rate
    IDDR #(
    .DDR_CLK_EDGE("SAME_EDGE_PIPELINED"), // "OPPOSITE_EDGE", "SAME_EDGE"
    // or "SAME_EDGE_PIPELINED"
    .INIT_Q1(1'b0), // Initial value of Q1: 1'b0 or 1'b1
    .INIT_Q2(1'b0), // Initial value of Q2: 1'b0 or 1'b1
    .SRTYPE("SYNC") // Set/Reset type: "SYNC" or "ASYNC"
    ) IDDR_inst0 (
    .Q1(gmii_rx_dv), // 1-bit output for positive edge of clock
    .Q2(gmii_rx_xor), // 1-bit output for negative edge of clock
    .C(rgmii_rxc), // 1-bit clock input posedge 
    .CE(1'b1), // 1-bit clock enable input
    .D(rgmii_rx_ctl_d), // 1-bit DDR data input posedge gmii_dv; negedge gmii_rx_xor
    .R(1'b0), // 1-bit reset
    .S(rst) // 1-bit set     
    );
generate
    genvar i;
 for(i=0;i<4;i=i+1'b1)begin

(* IODELAY_GROUP = "rgmii_rx_delay" *) 
IDELAYE2 #(
      .CINVCTRL_SEL("FALSE"),          // Enable dynamic clock inversion (FALSE, TRUE)
      .DELAY_SRC("IDATAIN"),           // Delay input (IDATAIN, DATAIN)
      .HIGH_PERFORMANCE_MODE("FALSE"), // Reduced jitter ("TRUE"), Reduced power ("FALSE")
      .IDELAY_TYPE("VAR_LOAD"),        // runtime-loadable: the tap value is a
                                       // board measurement, not a constant
      .IDELAY_VALUE(0),                // ignored in VAR_LOAD; taps come from CNTVALUEIN
      .PIPE_SEL("FALSE"),              // Select pipelined mode, FALSE, TRUE
      .REFCLK_FREQUENCY(200.0),        // IDELAYCTRL clock input frequency in MHz (190.0-210.0, 290.0-310.0).
      .SIGNAL_PATTERN("CLOCK")          // DATA, CLOCK input signal
   )
IDELAYE2_inst (
              .CNTVALUEOUT     (),                  // 5-bit output: Counter value output
              .DATAOUT         (rgmii_rd_d[i]),     // 1-bit output: Delayed data output
              .C               (idelay_clk),        // VAR_LOAD makes LD synchronous
              .CE              (1'b0),              // 1-bit input: enable increment/decrement
              .CINVCTRL        (1'b0),              // 1-bit input: Dynamic clock inversion input
              .CNTVALUEIN      (tap_value),              // 5-bit input: Counter value input
              .DATAIN          (1'b0),              // 1-bit input: Internal delay data input
              .IDATAIN         (rgmii_rd[i]),       // 1-bit input: Data input from the I/O
              .INC             (1'b0),              // 1-bit input: Increment / Decrement tap delay
              .LD              (tap_load),          // load CNTVALUEIN
              .LDPIPEEN        (1'b0),              // 1-bit input: Enable PIPELINE register
              .REGRST          (1'b0)               // 1-bit input: Active-high reset tap-delay input
   );
  IDDR #(
    .DDR_CLK_EDGE("SAME_EDGE_PIPELINED"), // "OPPOSITE_EDGE", "SAME_EDGE"
    // or "SAME_EDGE_PIPELINED"
    .INIT_Q1(1'b0), // Initial value of Q1: 1'b0 or 1'b1
    .INIT_Q2(1'b0), // Initial value of Q2: 1'b0 or 1'b1
    .SRTYPE("SYNC") // Set/Reset type: "SYNC" or "ASYNC"
    ) IDDR_inst0 (
    .Q1(gmii_rd[i]), // 1-bit output for positive edge of clock
    .Q2(gmii_rd[i+4]), // 1-bit output for negative edge of clock
    .C(rgmii_rxc), // 1-bit clock input posedge 
    .CE(1'b1), // 1-bit clock enable input
    .D(rgmii_rd_d[i]), // 1-bit DDR data input posedge gmii_dv; negedge gmii_rx_err
    .R(1'b0), // 1-bit reset
    .S(rst) // 1-bit set     
    );
 end
endgenerate
endmodule
