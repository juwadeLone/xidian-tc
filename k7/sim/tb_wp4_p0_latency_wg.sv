`timescale 1ns/1ps
module wp4_p0_latency_monitor(input wire clk,kernel_rst,in_valid,out_valid);
integer cycles=0,first_input=-1,first_output=-1;
always @(posedge clk) begin
    if(kernel_rst) begin
        cycles=0; first_input=-1; first_output=-1;
    end else begin
        if(in_valid && first_input<0) first_input=cycles;
        if(out_valid && first_output<0) begin
            first_output=cycles;
            if(first_input<0 || first_output-first_input!=292) $fatal(1,"P0 latency changed");
            $display("WP4_P0_LATENCY cycles=%0d",first_output-first_input);
        end
        cycles=cycles+1;
    end
end
endmodule
bind paper_fft_stream_engine wp4_p0_latency_monitor u_wp4_p0_latency(.clk(clk),.kernel_rst(kernel_rst),.in_valid(d_in_valid),.out_valid(d_out_valid));
