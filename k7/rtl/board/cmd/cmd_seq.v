`timescale 1ns / 1ps
`include "udp_cmd_hdr.vh"

// UART command dispatcher: ARM_WR / TRANSPOSE / RD_BACK, exclusive mux select.
module cmd_seq #(
    parameter integer ADDR_W   = 31,
    parameter integer SIDE_LOG2 = 11
) (
    input                      clk,
    input                      rst_n,
    input                      calib_done,

    input                      cmd_valid,
    output                     cmd_ready,
    input      [7:0]           cmd,
    input      [7:0]           flags,
    input      [15:0]          seq,
    input      [31:0]          addr,
    input      [31:0]          length,
    input                      hdr_ok,
    input                      cmd_is_wr,
    input                      cmd_is_rd,
    input                      cmd_is_xpose,

    output reg                 ack_start,
    output reg [7:0]           ack_cmd,
    output     [7:0]           ack_flags,
    output     [15:0]          ack_seq,
    output     [31:0]          ack_addr,
    output     [31:0]          ack_length,
    input                      ack_busy,

    output reg [1:0]           mux_sel,
    output     [ADDR_W-1:0]    op_addr,
    output     [31:0]          op_length,
    output reg                 wr_err_sticky,

    output reg                 wr_start,
    input                      wr_busy,
    input                      wr_done,
    input                      wr_error,

    output reg                 swap_start,
    input                      swap_busy,
    input                      swap_done,
    input                      swap_error,

    output reg                 rd_start,
    input                      rd_busy,
    input                      rd_done,
    input                      rd_error,

    output     [1:0]           fft_mode,
    output reg                 fft_start,
    input                      fft_busy,
    input                      fft_done,
    input                      fft_error,
    output     [2:0]           debug_state,

    output reg signed [15:0]   cs_a_hcs,
    output reg signed [15:0]   cs_b_hcs,
    output reg signed [15:0]   cs_c_hcs,
    output reg signed [15:0]   cs_a_hr,
    output reg signed [15:0]   cs_b_hr,
    output reg signed [15:0]   cs_c_hr,
    output reg signed [15:0]   cs_a_ha,
    output reg signed [15:0]   cs_b_ha,
    output reg signed [15:0]   cs_c_ha,
    output reg                 cs_tab_we,
    output reg [1:0]           cs_tab_sel,
    output reg [7:0]           cs_tab_fa,
    output reg signed [15:0]   cs_tab_a,
    output reg signed [15:0]   cs_tab_b,
    output reg signed [15:0]   cs_tab_c,
    output reg                 cs_param_we,
    output     [3:0]           cs_param_idx,
    output     [31:0]          cs_param_w0,
    output     [31:0]          cs_param_w1,
    output reg                 cs_build_start,
    input                      cs_build_busy,
    input                      cs_build_done,
    // Every other wait state has one; this one did not, so a build that failed
    // left the sequencer in S_WAIT_B forever.  csa_cs_app's ST_ERROR drives
    // both busy and done low, so there was nothing else to notice it by.
    input                      cs_build_error,

    output                     sem_busy,
    output reg                 sem_start,
    input                      sem_done,
    input                      sem_err,
    input      [7:0]           sem_resp_len,

    // ---- seed-table row write / read-back (host-built tables) ----
    input      [127:0]         rx_payload,
    output reg                 cs_row_we,
    output reg [1:0]           cs_row_sel,
    output reg [SIDE_LOG2-1:0] cs_row_idx,
    output reg [95:0]          cs_row_data,
    output reg [1:0]           cs_rb_sel,
    output reg [SIDE_LOG2-1:0] cs_rb_idx,
    output reg                 cs_rb_dbg,

    // ---- seed-table DDR dump (CS_DUMP streams the 4 tables to BASE_OUT) ----
    output reg                 cs_dump_start,
    input                      cs_dump_ready,
    input                      cs_dump_done
);

localparam S_IDLE     = 4'd0;
localparam S_ACK      = 4'd1;
localparam S_WAIT_WR  = 4'd2;
localparam S_WAIT_X   = 4'd3;
localparam S_WAIT_RD  = 4'd4;
localparam S_WAIT_F   = 4'd5;
localparam S_WAIT_B   = 4'd6;
localparam S_WAIT_SEM = 4'd7;
localparam S_WAIT_D   = 4'd8;

reg [3:0]  state;
reg [7:0]  cmd_q;
reg [7:0]  flags_q;
reg [15:0] seq_q;
reg [31:0] addr_q;
reg [31:0] len_q;
reg        pending_ack;
reg        wr_done_seen;

assign cmd_ready   = (state == S_IDLE) && calib_done;
assign debug_state = state[2:0];
assign ack_flags   = 8'd0;
assign ack_seq     = seq_q;
assign ack_addr    = addr_q;
assign ack_length  = len_q;
assign op_addr     = addr_q[ADDR_W-1:0];
assign op_length   = len_q;
assign fft_mode     = flags_q[1:0];
assign cs_param_idx = flags[3:0];
assign cs_param_w0  = addr;
assign cs_param_w1  = length;
assign sem_busy     = (state == S_WAIT_SEM);

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        state       <= S_IDLE;
        cmd_q       <= 8'd0;
        flags_q     <= 8'd0;
        seq_q       <= 16'd0;
        addr_q      <= 32'd0;
        len_q       <= 32'd0;
        mux_sel     <= 2'd0;
        wr_start    <= 1'b0;
        swap_start  <= 1'b0;
        rd_start    <= 1'b0;
        fft_start   <= 1'b0;
        ack_start   <= 1'b0;
        ack_cmd     <= 8'd0;
        pending_ack <= 1'b0;
        wr_err_sticky <= 1'b0;
        wr_done_seen  <= 1'b0;
        cs_a_hcs      <= 16'sd0;
        cs_b_hcs      <= 16'sd0;
        cs_c_hcs      <= 16'sd0;
        cs_a_hr       <= 16'sd0;
        cs_b_hr       <= 16'sd0;
        cs_c_hr       <= 16'sd0;
        cs_a_ha       <= 16'sd0;
        cs_b_ha       <= 16'sd0;
        cs_c_ha       <= 16'sd0;
        cs_tab_we     <= 1'b0;
        cs_tab_sel    <= 2'd0;
        cs_tab_fa     <= 8'd0;
        cs_tab_a      <= 16'sd0;
        cs_tab_b      <= 16'sd0;
        cs_tab_c      <= 16'sd0;
        cs_param_we   <= 1'b0;
        cs_build_start<= 1'b0;
        sem_start     <= 1'b0;
        cs_row_we     <= 1'b0;
        cs_row_sel    <= 2'd0;
        cs_row_idx    <= {SIDE_LOG2{1'b0}};
        cs_row_data   <= 96'd0;
        cs_rb_sel     <= 2'd0;
        cs_rb_idx     <= {SIDE_LOG2{1'b0}};
        cs_rb_dbg     <= 1'b0;
        cs_dump_start <= 1'b0;
    end else begin
        wr_start   <= 1'b0;
        swap_start <= 1'b0;
        rd_start   <= 1'b0;
        fft_start  <= 1'b0;
        ack_start  <= 1'b0;
        wr_done_seen <= 1'b0;
        cs_tab_we  <= 1'b0;
        cs_param_we <= 1'b0;
        cs_build_start <= 1'b0;
        sem_start      <= 1'b0;
        cs_row_we      <= 1'b0;
        cs_dump_start  <= 1'b0;

        case (state)
            S_IDLE: begin
                if (cmd_valid && cmd_ready) begin
                    cmd_q   <= cmd;
                    flags_q <= flags;
                    seq_q   <= seq;
                    addr_q <= addr;
                    len_q  <= length;
                    if (!hdr_ok) begin
                        ack_cmd     <= `UDP_CMD_NAK;
                        pending_ack <= 1'b1;
                        state       <= S_ACK;
                    end else if (cmd_is_wr) begin
                        mux_sel       <= 2'd0;
                        wr_start      <= 1'b1;
                        wr_err_sticky <= 1'b0;
                        ack_cmd       <= `UDP_CMD_WR_ACK;
                        pending_ack   <= 1'b1;
                        state         <= S_ACK;
                    end else if (cmd_is_xpose) begin
                        mux_sel    <= 2'd1;
                        swap_start <= 1'b1;
                        state      <= S_WAIT_X;
                    end else if (cmd_is_rd) begin
                        mux_sel  <= 2'd2;
                        rd_start <= 1'b1;
                        state    <= S_WAIT_RD;
                    end else if (cmd == `UDP_CMD_FFT2D) begin
                        if (cs_build_busy) begin
                            ack_cmd     <= `UDP_CMD_NAK;
                            pending_ack <= 1'b1;
                            state       <= S_ACK;
                        end else begin
                            mux_sel   <= 2'd1;
                            fft_start <= 1'b1;
                            state     <= S_WAIT_F;
                        end
                    end else if (cmd == `UDP_CMD_CS_ROW) begin
                        // One synchronous cycle writes one 96-bit seed row
                        // into the table RAMs: sel = flags[1:0], row =
                        // addr[10:0], the 12 payload bytes carry the row.
                        cs_row_we   <= 1'b1;
                        cs_row_sel  <= flags[1:0];
                        cs_row_idx  <= addr[SIDE_LOG2-1:0];
                        cs_row_data <= rx_payload[95:0];
                        ack_cmd     <= `UDP_CMD_CS_ROW_ACK;
                        pending_ack <= 1'b1;
                        state       <= S_ACK;
                    end else if (cmd == `UDP_CMD_CS_RB) begin
                        // Read-back.  The address is registered and stays put
                        // until the next command, and the top drives
                        // tx_payload straight from the table RAM output, so
                        // the ACK carries the live row.
                        cs_rb_sel   <= flags[1:0];
                        cs_rb_idx   <= addr[SIDE_LOG2-1:0];
                        cs_rb_dbg   <= flags[3];
                        ack_cmd     <= `UDP_CMD_CS_RB_ACK;
                        pending_ack <= 1'b1;
                        state       <= S_ACK;
                    end else if (cmd == `UDP_CMD_CS_DUMP) begin
                        // Synchronous dump: ACK only after the app FSM has
                        // streamed all four tables into DDR, so the host can
                        // issue RD immediately after the ACK arrives.
                        if (cs_dump_ready) begin
                            cs_dump_start <= 1'b1;
                            state         <= S_WAIT_D;
                        end else begin
                            ack_cmd     <= `UDP_CMD_NAK;
                            pending_ack <= 1'b1;
                            state       <= S_ACK;
                        end
                    end else if (cmd == `UDP_CMD_CS_PARAM) begin
                        if (flags[7]) begin
                            // The CS engine sits on mux slot 1, the same as
                            // TRANSPOSE and FFT2D.  This branch never set it,
                            // so the build ran with the mux still pointing at
                            // the UDP write master and its AXI traffic never
                            // reached memory: the board sat in S_WAIT_B with
                            // mux=WR until the command timed out.  The path had
                            // never been exercised -- every board top before
                            // this one left cs_param_we unconnected.
                            mux_sel        <= 2'd1;
                            cs_build_start <= 1'b1;
                            state          <= S_WAIT_B;
                        end else begin
                            cs_param_we <= 1'b1;
                            ack_cmd     <= `UDP_CMD_CS_PARAM_ACK;
                            pending_ack <= 1'b1;
                            state       <= S_ACK;
                        end
                    end else if (cmd == `UDP_CMD_CS_COEFF) begin
                        if (flags[2]) begin
                            cs_tab_we  <= 1'b1;
                            cs_tab_sel <= flags[1:0];
                            cs_tab_fa  <= addr[7:0];
                            cs_tab_a   <= addr[31:16];
                            cs_tab_b   <= length[15:0];
                            cs_tab_c   <= length[31:16];
                        end else if (flags[1:0] == 2'd3) begin
                            cs_a_hcs <= 16'sd0; cs_b_hcs <= 16'sd0; cs_c_hcs <= 16'sd0;
                            cs_a_hr  <= 16'sd0; cs_b_hr  <= 16'sd0; cs_c_hr  <= 16'sd0;
                            cs_a_ha  <= 16'sd0; cs_b_ha  <= 16'sd0; cs_c_ha  <= 16'sd0;
                        end else if (flags[1:0] == 2'd0) begin
                            cs_a_hcs <= addr[15:0];
                            cs_b_hcs <= addr[31:16];
                            cs_c_hcs <= length[15:0];
                        end else if (flags[1:0] == 2'd1) begin
                            cs_a_hr <= addr[15:0];
                            cs_b_hr <= addr[31:16];
                            cs_c_hr <= length[15:0];
                        end else begin
                            cs_a_ha <= addr[15:0];
                            cs_b_ha <= addr[31:16];
                            cs_c_ha <= length[15:0];
                        end
                        ack_cmd     <= `UDP_CMD_CS_COEFF_ACK;
                        pending_ack <= 1'b1;
                        state       <= S_ACK;
                    end else if (cmd == `UDP_CMD_SEM) begin
                        sem_start <= 1'b1;
                        state     <= S_WAIT_SEM;
                    end else begin
                        ack_cmd     <= `UDP_CMD_NAK;
                        pending_ack <= 1'b1;
                        state       <= S_ACK;
                    end
                end
            end
            S_ACK: begin
                if (pending_ack && !ack_busy) begin
                    ack_start   <= 1'b1;
                    pending_ack <= 1'b0;
                end else if (!pending_ack && !ack_busy) begin
                    if (ack_cmd == `UDP_CMD_WR_ACK)
                        state <= S_WAIT_WR;
                    else
                        state <= S_IDLE;
                end
            end
            S_WAIT_WR: begin
                // WR_ACK went out at arm time; the write itself is verified
                // here: completion with wr_error produces an unsolicited NAK
                // so the host learns the write failed (sticky flag feeds
                // telemetry until the next ARM_WR).
                if (wr_done)
                    wr_done_seen <= 1'b1;
                if (wr_done_seen) begin
                    if (wr_error) begin
                        ack_cmd       <= `UDP_CMD_NAK;
                        pending_ack   <= 1'b1;
                        wr_err_sticky <= 1'b1;
                        state         <= S_ACK;
                    end else begin
                        state <= S_IDLE;
                    end
                end
            end
            S_WAIT_X: begin
                if (swap_done && !swap_busy) begin
                    ack_cmd     <= swap_error ? `UDP_CMD_NAK : `UDP_CMD_XPOSE_ACK;
                    pending_ack <= 1'b1;
                    state       <= S_ACK;
                end
            end
            S_WAIT_RD: begin
                if (rd_done) begin
                    ack_cmd     <= rd_error ? `UDP_CMD_NAK : `UDP_CMD_RD_ACK;
                    pending_ack <= 1'b1;
                    state       <= S_ACK;
                end
            end
            S_WAIT_F: begin
                if (fft_done && !fft_busy) begin
                    ack_cmd     <= fft_error ? `UDP_CMD_NAK : `UDP_CMD_FFT2D_ACK;
                    pending_ack <= 1'b1;
                    state       <= S_ACK;
                end
            end
            S_WAIT_B: begin
                if ((cs_build_done || cs_build_error) && !cs_build_busy) begin
                    ack_cmd     <= cs_build_error ? `UDP_CMD_NAK
                                                  : `UDP_CMD_CS_PARAM_ACK;
                    pending_ack <= 1'b1;
                    state       <= S_ACK;
                end
            end
            S_WAIT_SEM: begin
                if (sem_done) begin
                    len_q       <= {24'd0, sem_resp_len};
                    ack_cmd     <= `UDP_CMD_SEM_ACK;
                    pending_ack <= 1'b1;
                    state       <= S_ACK;
                end
            end
            S_WAIT_D: begin
                if (cs_dump_done) begin
                    ack_cmd     <= `UDP_CMD_CS_DUMP_ACK;
                    pending_ack <= 1'b1;
                    state       <= S_ACK;
                end
            end
            default: state <= S_IDLE;
        endcase
    end
end

endmodule
