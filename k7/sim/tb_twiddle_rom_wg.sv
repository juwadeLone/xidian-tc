`timescale 1ns/1ps

// Unit test of twiddle_bank_wg (PORTS=2): every table entry through both
// ports, then single-bit upsets on 65 entries, each corrected by the SECDED.
// Expected data: sim/twiddle_rom_1024_expected_wg.hex (sim/gen_twiddle_rom_wg.py).
module tb_twiddle_rom_wg;
    reg clk = 0;
    always #4 clk = ~clk;                     // 125 MHz

    reg  [9:0] addr_a = 10'd0, addr_b = 10'd0;
    wire signed [17:0] coeff_a_re, coeff_a_im, coeff_b_re, coeff_b_im;
    wire det_a, cor_a, det_b, cor_b;

    twiddle_bank_wg #(.PORTS(2)) dut (
        .clk(clk), .addr_a(addr_a), .addr_b(addr_b),
        .coeff_a_re(coeff_a_re), .coeff_a_im(coeff_a_im),
        .coeff_b_re(coeff_b_re), .coeff_b_im(coeff_b_im),
        .det_a(det_a), .cor_a(cor_a), .det_b(det_b), .cor_b(cor_b));

    reg [35:0] exp_mem [0:1023];
    reg [42:0] save;
    integer errors = 0, checks = 0, i, b;

    task automatic report(input [8*16-1:0] what, input integer idx);
        begin
            errors = errors + 1;
            if (errors <= 5)
                $display("MISMATCH %0s idx=%0d a=%h/%h b=%h/%h exp=%h det/cor a=%b%b b=%b%b",
                         what, idx, coeff_a_re, coeff_a_im, coeff_b_re, coeff_b_im,
                         exp_mem[idx], det_a, cor_a, det_b, cor_b);
        end
    endtask

    // Address presented on one cycle, outputs sampled the next cycle (at negedge).
    task read_at(input [9:0] aa, input [9:0] ab);
        begin
            addr_a = aa;
            addr_b = ab;
            @(posedge clk);
            @(negedge clk);
        end
    endtask

    initial begin
        $readmemh("sim/twiddle_rom_1024_expected_wg.hex", exp_mem);
        @(negedge clk);

        // Pass 1: every entry on both ports (port b reads the mirrored index).
        for (i = 0; i < 1024; i = i + 1) begin
            read_at(i, 1023 - i);
            checks = checks + 1;
            if ({coeff_a_re, coeff_a_im} !== exp_mem[i]) report("port_a", i);
            if ({coeff_b_re, coeff_b_im} !== exp_mem[1023 - i]) report("port_b", i);
            if (det_a !== 1'b0 || det_b !== 1'b0) report("det", i);
        end

        // Pass 2: single-bit upsets, all 43 bit positions, both ports.
        for (i = 0; i < 1024; i = i + 1) begin
            if (i % 16 == 0 || i == 1023) begin
                save = dut.rom[i];
                for (b = 0; b < 43; b = b + 1) begin
                    dut.rom[i] = save ^ (43'd1 << b);
                    read_at(i, i);
                    checks = checks + 1;
                    if ({coeff_a_re, coeff_a_im} !== exp_mem[i]) report("upset_a", i);
                    if ({coeff_b_re, coeff_b_im} !== exp_mem[i]) report("upset_b", i);
                    if (cor_a !== 1'b1 || cor_b !== 1'b1) report("cor", i);
                    dut.rom[i] = save;
                end
            end
        end

        $display("TWIDDLE_ROM_CHECKS: %0d", checks);
        if (errors == 0)
            $display("TB_TWIDDLE_ROM: PASS");
        else
            $display("TB_TWIDDLE_ROM: FAIL %0d", errors);
        $finish;
    end
endmodule
