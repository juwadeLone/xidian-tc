`timescale 1ns/1ps

// ---------------------------------------------------------------------------
// Generic SECDED (Hamming single-error-correct / double-error-detect).
// Layout identical to secded_encode70/secded_decode70: codeword positions
// 1..CODE_W-1 (bits 0..CODE_W-2) hold parity at powers of two and data
// elsewhere; bit CODE_W-1 is the overall parity.
module secded_encode_w #(
    parameter integer DATA_W = 44,
    parameter integer PARITY = 6,                 // 2^PARITY >= DATA_W+PARITY+1
    parameter integer CODE_W = DATA_W + PARITY + 1
)(
    input  wire [DATA_W-1:0] data,
    output reg  [CODE_W-1:0] codeword
);
integer position, payload_index;
reg parity, overall;
always @* begin
    codeword = {CODE_W{1'b0}};
    payload_index = 0;
    for (position = 1; position <= CODE_W-1; position = position + 1) begin
        if ((position & (position - 1)) != 0) begin
            codeword[position-1] = data[payload_index];
            payload_index = payload_index + 1;
        end
    end
    for (position = 0; position < PARITY; position = position + 1) begin : par
        integer pp, pos;
        pp = 1 << position;
        parity = 1'b0;
        for (pos = 1; pos <= CODE_W-1; pos = pos + 1)
            if ((pos & pp) != 0) parity = parity ^ codeword[pos-1];
        codeword[pp-1] = parity;
    end
    overall = 1'b0;
    for (position = 0; position < CODE_W-1; position = position + 1)
        overall = overall ^ codeword[position];
    codeword[CODE_W-1] = overall;
end
endmodule


module secded_decode_w #(
    parameter integer DATA_W = 44,
    parameter integer PARITY = 6,
    parameter integer CODE_W = DATA_W + PARITY + 1
)(
    input  wire [CODE_W-1:0] codeword,
    output reg  [DATA_W-1:0] data,
    output reg               detected,
    output reg               corrected
);
integer position, payload_index;
reg [PARITY-1:0] syndrome;
reg overall, parity;
reg [CODE_W-1:0] fixed;
always @* begin
    syndrome = {PARITY{1'b0}};
    for (position = 0; position < PARITY; position = position + 1) begin : syn
        integer pp, pos;
        pp = 1 << position;
        parity = 1'b0;
        for (pos = 1; pos <= CODE_W-1; pos = pos + 1)
            if ((pos & pp) != 0) parity = parity ^ codeword[pos-1];
        if (parity) syndrome = syndrome | pp[PARITY-1:0];
    end
    overall = 1'b0;
    for (position = 0; position < CODE_W; position = position + 1)
        overall = overall ^ codeword[position];
    fixed = codeword;
    detected = 1'b0;
    corrected = 1'b0;
    if ((syndrome != 0) && overall) begin
        fixed[syndrome-1] = ~fixed[syndrome-1];
        detected = 1'b1;
        corrected = 1'b1;
    end else if ((syndrome == 0) && overall) begin
        fixed[CODE_W-1] = ~fixed[CODE_W-1];
        detected = 1'b1;
        corrected = 1'b1;
    end else if ((syndrome != 0) && !overall) begin
        detected = 1'b1;
    end
    data = {DATA_W{1'b0}};
    payload_index = 0;
    for (position = 1; position <= CODE_W-1; position = position + 1)
        if ((position & (position - 1)) != 0) begin
            data[payload_index] = fixed[position-1];
            payload_index = payload_index + 1;
        end
end
endmodule
