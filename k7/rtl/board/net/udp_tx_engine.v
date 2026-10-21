// Ported from the A7 project (modules/udp_tx_engine/udp_tx_engine.v).
// Verbatim apart from the include path and, where marked, ADDR_W 29->31
// for the K7's 2 GB address space (contracts/k7_ddr_axi_decision.md).
`timescale 1ns / 1ps

// Gather UDP payload and emit a gap-free fil_data stream plus status
// for the existing udp_eth_tx MAC/IP/UDP header rewriter.
module udp_tx_engine #(
    parameter integer MAX_PLD = 1024,
    // udp_eth_tx buffers a complete frame to calculate checksums. Its status
    // remains live while that buffer drains, so hold it beyond the last input
    // byte. Eight clocks are insufficient when the next packet is shorter.
    parameter integer DRAIN_CYCLES = MAX_PLD + 128
) (
    input              clk,
    input              rst,
    input              start,
    input      [31:0]  length,
    input      [47:0]  pc_mac,
    input      [31:0]  pc_ip,
    output             busy,
    output reg         done,

    input      [7:0]   pld_tdata,
    input              pld_tvalid,
    output             pld_tready,

    output     [128:0] status,
    output             fil_data_vld,
    output     [7:0]   fil_data
);

localparam S_IDLE = 2'd0;
localparam S_FILL = 2'd1;
localparam S_SEND = 2'd2;
localparam S_GAP  = 2'd3;

reg [1:0]  state;
reg [31:0] bytes_left;
reg [15:0] fill_cnt;
reg [15:0] send_cnt;
reg [15:0] pld_n;
reg [47:0] pc_mac_q;
reg [31:0] pc_ip_q;
localparam integer GAP_W = $clog2(DRAIN_CYCLES + 1);
reg [GAP_W-1:0] gap_cnt;
reg [7:0]  buf_mem [0:MAX_PLD-1];

wire [15:0] pkg_len = 16'd54 + pld_n;
wire [15:0] ip_len  = 16'd28 + pld_n;
wire [15:0] udp_len = 16'd8  + pld_n;
wire [15:0] send_max = 16'd50 + pld_n;

assign status       = {1'b0, pkg_len, ip_len, udp_len, pc_mac_q, pc_ip_q};
assign busy         = (state != S_IDLE);
assign pld_tready   = (state == S_FILL);
assign fil_data_vld = (state == S_SEND);
assign fil_data     = (send_cnt >= 16'd50) ? buf_mem[send_cnt - 16'd50] : 8'h00;

// The payload buffer is written from its own block, with no reset.
//
// It used to be written inside the FSM's always @(posedge clk or posedge rst),
// and an asynchronous reset makes RAM inference impossible -- neither block RAM
// nor distributed RAM has one.  Vivado said so and gave up:
//   [Synth 8-4767] Trying to implement RAM 'buf_mem_reg' in registers
//   RAM "buf_mem_reg" dissolved into registers
// which turned 1024x8 into 8192 flops behind a 1024-to-1 read mux and cost
// 167 failing endpoints at 125 MHz (WNS -1.563 ns), all of them routing.
//
// The array was never assigned in the reset branch, so nothing changes
// behaviourally: same write condition, same asynchronous read, same cycle.
// Keeping the read asynchronous is deliberate -- fil_data is consumed
// combinationally off send_cnt, so this must stay distributed RAM rather than
// become a block RAM with a registered output.
always @(posedge clk)
    if (state == S_FILL && pld_tvalid && pld_tready)
        buf_mem[fill_cnt] <= pld_tdata;

always @(posedge clk or posedge rst) begin
    if (rst) begin
        state      <= S_IDLE;
        bytes_left <= 32'd0;
        fill_cnt   <= 16'd0;
        send_cnt   <= 16'd0;
        pld_n      <= 16'd0;
        pc_mac_q   <= 48'd0;
        pc_ip_q    <= 32'd0;
        gap_cnt    <= 4'd0;
        done       <= 1'b0;
    end else begin
        done <= 1'b0;
        case (state)
            S_IDLE: begin
                if (start) begin
                    bytes_left <= length;
                    pc_mac_q   <= pc_mac;
                    pc_ip_q    <= pc_ip;
                    fill_cnt   <= 16'd0;
                    if (length == 32'd0) begin
                        done <= 1'b1;
                    end else begin
                        pld_n <= (length > MAX_PLD) ? MAX_PLD[15:0] : length[15:0];
                        state <= S_FILL;
                    end
                end
            end
            S_FILL: begin
                if (pld_tvalid && pld_tready) begin
                    // buf_mem[fill_cnt] is written by the unreset block above.
                    if (fill_cnt == pld_n - 16'd1) begin
                        fill_cnt <= 16'd0;
                        send_cnt <= 16'd0;
                        state    <= S_SEND;
                    end else begin
                        fill_cnt <= fill_cnt + 16'd1;
                    end
                end
            end
            S_SEND: begin
                if (send_cnt == send_max - 16'd1) begin
                    bytes_left <= bytes_left - {16'd0, pld_n};
                    gap_cnt    <= DRAIN_CYCLES;
                    state      <= S_GAP;
                end else begin
                    send_cnt <= send_cnt + 16'd1;
                end
            end
            S_GAP: begin
                if (gap_cnt != 4'd0)
                    gap_cnt <= gap_cnt - 4'd1;
                else if (bytes_left == 32'd0) begin
                    done  <= 1'b1;
                    state <= S_IDLE;
                end else begin
                    fill_cnt <= 16'd0;
                    pld_n    <= (bytes_left > MAX_PLD) ? MAX_PLD[15:0] : bytes_left[15:0];
                    state    <= S_FILL;
                end
            end
            default: state <= S_IDLE;
        endcase
    end
end

endmodule
