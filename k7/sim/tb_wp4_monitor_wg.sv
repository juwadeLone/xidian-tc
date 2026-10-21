`timescale 1ns/1ps
module wp4_stream_monitor #(parameter integer ARCH=0,DATA_W=128)(
    input wire clk,kernel_rst,in_valid,out_valid,
    input wire [DATA_W-1:0] input_data,
    input wire [31:0] out_commit,
    input wire [9:0] flags
);
integer cycles=0,first_input=-1,first_output=-1,observed_cycles=0;
integer unknown_input_ns=-1,committed_at_end=-1;
reg window_ended=0,window_failed=0,window_passed=0;
always @(posedge clk) begin
    if(kernel_rst===1'b1) begin
        cycles=0; first_input=-1; first_output=-1;
    end else if(kernel_rst===1'b0) begin
        cycles=cycles+1;
        if(in_valid===1'b1 && first_input<0) first_input=cycles;
        if(out_valid===1'b1 && first_output<0) begin
            first_output=cycles;
            $display("WP4_LATENCY arch=%0d cycles=%0d",ARCH,first_output-first_input);
        end
        if(ARCH==8 && !window_ended) begin
            if(in_valid===1'b1 && $isunknown(input_data)) begin
                window_ended=1; unknown_input_ns=$time; committed_at_end=out_commit;
                $display("WP4_FLAG_WINDOW arch=%0d cycles=%0d unknown_input_ns=%0d out_commit=%0d",ARCH,observed_cycles,unknown_input_ns,committed_at_end);
                if(out_commit<2048) begin
                    window_failed=1;
                    $fatal(1,"WP4 flag window does not cover eight output frames");
                end
                if(!window_failed && !$test$plusargs("TMR_FAULT")) begin
                    window_passed=1;
                    $display("WP4_NO_FAULT_FLAGS: PASS arch=%0d cycles=%0d unknown_input_ns=%0d out_commit=%0d",ARCH,observed_cycles,unknown_input_ns,committed_at_end);
                end
            end else begin
                observed_cycles=observed_cycles+1;
                if(!$test$plusargs("TMR_FAULT") && flags!==10'd0) begin
                    window_failed=1;
                    $display("WP4_NO_FAULT_FLAGS: FAIL arch=%0d flags=%b time_ns=%0d",ARCH,flags,$time);
                    $fatal(1,"WP4 nonzero or unknown flag in known-input window");
                end
            end
        end
    end
end
final begin
    if(ARCH==8 && !$test$plusargs("TMR_FAULT") && !window_passed)
        $display("WP4_NO_FAULT_FLAGS: FAIL arch=%0d window_ended=%b failed=%b cycles=%0d",ARCH,window_ended,window_failed,observed_cycles);
end
endmodule
bind paper_fft_stream_engine wp4_stream_monitor #(.ARCH(PAPER_ARCH),.DATA_W(IN_BEAT_W)) u_wp4_monitor(
    .clk(clk),.kernel_rst(kernel_rst),.in_valid(d_in_valid),.out_valid(d_out_valid),
    .input_data(d_in_data),.out_commit(out_commit),.flags(k_det));
