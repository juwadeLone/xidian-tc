// Ported from the A7 project (code_v/eth/eth_tx_crc.v).
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
// 2022.07.18     Ao Guohua     1.0          Original
//                                                      
// --------------------------------------------------------------------
// --------------------------------------------------------------------
//此模块用于对后面的数据流加上校验位
module eth_tx_crc(
         input               clk,
         input               rst,
         input               pi_data_vld,
         input       [7:0]   pi_data,
         output  reg         po_data_vld,
         output  reg [7:0]   po_data 
    );
localparam CRC_LEN = 4  ;
reg            crc_en   ;
reg     [15:0] data_cnt ;
reg     [31:0] c        ;     //校验的数据
wire     [7:0] d        ;
reg            pi_data_vld_d; //用于对输入数据有效信号延时一拍
wire           pi_data_vld_nedge;
reg     [7:0]  pi_data_d;     //用于对数据数据延时一拍
reg     [1:0]  crc_cnt  ; 
reg            crc_flag ;
wire           end_data_cnt ;
//*******pi_data_vld_d*************
always@(posedge clk)begin
  if(rst)                         
        pi_data_vld_d<='d0;                      
  else                               
        pi_data_vld_d<= pi_data_vld;                      
end
assign pi_data_vld_nedge=!pi_data_vld&pi_data_vld_d;
//***********data_cnt*************
always@(posedge clk)begin
  if(rst)                         
        data_cnt<='d0;                      
  else  if(pi_data_vld_d)begin 
            if(pi_data_vld_nedge)
                data_cnt<='d0;
            else 
                data_cnt<=data_cnt+1'b1 ;
  end                                    
  else 
        data_cnt<='d0;                   
end
//************crc_en*****************
always@(posedge clk)begin
  if(rst)                         
       crc_en<='d0;                      
  else if(pi_data_vld_nedge)                              
            crc_en<='d0;   
  else if(data_cnt=='d7) 
            crc_en<='d1; 
  else      crc_en<=crc_en;                        
end
//pi_data_d
always@(posedge clk)begin
  if(rst)
      pi_data_d<='d0;
  else 
      pi_data_d<=pi_data;
end
//d
assign d=pi_data_d;
//----------------crc------------------
always @(posedge clk) begin
	if (rst == 1'b1) begin
		c <= {32{1'b1}};
	end
    else if(pi_data_vld==1'b1&&pi_data_vld_d==1'b0)
            c <= {32{1'b1}};
    else if (crc_en == 1'b1) begin
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
    else begin
        c <= c;
    end
end
//crc_flag
always @(posedge clk or posedge rst) begin
    if(rst)
        crc_flag<=1'b0;
    else if(pi_data_vld_nedge)
            crc_flag<=1'b1;
    else if(crc_cnt=='d3)
             crc_flag<=1'b0;
    else  crc_flag<=crc_flag;
end
//crc_cnt
always @(posedge clk or posedge rst) begin
    if(rst)
        crc_cnt<='d0;
    else if(crc_flag)begin
             if(crc_cnt=='d3)
                  crc_cnt<='d0;
             else 
                  crc_cnt<=crc_cnt+1'b1;
    end   
    else crc_cnt<='d0; 
end
//po_data_vld
always @(posedge clk or posedge rst) begin
    if(rst)
        po_data_vld<=1'b0;
    else if(pi_data_vld_d|crc_flag)
              po_data_vld<=1'b1;
    else  po_data_vld<=1'b0;
end
//po_data
always @(posedge clk or posedge rst) begin
    if(rst)
        po_data<='d0;
    else if(crc_flag)begin
            case (crc_cnt)
                0:po_data<=~{c[24],c[25],c[26],c[27],c[28],c[29],c[30],c[31]};
                1:po_data<=~{c[16],c[17],c[18],c[19],c[20],c[21],c[22],c[23]};
                2:po_data<=~{c[8],c[9],c[10],c[11],c[12],c[13],c[14],c[15]};
                3:po_data<=~{c[0],c[1],c[2],c[3],c[4],c[5],c[6],c[7]};
                default: po_data<='d0;
            endcase
    end
    else po_data<=pi_data_d;
          
end
endmodule
