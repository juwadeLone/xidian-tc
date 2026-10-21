// tb_power_saif_wg.sv -- WP7b SAIF capture on the routed kernel netlist.
//
// Mounting (see results/wp7b/REPORT.md for the full rationale):
//   The routed kernel cell (u_core/u_dut/g_k.u_kernel) has a *physical*
//   boundary: synthesis pulled engine-FSM signals (rstate bits, last_* regs,
//   feed/out-commit terms, proc_rst_n, cs_build_start=run_start) across the
//   keep_hierarchy edge, so a literal "RTL engine + netlist kernel" port swap
//   is impossible without added logic.  Instead the RTL stream engine runs
//   UNMODIFIED with its own RTL kernel (it produces the checked 2048-beat
//   output stream and the FSM context) and the netlist kernel rides as a
//   full-fidelity passenger: every one of its inputs is wired to the exact
//   engine signal the routed design feeds it.  The shell hierarchy
//   u_core/u_dut/g_k.u_kernel reproduces the checkpoint cell path, so the
//   recorded SAIF maps 1:1 onto the routed design.
//
//   Clock: 8.0 ns == clk_out2_clk_wiz_k7 (125 MHz) -- the implemented kernel
//   clock (k7_paper_top.sv drives u_core.clk with clk125).  The work package
//   text's "5 ns / 200 MHz" assumes clk_out1, which is the IDELAY clock.
//
//   SAIF window: first kernel-valid input beat (dut.d_in_valid_r) through the
//   last output commit (dut.out_commit == dut.needed_beats).
//
// Defines required at compile time:
//   WP7B_NL_TOP  -- renamed netlist top module (wp7b_<design>_<top>)
//   WP7B_ECC or WP7B_TMR -- kernel family port shape
`timescale 1ns/1ps

module tb_power_saif_wg;
`include "tb_arch_sel.svh"
    localparam integer FRAME_BEATS  = 256;
    localparam integer IN_W         = 16;
    localparam integer OUT_W        = 27;
    localparam integer IN_BEAT_W    = 8 * IN_W;    // 128 == wire word
    localparam integer OUT_BEAT_W   = 8 * OUT_W;   // 216
    localparam integer WORD_W       = 128;
    localparam integer IN_FRAME_BYTES  = FRAME_BEATS * IN_W;   // 4096
    localparam integer OUT_FRAME_BYTES = FRAME_BEATS * OUT_W;  // 6912
    localparam integer N_FRAMES     = 10;
    localparam integer EXP_BEATS    = 8 * FRAME_BEATS;
    localparam integer TOTAL_BEATS  = N_FRAMES * FRAME_BEATS;
    localparam integer ACC_W        = OUT_BEAT_W + WORD_W;

    reg clk = 0; always #4 clk = ~clk;   // 8 ns == clk_out2_clk_wiz_k7 (125 MHz)
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

    task chk(input bit ok, input string msg);
        if (!ok) begin errors = errors + 1; $display("FAIL: %s", msg); end
    endtask

    // +VSET=<name> selects sim/vectors_wg/<name>/; without it (or with
    // "qual") the original qualification vectors are used.
    string vset = "qual", vmodel, vfile;
    integer vset_ok;
    task load_vectors;
        integer vi, xi, xe;
        begin
            vset_ok = $value$plusargs("VSET=%s", vset);
            case (ARCH_SEL)
                0,1,2,3,7,10: vmodel = "subfft";
                4: vmodel = "pfft_no_exchange";
                5,6,8,9: vmodel = "pfft_exchange";
                default: $fatal(1, "unknown architecture reference model");
            endcase
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
            // non-vacuity: vectors/expected must be fully defined; a missing
            // file leaves X entries and would make the === compare vacuous
            xi = 0; xe = 0;
            for (vi = 0; vi < TOTAL_BEATS; vi = vi + 1)
                if ($isunknown(input_mem[vi])) xi = xi + 1;
            for (vi = 0; vi < EXP_BEATS; vi = vi + 1)
                if ($isunknown(expected_mem[vi])) xe = xe + 1;
            $display("WP7B_XCHECK vectors input_x=%0d expected_x=%0d", xi, xe);
            chk(xi == 0, "input_mem contains X/Z entries");
            chk(xe == 0, "expected_mem contains X/Z entries");
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
            racc_n = 0;
            for (i = 0; i < EXP_BEATS; i = i + 1)
                if ($isunknown(got_beats[i])) racc_n = racc_n + 1;
            $display("WP7B_XCHECK beats got_x=%0d", racc_n);
            chk(racc_n == 0, "compared output beats contain X/Z");
            for (i = 0; i < EXP_BEATS; i = i + 1) begin
                chk(got_beats[i] === expected_mem[i],
                    $sformatf("output beat %0d mismatch", i));
                if (got_beats[i] !== expected_mem[i] && errors < 5)
                    $display("  beat %0d got=%h exp=%h", i,
                             got_beats[i], expected_mem[i]);
            end
        end
    endtask

    // ================================================= WP7b passenger ====
    // Routed kernel netlist driven by the engine's own signals.  Aux-input
    // expressions below are decoded verbatim from the checkpoint's engine-
    // side cells (INITs recorded in results/wp7b/REPORT.md); they are pure
    // wiring of existing engine/TB signals.
    wire prst_n = ~rst;                 // proc_rst_n (core-level reset, act-low)
    wire aux_idle_start = run_start & (dut.rstate == 2'b00);
    wire co_term = (dut.out_commit < dut.needed_beats);  // feed_issue*_in

    function automatic bit lut5(input bit [31:0] init, input bit [4:0] i);
        lut5 = init[i];
    endfunction

    reg saif_begin = 0, saif_end = 0;
    always @(posedge clk) begin
        if (!saif_begin && dut.d_in_valid_r) begin
            saif_begin <= 1'b1;
            $display("WP7B saif_begin @%0t rstate=%0d feed_issue=%b",
                     $time, dut.rstate, dut.feed_issue);
        end
        if (!saif_end && dut.out_commit != 0 &&
            dut.out_commit == dut.needed_beats)
            saif_end <= 1'b1;
    end

    wire [OUT_BEAT_W-1:0] nl_out_beat;
    wire nl_out_valid, nl_out_last;

    // event counters on the netlist pipeline's status ports (V2)
    integer nl_ev_det = 0, nl_ev_cor = 0, nl_ev_unc = 0, nl_ev_mis = 0;
    // netlist output beats checked against the engine's own output (V2)
    integer nl_beats = 0, nl_beat_err = 0;

`ifdef WP7B_ECC
    // run_done_i_2 LUT5 (P0/S0): INIT 32'h55555455 over
    //   I0=eng_out_busy I1=|{tf6,9,4,8} I2=|{tf5,11,1,13}
    //   I3=NOR{tf2,15,10,12} I4=|{tf0,7,3,14}
    wire rd2 = lut5(32'h55555455,
        { |{dut.target_frames[0],dut.target_frames[7],dut.target_frames[3],dut.target_frames[14]},
          ~|{dut.target_frames[2],dut.target_frames[15],dut.target_frames[10],dut.target_frames[12]},
          |{dut.target_frames[5],dut.target_frames[11],dut.target_frames[1],dut.target_frames[13]},
          |{dut.target_frames[6],dut.target_frames[9],dut.target_frames[4],dut.target_frames[8]},
          dut.out_busy });
    wp7b_ucore_shell_ecc u_core (
        .clk_out2(clk), .rst(dut.kernel_rst), .proc_rst_n(prst_n),
        .in_valid(dut.d_in_valid_r), .in_last(dut.d_in_last_r),
        .d_in_data_r(dut.d_in_data_r),
        .data2({dut.last_win[0], dut.last_stage[2], dut.last_stage[1],
                dut.last_class[1], dut.last_class[0]}),
        .rstate0(dut.rstate[0]), .rstate1(dut.rstate[1]),
        .aux_ls2(prst_n & ~aux_idle_start),
        .aux_lw0(~aux_idle_start),
        .aux_oc0(rd2 & aux_idle_start),
        .co(co_term), .cs_build_start(run_start),
        .out_beat(nl_out_beat));
    assign nl_out_valid = u_core.u_dut.\g_k.u_kernel .u_pipeline_n_0;
    assign nl_out_last  = u_core.u_dut.\g_k.u_kernel .u_pipeline_n_1;
    // ECC: u_pipeline.up_* status ports are NLW (unconnected) in the routed
    // netlist -> read X.  The per-stage syndrome registers (u_decode.sy*) and
    // event flags (u_flags.det/cor/unc) are verified from the SAIF toggle
    // counts instead (V4 check in wp7b_power_saif.py); keep TB counters at 0.
    wire [9:0] nl_det = '0;
    wire [9:0] nl_cor = '0;
    wire [9:0] nl_unc = '0;
    wire [9:0] nl_mis = '0;
`endif
`ifdef WP7B_TMR
    // run_done_i_2 LUT5 (P1): INIT 32'hAAAAABAA over
    //   I0=eng_out_busy I1=|{tf2,15,4,11} I2=|{tf3,7,5,14}
    //   I3=NOR{tf0,10,1,6} I4=|{tf8,9,12,13}
    // (S1): same INIT, I1=|{tf14,15,11,12} I2=|{tf0,5,3,13}
    //   I3=NOR{tf1,10,2,4} I4=|{tf6,7,8,9}
`ifdef WP7B_S1
    wire rd2 = lut5(32'hAAAAABAA,
        { |{dut.target_frames[6],dut.target_frames[7],dut.target_frames[8],dut.target_frames[9]},
          ~|{dut.target_frames[1],dut.target_frames[10],dut.target_frames[2],dut.target_frames[4]},
          |{dut.target_frames[0],dut.target_frames[5],dut.target_frames[3],dut.target_frames[13]},
          |{dut.target_frames[14],dut.target_frames[15],dut.target_frames[11],dut.target_frames[12]},
          dut.out_busy });
`else
    wire rd2 = lut5(32'hAAAAABAA,
        { |{dut.target_frames[8],dut.target_frames[9],dut.target_frames[12],dut.target_frames[13]},
          ~|{dut.target_frames[0],dut.target_frames[10],dut.target_frames[1],dut.target_frames[6]},
          |{dut.target_frames[3],dut.target_frames[7],dut.target_frames[5],dut.target_frames[14]},
          |{dut.target_frames[2],dut.target_frames[15],dut.target_frames[4],dut.target_frames[11]},
          dut.out_busy });
`endif
    wp7b_ucore_shell_tmr u_core (
        .clk_out2(clk), .rst(dut.kernel_rst), .proc_rst_n(prst_n),
        .in_valid(dut.d_in_valid_r), .in_last(dut.d_in_last_r),
        .d_in_data_r(dut.d_in_data_r),
        .data2({dut.last_stage[2], dut.last_stage[1], dut.last_class[0]}),
        .rstate0(dut.rstate[0]), .rstate1(dut.rstate[1]),
        .aux_sr(~prst_n | aux_idle_start),
        .aux_oc0(~rd2 & aux_idle_start),
        .co(co_term), .cs_build_start(run_start),
        .out_beat(nl_out_beat));
    assign nl_out_valid = u_core.u_dut.\g_k.u_kernel .u_pipeline_n_0;
    assign nl_out_last  = u_core.u_dut.\g_k.u_kernel .u_pipeline_n_1;
    wire [9:0] nl_det = '0;
    wire [9:0] nl_cor = '0;
    wire [9:0] nl_unc = '0;
    wire [9:0] nl_mis = u_core.u_dut.\g_k.u_kernel .u_pipeline.stage_mismatch;
`endif

    always @(posedge clk) begin
        if (saif_begin && !saif_end) begin
            nl_ev_det <= nl_ev_det + (|nl_det);
            nl_ev_cor <= nl_ev_cor + (|nl_cor);
            nl_ev_unc <= nl_ev_unc + (|nl_unc);
            nl_ev_mis <= nl_ev_mis + ((|nl_mis) === 1'b1);
        end
        if (nl_out_valid && dut.d_out_valid) begin
            nl_beats <= nl_beats + 1;
            if (nl_out_beat !== dut.out_beat && nl_beat_err < 5) begin
                nl_beat_err <= nl_beat_err + 1;
                $display("NL beat mismatch @%0t: nl=%h dut=%h",
                         $time, nl_out_beat, dut.out_beat);
            end
        end
    end

    initial begin
        load_vectors;
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
        // non-vacuity: the counters the SAIF window keys off must be definite
        $display("WP7B_XCHECK state commit_x=%b frames_x=%b",
                 $isunknown(dut.out_commit), $isunknown(dut.frames_committed));
        chk(!$isunknown(dut.out_commit), "out_commit is X/Z");
        chk(!$isunknown(dut.frames_committed), "frames_committed is X/Z");

        // SAIF window must have closed by now
        chk(saif_begin, "SAIF window never opened");
        chk(saif_end, "SAIF window never closed");

        // RD + golden compare
        @(posedge clk) out_start <= 1; out_len <= N_FRAMES*OUT_FRAME_BYTES;
        @(posedge clk) out_start <= 0;
        drain_and_check;
        chk(!out_error, "drain error");

        // WP7b netlist checks (V2)
        chk(nl_beats >= EXP_BEATS,
            $sformatf("netlist kernel produced only %0d beats", nl_beats));
        chk(nl_beat_err == 0,
            $sformatf("netlist kernel output mismatch x%0d", nl_beat_err));
        chk(nl_ev_det == 0 && nl_ev_cor == 0 && nl_ev_unc == 0 &&
            nl_ev_mis == 0,
            $sformatf("netlist events det=%0d cor=%0d unc=%0d mis=%0d",
                      nl_ev_det, nl_ev_cor, nl_ev_unc, nl_ev_mis));
        $display("WP7B_NL arch=%0d vset=%s: beats=%0d err=%0d det=%0d cor=%0d unc=%0d mis=%0d",
                 ARCH_SEL, vset, nl_beats, nl_beat_err,
                 nl_ev_det, nl_ev_cor, nl_ev_unc, nl_ev_mis);

        if (errors == 0)
            $display("TB_GOLDEN_WG arch=%0d vset=%s: ALL %0d BEATS BIT-EXACT", ARCH_SEL, vset, EXP_BEATS);
        else
            $display("TB_GOLDEN_WG arch=%0d vset=%s: %0d FAILURES", ARCH_SEL, vset, errors);
        $finish;
    end

    initial begin
        #500000000;
        $display("TB_GOLDEN_WG: TIMEOUT");
        $finish;
    end
endmodule
