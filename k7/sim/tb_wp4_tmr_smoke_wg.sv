`timescale 1ns/1ps
module tb_wp4_tmr_smoke;
`include "tb_arch_sel.svh"
tb_paper_stream_golden_wg tb();
integer fault=-1,unused,target_stage=0,checked_cycles=0,unknown_input_ns=-1,committed_at_end=-1;
integer counts[0:9];
integer window_errors=0,failures=0;
reg applied=0,window_ended=0;
initial begin for(integer i=0;i<10;i=i+1) counts[i]=0; end
generate if(ARCH_SEL==8) begin : g_p2h
    initial begin
        unused=$value$plusargs("TMR_FAULT=%d",fault);
        if(fault<0 || fault>7) $fatal(1,"invalid WP4 smoke case");
        wait(tb.dut.kernel_rst===1'b1);
        wait(tb.dut.kernel_rst===1'b0);
        case(fault)
            0: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[1].u_stage.g_replica[1].u_copy.out_data[135]=1'b1;
            1: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[4].u_stage.g_replica[1].u_copy.out_data[157]=1'b1;
            2: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[8].u_stage.g_replica[1].u_copy.out_data[187]=1'b1;
            3: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[9].u_stage.g_replica[1].u_copy.out_data[195]=1'b1;
            4: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[10].u_stage.g_replica[1].u_copy.out_data[202]=1'b1;
            5: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[7].u_stage.g_replica[2].u_copy.g_lanes.g_lane[1].u_lane.mul_out_re[12]=1'b0;
            6: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[3].u_stage.g_shared_memory.g_mem[1].u_shared_memory.read_code[2]=1'b1;
            7: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[6].u_stage.g_shared_memory.g_mem[2].u_shared_memory.write_code[12]=1'b0;
        endcase
        applied=1;
    end
    always @(posedge tb.clk) begin
        if(tb.dut.kernel_rst===1'b0 && !window_ended) begin
            if(tb.dut.d_in_valid===1'b1 && $isunknown(tb.dut.d_in_data)) begin
                window_ended=1; unknown_input_ns=$time; committed_at_end=tb.dut.out_commit;
                if(committed_at_end<2048) begin
                    window_errors=window_errors+1;
                    $fatal(1,"WP4 smoke window lacks eight output frames");
                end
                case(fault)
                    0: release tb.dut.g_k.u_kernel.u_pipeline.g_stage[1].u_stage.g_replica[1].u_copy.out_data[135];
                    1: release tb.dut.g_k.u_kernel.u_pipeline.g_stage[4].u_stage.g_replica[1].u_copy.out_data[157];
                    2: release tb.dut.g_k.u_kernel.u_pipeline.g_stage[8].u_stage.g_replica[1].u_copy.out_data[187];
                    3: release tb.dut.g_k.u_kernel.u_pipeline.g_stage[9].u_stage.g_replica[1].u_copy.out_data[195];
                    4: release tb.dut.g_k.u_kernel.u_pipeline.g_stage[10].u_stage.g_replica[1].u_copy.out_data[202];
                    5: release tb.dut.g_k.u_kernel.u_pipeline.g_stage[7].u_stage.g_replica[2].u_copy.g_lanes.g_lane[1].u_lane.mul_out_re[12];
                    6: release tb.dut.g_k.u_kernel.u_pipeline.g_stage[3].u_stage.g_shared_memory.g_mem[1].u_shared_memory.read_code[2];
                    7: release tb.dut.g_k.u_kernel.u_pipeline.g_stage[6].u_stage.g_shared_memory.g_mem[2].u_shared_memory.write_code[12];
                endcase
                $display("WP4_SMOKE_WINDOW case=%0d cycles=%0d unknown_input_ns=%0d out_commit=%0d",fault,checked_cycles,unknown_input_ns,committed_at_end);
            end else begin
                checked_cycles=checked_cycles+1;
                for(integer i=0;i<10;i=i+1) begin
                    if(tb.dut.k_det[i]===1'b1) counts[i]=counts[i]+1;
                    else if(tb.dut.k_det[i]!==1'b0) begin
                        window_errors=window_errors+1;
                        $fatal(1,"unknown WP4 smoke flag inside known-input window");
                    end
                end
            end
        end
    end
end else begin : g_wrong_arch
    initial $fatal(1,"WP4 smoke requires architecture8");
end endgenerate
final begin
    case(fault)
        0:target_stage=1; 1:target_stage=4; 2:target_stage=8;
        3:target_stage=9; 4:target_stage=10; 5:target_stage=7;
        default:target_stage=0;
    endcase
    failures=window_errors;
    if(!applied || !window_ended || committed_at_end<2048) failures=failures+1;
    for(integer i=0;i<10;i=i+1) begin
        if(i+1==target_stage) begin if(counts[i]==0) failures=failures+1; end
        else if(counts[i]!=0) failures=failures+1;
    end
    $display("WP4_SMOKE_RESULT case=%0d errors=%0d applied=%0d counts=%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",fault,failures,applied,counts[0],counts[1],counts[2],counts[3],counts[4],counts[5],counts[6],counts[7],counts[8],counts[9]);
    if(failures==0) $display("TB_WP4_TMR_SMOKE: PASS"); else $display("TB_WP4_TMR_SMOKE: FAIL");
end
endmodule
