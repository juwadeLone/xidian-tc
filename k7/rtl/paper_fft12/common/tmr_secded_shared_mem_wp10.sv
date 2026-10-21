`timescale 1ns/1ps

// WP10 hybrid-TMR shared delay memory (task 2.7, revised per task 8.1).
// The three replicas of a stage each drive the same six storage-facing
// signals a lane memory would take: read enable, read address, pending
// write enable, pending write address, forward active and write data.
// Read enable/address, pending write enable/address and forward active are
// voted 2-of-3 bitwise on entry.  The write data of each replica goes into
// ITS OWN late-write register (three registers, the same write-one-beat-
// late + forward contract as s0_secded_delay_wg / ecc643_secded_delay6_wg);
// the three registered values are then voted, SECDED encoded and stored in
// ONE shared memory, and the voted registered value is what forwarding
// returns.  The decoded word fans back out to all three replicas.  The six
// vote mismatches are OR-ed, registered one beat and merged into the
// stage's TMR mismatch.
//
// WORDS = 1: SubFFT per-lane shared memory (four instances per stage).
// WORDS = 4: PFFT per-stage shared memory (one address holds all four lane
//            words, same packing as pfft_sdf4_wp4r's 4*DW memory word).
// SECDED: one codeword per word, DF -> CF = DF + 7 with PARITY(6), the same
// code the S0/P0 functional lanes use.  Only wp4r_hier_* shells are
// instantiated so the functional breakdown keeps its rows.
(* keep_hierarchy = "yes" *)
module tmr_secded_shared_mem_wp10 #(
    parameter integer WORDS = 1,
    parameter integer DATA_W = 34,
    parameter integer DEPTH = 128,
    parameter integer AW = (DEPTH <= 1) ? 1 : $clog2(DEPTH),
    parameter integer CODE_W = DATA_W + 7,
    parameter integer BUS_W = WORDS * DATA_W,
    parameter integer MEM_W = WORDS * CODE_W
)(
    input  wire              clk,
    input  wire              rst,
    input  wire              a_rd_en,
    input  wire [AW-1:0]     a_rd_addr,
    input  wire              a_pending_wr_en,
    input  wire [AW-1:0]     a_pending_wr_addr,
    input  wire              a_forward_active,
    input  wire [BUS_W-1:0]  a_wr_data,
    input  wire              b_rd_en,
    input  wire [AW-1:0]     b_rd_addr,
    input  wire              b_pending_wr_en,
    input  wire [AW-1:0]     b_pending_wr_addr,
    input  wire              b_forward_active,
    input  wire [BUS_W-1:0]  b_wr_data,
    input  wire              c_rd_en,
    input  wire [AW-1:0]     c_rd_addr,
    input  wire              c_pending_wr_en,
    input  wire [AW-1:0]     c_pending_wr_addr,
    input  wire              c_forward_active,
    input  wire [BUS_W-1:0]  c_wr_data,
    output wire [BUS_W-1:0]  rd_data,
    output wire              vote_mismatch
);

// Five storage-facing control signals, each voted 2-of-3 bitwise.
wire v_rd_en, v_pending_wr_en, v_forward_active;
wire [AW-1:0] v_rd_addr, v_pending_wr_addr;
wire [5:0] mm;
wp4r_hier_tmr_vote_w #(.W(1)) u_rd_en_vote (
    .a(a_rd_en), .b(b_rd_en), .c(c_rd_en), .y(v_rd_en), .mismatch(mm[0]));
wp4r_hier_tmr_vote_w #(.W(AW)) u_rd_addr_vote (
    .a(a_rd_addr), .b(b_rd_addr), .c(c_rd_addr),
    .y(v_rd_addr), .mismatch(mm[1]));
wp4r_hier_tmr_vote_w #(.W(1)) u_wr_en_vote (
    .a(a_pending_wr_en), .b(b_pending_wr_en), .c(c_pending_wr_en),
    .y(v_pending_wr_en), .mismatch(mm[2]));
wp4r_hier_tmr_vote_w #(.W(AW)) u_wr_addr_vote (
    .a(a_pending_wr_addr), .b(b_pending_wr_addr), .c(c_pending_wr_addr),
    .y(v_pending_wr_addr), .mismatch(mm[3]));
wp4r_hier_tmr_vote_w #(.W(1)) u_fwd_vote (
    .a(a_forward_active), .b(b_forward_active), .c(c_forward_active),
    .y(v_forward_active), .mismatch(mm[4]));
// Late write: each replica's write data is registered one beat (three
// registers, task 8.1.1); the three registered values are voted bitwise
// and the voted word is encoded and stored.  Forwarding returns the same
// voted registered value.
reg [BUS_W-1:0] pending_a, pending_b, pending_c;
always @(posedge clk) begin
    pending_a <= a_wr_data;
    pending_b <= b_wr_data;
    pending_c <= c_wr_data;
end
wire [BUS_W-1:0] v_wr_data;
wp4r_hier_tmr_vote_w #(.W(BUS_W)) u_wr_data_vote (
    .a(pending_a), .b(pending_b), .c(pending_c),
    .y(v_wr_data), .mismatch(mm[5]));

// Mismatches merge registered (task 8.1.2): the stage flag sees them one
// beat late, which the clean-window monitor tolerates.
reg mm_q;
always @(posedge clk)
    if (rst)
        mm_q <= 1'b0;
    else
        mm_q <= |mm;
assign vote_mismatch = mm_q;

wire [MEM_W-1:0] write_code;
wire [MEM_W-1:0] read_code;
generate
for (genvar w = 0; w < WORDS; w = w + 1) begin : g_word
    wire [DATA_W-1:0] decoded;
    wp4r_hier_secded_encode_w #(.DATA_W(DATA_W), .PARITY(6),
                                .CODE_W(CODE_W)) u_encode (
        .data(v_wr_data[(w+1)*DATA_W-1 -: DATA_W]),
        .codeword(write_code[(w+1)*CODE_W-1 -: CODE_W]));
    wp4r_hier_secded_decode_w #(.DATA_W(DATA_W), .PARITY(6),
                                .CODE_W(CODE_W)) u_decode (
        .codeword(read_code[(w+1)*CODE_W-1 -: CODE_W]), .data(decoded),
        .detected(), .corrected());
    assign rd_data[(w+1)*DATA_W-1 -: DATA_W] =
        v_forward_active ? v_wr_data[(w+1)*DATA_W-1 -: DATA_W] : decoded;
end
endgenerate

wp4r_hier_fft_memory_v5 #(.WIDTH(MEM_W), .DEPTH(DEPTH), .ADDR_W(AW)) u_mem (
    .clk(clk), .rd_en(v_rd_en), .rd_addr(v_rd_addr), .rd_data(read_code),
    .wr_en(v_pending_wr_en), .wr_addr(v_pending_wr_addr),
    .wr_data(write_code));
endmodule
