`timescale 1ns/1ps

module tb_wp4r_p2_fault_wg;
`include "tb_arch_sel.svh"
`define WP4R_P2_PIPELINE tb.dut.g_k.u_kernel.u_pipeline

tb_paper_stream_golden_wg tb();

integer fault_case = 0;
integer plus_ok;
integer det_count [0:9];
integer cor_count [0:9];
integer unc_count [0:9];
integer det_xz_count [0:9];
integer cor_xz_count [0:9];
integer unc_xz_count [0:9];
integer stage;
reg applied = 0;
reg window_ended = 0;

`define WP4R_ZERO_MEM(path, depth) \
    for (zi = 0; zi < depth; zi = zi + 1) path[zi] = '0;

// WP4-R batch 2 (task 11.4): zero every delay-storage array at time 0,
// modelling the power-up state of 7-series distributed RAM without INIT.
// The qualification flow does not do this.
initial begin : g_zero_mem
    integer zi;
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[1].u_stage.g_replica[0].g_sdf.u_copy.u_memory.u.g_sync_distributed.mem, 128)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[1].u_stage.g_replica[1].g_sdf.u_copy.u_memory.u.g_sync_distributed.mem, 128)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[1].u_stage.g_replica[2].g_sdf.u_copy.u_memory.u.g_sync_distributed.mem, 128)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[2].u_stage.g_replica[0].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 64)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[2].u_stage.g_replica[1].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 64)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[2].u_stage.g_replica[2].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 64)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[3].u_stage.g_replica[0].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 32)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[3].u_stage.g_replica[1].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 32)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[3].u_stage.g_replica[2].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 32)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[4].u_stage.g_replica[0].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 16)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[4].u_stage.g_replica[1].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 16)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[4].u_stage.g_replica[2].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 16)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[5].u_stage.g_replica[0].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 8)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[5].u_stage.g_replica[1].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 8)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[5].u_stage.g_replica[2].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 8)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[6].u_stage.g_replica[0].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 4)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[6].u_stage.g_replica[1].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 4)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[6].u_stage.g_replica[2].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 4)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[7].u_stage.g_replica[0].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 2)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[7].u_stage.g_replica[1].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 2)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[7].u_stage.g_replica[2].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 2)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[8].u_stage.g_replica[0].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 1)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[8].u_stage.g_replica[1].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 1)
    `WP4R_ZERO_MEM(`WP4R_P2_PIPELINE.g_stage[8].u_stage.g_replica[2].g_sdf.u_copy.u_memory.u.g_async_distributed.mem, 1)
    $display("WP4R_P2_FAULT_MEM_ZEROED");
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
    if (fault_case < 1 || fault_case > 18)
        $fatal(1, "WP4R_CASE must be in 1..18, got %0d", fault_case);
end

function automatic integer target_stage(input integer case_no);
    begin
        if (case_no <= 10)
            target_stage = case_no;
        else if (case_no <= 14)
            target_stage = 3;
        else
            target_stage = 8;
    end
endfunction

task automatic apply_fault;
    begin
        case (fault_case)
            1:  force `WP4R_P2_PIPELINE.g_stage[1].u_stage.g_replica[1].g_sdf.u_copy.out_data[135] = 1'b1;
            2:  force `WP4R_P2_PIPELINE.g_stage[2].u_stage.g_replica[1].g_sdf.u_copy.out_data[142] = 1'b1;
            3:  force `WP4R_P2_PIPELINE.g_stage[3].u_stage.g_replica[1].g_sdf.u_copy.out_data[150] = 1'b1;
            4:  force `WP4R_P2_PIPELINE.g_stage[4].u_stage.g_replica[1].g_sdf.u_copy.out_data[157] = 1'b1;
            5:  force `WP4R_P2_PIPELINE.g_stage[5].u_stage.g_replica[1].g_sdf.u_copy.out_data[165] = 1'b1;
            6:  force `WP4R_P2_PIPELINE.g_stage[6].u_stage.g_replica[1].g_sdf.u_copy.out_data[172] = 1'b1;
            7:  force `WP4R_P2_PIPELINE.g_stage[7].u_stage.g_replica[1].g_sdf.u_copy.out_data[180] = 1'b1;
            8:  force `WP4R_P2_PIPELINE.g_stage[8].u_stage.g_replica[1].g_sdf.u_copy.out_data[187] = 1'b1;
            9:  force `WP4R_P2_PIPELINE.g_stage[9].u_stage.g_replica[1].g_s9.u_copy.out_data[195] = 1'b1;
            10: force `WP4R_P2_PIPELINE.g_stage[10].u_stage.g_replica[1].g_s10.u_copy.out_data[202] = 1'b1;
            11: force `WP4R_P2_PIPELINE.g_stage[3].u_stage.g_replica[2].g_sdf.u_copy.u_control.phase_next[3] = 1'b1;
            12: force `WP4R_P2_PIPELINE.g_stage[3].u_stage.g_replica[2].g_sdf.u_copy.u_memory.rd_data[30] = 1'b1;
            13: force `WP4R_P2_PIPELINE.g_stage[3].u_stage.g_replica[2].g_sdf.u_copy.g_shared_rom.coeff_re[9] = 1'b1;
            14: force `WP4R_P2_PIPELINE.g_stage[3].u_stage.g_replica[2].g_sdf.u_copy.g_lane[2].rotated_re[10] = 1'b1;
            15: force `WP4R_P2_PIPELINE.g_stage[8].u_stage.g_replica[2].g_sdf.u_copy.u_control.phase_next[0] = 1'b1;
            16: force `WP4R_P2_PIPELINE.g_stage[8].u_stage.g_replica[2].g_sdf.u_copy.u_memory.rd_data[37] = 1'b1;
            17: force `WP4R_P2_PIPELINE.g_stage[8].u_stage.g_replica[2].g_sdf.u_copy.g_lane[2].g_multiply.private_re[9] = 1'b1;
            18: force `WP4R_P2_PIPELINE.g_stage[8].u_stage.g_replica[2].g_sdf.u_copy.g_lane[2].rotated_re[12] = 1'b1;
            default: $fatal(1, "unsupported WP4R_CASE %0d", fault_case);
        endcase
    end
endtask

initial begin
    wait (tb.dut.kernel_rst === 1'b1);
    wait (tb.dut.kernel_rst === 1'b0);
    apply_fault;
    applied = 1;
    $display("WP4R_P2_FAULT_APPLIED case=%0d", fault_case);
end

always @(posedge tb.clk) begin
    if (tb.dut.kernel_rst === 1'b0 && !window_ended) begin
        if (tb.dut.d_in_valid === 1'b1 && $isunknown(tb.dut.d_in_data)) begin
            window_ended = 1;
            for (stage = 0; stage < 10; stage = stage + 1) begin
                $display("WP4R_P2_FAULT_COUNTS case=%0d stage=%0d det=%0d cor=%0d unc=%0d",
                         fault_case, stage + 1, det_count[stage], cor_count[stage],
                         unc_count[stage]);
                $display("WP4R_P2_FAULT_XZ_COUNTS case=%0d stage=%0d det_xz=%0d cor_xz=%0d unc_xz=%0d",
                         fault_case, stage + 1, det_xz_count[stage],
                         cor_xz_count[stage], unc_xz_count[stage]);
            end
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
        end
    end
end

function automatic integer flags_expected;
    integer s;
    integer target;
    begin
        target = target_stage(fault_case) - 1;
        flags_expected = 1;
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
endfunction

final begin
    if (applied && window_ended && tb.errors == 0 && flags_expected()) begin
        $display("WP4R_P2_FAULT_RESULT case=%0d applied=%0d window_ended=%0d output_bit_exact=1 flags_expected=1 result=PASS",
                 fault_case, applied, window_ended);
    end else begin
        $display("WP4R_P2_FAULT_RESULT case=%0d applied=%0d window_ended=%0d output_bit_exact=%0d flags_expected=%0d result=FAIL",
                 fault_case, applied, window_ended, tb.errors == 0, flags_expected());
    end
end
endmodule
