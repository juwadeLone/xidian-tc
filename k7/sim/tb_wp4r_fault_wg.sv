`timescale 1ns/1ps

module tb_wp4r_fault_wg;
`include "tb_arch_sel.svh"
`define WP4R_PIPELINE tb.dut.g_k.u_kernel.u_pipeline

tb_paper_stream_golden_wg tb();

integer fault_case = 0;
integer plus_ok;
integer det_count [0:9];
integer cor_count [0:9];
integer unc_count [0:9];
integer stage;
integer det_xz_count [0:9];
integer cor_xz_count [0:9];
integer unc_xz_count [0:9];
integer secded_cor_count = 0;
integer secded_uncorr_count = 0;
integer secded_flip_count = 0;
integer probe_count = 0;
reg probe_started = 0;
reg probe_enable = 0;
reg applied = 0;
reg window_ended = 0;

wire mem_det [0:6];
wire mem_cor [0:6];
assign mem_det[0] = `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.u_memory.g_func[0].u_decode.detected;
assign mem_cor[0] = `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.u_memory.g_func[0].u_decode.corrected;
assign mem_det[1] = `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.u_memory.g_func[0].u_decode.detected;
assign mem_cor[1] = `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.u_memory.g_func[0].u_decode.corrected;
assign mem_det[2] = `WP4R_PIPELINE.g_stage[3].g_ecc.u_stage.u_memory.g_func[0].u_decode.detected;
assign mem_cor[2] = `WP4R_PIPELINE.g_stage[3].g_ecc.u_stage.u_memory.g_func[0].u_decode.corrected;
assign mem_det[3] = `WP4R_PIPELINE.g_stage[4].g_ecc.u_stage.u_memory.g_func[0].u_decode.detected;
assign mem_cor[3] = `WP4R_PIPELINE.g_stage[4].g_ecc.u_stage.u_memory.g_func[0].u_decode.corrected;
assign mem_det[4] = `WP4R_PIPELINE.g_stage[5].g_ecc.u_stage.u_memory.g_func[0].u_decode.detected;
assign mem_cor[4] = `WP4R_PIPELINE.g_stage[5].g_ecc.u_stage.u_memory.g_func[0].u_decode.corrected;
assign mem_det[5] = `WP4R_PIPELINE.g_stage[6].g_ecc.u_stage.u_memory.g_func[0].u_decode.detected;
assign mem_cor[5] = `WP4R_PIPELINE.g_stage[6].g_ecc.u_stage.u_memory.g_func[0].u_decode.corrected;
assign mem_det[6] = `WP4R_PIPELINE.g_stage[7].g_ecc.u_stage.u_memory.g_func[0].u_decode.detected;
assign mem_cor[6] = `WP4R_PIPELINE.g_stage[7].g_ecc.u_stage.u_memory.g_func[0].u_decode.corrected;

wire mem_code_set [0:6];
wire mem_data_clear [0:6];
assign mem_code_set[0] = `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[20];
assign mem_data_clear[0] = `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.u_memory.g_func[0].u_decode.data[15];
assign mem_code_set[1] = `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[22];
assign mem_data_clear[1] = `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.u_memory.g_func[0].u_decode.data[17];
assign mem_code_set[2] = `WP4R_PIPELINE.g_stage[3].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[23];
assign mem_data_clear[2] = `WP4R_PIPELINE.g_stage[3].g_ecc.u_stage.u_memory.g_func[0].u_decode.data[18];
assign mem_code_set[3] = `WP4R_PIPELINE.g_stage[4].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[24];
assign mem_data_clear[3] = `WP4R_PIPELINE.g_stage[4].g_ecc.u_stage.u_memory.g_func[0].u_decode.data[19];
assign mem_code_set[4] = `WP4R_PIPELINE.g_stage[5].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[25];
assign mem_data_clear[4] = `WP4R_PIPELINE.g_stage[5].g_ecc.u_stage.u_memory.g_func[0].u_decode.data[20];
assign mem_code_set[5] = `WP4R_PIPELINE.g_stage[6].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[26];
assign mem_data_clear[5] = `WP4R_PIPELINE.g_stage[6].g_ecc.u_stage.u_memory.g_func[0].u_decode.data[21];
assign mem_code_set[6] = `WP4R_PIPELINE.g_stage[7].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[27];
assign mem_data_clear[6] = `WP4R_PIPELINE.g_stage[7].g_ecc.u_stage.u_memory.g_func[0].u_decode.data[22];

wire probe_read_code;
wire probe_mem_rd_data;
wire probe_dec_codeword;
wire probe_detected;
wire probe_corrected;
wire probe_mem_cor;
wire probe_forward_active;
wire probe_in_valid;
assign probe_read_code = (fault_case == 4) ?
    `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.u_memory.read_code[20] :
    (fault_case == 8) ? `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.u_memory.read_code[22] : 1'b0;
assign probe_mem_rd_data = (fault_case == 4) ?
    `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.u_memory.u_mem.rd_data[20] :
    (fault_case == 8) ? `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.u_memory.u_mem.rd_data[22] : 1'b0;
assign probe_dec_codeword = (fault_case == 4) ?
    `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[20] :
    (fault_case == 8) ? `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[22] : 1'b0;
assign probe_detected = (fault_case == 4) ? mem_det[0] :
    (fault_case == 8) ? mem_det[1] : 1'b0;
assign probe_corrected = (fault_case == 4) ? mem_cor[0] :
    (fault_case == 8) ? mem_cor[1] : 1'b0;
assign probe_mem_cor = probe_corrected;
assign probe_forward_active = (fault_case == 4) ?
    `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.u_memory.forward_active :
    (fault_case == 8) ? `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.u_memory.forward_active : 1'b0;
assign probe_in_valid = (fault_case == 4) ?
    `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.in_valid :
    (fault_case == 8) ? `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.in_valid : 1'b0;

`define WP4R_ZERO_MEM(path, depth) \
    for (zi = 0; zi < depth; zi = zi + 1) path[zi] = '0;

// WP4-R batch 2 (task 11.4): zero every delay-storage array at time 0,
// modelling the power-up state of 7-series distributed RAM without INIT,
// so a stuck control reading never-written addresses does not see
// simulation-only X.  The qualification flow does not do this.
initial begin : g_zero_mem
    integer zi;
    `WP4R_ZERO_MEM(`WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.u_memory.u_mem.u.g_sync_distributed.mem, 128)
    `WP4R_ZERO_MEM(`WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.u_memory.u_mem.u.g_async_distributed.mem, 64)
    `WP4R_ZERO_MEM(`WP4R_PIPELINE.g_stage[3].g_ecc.u_stage.u_memory.u_mem.u.g_async_distributed.mem, 32)
    `WP4R_ZERO_MEM(`WP4R_PIPELINE.g_stage[4].g_ecc.u_stage.u_memory.u_mem.u.g_async_distributed.mem, 16)
    `WP4R_ZERO_MEM(`WP4R_PIPELINE.g_stage[5].g_ecc.u_stage.u_memory.u_mem.u.g_async_distributed.mem, 8)
    `WP4R_ZERO_MEM(`WP4R_PIPELINE.g_stage[6].g_ecc.u_stage.u_memory.u_mem.u.g_async_distributed.mem, 4)
    `WP4R_ZERO_MEM(`WP4R_PIPELINE.g_stage[7].g_ecc.u_stage.u_memory.u_mem.u.g_async_distributed.mem, 2)
    `WP4R_ZERO_MEM(`WP4R_PIPELINE.g_stage[8].g_tmr.u_stage.g_replica[0].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 1)
    `WP4R_ZERO_MEM(`WP4R_PIPELINE.g_stage[8].g_tmr.u_stage.g_replica[1].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 1)
    `WP4R_ZERO_MEM(`WP4R_PIPELINE.g_stage[8].g_tmr.u_stage.g_replica[2].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 1)
    $display("WP4R_FAULT_MEM_ZEROED");
end

initial begin
    for (stage = 0; stage < 10; stage = stage + 1) begin
        det_count[stage] = 0;
        cor_count[stage] = 0;
        unc_count[stage] = 0;
        det_xz_count[stage] = 0;
        cor_xz_count[stage] = 0;
        unc_xz_count[stage] = 0;
    end
    plus_ok = $value$plusargs("WP4R_CASE=%d", fault_case);
    probe_enable = $test$plusargs("WP4R_PROBE");
    if (!((fault_case >= 1 && fault_case <= 37) ||
          (fault_case >= 104 && fault_case <= 128 && ((fault_case - 104) % 4 == 0))))
        $fatal(1, "unsupported WP4R_CASE %0d", fault_case);
end

function automatic integer target_stage(input integer case_no);
    begin
        if (case_no <= 28)
            target_stage = ((case_no - 1) / 4) + 1;
        else if (case_no == 30)
            target_stage = 9;
        else if (case_no == 31)
            target_stage = 10;
        else if (case_no >= 104)
            target_stage = (case_no - 100) / 4;
        else if (case_no >= 36)
            target_stage = 3;
        else
            target_stage = 8;
    end
endfunction

function automatic integer criterion_kind(input integer case_no);
    begin
        if (case_no <= 28) begin
            if (((case_no - 1) % 4) == 3)
                criterion_kind = 2;
            else
                criterion_kind = 1;
        end else if (case_no <= 35) begin
            criterion_kind = 3;
        end else begin
            criterion_kind = 4;
        end
    end
endfunction

task automatic apply_fault;
    begin
        case (fault_case)
            1:  force `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.g_lane[1].u_lane.boundary_re[18] = 1'b1;
            2:  force `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.g_lane[4].u_lane.boundary_re[19] = 1'b1;
            3:  force `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.g_lane[2].u_lane.difference_re[8] = 1'b1;
            4:  force `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.u_memory.read_code[20] = 1'b1;
            5:  force `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.g_lane[1].u_lane.boundary_re[19] = 1'b1;
            6:  force `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.g_lane[4].u_lane.boundary_re[20] = 1'b1;
            7:  force `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.g_lane[2].u_lane.difference_re[9] = 1'b1;
            8:  force `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.u_memory.read_code[22] = 1'b1;
            9:  force `WP4R_PIPELINE.g_stage[3].g_ecc.u_stage.g_lane[1].u_lane.boundary_re[19] = 1'b1;
            10: force `WP4R_PIPELINE.g_stage[3].g_ecc.u_stage.g_lane[4].u_lane.boundary_re[20] = 1'b1;
            11: force `WP4R_PIPELINE.g_stage[3].g_ecc.u_stage.g_lane[2].u_lane.difference_re[10] = 1'b1;
            12: force `WP4R_PIPELINE.g_stage[3].g_ecc.u_stage.u_memory.read_code[23] = 1'b1;
            13: force `WP4R_PIPELINE.g_stage[4].g_ecc.u_stage.g_lane[1].u_lane.boundary_re[20] = 1'b1;
            14: force `WP4R_PIPELINE.g_stage[4].g_ecc.u_stage.g_lane[4].u_lane.boundary_re[21] = 1'b1;
            15: force `WP4R_PIPELINE.g_stage[4].g_ecc.u_stage.g_lane[2].u_lane.difference_re[10] = 1'b1;
            16: force `WP4R_PIPELINE.g_stage[4].g_ecc.u_stage.u_memory.read_code[24] = 1'b1;
            17: force `WP4R_PIPELINE.g_stage[5].g_ecc.u_stage.g_lane[1].u_lane.boundary_re[20] = 1'b1;
            18: force `WP4R_PIPELINE.g_stage[5].g_ecc.u_stage.g_lane[4].u_lane.boundary_re[21] = 1'b1;
            19: force `WP4R_PIPELINE.g_stage[5].g_ecc.u_stage.g_lane[2].u_lane.difference_re[11] = 1'b1;
            20: force `WP4R_PIPELINE.g_stage[5].g_ecc.u_stage.u_memory.read_code[25] = 1'b1;
            21: force `WP4R_PIPELINE.g_stage[6].g_ecc.u_stage.g_lane[1].u_lane.boundary_re[21] = 1'b1;
            22: force `WP4R_PIPELINE.g_stage[6].g_ecc.u_stage.g_lane[4].u_lane.boundary_re[22] = 1'b1;
            23: force `WP4R_PIPELINE.g_stage[6].g_ecc.u_stage.g_lane[2].u_lane.difference_re[11] = 1'b1;
            24: force `WP4R_PIPELINE.g_stage[6].g_ecc.u_stage.u_memory.read_code[26] = 1'b1;
            25: force `WP4R_PIPELINE.g_stage[7].g_ecc.u_stage.g_lane[1].u_lane.boundary_re[21] = 1'b1;
            26: force `WP4R_PIPELINE.g_stage[7].g_ecc.u_stage.g_lane[4].u_lane.boundary_re[22] = 1'b1;
            27: force `WP4R_PIPELINE.g_stage[7].g_ecc.u_stage.g_lane[2].u_lane.difference_re[12] = 1'b1;
            28: force `WP4R_PIPELINE.g_stage[7].g_ecc.u_stage.u_memory.read_code[27] = 1'b1;
            29: force `WP4R_PIPELINE.g_stage[8].g_tmr.u_stage.g_replica[1].g_sdf.u_copy.out_data[187] = 1'b1;
            30: force `WP4R_PIPELINE.g_stage[9].g_tmr.u_stage.g_replica[1].g_s9.u_copy.out_data[195] = 1'b1;
            31: force `WP4R_PIPELINE.g_stage[10].g_tmr.u_stage.g_replica[1].g_s10.u_copy.out_data[202] = 1'b1;
            32: force `WP4R_PIPELINE.g_stage[8].g_tmr.u_stage.g_replica[2].g_sdf.u_copy.u_control.phase = 1'b1;
            33: force `WP4R_PIPELINE.g_stage[8].g_tmr.u_stage.g_replica[2].g_sdf.u_copy.u_memory.rd_data[25] = 1'b1;
            34: force `WP4R_PIPELINE.g_stage[8].g_tmr.u_stage.g_replica[2].g_sdf.u_copy.g_lane[2].g_multiply.private_re[9] = 1'b1;
            35: force `WP4R_PIPELINE.g_stage[8].g_tmr.u_stage.g_replica[2].g_sdf.u_copy.g_lane[2].rotated_re[12] = 1'b1;
            36: force `WP4R_PIPELINE.g_stage[3].g_ecc.u_stage.u_control.phase_next[3] = 1'b1;
            37: force `WP4R_PIPELINE.g_stage[3].g_ecc.u_stage.g_rom.u_rom.coeff_re[9] = 1'b1;
            104: force `WP4R_PIPELINE.g_stage[1].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[20] = 1'b1;
            108: force `WP4R_PIPELINE.g_stage[2].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[22] = 1'b1;
            112: force `WP4R_PIPELINE.g_stage[3].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[23] = 1'b1;
            116: force `WP4R_PIPELINE.g_stage[4].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[24] = 1'b1;
            120: force `WP4R_PIPELINE.g_stage[5].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[25] = 1'b1;
            124: force `WP4R_PIPELINE.g_stage[6].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[26] = 1'b1;
            128: force `WP4R_PIPELINE.g_stage[7].g_ecc.u_stage.u_memory.g_func[0].u_decode.codeword[27] = 1'b1;
            default: $fatal(1, "unsupported WP4R_CASE %0d", fault_case);
        endcase
    end
endtask

initial begin
    wait (tb.dut.kernel_rst === 1'b1);
    wait (tb.dut.kernel_rst === 1'b0);
    apply_fault;
    applied = 1;
    $display("WP4R_FAULT_APPLIED case=%0d", fault_case);
end

always @(posedge tb.clk) begin
    if (tb.dut.kernel_rst === 1'b0 && !window_ended) begin
        if (tb.dut.d_in_valid === 1'b1 && $isunknown(tb.dut.d_in_data)) begin
            window_ended = 1;
            for (stage = 0; stage < 10; stage = stage + 1) begin
                $display("WP4R_FAULT_COUNTS case=%0d stage=%0d det=%0d cor=%0d unc=%0d",
                         fault_case, stage + 1, det_count[stage], cor_count[stage],
                         unc_count[stage]);
                $display("WP4R_FAULT_XZ_COUNTS case=%0d stage=%0d det_xz=%0d cor_xz=%0d unc_xz=%0d",
                         fault_case, stage + 1, det_xz_count[stage],
                         cor_xz_count[stage], unc_xz_count[stage]);
            end
            $display("WP4R_FAULT_MEMORY case=%0d corrected=%0d detected_uncorrected=%0d codeword_set_data_clear=%0d",
                     fault_case, secded_cor_count, secded_uncorr_count,
                     secded_flip_count);
        end else begin
            for (stage = 0; stage < 10; stage = stage + 1) begin
                if (tb.dut.k_det[stage] === 1'b1)
                    det_count[stage] = det_count[stage] + 1;
                else if (tb.dut.k_det[stage] !== 1'b0)
                    det_xz_count[stage] = det_xz_count[stage] + 1;
                if (tb.dut.k_cor[stage] === 1'b1)
                    cor_count[stage] = cor_count[stage] + 1;
                else if (tb.dut.k_cor[stage] !== 1'b0)
                    cor_xz_count[stage] = cor_xz_count[stage] + 1;
                if (tb.dut.k_unc[stage] === 1'b1)
                    unc_count[stage] = unc_count[stage] + 1;
                else if (tb.dut.k_unc[stage] !== 1'b0)
                    unc_xz_count[stage] = unc_xz_count[stage] + 1;
            end
            if (criterion_kind(fault_case) == 2) begin
                if (mem_cor[target_stage(fault_case)-1] === 1'b1)
                    secded_cor_count = secded_cor_count + 1;
                if (mem_det[target_stage(fault_case)-1] === 1'b1 &&
                    mem_cor[target_stage(fault_case)-1] === 1'b0)
                    secded_uncorr_count = secded_uncorr_count + 1;
                if (mem_code_set[target_stage(fault_case)-1] === 1'b1 &&
                    mem_data_clear[target_stage(fault_case)-1] === 1'b0)
                    secded_flip_count = secded_flip_count + 1;
            end
            if (probe_enable && (fault_case == 4 || fault_case == 8)) begin
                if (!probe_started && probe_in_valid === 1'b1) begin
                    probe_started = 1;
                    $display("WP4R_PROBE case=%0d stage=%0d beat=%0d read_code=%b mem_rd_data=%b dec_codeword=%b detected=%b corrected=%b mem_cor=%b forward_active=%b",
                             fault_case, target_stage(fault_case), 0,
                             probe_read_code, probe_mem_rd_data, probe_dec_codeword,
                             probe_detected, probe_corrected, probe_mem_cor,
                             probe_forward_active);
                end else if (probe_started && probe_count < 31) begin
                    probe_count = probe_count + 1;
                    $display("WP4R_PROBE case=%0d stage=%0d beat=%0d read_code=%b mem_rd_data=%b dec_codeword=%b detected=%b corrected=%b mem_cor=%b forward_active=%b",
                             fault_case, target_stage(fault_case), probe_count,
                             probe_read_code, probe_mem_rd_data, probe_dec_codeword,
                             probe_detected, probe_corrected, probe_mem_cor,
                             probe_forward_active);
                end
            end
        end
    end
end

function automatic integer flags_expected;
    integer s;
    integer target;
    integer kind;
    begin
        target = target_stage(fault_case) - 1;
        kind = criterion_kind(fault_case);
        flags_expected = 1;
        if (kind == 1) begin
            if (cor_count[target] == 0 || unc_count[target] != 0)
                flags_expected = 0;
            for (s = 0; s < 10; s = s + 1) begin
                if (s != target &&
                    (det_count[s] != 0 || cor_count[s] != 0 || unc_count[s] != 0 ||
                     det_xz_count[s] != 0 || cor_xz_count[s] != 0 ||
                     unc_xz_count[s] != 0))
                    flags_expected = 0;
            end
        end else if (kind == 2) begin
            if (secded_cor_count == 0 || secded_uncorr_count != 0 ||
                secded_flip_count == 0)
                flags_expected = 0;
            for (s = 0; s < 10; s = s + 1) begin
                if (det_count[s] != 0 || cor_count[s] != 0 || unc_count[s] != 0)
                    flags_expected = 0;
                if (s != target &&
                    (det_xz_count[s] != 0 || cor_xz_count[s] != 0 ||
                     unc_xz_count[s] != 0))
                    flags_expected = 0;
            end
        end else if (kind == 3) begin
            if (det_count[target] == 0 || cor_count[target] == 0 ||
                unc_count[target] != 0)
                flags_expected = 0;
            for (s = 0; s < 10; s = s + 1) begin
                if (s != target &&
                    (det_count[s] != 0 || cor_count[s] != 0 || unc_count[s] != 0 ||
                     det_xz_count[s] != 0 || cor_xz_count[s] != 0 ||
                     unc_xz_count[s] != 0))
                    flags_expected = 0;
            end
        end
    end
endfunction

final begin
    if (criterion_kind(fault_case) == 4) begin
        $display("WP4R_FAULT_RESULT case=%0d applied=%0d window_ended=%0d output_bit_exact=%0d result=RECORD_ONLY",
                 fault_case, applied, window_ended, tb.errors == 0);
    end else if (applied && window_ended && tb.errors == 0 && flags_expected()) begin
        $display("WP4R_FAULT_RESULT case=%0d applied=%0d window_ended=%0d output_bit_exact=1 flags_expected=1 result=PASS",
                 fault_case, applied, window_ended);
    end else begin
        $display("WP4R_FAULT_RESULT case=%0d applied=%0d window_ended=%0d output_bit_exact=%0d flags_expected=%0d result=FAIL",
                 fault_case, applied, window_ended, tb.errors == 0, flags_expected());
    end
end
endmodule
