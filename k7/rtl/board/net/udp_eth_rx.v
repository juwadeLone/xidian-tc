// Ported from the A7 project (code_v/eth/udp_eth_rx.v).
// Verbatim apart from the include path and, where marked, ADDR_W 29->31
// for the K7's 2 GB address space (contracts/k7_ddr_axi_decision.md).
`timescale 1ns / 1ps

module udp_eth_rx
   #(parameter BOARD_MAC_ADDR = 48'h00_11_22_33_44_55,
     parameter BOARD_IP_ADDR  = {8'd192, 8'd168, 8'd0, 8'd4})
   (
    input                               clk                        ,
    input                               rst                        ,
    input                               I_sys_clk                  ,
    input                               I_sys_rst_n                ,
    input                               gmii_rx_dv                 ,
    input              [   7:0]         gmii_rd                    ,
    output             [ 128:0]         status                     ,
    output             [   7:0]         fil_data                   ,
    output                              fil_data_vld               ,
    output             [ 127:0]         pld_tdata                  ,
    output                              pld_tvalid                 ,
    input                               pld_tready                 ,
    output             [  47:0]         pc_mac                     ,
    output             [  31:0]         pc_ip
    );

wire        crc_last;
wire        crc_err ;
wire        buf_busy;
wire        rd_ack  ;
wire        rd_req  ;

eth_rx_crc udp_eth_rx_crc(
    .clk         (clk     ),
    .rst         (rst     ),
    .gmii_rx_dv  (gmii_rx_dv ),
    .gmii_rd     (gmii_rd ),
    .crc_last    (crc_last),
    .crc_err     (crc_err )
);
udp_eth_rx_info  #(
    .BOARD_MAC_ADDR (BOARD_MAC_ADDR),
    .BOARD_IP_ADDR  (BOARD_IP_ADDR)
) udp_eth_rx_info (
    .rst       (rst       ),
    .clk       (clk       ),
    .gmii_rx_dv(gmii_rx_dv),
    .gmii_rd   (gmii_rd   ),
    .crc_err   (crc_err   ),
    .crc_last  (crc_last  ),
    .buf_busy  (buf_busy  ),
    .status    (status    ),
    .rd_ack    (rd_ack    ),
    .rd_req    (rd_req    )
);
udp_eth_rx_data udp_eth_rx_data(
    .clk         (clk         ),
    .rst         (rst         ),
    .gmii_rx_dv  (gmii_rx_dv  ),
    .gmii_rd     (gmii_rd     ),
    .rd_req      (rd_req      ),
    .rd_ack      (rd_ack      ),
    .status      (status      ),
    .buf_busy    (buf_busy    ),
    .fil_data    (fil_data    ),
    .fil_data_vld(fil_data_vld)
);

wire [15:0] pkg_len = status[127:112];
wire [15:0] udp_len = status[95:80];
wire [47:0] rx_pc_mac = status[79:32];
wire [31:0] rx_pc_ip  = status[31:0];
wire        rx_err    = status[128];

reg [15:0] fil_cnt;
reg [47:0] pc_mac_r;
reg [31:0] pc_ip_r;
wire [15:0] pld_bytes = (udp_len > 16'd8) ? (udp_len - 16'd8) : 16'd0;
wire        pld_byte_en = fil_data_vld && !rx_err &&
                          (fil_cnt >= 16'd50) &&
                          (fil_cnt < (16'd50 + pld_bytes));

always @(posedge clk or posedge rst) begin
    if (rst)
        fil_cnt <= 16'd0;
    else if (!fil_data_vld)
        fil_cnt <= 16'd0;
    else
        fil_cnt <= fil_cnt + 16'd1;
end

always @(posedge clk or posedge rst) begin
    if (rst) begin
        pc_mac_r <= 48'd0;
        pc_ip_r  <= 32'd0;
    end else if (rd_ack && !rx_err) begin
        pc_mac_r <= rx_pc_mac;
        pc_ip_r  <= rx_pc_ip;
    end
end

assign pc_mac = pc_mac_r;
assign pc_ip  = pc_ip_r;

FWFT_Async_fifo #(
    .WRITE_DATA_WIDTH (8),
    .FIFO_WRITE_DEPTH (4096),
    .READ_DATA_WIDTH  (128),
    .PROG_FULL_THRESH (4000)
) inst_pld_fifo (
    // XPM reset must be synchronous to its write clock (RX), not system clk.
    // The board top provides async assertion / RX-synchronous release of rst.
    .rst        (rst),
    .wr_clk     (clk),
    .rd_clk     (I_sys_clk),
    .din        (fil_data),
    .wr_en      (pld_byte_en),
    .rd_en      (pld_tready),
    .dout       (pld_tdata),
    .full       (),
    .empty      (),
    .data_valid (pld_tvalid),
    .prog_full  ()
);

endmodule
