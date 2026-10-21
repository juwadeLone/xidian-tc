`timescale 1ns/1ps
module wp2_stream_monitor #(parameter integer ARCH=0, DATA_W=128)(
    input wire clk, kernel_rst, in_valid, out_valid,
    input wire [DATA_W-1:0] input_data,
    input wire [31:0] out_commit,
    input wire [9:0] flags
);
integer cycles=0, first_input=-1, first_output=-1, observed_cycles=0;
integer unknown_input_ns=-1, committed_at_end=-1;
reg window_ended=0, window_failed=0, window_passed=0;
function automatic [7:0] scalar_value(input value);
    if(value===1'b0) scalar_value="0";
    else if(value===1'b1) scalar_value="1";
    else scalar_value="X";
endfunction
always @(posedge clk) begin
    if(kernel_rst===1'b1) begin
        cycles=0; first_input=-1; first_output=-1;
    end else if(kernel_rst===1'b0) begin
        cycles=cycles+1;
        if(in_valid && first_input<0) first_input=cycles;
        if(out_valid && first_output<0) begin
            first_output=cycles;
            $display("WP2_LATENCY arch=%0d cycles=%0d",ARCH,first_output-first_input);
            if(ARCH==0 && first_output-first_input!=289) $fatal(1,"S0 latency changed");
        end
        if(!window_ended && (ARCH==2 || ARCH==7)) begin
            if(in_valid===1'b1 && $isunknown(input_data)) begin
                window_ended=1;
                unknown_input_ns=$time;
                committed_at_end=out_commit;
                $display("WP2_FLAG_WINDOW arch=%0d cycles=%0d unknown_input_ns=%0d out_commit=%0d",ARCH,observed_cycles,unknown_input_ns,committed_at_end);
                if(out_commit<2048) begin
                    window_failed=1;
                    $display("WP2_NO_FAULT_FLAGS: FAIL arch=%0d insufficient_committed_output=%0d",ARCH,out_commit);
                    $fatal(1,"flag window does not cover eight output frames");
                end
                if(!window_failed && !$test$plusargs("TMR_FAULT")) begin
                    window_passed=1;
                    $display("WP2_NO_FAULT_FLAGS: PASS arch=%0d cycles=%0d unknown_input_ns=%0d out_commit=%0d",ARCH,observed_cycles,unknown_input_ns,committed_at_end);
                end
            end else begin
                observed_cycles=observed_cycles+1;
                if(!$test$plusargs("TMR_FAULT") && flags!==10'd0) begin
                    window_failed=1;
                    $display("WP2_NO_FAULT_FLAGS: FAIL arch=%0d time_ns=%0d",ARCH,$time);
                    for(integer stage=0;stage<10;stage=stage+1)
                        $display("WP2_FLAG_STAGE stage=%0d value=%s raw=%b",stage+1,scalar_value(flags[stage]),flags[stage]);
                    $fatal(1,"nonzero or unknown TMR flag in the known-input window");
                end
            end
        end
    end
end
generate
if(ARCH==7) begin : g_forward_checks
    for(genvar s=1;s<=8;s=s+1) begin : g_stage
        for(genvar lane=0;lane<4;lane=lane+1) begin : g_lane
            integer forward_count=0, unknown_forward=0;
            always @(posedge clk) begin
                if(kernel_rst===1'b0 && !window_ended && !(in_valid===1'b1 && $isunknown(input_data))) begin
                    if(g_k.u_kernel.u_pipeline.g_stage[s].u_stage.g_shared_memory.g_mem[lane].u_shared_memory.forward_active===1'b1)
                        forward_count=forward_count+1;
                    else if(g_k.u_kernel.u_pipeline.g_stage[s].u_stage.g_shared_memory.g_mem[lane].u_shared_memory.forward_active!==1'b0) begin
                        unknown_forward=unknown_forward+1;
                        $display("WP2_FORWARD_FAIL stage=%0d lane=%0d value=X",s,lane);
                        $fatal(1,"unknown forwarding inside check window");
                    end
                end
            end
            final begin
                $display("WP2_FORWARD stage=%0d lane=%0d count=%0d unknown=%0d",s,lane,forward_count,unknown_forward);
                if((s<8 && forward_count!=0) || (s==8 && forward_count==0) || unknown_forward!=0)
                    $display("WP2_FORWARD_FAIL stage=%0d lane=%0d",s,lane);
            end
        end
    end
end
endgenerate
final begin
    if((ARCH==2 || ARCH==7) && !$test$plusargs("TMR_FAULT") && !window_passed)
        $display("WP2_NO_FAULT_FLAGS: FAIL arch=%0d window_ended=%b failed=%b cycles=%0d",ARCH,window_ended,window_failed,observed_cycles);
end
endmodule
bind paper_fft_stream_engine wp2_stream_monitor #(.ARCH(PAPER_ARCH), .DATA_W(IN_BEAT_W)) u_wp2_monitor(
    .clk(clk), .kernel_rst(kernel_rst), .in_valid(d_in_valid), .out_valid(d_out_valid),
    .input_data(d_in_data), .out_commit(out_commit), .flags(k_det));
