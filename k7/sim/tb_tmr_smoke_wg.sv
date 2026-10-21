`timescale 1ns/1ps
module tb_tmr_smoke_wg;
`include "tb_arch_sel.svh"
tb_paper_stream_golden_wg tb();
integer fault=-1, unused, i, failures=0;
integer counts[0:9];
integer checked_cycles=0, unknown_input_ns=-1, committed_at_end=-1, window_errors=0;
reg window_ended=0;
initial begin for(i=0;i<10;i=i+1) counts[i]=0; end
generate
if(ARCH_SEL==7) begin : g_s2h
    initial begin
        unused=$value$plusargs("TMR_FAULT=%d",fault);
        case(fault)
            0: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[1].u_stage.rep_data[1][135] = 1'b1;
            1: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[4].u_stage.rep_data[1][157] = 1'b1;
            2: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[8].u_stage.rep_data[1][187] = 1'b1;
            3: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[9].u_stage.rep_data[1][195] = 1'b1;
            4: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[10].u_stage.rep_data[1][202] = 1'b1;
            5: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[8].u_stage.g_rom.g_replica[2].g_shared.u_copy.g_sdf.g_cross.u_cross.c1_re[12] = 1'b0;
            6: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[3].u_stage.g_shared_memory.g_mem[1].u_shared_memory.read_code[2] = 1'b1;
            7: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[6].u_stage.g_shared_memory.g_mem[2].u_shared_memory.write_code[12] = 1'b0;
            8: force tb.dut.g_k.u_kernel.u_pipeline.g_stage[8].u_stage.g_shared_memory.g_mem[0].u_shared_memory.q_data1[25] = 1'b1;
            default: $fatal(1,"invalid smoke case");
        endcase
    end
end
endgenerate
always @(posedge tb.clk) begin
    if(tb.dut.kernel_rst===1'b0 && !window_ended) begin
        if(tb.dut.d_in_valid===1'b1 && $isunknown(tb.dut.d_in_data)) begin
            window_ended=1; unknown_input_ns=$time; committed_at_end=tb.dut.out_commit;
            $display("TMR_SMOKE_WINDOW arch=%0d case=%0d cycles=%0d unknown_input_ns=%0d out_commit=%0d",ARCH_SEL,fault,checked_cycles,unknown_input_ns,committed_at_end);
            if(committed_at_end<2048) begin window_errors=window_errors+1; $fatal(1,"smoke window lacks eight output frames"); end
        end else begin
            checked_cycles=checked_cycles+1;
            for(integer s=0;s<10;s=s+1) begin
                if(tb.dut.g_k.u_kernel.stage_mismatch[s]===1'b1) counts[s]=counts[s]+1;
                else if(tb.dut.g_k.u_kernel.stage_mismatch[s]!==1'b0) begin
                    window_errors=window_errors+1;
                    $display("TMR_SMOKE_FLAG_FAIL stage=%0d value=X raw=%b",s+1,tb.dut.g_k.u_kernel.stage_mismatch[s]);
                    $fatal(1,"unknown mismatch flag inside smoke window");
                end
            end
        end
    end
end
integer target_stage;
final begin
    case(fault)
        0:target_stage=1; 1:target_stage=4; 2:target_stage=8;
        3:target_stage=9; 4:target_stage=10; 5:target_stage=8; 8:target_stage=8;
        default:target_stage=0;
    endcase
    failures=tb.errors+window_errors;
    if(!window_ended || committed_at_end<2048) failures=failures+1;
    for(i=0;i<10;i=i+1) begin
        if(target_stage==i+1) begin if(counts[i]==0) failures=failures+1; end
        else if(counts[i]!=0) failures=failures+1;
    end
    $display("TMR_SMOKE_RESULT arch=%0d case=%0d errors=%0d counts=%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",ARCH_SEL,fault,failures,counts[0],counts[1],counts[2],counts[3],counts[4],counts[5],counts[6],counts[7],counts[8],counts[9]);
    if(failures==0) $display("TB_TMR_SMOKE: PASS"); else $display("TB_TMR_SMOKE: FAIL");
end
endmodule
