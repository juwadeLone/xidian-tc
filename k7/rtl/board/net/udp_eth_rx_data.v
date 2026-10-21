// Ported from the A7 project (code_v/eth/udp_eth_rx_data.v).
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
//这个模块缓存了udp传输过来的数据，并抛弃udp的错误包
module udp_eth_rx_data(
    input                               clk                        ,
    input                               rst                        ,
    input                               gmii_rx_dv                 ,
    input              [   7:0]         gmii_rd                    ,
    input                               rd_req                     ,
    output                              rd_ack                     ,
    input              [ 128:0]         status                     ,
    output                              buf_busy                   ,
    output             [   7:0]         fil_data                   ,
    output                              fil_data_vld                
    );
    parameter                           ARBIT = 2'b01              ;
    parameter                           READ =  2'b10              ;
reg                    [   1:0]         state                      ;
reg                    [  15:0]         data_cnt                   ;
reg                                     rd_en                      ;
wire                                    full                       ;
wire                                    empty                      ;
wire                                    end_data_cnt               ;
wire                                    err_flag                   ;//这帧数据的错误标志
wire                   [  15:0]         pkg_len                    ;//这帧数据的长度，包含前导码、sfd以及crc32的验证位
/*status={err_flag,pkg_len,ip_len,udp_len,pc_mac_addr.pc_ip_addr};*/
assign  err_flag=status[128];
assign  pkg_len =status[127:112];
assign  buf_busy=state==READ;
assign fil_data_vld=err_flag?1'b0:rd_en;
assign rd_ack=state==ARBIT&&rd_req;

//状态机
always @(posedge clk or posedge rst ) begin
    if(rst)
        state<=ARBIT;
    else
      case (state)
            ARBIT:begin
                  if(rd_ack)
                      state<=READ;
                  else
                      state<=ARBIT;
            end
            READ:begin
                 if(end_data_cnt)
                     state<=ARBIT;
                 else
                     state<=READ;
            end
            default: state<=ARBIT;
         endcase
end
//fifo存储数据
FWFT_sync_fifo #(
    .WRITE_DATA_WIDTH                  (8                         ),
    .FIFO_WRITE_DEPTH                  (2048                      ),
    .PROG_FULL_THRESH                  (1920                      )
)fifo_udp_data (
    .clk                               (clk                       ),// input wire clk
    .rst                               (rst                       ),// input wire srst
    .din                               (gmii_rd                   ),// input wire [7 : 0] din
    .wr_en                             (gmii_rx_dv                ),// input wire wr_en
    .rd_en                             (rd_en                     ),// input wire rd_en
    .dout                              (fil_data                  ),// output wire [7 : 0] dout
    .full                              (full                      ),// output wire full
    .empty                             (empty                     ) // output wire empty
  );

//rd_en
always @(posedge clk or posedge rst ) begin
    if(rst)
        rd_en<=1'b0;
    else if(rd_ack)
               rd_en<=1'b1;
    else if(end_data_cnt)
               rd_en<=1'b0;
    else  rd_en<=rd_en;
end
//data_cnt
always @(posedge clk or posedge rst ) begin
    if(rst)
        data_cnt<='d0;
    else if(end_data_cnt)
            data_cnt<='d0;
    else if(rd_en)
             data_cnt<=data_cnt+1'b1;
    else  data_cnt<='d0;
end

assign end_data_cnt=data_cnt==pkg_len-1'b1;
endmodule
