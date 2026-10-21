// Ported from the A7 project (code_v/eth/eth_rx_crc.v).
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
//此模块用于对接受的数据进行crc32位校验
module eth_rx_crc(
        input          clk      ,
        input          rst      ,
        input          gmii_rx_dv  ,
        input [7:0]    gmii_rd  ,
        output  reg    crc_last ,//输出一个高电平表示校验结束
        output  reg    crc_err   //用于判断校验是否正确
    );
wire [7:0] d;
wire  crc_en;
reg [31:0] c;
reg [31:0]data_cnt;
reg data_temp;
reg gmii_rx_dv_d;
//data_cnt
always @(posedge clk or posedge rst) begin
    if(rst)
        data_cnt<='d0;
    else if(gmii_rx_dv)
            data_cnt<=data_cnt+1'b1;
    else  data_cnt<='d0;  
end
//data_temp 用于去掉前导码和帧起始界定符
always @(posedge clk or posedge rst) begin
    if(rst)
        data_temp<=1'b0;
    else if(data_cnt=='d7)
             data_temp<=1'b1;
    else if(gmii_rx_dv==1'b0)
              data_temp<=1'b0;
    else   data_temp<=data_temp;
end
//crc_en为高电平时所对应的数据去掉了前面的7字节的前导码和一个字节的帧起始界定符
assign crc_en=data_temp&gmii_rx_dv;
assign d=gmii_rd;
//crc32校验
always @(posedge clk or posedge rst) begin
    if(rst)
        c<={32{1'b1}};
    else if(crc_en)begin
            c[0] <= c[24]^c[30]^d[1]^d[7];
            c[1] <= c[25]^c[31]^d[0]^d[6]^c[24]^c[30]^d[1]^d[7];
            c[2] <= c[26]^d[5]^c[25]^c[31]^d[0]^d[6]^c[24]^c[30]^d[1]^d[7];
            c[3] <= c[27]^d[4]^c[26]^d[5]^c[25]^c[31]^d[0]^d[6];
            c[4] <= c[28]^d[3]^c[27]^d[4]^c[26]^d[5]^c[24]^c[30]^d[1]^d[7];
            c[5] <= c[29]^d[2]^c[28]^d[3]^c[27]^d[4]^c[25]^c[31]^d[0]^d[6]^c[24]^c[30]^d[1]^d[7];
            c[6] <= c[30]^d[1]^c[29]^d[2]^c[28]^d[3]^c[26]^d[5]^c[25]^c[31]^d[0]^d[6];
            c[7] <= c[31]^d[0]^c[29]^d[2]^c[27]^d[4]^c[26]^d[5]^c[24]^d[7];
            c[8] <= c[0]^c[28]^d[3]^c[27]^d[4]^c[25]^d[6]^c[24]^d[7];
            c[9] <= c[1]^c[29]^d[2]^c[28]^d[3]^c[26]^d[5]^c[25]^d[6];
            c[10]<= c[2]^c[29]^d[2]^c[27]^d[4]^c[26]^d[5]^c[24]^d[7];
            c[11]<= c[3]^c[28]^d[3]^c[27]^d[4]^c[25]^d[6]^c[24]^d[7];
            c[12]<= c[4]^c[29]^d[2]^c[28]^d[3]^c[26]^d[5]^c[25]^d[6]^c[24]^c[30]^d[1]^d[7];
            c[13]<= c[5]^c[30]^d[1]^c[29]^d[2]^c[27]^d[4]^c[26]^d[5]^c[25]^c[31]^d[0]^d[6];
            c[14]<= c[6]^c[31]^d[0]^c[30]^d[1]^c[28]^d[3]^c[27]^d[4]^c[26]^d[5];
            c[15]<= c[7]^c[31]^d[0]^c[29]^d[2]^c[28]^d[3]^c[27]^d[4];
            c[16]<= c[8]^c[29]^d[2]^c[28]^d[3]^c[24]^d[7];
            c[17]<= c[9]^c[30]^d[1]^c[29]^d[2]^c[25]^d[6];
            c[18]<= c[10]^c[31]^d[0]^c[30]^d[1]^c[26]^d[5];
            c[19]<= c[11]^c[31]^d[0]^c[27]^d[4];
            c[20]<= c[12]^c[28]^d[3];
            c[21]<= c[13]^c[29]^d[2];
            c[22]<= c[14]^c[24]^d[7];
            c[23]<= c[15]^c[25]^d[6]^c[24]^c[30]^d[1]^d[7];
            c[24]<= c[16]^c[26]^d[5]^c[25]^c[31]^d[0]^d[6];
            c[25]<= c[17]^c[27]^d[4]^c[26]^d[5];
            c[26]<= c[18]^c[28]^d[3]^c[27]^d[4]^c[24]^c[30]^d[1]^d[7];
            c[27]<= c[19]^c[29]^d[2]^c[28]^d[3]^c[25]^c[31]^d[0]^d[6];
            c[28]<= c[20]^c[30]^d[1]^c[29]^d[2]^c[26]^d[5];
            c[29]<= c[21]^c[31]^d[0]^c[30]^d[1]^c[27]^d[4];
            c[30]<= c[22]^c[31]^d[0]^c[28]^d[3];
            c[31]<= c[23]^c[29]^d[2];
    end
    else 
            c<={32{1'b1}}; 
end
//gmii_rx_dv_d
always @(posedge clk or posedge rst) begin
   if(rst) 
        gmii_rx_dv_d<=1'b0;
   else 
        gmii_rx_dv_d<=gmii_rx_dv;
end
//crc_last
always @(posedge clk or posedge rst) begin
   if(rst) 
        crc_last<=1'b0;
   else if(gmii_rx_dv==1'b0&&gmii_rx_dv_d==1'b1)
               crc_last<=1'b1;
   else  crc_last<=1'b0;
end
//crc_err
always @(posedge clk or posedge rst) begin
   if(rst)
        crc_err<=1'b0;
    else if(gmii_rx_dv==1'b0&&gmii_rx_dv_d==1'b1) begin
              if(c==32'hc704dd7b)
                    crc_err<=1'b0;
              else 
                    crc_err<=1'b1;
    end
    else   crc_err<= crc_err ;
end
endmodule
