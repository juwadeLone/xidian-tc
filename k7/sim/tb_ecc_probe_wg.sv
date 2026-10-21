// tb_paper_stream_golden_wg.sv -- end-to-end numeric check of the
// growing-width (12-bit in, +1/stage, 22-bit out) paper_fft12 kernels
// against the fixed_fft_wgrow golden model.
//
// Contract:
//   * input  = 10 frames = 8 data frames + 2 zero padding frames
//              (sim/vectors_wg/wg_input_10frames.hex, 96-bit beats)
//   * stream = 30720 bytes over the WR 128-bit port (beats packed
//              little-endian into bytes)
//   * RUN target = 10 frames; the engine feeds the 10 frames plus its own
//     tail flush; the first 2048 output beats are the checked outputs
//   * expected file per arch:
//       s0 -> qualification_subfft_expected_8frames_wg.hex
//       p0 -> qualification_pfft_no_exchange_expected_8frames_wg.hex
//
// PASS = every checked beat bit-identical and run/drain complete.
`timescale 1ns/1ps

module tb_ecc_probe_wg;
`include "tb_arch_sel.svh"
    localparam integer FRAME_BEATS  = 256;
    localparam integer IN_W         = 16;
    localparam integer OUT_W        = 22;
    localparam integer IN_BEAT_W    = 8 * IN_W;    // 128 == wire word
    localparam integer OUT_BEAT_W   = 8 * OUT_W;   // 176
    localparam integer WORD_W       = 128;
    localparam integer IN_FRAME_BYTES  = FRAME_BEATS * IN_W;   // 4096
    localparam integer OUT_FRAME_BYTES = FRAME_BEATS * OUT_W;  // 5632
    localparam integer N_FRAMES     = 10;
    localparam integer EXP_BEATS    = 8 * FRAME_BEATS;
    localparam integer TOTAL_BEATS  = N_FRAMES * FRAME_BEATS;
    localparam integer ACC_W        = OUT_BEAT_W + WORD_W;

    reg clk = 0; always #4 clk = ~clk;   // 125 MHz
    reg rst = 1;

    reg         ing_start = 0;
    reg  [31:0] ing_len = 0;
    reg  [127:0] ing_data = 0;
    reg         ing_valid = 0;
    wire        ing_ready, ing_busy, ing_done, ing_error;
    reg         run_start = 0;
    wire        run_busy, run_done, run_error;
    reg         out_start = 0;
    reg  [31:0] out_len = 0;
    wire [127:0] out_data;
    wire        out_valid, out_busy, out_done, out_error;
    reg         out_ready = 0;
    reg  [4:0]  rb_idx = 0;
    wire [95:0] rb_data;
    wire [2:0]  fsm_state;

    paper_fft_stream_engine #(
        .PAPER_ARCH(ARCH_SEL),
        .MAX_FRAMES(16),
        .IN_W(IN_W),
        .OUT_W(OUT_W)
    ) dut (
        .clk(clk), .rst(rst), .clear(1'b0),
        .ing_start(ing_start), .ing_len(ing_len),
        .ing_data(ing_data), .ing_valid(ing_valid),
        .ing_ready(ing_ready), .ing_busy(ing_busy),
        .ing_done(ing_done), .ing_error(ing_error),
        .run_start(run_start), .run_busy(run_busy),
        .run_done(run_done), .run_error(run_error),
        .out_start(out_start), .out_len(out_len),
        .out_data(out_data), .out_valid(out_valid), .out_ready(out_ready),
        .out_busy(out_busy), .out_done(out_done), .out_error(out_error),
        .rb_idx(rb_idx), .rb_data(rb_data), .fsm_state(fsm_state));

    // ------------------------------------------------------------ vectors
    reg [IN_BEAT_W-1:0]  input_mem    [0:TOTAL_BEATS-1];
    reg [OUT_BEAT_W-1:0] expected_mem [0:EXP_BEATS-1];
    integer errors = 0;
    reg seen_run_done = 0, seen_run_err = 0;
    always @(posedge clk) begin
        if (run_done)  seen_run_done <= 1'b1;
        if (run_error) seen_run_err  <= 1'b1;
    end

    // ---- per-stage ECC event counters (hierarchical tap) ----
    integer det_n [0:7];
    integer cor_n [0:7];
    integer unc_n [0:7];
    integer loc_hist [0:7][0:7];
    integer pk, pj;
    initial begin
        for (pk = 0; pk < 8; pk = pk + 1) begin
            det_n[pk] = 0; cor_n[pk] = 0; unc_n[pk] = 0;
            for (pj = 0; pj < 8; pj = pj + 1) loc_hist[pk][pj] = 0;
        end
    end
    wire [7:0]  w_det = dut.k_det;
    wire [7:0]  w_cor = dut.k_cor;
    wire [7:0]  w_unc = dut.k_unc;
    wire [23:0] w_loc = dut.k_loc;
    always @(posedge clk) begin
        for (pk = 0; pk < 8; pk = pk + 1) begin
            if (w_det[pk] === 1'b1) begin
                det_n[pk] = det_n[pk] + 1;
                loc_hist[pk][w_loc[pk*3 +: 3]] = loc_hist[pk][w_loc[pk*3 +: 3]] + 1;
            end
            if (w_cor[pk] === 1'b1) cor_n[pk] = cor_n[pk] + 1;
            if (w_unc[pk] === 1'b1) unc_n[pk] = unc_n[pk] + 1;
        end
    end

    // ---- residual magnitude taps (s3 stage syndrome regs) ----
    integer maxsy [0:7];
    integer big_first = 0;
    integer beat_no = 0;
    initial for (pk = 0; pk < 8; pk = pk + 1) maxsy[pk] = 0;
    function integer absi; input signed [31:0] v; absi = (v < 0) ? -v : v; endfunction
    task watch(input integer idx, input signed [31:0] a, input signed [31:0] b,
               input signed [31:0] c, input signed [31:0] d);
        begin
            if (absi(a) > maxsy[idx]) maxsy[idx] = absi(a);
            if (absi(b) > maxsy[idx]) maxsy[idx] = absi(b);
            if (absi(c) > maxsy[idx]) maxsy[idx] = absi(c);
            if (absi(d) > maxsy[idx]) maxsy[idx] = absi(d);
        end
    endtask
    if (ARCH_SEL == 1) begin : g_s1tap
    reg signed [23:0] lastr [0:6], lasti [0:6];
    integer pk2;
    always @(posedge clk) begin
        if (!rst) begin
            if (dut.g_k.u_kernel.pv_d2) begin
                for (pk2 = 0; pk2 < 7; pk2 = pk2 + 1) begin
                    lastr[pk2] <= dut.g_k.u_kernel.cap2_r[pk2];
                    lasti[pk2] <= dut.g_k.u_kernel.cap2_i[pk2];
                end
            end
            watch(0, dut.g_k.u_kernel.syn0r, dut.g_k.u_kernel.syn0i,
                  dut.g_k.u_kernel.syn1r, dut.g_k.u_kernel.syn1i);
            watch(1, dut.g_k.u_kernel.syn2r, dut.g_k.u_kernel.syn2i, 0, 0);
            if (dut.g_k.u_kernel.pv !== 7'b0 && dut.g_k.u_kernel.pv !== 7'b1111111)
                $display("PVSKEW t=%0t pv=%b pl=%b", $time,
                         dut.g_k.u_kernel.pv, dut.g_k.u_kernel.pl);
            if (dut.g_k.u_kernel.pv_d2) beat_no = beat_no + 1;
            if (dut.g_k.u_kernel.obs_det_r && big_first < 12) begin
                if ((dut.g_k.u_kernel.syn0r > 256) || (dut.g_k.u_kernel.syn0r < -256) ||
                    (dut.g_k.u_kernel.syn1r > 256) || (dut.g_k.u_kernel.syn1r < -256) ||
                    (dut.g_k.u_kernel.syn2r > 256) || (dut.g_k.u_kernel.syn2r < -256) ||
                    (dut.g_k.u_kernel.syn0i > 256) || (dut.g_k.u_kernel.syn0i < -256) ||
                    (dut.g_k.u_kernel.syn1i > 256) || (dut.g_k.u_kernel.syn1i < -256) ||
                    (dut.g_k.u_kernel.syn2i > 256) || (dut.g_k.u_kernel.syn2i < -256)) begin
                    big_first = big_first + 1;
                    $display("S1BIG b=%0d syn0=%0d/%0d syn1=%0d/%0d syn2=%0d/%0d",
                             beat_no,
                             dut.g_k.u_kernel.syn0r, dut.g_k.u_kernel.syn0i,
                             dut.g_k.u_kernel.syn1r, dut.g_k.u_kernel.syn1i,
                             dut.g_k.u_kernel.syn2r, dut.g_k.u_kernel.syn2i);
                    $display("  cap3r=[%0d %0d %0d %0d %0d %0d %0d]",
                             dut.g_k.u_kernel.cap3_r[0], dut.g_k.u_kernel.cap3_r[1],
                             dut.g_k.u_kernel.cap3_r[2], dut.g_k.u_kernel.cap3_r[3],
                             dut.g_k.u_kernel.cap3_r[4], dut.g_k.u_kernel.cap3_r[5],
                             dut.g_k.u_kernel.cap3_r[6]);
                    $display("  cap3i=[%0d %0d %0d %0d %0d %0d %0d]",
                             dut.g_k.u_kernel.cap3_i[0], dut.g_k.u_kernel.cap3_i[1],
                             dut.g_k.u_kernel.cap3_i[2], dut.g_k.u_kernel.cap3_i[3],
                             dut.g_k.u_kernel.cap3_i[4], dut.g_k.u_kernel.cap3_i[5],
                             dut.g_k.u_kernel.cap3_i[6]);
                    $display("  lastr=[%0d %0d %0d %0d %0d %0d %0d]",
                             lastr[0], lastr[1], lastr[2], lastr[3], lastr[4], lastr[5], lastr[6]);
                    $display("  lasti=[%0d %0d %0d %0d %0d %0d %0d]",
                             lasti[0], lasti[1], lasti[2], lasti[3], lasti[4], lasti[5], lasti[6]);
                end
            end
        end
    end
    end
    if (ARCH_SEL == 3) begin : g_s3tap
    always @(posedge clk) begin
        if (!rst) begin
            watch(0, dut.g_k.u_kernel.u.s1.sy0r, dut.g_k.u_kernel.u.s1.sy0i, dut.g_k.u_kernel.u.s1.sy1r, dut.g_k.u_kernel.u.s1.sy1i);
            watch(1, dut.g_k.u_kernel.u.s2.sy0r, dut.g_k.u_kernel.u.s2.sy0i, dut.g_k.u_kernel.u.s2.sy1r, dut.g_k.u_kernel.u.s2.sy1i);
            watch(2, dut.g_k.u_kernel.u.s3.sy0r, dut.g_k.u_kernel.u.s3.sy0i, dut.g_k.u_kernel.u.s3.sy1r, dut.g_k.u_kernel.u.s3.sy1i);
            watch(3, dut.g_k.u_kernel.u.s4.sy0r, dut.g_k.u_kernel.u.s4.sy0i, dut.g_k.u_kernel.u.s4.sy1r, dut.g_k.u_kernel.u.s4.sy1i);
            watch(4, dut.g_k.u_kernel.u.s5.sy0r, dut.g_k.u_kernel.u.s5.sy0i, dut.g_k.u_kernel.u.s5.sy1r, dut.g_k.u_kernel.u.s5.sy1i);
            watch(5, dut.g_k.u_kernel.u.s6.sy0r, dut.g_k.u_kernel.u.s6.sy0i, dut.g_k.u_kernel.u.s6.sy1r, dut.g_k.u_kernel.u.s6.sy1i);
            watch(6, dut.g_k.u_kernel.u.s7.sy0r, dut.g_k.u_kernel.u.s7.sy0i, dut.g_k.u_kernel.u.s7.sy1r, dut.g_k.u_kernel.u.s7.sy1i);
            watch(7, dut.g_k.u_kernel.u.s8.sy0r, dut.g_k.u_kernel.u.s8.sy0i, dut.g_k.u_kernel.u.s8.sy1r, dut.g_k.u_kernel.u.s8.sy1i);
        end
    end
    end

    task chk(input bit ok, input string msg);
        if (!ok) begin errors = errors + 1; $display("FAIL: %s", msg); end
    endtask

    task load_expected;
        begin
            if (ARCH_SEL <= 3) begin
                $readmemh("sim/vectors_wg/qualification_subfft_expected_8frames_wg.hex", expected_mem);
            end else if (ARCH_SEL == 5) begin
                $readmemh("sim/vectors_wg/qualification_pfft_exchange_expected_8frames_wg.hex", expected_mem);
            end else begin
                $readmemh("sim/vectors_wg/qualification_pfft_no_exchange_expected_8frames_wg.hex", expected_mem);
            end
        end
    endtask

    // ------------------------------------------------------- wire packing
    task send_word(input [127:0] d);
        begin
            @(negedge clk);
            ing_data  <= d;
            ing_valid <= 1'b1;
            @(posedge clk);
            while (!ing_ready) @(posedge clk);
            @(negedge clk);
            ing_valid <= 1'b0;
        end
    endtask

    // Serialize each input beat little-endian into a byte stream and pack
    // into 128-bit words -- identical byte order to the engine repacker.
    task send_all_beats;
        integer b, by, w;
        reg [7:0] byte_v;
        reg [WORD_W-1:0] cur;
        integer nbits;
        begin
            cur = '0; nbits = 0; w = 0;
            for (b = 0; b < TOTAL_BEATS; b = b + 1) begin
                for (by = 0; by < IN_BEAT_W/8; by = by + 1) begin
                    byte_v = input_mem[b][by*8 +: 8];
                    cur = cur | ({{(WORD_W-8){1'b0}}, byte_v} << nbits);
                    nbits = nbits + 8;
                    if (nbits == WORD_W) begin
                        send_word(cur);
                        cur = '0; nbits = 0; w = w + 1;
                    end
                end
            end
            chk(nbits == 0, "trailing partial word after 10 frames");
            chk(w == N_FRAMES*IN_FRAME_BYTES/(WORD_W/8),
                $sformatf("sent %0d words, want %0d", w,
                          N_FRAMES*IN_FRAME_BYTES/(WORD_W/8)));
        end
    endtask

    // -------------------------------------------------------- drain/check
    reg [OUT_BEAT_W-1:0] got_beats [0:TOTAL_BEATS-1];
    integer got_n, i;

    task drain_and_check;
        integer t;
        reg [ACC_W-1:0] racc;
        integer racc_n;
        begin
            got_n = 0; racc = '0; racc_n = 0; t = 0;
            while (got_n < TOTAL_BEATS && t < 80000) begin
                @(negedge clk);
                out_ready = (t % 7 != 2);   // irregular backpressure
                t = t + 1;
                if (out_valid && out_ready) begin
                    racc = racc | ({{(ACC_W-WORD_W){1'b0}}, out_data} << racc_n);
                    racc_n = racc_n + WORD_W;
                    if (racc_n >= OUT_BEAT_W) begin
                        got_beats[got_n] = racc[OUT_BEAT_W-1:0];
                        got_n = got_n + 1;
                        racc = racc >> OUT_BEAT_W;
                        racc_n = racc_n - OUT_BEAT_W;
                    end
                    @(posedge clk);
                end
            end
            chk(got_n == TOTAL_BEATS,
                $sformatf("drain got %0d/%0d beats", got_n, TOTAL_BEATS));
            for (i = 0; i < EXP_BEATS; i = i + 1) begin
                chk(got_beats[i] === expected_mem[i],
                    $sformatf("output beat %0d mismatch", i));
                if (got_beats[i] !== expected_mem[i] && errors < 5)
                    $display("  beat %0d got=%h exp=%h", i,
                             got_beats[i], expected_mem[i]);
            end
        end
    endtask

    initial begin
        $readmemh("sim/vectors_wg/wg_input_10frames.hex", input_mem);
        load_expected;
        repeat (8) @(posedge clk);
        rst <= 0;
        repeat (8) @(posedge clk);

        // WR: 10 frames = 40960 bytes = 2560 words
        @(posedge clk) ing_start <= 1; ing_len <= N_FRAMES*IN_FRAME_BYTES;
        @(posedge clk) ing_start <= 0;
        send_all_beats;
        repeat (20) @(posedge clk);
        chk(!ing_error, "ingest error");
        rb_idx = 5'd3; #1;
        chk(rb_data[15:0] == 16'd10, "frames_committed != 10");

        // RUN (run_done/run_error are pulses -- latch them)
        seen_run_done = 0; seen_run_err = 0;
        @(posedge clk) run_start <= 1;
        @(posedge clk) run_start <= 0;
        i = 0;
        while (!seen_run_done && i < 100000) begin @(posedge clk); i = i + 1; end
        chk(seen_run_done, "run never finished");
        chk(!seen_run_err, "run_error on clean run");
        chk(dut.out_commit == TOTAL_BEATS, "out_commit != 2560");

        // RD + golden compare
        @(posedge clk) out_start <= 1; out_len <= N_FRAMES*OUT_FRAME_BYTES;
        @(posedge clk) out_start <= 0;
        drain_and_check;
        chk(!out_error, "drain error");

        if (errors == 0)
            $display("TB_GOLDEN_WG arch=%0d: ALL %0d BEATS BIT-EXACT", ARCH_SEL, EXP_BEATS);
        else
            $display("TB_GOLDEN_WG arch=%0d: %0d FAILURES", ARCH_SEL, errors);
        for (pk = 0; pk < 8; pk = pk + 1) begin
            $display("PROBE maxsy%0d=%0d", pk, maxsy[pk]);
        end
        for (pk = 0; pk < 8; pk = pk + 1)
            $display("PROBE stage%0d det=%0d cor=%0d unc=%0d loc=[%0d %0d %0d %0d %0d %0d %0d %0d]",
                     pk, det_n[pk], cor_n[pk], unc_n[pk],
                     loc_hist[pk][0], loc_hist[pk][1], loc_hist[pk][2], loc_hist[pk][3],
                     loc_hist[pk][4], loc_hist[pk][5], loc_hist[pk][6], loc_hist[pk][7]);
        $finish;
    end

    initial begin
        #500000000;
        $display("TB_GOLDEN_WG: TIMEOUT");
        $finish;
    end
endmodule
