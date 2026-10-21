// wp7b_nl_shells.sv -- pure-connectivity shells reproducing the checkpoint
// hierarchy path u_core/u_dut/g_k.u_kernel for the routed kernel netlist.
// WP7B_NL_TOP names the (compile-time renamed) netlist top module.
// No logic here: every port is wired straight through.

`timescale 1ns/1ps
// The routed netlist lives in library `wp7bnl` (its module names collide
// with the RTL kernel modules in `work`) -- resolve it first, work second.
`uselib lib=wp7bnl lib=work

// ---------------------------------------------------------- ECC family --
// Netlist top ports (P0 top_p1_pfft_ecc, S0 top_s3_subfft_ecc):
//   in : clk_out2 rst proc_rst_n in_valid in_last d_in_data_r[127:0]
//        data2[4:0] \last_class_reg[0] \last_class_reg[0]_0
//        \last_stage_reg[2]_0 \last_win_reg[0] \out_commit_reg[0]
//        CO[0:0] cs_build_start
//   out: out_beat[215:0] proc_rst_n_r_reg{,_0,_1} flag_reg{,_0,_1}
//        out_frames0 E[0:0] wr_ptr_out0 unc_reg D[2:0] det_cnt0__0
//        \rstate_reg[1] \last_stage_reg[2] \last_stage_reg[1]
module wp7b_udut_shell_ecc(
    input  wire         clk_out2,
    input  wire         rst,
    input  wire         proc_rst_n,
    input  wire         in_valid,
    input  wire         in_last,
    input  wire [127:0] d_in_data_r,
    input  wire [4:0]   data2,
    input  wire         rstate0,
    input  wire         rstate1,
    input  wire         aux_ls2,
    input  wire         aux_lw0,
    input  wire         aux_oc0,
    input  wire         co,
    input  wire         cs_build_start,
    output wire [215:0] out_beat
);
    // generate-if scope "g_k" (no loop index) + instance "u_kernel": xsim
    // flattens it to scope "g_k.u_kernel" -- the checkpoint's cell name.
    if (1) begin : g_k
        `WP7B_NL_TOP u_kernel (
            .clk_out2(clk_out2), .rst(rst), .proc_rst_n(proc_rst_n),
            .in_valid(in_valid), .in_last(in_last),
            .d_in_data_r(d_in_data_r),
            .data2(data2),
            .\last_class_reg[0] (rstate0),
            .\last_class_reg[0]_0 (rstate1),
            .\last_stage_reg[2]_0 (aux_ls2),
            .\last_win_reg[0] (aux_lw0),
            .\out_commit_reg[0] (aux_oc0),
            .CO(co), .cs_build_start(cs_build_start),
            .out_beat(out_beat),
            .proc_rst_n_r_reg(), .proc_rst_n_r_reg_0(),
            .proc_rst_n_r_reg_1(),
            .flag_reg(), .flag_reg_0(), .flag_reg_1(),
            .out_frames0(), .E(), .wr_ptr_out0(), .unc_reg(), .D(),
            .det_cnt0__0(), .\rstate_reg[1] (),
            .\last_stage_reg[2] (), .\last_stage_reg[1] ());
    end
endmodule

module wp7b_ucore_shell_ecc(
    input  wire         clk_out2,
    input  wire         rst,
    input  wire         proc_rst_n,
    input  wire         in_valid,
    input  wire         in_last,
    input  wire [127:0] d_in_data_r,
    input  wire [4:0]   data2,
    input  wire         rstate0,
    input  wire         rstate1,
    input  wire         aux_ls2,
    input  wire         aux_lw0,
    input  wire         aux_oc0,
    input  wire         co,
    input  wire         cs_build_start,
    output wire [215:0] out_beat
);
    wp7b_udut_shell_ecc u_dut (
        .clk_out2(clk_out2), .rst(rst), .proc_rst_n(proc_rst_n),
        .in_valid(in_valid), .in_last(in_last),
        .d_in_data_r(d_in_data_r), .data2(data2),
        .rstate0(rstate0), .rstate1(rstate1),
        .aux_ls2(aux_ls2), .aux_lw0(aux_lw0), .aux_oc0(aux_oc0),
        .co(co), .cs_build_start(cs_build_start), .out_beat(out_beat));
endmodule

// ---------------------------------------------------------- TMR family --
// Netlist top ports (P1 top_p2_pfft_tmr_no_framebuf_v1, S1 top_s2_subfft_tmr):
//   in : clk_out2 rst proc_rst_n in_valid in_last d_in_data_r[127:0]
//        data2[2:0] \last_class_reg[0] \last_class_reg[0]_0
//        \out_commit_reg[0] CO[0:0] cs_build_start SR[0:0]
//   out: out_beat[215:0] proc_rst_n_r_reg E[0:0] out_frames0 out_valid_reg
//        wr_ptr_out0 flag_reg{,_0} \last_stage_reg[2] \last_stage_reg[1]
module wp7b_udut_shell_tmr(
    input  wire         clk_out2,
    input  wire         rst,
    input  wire         proc_rst_n,
    input  wire         in_valid,
    input  wire         in_last,
    input  wire [127:0] d_in_data_r,
    input  wire [2:0]   data2,
    input  wire         rstate0,
    input  wire         rstate1,
    input  wire         aux_sr,
    input  wire         aux_oc0,
    input  wire         co,
    input  wire         cs_build_start,
    output wire [215:0] out_beat
);
    if (1) begin : g_k
        `WP7B_NL_TOP u_kernel (
            .clk_out2(clk_out2), .rst(rst), .proc_rst_n(proc_rst_n),
            .in_valid(in_valid), .in_last(in_last),
            .d_in_data_r(d_in_data_r),
            .data2(data2),
            .\last_class_reg[0] (rstate0),
            .\last_class_reg[0]_0 (rstate1),
            .\out_commit_reg[0] (aux_oc0),
            .SR(aux_sr),
            .CO(co), .cs_build_start(cs_build_start),
            .out_beat(out_beat),
            .proc_rst_n_r_reg(), .out_valid_reg(),
            .flag_reg(), .flag_reg_0(),
            .out_frames0(), .E(), .wr_ptr_out0(),
            .\last_stage_reg[2] (), .\last_stage_reg[1] ());
    end
endmodule

module wp7b_ucore_shell_tmr(
    input  wire         clk_out2,
    input  wire         rst,
    input  wire         proc_rst_n,
    input  wire         in_valid,
    input  wire         in_last,
    input  wire [127:0] d_in_data_r,
    input  wire [2:0]   data2,
    input  wire         rstate0,
    input  wire         rstate1,
    input  wire         aux_sr,
    input  wire         aux_oc0,
    input  wire         co,
    input  wire         cs_build_start,
    output wire [215:0] out_beat
);
    wp7b_udut_shell_tmr u_dut (
        .clk_out2(clk_out2), .rst(rst), .proc_rst_n(proc_rst_n),
        .in_valid(in_valid), .in_last(in_last),
        .d_in_data_r(d_in_data_r), .data2(data2),
        .rstate0(rstate0), .rstate1(rstate1),
        .aux_sr(aux_sr), .aux_oc0(aux_oc0),
        .co(co), .cs_build_start(cs_build_start), .out_beat(out_beat));
endmodule
