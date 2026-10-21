`timescale 1ns/1ps
module wp4r_stream_monitor #(
    parameter integer ARCH = 5,
    parameter integer DATA_W = 128
)(
    input wire clk,
    input wire kernel_rst,
    input wire in_valid,
    input wire [DATA_W-1:0] input_data,
    input wire [31:0] out_commit,
    input wire [9:0] det,
    input wire [9:0] cor,
    input wire [9:0] unc
);
integer observed_cycles = 0;
integer unknown_input_ns = -1;
integer committed_at_end = -1;
integer det_status [0:9];
integer cor_status [0:9];
integer unc_status [0:9];
reg window_ended = 0;
reg window_failed = 0;
reg window_passed = 0;
reg fail_reported = 0;
integer init_stage;
initial begin
    for (init_stage = 0; init_stage < 10; init_stage = init_stage + 1) begin
        det_status[init_stage] = 0;
        cor_status[init_stage] = 0;
        unc_status[init_stage] = 0;
    end
end

function automatic string status_text(input integer status);
    begin
        case (status)
            0: status_text = "0";
            1: status_text = "1";
            default: status_text = "X";
        endcase
    end
endfunction

always @(posedge clk) begin
    if (kernel_rst === 1'b1) begin
        observed_cycles = 0;
        unknown_input_ns = -1;
        committed_at_end = -1;
    end else if (kernel_rst === 1'b0 && !window_ended &&
                 (ARCH == 5 || ARCH == 6 || ARCH == 3 || ARCH == 2 ||
                  ARCH == 10 || ARCH == 11)) begin
        if (in_valid === 1'b1 && $isunknown(input_data)) begin
            window_ended = 1;
            unknown_input_ns = $time;
            committed_at_end = out_commit;
            $display("WP4R_FLAG_WINDOW arch=%0d cycles=%0d unknown_input_ns=%0d out_commit=%0d",
                     ARCH, observed_cycles, unknown_input_ns, committed_at_end);
            for (integer stage = 0; stage < 10; stage = stage + 1) begin
                $display("WP4R_FLAG_STATUS arch=%0d stage=%0d det=%s cor=%s unc=%s",
                         ARCH, stage + 1, status_text(det_status[stage]),
                         status_text(cor_status[stage]), status_text(unc_status[stage]));
            end
            if (committed_at_end < 2048) begin
                window_failed = 1;
                fail_reported = 1;
                $display("WP4R_NO_FAULT_FLAGS: FAIL arch=%0d insufficient_committed_output=%0d",
                         ARCH, committed_at_end);
                $fatal(1, "WP4-R flag window does not cover eight output frames");
            end
            if (window_failed) begin
                fail_reported = 1;
                $display("WP4R_NO_FAULT_FLAGS: FAIL arch=%0d", ARCH);
                $fatal(1, "WP4-R nonzero or unknown event in the known-input window");
            end else if (!$test$plusargs("WP4R_FAULT")) begin
                window_passed = 1;
                $display("WP4R_NO_FAULT_FLAGS: PASS arch=%0d cycles=%0d unknown_input_ns=%0d out_commit=%0d",
                         ARCH, observed_cycles, unknown_input_ns, committed_at_end);
            end
        end else begin
            observed_cycles = observed_cycles + 1;
            if (!$test$plusargs("WP4R_FAULT")) begin
                for (integer stage = 0; stage < 10; stage = stage + 1) begin
                    if (det[stage] === 1'b1) begin
                        det_status[stage] = 1;
                        window_failed = 1;
                    end else if (det[stage] !== 1'b0 && det_status[stage] != 1) begin
                        det_status[stage] = 2;
                        window_failed = 1;
                    end
                    if (cor[stage] === 1'b1) begin
                        cor_status[stage] = 1;
                        window_failed = 1;
                    end else if (cor[stage] !== 1'b0 && cor_status[stage] != 1) begin
                        cor_status[stage] = 2;
                        window_failed = 1;
                    end
                    if (unc[stage] === 1'b1) begin
                        unc_status[stage] = 1;
                        window_failed = 1;
                    end else if (unc[stage] !== 1'b0 && unc_status[stage] != 1) begin
                        unc_status[stage] = 2;
                        window_failed = 1;
                    end
                end
            end
        end
    end
end

final begin
    if ((ARCH == 5 || ARCH == 6 || ARCH == 3 || ARCH == 2 ||
         ARCH == 10 || ARCH == 11) &&
        !$test$plusargs("WP4R_FAULT") && !window_passed && !fail_reported) begin
        $display("WP4R_NO_FAULT_FLAGS: FAIL arch=%0d window_ended=%b cycles=%0d",
                 ARCH, window_ended, observed_cycles);
        $fatal(1, "WP4-R flag window did not produce PASS");
    end
end
endmodule

`ifndef WP4R_NO_BIND
bind paper_fft_stream_engine wp4r_stream_monitor #(
    .ARCH(PAPER_ARCH), .DATA_W(IN_BEAT_W)) u_wp4r_monitor (
    .clk(clk), .kernel_rst(kernel_rst), .in_valid(d_in_valid),
    .input_data(d_in_data), .out_commit(out_commit),
    .det(k_det), .cor(k_cor), .unc(k_unc));
`endif
