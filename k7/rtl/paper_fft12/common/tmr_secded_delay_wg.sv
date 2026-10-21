`timescale 1ns/1ps
(* keep_hierarchy="yes" *)
module tmr_secded_delay_wg #(
    parameter integer DATA_W=34, DEPTH=128,
    parameter integer AW=(DEPTH<=1 ? 1 : $clog2(DEPTH)),
    parameter integer CODE_W=DATA_W+7
)(
    input wire clk,
    input wire [2:0] rd_en, wr_en,
    input wire [AW-1:0] rd_addr0, rd_addr1, rd_addr2, wr_addr0, wr_addr1, wr_addr2,
    input wire [DATA_W-1:0] wr_data0, wr_data1, wr_data2,
    output wire [DATA_W-1:0] rd_data,
    output wire [4:0] mismatch
);
wire voted_rd_en, voted_wr_en;
wire [AW-1:0] voted_rd_addr, voted_wr_addr;
wire [DATA_W-1:0] voted_wr_data;
wire [CODE_W-1:0] read_code, write_code;
(* DONT_TOUCH="yes" *) reg [2:0] wr_en_q;
(* DONT_TOUCH="yes" *) reg [AW-1:0] wr_addr0_q, wr_addr1_q, wr_addr2_q;
(* DONT_TOUCH="yes" *) reg [DATA_W-1:0] wr_data0_q, wr_data1_q, wr_data2_q;
wire [DATA_W-1:0] q_data0=wr_data0_q, q_data1=wr_data1_q, q_data2=wr_data2_q;
wire [DATA_W-1:0] decoded_data;
wire [AW-1:0] read_address;
wire read_active;
wire forward_active=read_active && voted_wr_en && (read_address==voted_wr_addr);
always @(posedge clk) begin
    wr_en_q<=wr_en;
    wr_addr0_q<=wr_addr0; wr_addr1_q<=wr_addr1; wr_addr2_q<=wr_addr2;
    wr_data0_q<=wr_data0; wr_data1_q<=wr_data1; wr_data2_q<=wr_data2;
end
generate
if(DEPTH==128) begin : g_sync_read_forward
    reg [AW-1:0] rd_addr_q;
    reg rd_en_q;
    always @(posedge clk) begin
        rd_en_q<=voted_rd_en;
        if(voted_rd_en) rd_addr_q<=voted_rd_addr;
    end
    assign read_address=rd_addr_q;
    assign read_active=rd_en_q;
end else begin : g_async_read_forward
    assign read_address=voted_rd_addr;
    assign read_active=voted_rd_en;
end
endgenerate
tmr_vote_w #(.W(1)) u_rd_en_vote(rd_en[0],rd_en[1],rd_en[2],voted_rd_en,mismatch[0]);
tmr_vote_w #(.W(AW)) u_rd_addr_vote(rd_addr0,rd_addr1,rd_addr2,voted_rd_addr,mismatch[1]);
tmr_vote_w #(.W(1)) u_wr_en_vote(wr_en_q[0],wr_en_q[1],wr_en_q[2],voted_wr_en,mismatch[2]);
tmr_vote_w #(.W(AW)) u_wr_addr_vote(wr_addr0_q,wr_addr1_q,wr_addr2_q,voted_wr_addr,mismatch[3]);
tmr_vote_w #(.W(DATA_W)) u_wr_data_vote(q_data0,q_data1,q_data2,voted_wr_data,mismatch[4]);
secded_encode_w #(.DATA_W(DATA_W), .PARITY(6)) u_encode(voted_wr_data,write_code);
fft_memory_v5 #(.WIDTH(CODE_W), .DEPTH(DEPTH), .ADDR_W(AW)) u_memory(
    .clk(clk), .rd_en(voted_rd_en), .rd_addr(voted_rd_addr), .rd_data(read_code),
    .wr_en(voted_wr_en), .wr_addr(voted_wr_addr), .wr_data(write_code));
secded_decode_w #(.DATA_W(DATA_W), .PARITY(6)) u_decode(
    .codeword(read_code), .data(decoded_data), .detected(), .corrected());
assign rd_data=forward_active ? voted_wr_data : decoded_data;
endmodule
