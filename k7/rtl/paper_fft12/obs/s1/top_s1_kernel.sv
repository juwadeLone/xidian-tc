// S1 obs top (pg): Gao (7,4) path-ECC boundary decoder + TMR 9/10 wiring.
// Module name matches the engine kernel slot; file lives under obs/ per
// create_k7_paper.tcl convention.
`timescale 1ns/1ps

// obs top: telemetry mirrors the boundary decoder (path ECC decodes once per
// output beat, so all 8 lanes report the same event).
module top_s1_gao_subfft_ecc #(
    parameter integer ARITH_THRESHOLD = 16
)(
    input  wire clk,
    input  wire rst,
    input  wire in_valid,
    input  wire in_last,
    input  wire signed [15:0] in0_re, in0_im, in1_re, in1_im,
    input  wire signed [15:0] in2_re, in2_im, in3_re, in3_im,
    output wire out_valid,
    output wire out_last,
    output wire signed [21:0] out0_re, out0_im, out1_re, out1_im,
    output wire signed [21:0] out2_re, out2_im, out3_re, out3_im,
    output wire [7:0]  win_second,
    output wire [7:0]  up_detected,
    output wire [7:0]  up_corrected,
    output wire [7:0]  up_uncorrectable,
    output wire [23:0] up_error_location
);
    // Parity inputs: 3-term sums of 16-bit data -> 18-bit paths.
    wire signed [17:0] a01r = $signed(in0_re) + $signed(in1_re);
    wire signed [17:0] a01i = $signed(in0_im) + $signed(in1_im);
    wire signed [17:0] a02r = $signed(in0_re) + $signed(in2_re);
    wire signed [17:0] a02i = $signed(in0_im) + $signed(in2_im);
    wire signed [17:0] a12r = $signed(in1_re) + $signed(in2_re);
    wire signed [17:0] a12i = $signed(in1_im) + $signed(in2_im);
    wire signed [17:0] p0r = -($signed(a01r) + $signed(in3_re));
    wire signed [17:0] p0i = -($signed(a01i) + $signed(in3_im));
    wire signed [17:0] p1r = -($signed(a02r) + $signed(in3_re));
    wire signed [17:0] p1i = -($signed(a02i) + $signed(in3_im));
    wire signed [17:0] p3r = -($signed(a12r) + $signed(in3_re));
    wire signed [17:0] p3i = -($signed(a12i) + $signed(in3_im));

    // Path outputs unified at 24 bits (parity width); data paths must be
    // sign-extended explicitly: connecting a 22-bit output directly to a
    // 24-bit net leaves the top 2 bits undriven (z in sim -> x masks the
    // syndrome logic; tied to 0 in silicon -> false detects on negative
    // path values).
    wire signed [23:0] pathr [0:6], pathi [0:6];
    wire signed [21:0] d0r_w, d0i_w, d1r_w, d1i_w, d2r_w, d2i_w, d3r_w, d3i_w;
    wire [6:0] pv, pl;

    wire [6:0] povf;
    s1_gao_path_wg #(.WIN(18), .WMAX(24)) u_p0 (clk, rst, in_valid, p0r, p0i, pv[0], pl[0], pathr[0], pathi[0], povf[0]);
    s1_gao_path_wg #(.WIN(18), .WMAX(24)) u_p1 (clk, rst, in_valid, p1r, p1i, pv[1], pl[1], pathr[1], pathi[1], povf[1]);
    s1_gao_path_wg #(.WIN(16), .WMAX(22)) u_d0 (clk, rst, in_valid, in0_re, in0_im, pv[2], pl[2], d0r_w, d0i_w, povf[2]);
    s1_gao_path_wg #(.WIN(18), .WMAX(24)) u_p3 (clk, rst, in_valid, p3r, p3i, pv[3], pl[3], pathr[3], pathi[3], povf[3]);
    s1_gao_path_wg #(.WIN(16), .WMAX(22)) u_d1 (clk, rst, in_valid, in1_re, in1_im, pv[4], pl[4], d1r_w, d1i_w, povf[4]);
    s1_gao_path_wg #(.WIN(16), .WMAX(22)) u_d2 (clk, rst, in_valid, in2_re, in2_im, pv[5], pl[5], d2r_w, d2i_w, povf[5]);
    s1_gao_path_wg #(.WIN(16), .WMAX(22)) u_d3 (clk, rst, in_valid, in3_re, in3_im, pv[6], pl[6], d3r_w, d3i_w, povf[6]);

    // Legitimate-overflow mask: when a functional lane wraps at a stage
    // boundary (contract behaviour under the growing-width profile), the
    // path-ECC identity cannot hold for the beats derived from that sample.
    // Suppress detect/correct on the contaminated window instead of letting
    // the decoder mis-correct good data. A wrap in any path masks ~one frame
    // of downstream output.
    localparam integer MASK_LEN = 512;
    reg [MASK_LEN-1:0] ovf_sr;
    always @(posedge clk) begin
        if (rst) ovf_sr <= {MASK_LEN{1'b0}};
        else     ovf_sr <= {ovf_sr[MASK_LEN-2:0], |povf};
    end
    wire mask_on = |ovf_sr;
    assign pathr[2] = {{2{d0r_w[21]}}, d0r_w}; assign pathi[2] = {{2{d0i_w[21]}}, d0i_w};
    assign pathr[4] = {{2{d1r_w[21]}}, d1r_w}; assign pathi[4] = {{2{d1i_w[21]}}, d1i_w};
    assign pathr[5] = {{2{d2r_w[21]}}, d2r_w}; assign pathi[5] = {{2{d2i_w[21]}}, d2i_w};
    assign pathr[6] = {{2{d3r_w[21]}}, d3r_w}; assign pathi[6] = {{2{d3i_w[21]}}, d3i_w};

    // 125 MHz: 4-stage boundary pipeline (capture, copy, syndromes, correct).
    localparam integer PW = 24;
    localparam integer SW = 28;
    localparam signed [SW-1:0] TH  = ARITH_THRESHOLD;
    localparam signed [SW-1:0] TOL = 2 * ARITH_THRESHOLD;

    reg pv_d, pl_d;
    reg signed [PW-1:0] cap_r [0:6], cap_i [0:6];
    integer ci;
    always @(posedge clk) begin
        if (rst) begin
            pv_d <= 1'b0; pl_d <= 1'b0;
            for (ci = 0; ci < 7; ci = ci + 1) begin
                cap_r[ci] <= {PW{1'b0}}; cap_i[ci] <= {PW{1'b0}};
            end
        end else begin
            pv_d <= pv[0];
            pl_d <= pl[0];
            if (pv[0]) begin
                for (ci = 0; ci < 7; ci = ci + 1) begin
                    cap_r[ci] <= pathr[ci];
                    cap_i[ci] <= pathi[ci];
                end
            end
        end
    end

    reg pv_d2, pl_d2;
    reg signed [PW-1:0] cap2_r [0:6], cap2_i [0:6];
    always @(posedge clk) begin
        if (rst) begin
            pv_d2 <= 1'b0; pl_d2 <= 1'b0;
            for (ci = 0; ci < 7; ci = ci + 1) begin
                cap2_r[ci] <= {PW{1'b0}}; cap2_i[ci] <= {PW{1'b0}};
            end
        end else begin
            pv_d2 <= pv_d;
            pl_d2 <= pl_d;
            if (pv_d) begin
                for (ci = 0; ci < 7; ci = ci + 1) begin
                    cap2_r[ci] <= cap_r[ci];
                    cap2_i[ci] <= cap_i[ci];
                end
            end
        end
    end

    wire signed [SW-1:0] s0r_c = $signed(cap2_r[0]) + $signed(cap2_r[2]) + $signed(cap2_r[4]) + $signed(cap2_r[6]);
    wire signed [SW-1:0] s0i_c = $signed(cap2_i[0]) + $signed(cap2_i[2]) + $signed(cap2_i[4]) + $signed(cap2_i[6]);
    wire signed [SW-1:0] s1r_c = $signed(cap2_r[1]) + $signed(cap2_r[2]) + $signed(cap2_r[5]) + $signed(cap2_r[6]);
    wire signed [SW-1:0] s1i_c = $signed(cap2_i[1]) + $signed(cap2_i[2]) + $signed(cap2_i[5]) + $signed(cap2_i[6]);
    wire signed [SW-1:0] s2r_c = $signed(cap2_r[3]) + $signed(cap2_r[4]) + $signed(cap2_r[5]) + $signed(cap2_r[6]);
    wire signed [SW-1:0] s2i_c = $signed(cap2_i[3]) + $signed(cap2_i[4]) + $signed(cap2_i[5]) + $signed(cap2_i[6]);

    reg pv_d3, pl_d3;
    reg signed [PW-1:0] cap3_r [0:6], cap3_i [0:6];
    reg signed [SW-1:0] syn0r, syn0i, syn1r, syn1i, syn2r, syn2i;
    always @(posedge clk) begin
        if (rst) begin
            pv_d3 <= 1'b0; pl_d3 <= 1'b0;
            syn0r <= {SW{1'b0}}; syn0i <= {SW{1'b0}};
            syn1r <= {SW{1'b0}}; syn1i <= {SW{1'b0}};
            syn2r <= {SW{1'b0}}; syn2i <= {SW{1'b0}};
            for (ci = 0; ci < 7; ci = ci + 1) begin
                cap3_r[ci] <= {PW{1'b0}}; cap3_i[ci] <= {PW{1'b0}};
            end
        end else begin
            pv_d3 <= pv_d2;
            pl_d3 <= pl_d2;
            if (pv_d2) begin
                syn0r <= s0r_c; syn0i <= s0i_c;
                syn1r <= s1r_c; syn1i <= s1i_c;
                syn2r <= s2r_c; syn2i <= s2i_c;
                for (ci = 0; ci < 7; ci = ci + 1) begin
                    cap3_r[ci] <= cap2_r[ci];
                    cap3_i[ci] <= cap2_i[ci];
                end
            end
        end
    end

    function [SW-1:0] abs_sw;
        input signed [SW-1:0] value;
        begin
            abs_sw = value[SW-1] ? (~value + {{(SW-1){1'b0}}, 1'b1}) : value;
        end
    endfunction

    wire det_any = (abs_sw(syn0r) > TH) || (abs_sw(syn0i) > TH) ||
                   (abs_sw(syn1r) > TH) || (abs_sw(syn1i) > TH) ||
                   (abs_sw(syn2r) > TH) || (abs_sw(syn2i) > TH);
    reg gv, gl;
    reg signed [21:0] gd0r, gd0i, gd1r, gd1i, gd2r, gd2i, gd3r, gd3i;
    reg signed [SW-1:0] er, ei;
    reg [2:0] loc;
    reg found;
    reg obs_det_r, obs_cor_r;
    reg [2:0] obs_loc_r;
    always @(posedge clk) begin
        if (rst) begin
            gv <= 1'b0; gl <= 1'b0;
            gd0r <= 22'sd0; gd0i <= 22'sd0; gd1r <= 22'sd0; gd1i <= 22'sd0;
            gd2r <= 22'sd0; gd2i <= 22'sd0; gd3r <= 22'sd0; gd3i <= 22'sd0;
            obs_det_r <= 1'b0; obs_cor_r <= 1'b0; obs_loc_r <= 3'd0;
        end else begin
            gv <= pv_d3;
            gl <= pl_d3;
            obs_det_r <= 1'b0; obs_cor_r <= 1'b0; obs_loc_r <= 3'd0;
            if (pv_d3) begin
                gd0r <= cap3_r[2][21:0]; gd0i <= cap3_i[2][21:0];
                gd1r <= cap3_r[4][21:0]; gd1i <= cap3_i[4][21:0];
                gd2r <= cap3_r[5][21:0]; gd2i <= cap3_i[5][21:0];
                gd3r <= cap3_r[6][21:0]; gd3i <= cap3_i[6][21:0];
                found = 1'b0; loc = 3'd0; er = {SW{1'b0}}; ei = {SW{1'b0}};
                if (!mask_on && det_any) begin
                    obs_det_r <= 1'b1;
                    if ((abs_sw(syn0r) > TH || abs_sw(syn0i) > TH) && (abs_sw(syn1r) <= TH && abs_sw(syn1i) <= TH) && (abs_sw(syn2r) <= TH && abs_sw(syn2i) <= TH)) begin
                        loc = 3'd0; found = 1'b1; er = syn0r; ei = syn0i;
                    end else if ((abs_sw(syn1r) > TH || abs_sw(syn1i) > TH) && (abs_sw(syn0r) <= TH && abs_sw(syn0i) <= TH) && (abs_sw(syn2r) <= TH && abs_sw(syn2i) <= TH)) begin
                        loc = 3'd1; found = 1'b1; er = syn1r; ei = syn1i;
                    end else if ((abs_sw(syn0r - syn1r) <= TOL) && (abs_sw(syn0i - syn1i) <= TOL) && (abs_sw(syn2r) <= TH && abs_sw(syn2i) <= TH)) begin
                        loc = 3'd2; found = 1'b1; er = syn0r; ei = syn0i;
                    end else if ((abs_sw(syn2r) > TH || abs_sw(syn2i) > TH) && (abs_sw(syn0r) <= TH && abs_sw(syn0i) <= TH) && (abs_sw(syn1r) <= TH && abs_sw(syn1i) <= TH)) begin
                        loc = 3'd3; found = 1'b1; er = syn2r; ei = syn2i;
                    end else if ((abs_sw(syn0r - syn2r) <= TOL) && (abs_sw(syn0i - syn2i) <= TOL) && (abs_sw(syn1r) <= TH && abs_sw(syn1i) <= TH)) begin
                        loc = 3'd4; found = 1'b1; er = syn0r; ei = syn0i;
                    end else if ((abs_sw(syn1r - syn2r) <= TOL) && (abs_sw(syn1i - syn2i) <= TOL) && (abs_sw(syn0r) <= TH && abs_sw(syn0i) <= TH)) begin
                        loc = 3'd5; found = 1'b1; er = syn1r; ei = syn1i;
                    end else if ((abs_sw(syn0r - syn1r) <= TOL) && (abs_sw(syn0i - syn1i) <= TOL) && (abs_sw(syn0r - syn2r) <= TOL) && (abs_sw(syn0i - syn2i) <= TOL)) begin
                        loc = 3'd6; found = 1'b1; er = syn0r; ei = syn0i;
                    end
                    if (found) begin
                        obs_cor_r <= 1'b1; obs_loc_r <= loc;
                        case (loc)
                            3'd2: begin gd0r <= cap3_r[2][21:0] - er[21:0]; gd0i <= cap3_i[2][21:0] - ei[21:0]; end
                            3'd4: begin gd1r <= cap3_r[4][21:0] - er[21:0]; gd1i <= cap3_i[4][21:0] - ei[21:0]; end
                            3'd5: begin gd2r <= cap3_r[5][21:0] - er[21:0]; gd2i <= cap3_i[5][21:0] - ei[21:0]; end
                            3'd6: begin gd3r <= cap3_r[6][21:0] - er[21:0]; gd3i <= cap3_i[6][21:0] - ei[21:0]; end
                            default: begin end
                        endcase
                    end
                end
            end
        end
    end

    wire v9, l9;
    wire signed [21:0] s0r, s0i, s1r, s1i, s2r, s2i, s3r, s3i;
    s1_tmr_subfft_stage9_wg s9 (
        clk, rst, gv, gl,
        gd0r, gd0i, gd1r, gd1i, gd2r, gd2i, gd3r, gd3i,
        v9, l9, s0r, s0i, s1r, s1i, s2r, s2i, s3r, s3i
    );
    s1_tmr_subfft_stage10_wg s10 (
        clk, rst, v9, l9,
        s0r, s0i, s1r, s1i, s2r, s2i, s3r, s3i,
        out_valid, out_last,
        out0_re, out0_im, out1_re, out1_im, out2_re, out2_im, out3_re, out3_im
    );

    assign win_second        = 8'd0;
    assign up_detected       = {8{obs_det_r}};
    assign up_corrected      = {8{obs_cor_r}};
    assign up_uncorrectable  = {8{obs_det_r & ~obs_cor_r}};
    assign up_error_location = {8{obs_loc_r}};
    wire _unused = in_last ^ pv[1] ^ pv[2] ^ pv[3] ^ pv[4] ^ pv[5] ^ pv[6]
                 ^ pl[1] ^ pl[2] ^ pl[3] ^ pl[4] ^ pl[5] ^ pl[6];
endmodule
