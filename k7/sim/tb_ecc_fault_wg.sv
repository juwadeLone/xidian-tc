// tb_ecc_fault_wg.sv -- fault acceptance test for one stage of the
// paper_fft12 s3 / p1 kernels.
//
// Same WR -> RUN -> RD flow as tb_ecc_probe_wg.  One internal node of one
// stage is disturbed and the read-back is compared with the vectors.
//
// Compile-time selection (sim/tb_fault_sel.svh, written by
// scripts/k7_mod/fault_accept_wg.py):
//   `FSTG            stage instance (s1 ... s10)
//   `FKIND_LANE      lane ECC stage: six butterflies with feedback memories
//   `FKIND_S10       P1 stage 10: time-aligned complete-butterfly ECC
//   `FKIND_TMR       triplicated stage
//   `FROT(L)         rotator output register of functional stream L
//   `FHAS_CHECK_MEM  the check streams own their feedback memories
//   `FSYN_RED        the decoders take unreduced syndromes and expose the
//                    ones reduced modulo the word size (u_clauses / u_loc);
//                    the syndrome maxima are taken from the reduced values,
//                    i.e. as the detect decision sees them
//   `FARCH_P1        P1 kernel: also report the syndrome maximum of every stage
//
// Run-time selection (plusargs):
//   +VSET=name       vector set (sim/vectors_wg/<name>/); default "qual"
//   +FAULT_CASE=n    0 none
//                    lane stage:
//                      1 functional subtractor output   (second half)
//                      2 functional feedback word       (second half)
//                      3 functional feedback word       (first half)
//                      4 functional adder output        (second half)
//                      5 functional rotator output
//                      6 check subtractor output        (second half)
//                      7 check feedback word            (first half)
//                    stage 10:
//                      1 functional subtractor, 4 functional adder,
//                      6 check subtractor
//                    triplicated stage:
//                      9 output register of one replica
//   +FAULT_LANE=n    functional stream 0-3 (triplicated stage: replica 0-2)
//   +FAULT_WK=n      which beat takes the fault: work cycle of a lane stage
//                    (rotator output beat for case 5), beat pair of stage
//                    10, valid output beat of a replica
//   +FAULT_BIT=n     affected bit of the real part
//   +FAULT_HOLD=1    resident fault instead of a single-cycle flip: from
//                    beat FAULT_WK on the bit is stuck at 1 to the end of the
//                    run (lane stage, cases 1, 4, 5).  Simulation counterpart
//                    of a configuration upset that stays in place.
//
// Reported, not judged here: per-stage detect / correct / uncorrectable
// counts, the largest syndrome seen, the number of output beats that differ
// from the vectors, the largest deviation and the error energy.
`timescale 1ns/1ps

module tb_ecc_fault_wg;
`include "tb_arch_sel.svh"
`include "tb_fault_sel.svh"
`define FK dut.g_k.u_kernel.u
`define FS `FK.`FSTG
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

    string vset = "qual", vmodel, vfile;
    task load_vectors;
        begin
            vmodel = (ARCH_SEL <= 3) ? "subfft" :
                     (ARCH_SEL == 5) ? "pfft_exchange" : "pfft_no_exchange";
            if (vset == "qual") begin
                vfile = "sim/vectors_wg/wg_input_10frames.hex";
                $readmemh(vfile, input_mem);
                vfile = {"sim/vectors_wg/qualification_", vmodel, "_expected_8frames_wg.hex"};
                $readmemh(vfile, expected_mem);
            end else begin
                vfile = {"sim/vectors_wg/", vset, "/input_10frames.hex"};
                $readmemh(vfile, input_mem);
                vfile = {"sim/vectors_wg/", vset, "/", vmodel, "_expected_8frames.hex"};
                $readmemh(vfile, expected_mem);
            end
        end
    endtask

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

    // ---- syndrome maxima ----
    // 64-bit: the untruncated-product check carries syndromes wider than 32 bits
    function longint absi; input signed [63:0] v; absi = (v < 0) ? -v : v; endfunction
    function longint max4; input longint m; input signed [63:0] a, b, c, d;
        begin
            max4 = m;
            if (absi(a) > max4) max4 = absi(a);
            if (absi(b) > max4) max4 = absi(b);
            if (absi(c) > max4) max4 = absi(c);
            if (absi(d) > max4) max4 = absi(d);
        end
    endfunction
    longint maxsy = 0;          // probed stage
    longint allsy [1:10];       // every ECC stage of the kernel
    integer sj;
    initial for (sj = 1; sj <= 10; sj = sj + 1) allsy[sj] = 0;
`ifdef FSYN_RED
`define LSY(S)  S.u_clauses.red0r, S.u_clauses.red0i, S.u_clauses.red1r, S.u_clauses.red1i
`define TSYU(S) S.u_loc.red0_ur, S.u_loc.red0_ui, S.u_loc.red1_ur, S.u_loc.red1_ui
`define TSYL(S) S.u_loc.red0_lr, S.u_loc.red0_li, S.u_loc.red1_lr, S.u_loc.red1_li
`else
`define LSY(S)  S.sy0r, S.sy0i, S.sy1r, S.sy1i
`define TSYU(S) S.sy0_ur, S.sy0_ui, S.sy1_ur, S.sy1_ui
`define TSYL(S) S.sy0_lr, S.sy0_li, S.sy1_lr, S.sy1_li
`endif
`ifdef FKIND_LANE
    always @(posedge clk)
        if (!rst && `FS.syn_v)
            maxsy = max4(maxsy, `LSY(`FS));
`endif
`ifdef FKIND_S10
    always @(posedge clk)
        if (!rst && `FS.go_loc) begin
            maxsy = max4(maxsy, `TSYU(`FS));
            maxsy = max4(maxsy, `TSYL(`FS));
        end
`endif
`ifdef FARCH_P1
`define TRACK_LANE(N, S) \
    always @(posedge clk) \
        if (!rst && `FK.S.syn_v) \
            allsy[N] = max4(allsy[N], `LSY(`FK.S));
    `TRACK_LANE(1, s1)
    `TRACK_LANE(2, s2)
    `TRACK_LANE(3, s3)
    `TRACK_LANE(4, s4)
    `TRACK_LANE(5, s5)
    `TRACK_LANE(6, s6)
    `TRACK_LANE(7, s7)
    always @(posedge clk)
        if (!rst && `FK.s10.go_loc) begin
            allsy[10] = max4(allsy[10], `TSYU(`FK.s10));
            allsy[10] = max4(allsy[10], `TSYL(`FK.s10));
        end
`endif

    // ------------------------------------------------------ fault injector
    integer fault_case = 0, fault_lane = 0, fault_wk = 0, fault_bit = 8, fault_hold = 0;
    integer wk = 0, rv = 0, fault_applied = 0, fault_second = -1;
    integer plus_ok;
    reg signed [31:0] fv, hv, fmask;
    reg hold_on = 0;
    initial begin
        plus_ok = $value$plusargs("VSET=%s", vset);
        plus_ok = $value$plusargs("FAULT_CASE=%d", fault_case);
        plus_ok = $value$plusargs("FAULT_LANE=%d", fault_lane);
        plus_ok = $value$plusargs("FAULT_WK=%d", fault_wk);
        plus_ok = $value$plusargs("FAULT_BIT=%d", fault_bit);
        plus_ok = $value$plusargs("FAULT_HOLD=%d", fault_hold);
        fmask = 32'sd1 <<< fault_bit;
    end
    // A flip is set up on the falling edge so exactly one register / memory
    // capture sees it.  Nets are forced across the next rising edge; state
    // elements are overwritten in place and reloaded by their own clocking.
    // A resident fault re-forces a net every falling edge from its driven
    // value, so each rising edge samples "driven value with the bit set".
    genvar gl;

`ifdef FKIND_LANE
    always @(posedge clk) begin
        if (!rst) begin
            if (`FS.do_work) wk <= wk + 1;
            if (`FS.mul_valid_out) rv <= rv + 1;
        end
    end
    generate for (gl = 0; gl < 4; gl = gl + 1) begin : inj
        always @(negedge clk) begin
            if (!rst && fault_lane == gl && fault_hold == 0 && !fault_applied) begin
                if (fault_case == 5) begin
                    if (`FS.mul_valid_out && rv == fault_wk) begin
                        `FROT(gl) = `FROT(gl) ^ fmask;
                        fault_applied = 1;
                    end
                end else if (`FS.do_work && wk == fault_wk) begin
                    if (fault_case == 1) begin
                        fault_applied = 1; fault_second = `FS.do_second;
                        fv = `FS.func_butterflies[gl].diffr ^ fmask;
                        force `FS.func_butterflies[gl].diffr = fv;
                        @(posedge clk); #1 release `FS.func_butterflies[gl].diffr;
                    end else if (fault_case == 4) begin
                        fault_applied = 1; fault_second = `FS.do_second;
                        fv = `FS.func_butterflies[gl].sumr ^ fmask;
                        force `FS.func_butterflies[gl].sumr = fv;
                        @(posedge clk); #1 release `FS.func_butterflies[gl].sumr;
                    end
                end
            end
        end
        always @(negedge clk) begin
            if (!rst && fault_lane == gl && fault_hold != 0) begin
                if (!hold_on && `FS.do_work && wk == fault_wk) begin
                    hold_on = 1;
                    fault_applied = 1;
                    fault_second = `FS.do_second;
                end
                if (hold_on) begin
                    if (fault_case == 1) begin
                        release `FS.func_butterflies[gl].diffr;
                        #1 hv = `FS.func_butterflies[gl].diffr | fmask;
                        force `FS.func_butterflies[gl].diffr = hv;
                    end else if (fault_case == 4) begin
                        release `FS.func_butterflies[gl].sumr;
                        #1 hv = `FS.func_butterflies[gl].sumr | fmask;
                        force `FS.func_butterflies[gl].sumr = hv;
                    end else if (fault_case == 5) begin
                        `FROT(gl) = `FROT(gl) | fmask;
                    end
                end
            end
        end
    end endgenerate
    // Nodes that are not indexed by a generate loop: the feedback words of
    // the functional streams and everything in the check streams.
    always @(negedge clk) begin
        if (!rst && fault_hold == 0 && !fault_applied && `FS.do_work && wk == fault_wk) begin
            if (fault_case == 2 || fault_case == 3) begin
                fault_applied = 1; fault_second = `FS.do_second;
                case (fault_lane)
                0: `FS.ca0r = `FS.ca0r ^ fmask;
                1: `FS.ca1r = `FS.ca1r ^ fmask;
                2: `FS.ca2r = `FS.ca2r ^ fmask;
                default: `FS.ca3r = `FS.ca3r ^ fmask;
                endcase
            end
`ifdef FHAS_CHECK_MEM
            else if (fault_case == 6) begin
                fault_applied = 1; fault_second = `FS.do_second;
                fv = `FS.dif4r ^ fmask;
                force `FS.dif4r = fv;
                @(posedge clk); #1 release `FS.dif4r;
            end else if (fault_case == 7) begin
                fault_applied = 1; fault_second = `FS.do_second;
                `FS.ca4r = `FS.ca4r ^ fmask;
            end
`endif
        end
    end
`endif

`ifdef FKIND_S10
    // go_bf marks the cycle in which the six butterflies of a beat pair are
    // registered.
    always @(posedge clk) if (!rst && `FS.go_bf) wk <= wk + 1;
    generate for (gl = 0; gl < 4; gl = gl + 1) begin : inj
        always @(negedge clk) begin
            if (!rst && fault_lane == gl && !fault_applied && `FS.go_bf && wk == fault_wk) begin
                if (fault_case == 1) begin
                    fault_applied = 1;
                    fv = `FS.bfly[gl].diffr ^ fmask;
                    force `FS.bfly[gl].diffr = fv;
                    @(posedge clk); #1 release `FS.bfly[gl].diffr;
                end else if (fault_case == 4) begin
                    fault_applied = 1;
                    fv = `FS.bfly[gl].sumr ^ fmask;
                    force `FS.bfly[gl].sumr = fv;
                    @(posedge clk); #1 release `FS.bfly[gl].sumr;
                end
            end
        end
    end endgenerate
    always @(negedge clk) begin
        if (!rst && !fault_applied && `FS.go_bf && wk == fault_wk && fault_case == 6) begin
            fault_applied = 1;
            fv = `FS.sd4r ^ fmask;
            force `FS.sd4r = fv;
            @(posedge clk); #1 release `FS.sd4r;
        end
    end
`endif

`ifdef FKIND_TMR
    always @(posedge clk) if (!rst && `FS.replicas[0].u.out_valid) wk <= wk + 1;
    generate for (gl = 0; gl < 3; gl = gl + 1) begin : inj
        always @(negedge clk) begin
            if (!rst && fault_lane == gl && !fault_applied && fault_case == 9
                && `FS.replicas[gl].u.out_valid && wk == fault_wk) begin
                `FS.replicas[gl].u.out1_re = `FS.replicas[gl].u.out1_re ^ fmask;
                fault_applied = 1;
            end
        end
    end endgenerate
`endif

    task chk(input bit ok, input string msg);
        if (!ok) begin errors = errors + 1; $display("FAIL: %s", msg); end
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
        end
    endtask

    // ------------------------------------------------------ drain/compare
    reg [OUT_BEAT_W-1:0] got_beats [0:TOTAL_BEATS-1];
    reg [OUT_BEAT_W-1:0] exp_beat;
    integer got_n, i, f;
    integer ndiff = 0, maxdev = 0, dv;
    longint devsq = 0, goldsq = 0;
    reg signed [OUT_W-1:0] gq, eq;

    task drain_and_compare;
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
            // Frames 8 and 9 answer the zero padding and must come back zero.
            for (i = 0; i < TOTAL_BEATS; i = i + 1) begin
                exp_beat = (i < EXP_BEATS) ? expected_mem[i] : {OUT_BEAT_W{1'b0}};
                if (got_beats[i] !== exp_beat) ndiff = ndiff + 1;
                for (f = 0; f < 8; f = f + 1) begin
                    gq = got_beats[i][f*OUT_W +: OUT_W];
                    eq = exp_beat[f*OUT_W +: OUT_W];
                    dv = gq - eq;
                    if (dv < 0) dv = -dv;
                    if (dv > maxdev) maxdev = dv;
                    devsq = devsq + longint'(dv) * longint'(dv);
                    goldsq = goldsq + longint'(eq) * longint'(eq);
                end
            end
        end
    endtask

    initial begin
        #1 load_vectors;
        repeat (8) @(posedge clk);
        rst <= 0;
        repeat (8) @(posedge clk);

        @(posedge clk) ing_start <= 1; ing_len <= N_FRAMES*IN_FRAME_BYTES;
        @(posedge clk) ing_start <= 0;
        send_all_beats;
        repeat (20) @(posedge clk);
        chk(!ing_error, "ingest error");

        seen_run_done = 0; seen_run_err = 0;
        @(posedge clk) run_start <= 1;
        @(posedge clk) run_start <= 0;
        i = 0;
        while (!seen_run_done && i < 100000) begin @(posedge clk); i = i + 1; end
        chk(seen_run_done, "run never finished");
        chk(!seen_run_err, "run_error");
        chk(dut.out_commit == TOTAL_BEATS, "out_commit != 2560");

        @(posedge clk) out_start <= 1; out_len <= N_FRAMES*OUT_FRAME_BYTES;
        @(posedge clk) out_start <= 0;
        drain_and_compare;
        chk(!out_error, "drain error");

        $display("FAULT_RESULT arch=%0d case=%0d lane=%0d applied=%0d second=%0d wk=%0d bit=%0d hold=%0d flow_errors=%0d ndiff_beats=%0d maxdev=%0d stage_maxsy=%0d devsq=%0d goldsq=%0d",
                 ARCH_SEL, fault_case, fault_lane, fault_applied, fault_second, fault_wk, fault_bit,
                 fault_hold, errors, ndiff, maxdev, maxsy, devsq, goldsq);
        $display("FAULT_VSET %s", vset);
`ifdef FARCH_P1
        $display("SYN_MAX s1=%0d s2=%0d s3=%0d s4=%0d s5=%0d s6=%0d s7=%0d s10=%0d",
                 allsy[1], allsy[2], allsy[3], allsy[4], allsy[5], allsy[6], allsy[7], allsy[10]);
`endif
        for (pk = 0; pk < 8; pk = pk + 1)
            $display("PROBE stage%0d det=%0d cor=%0d unc=%0d loc=[%0d %0d %0d %0d %0d %0d %0d %0d]",
                     pk, det_n[pk], cor_n[pk], unc_n[pk],
                     loc_hist[pk][0], loc_hist[pk][1], loc_hist[pk][2], loc_hist[pk][3],
                     loc_hist[pk][4], loc_hist[pk][5], loc_hist[pk][6], loc_hist[pk][7]);
        $finish;
    end

    initial begin
        #500000000;
        $display("TB_ECC_FAULT_WG: TIMEOUT");
        $finish;
    end
endmodule
