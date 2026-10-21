# Independent K7 paper-FFT bitstream: UDP payload -> paper kernel -> UDP,
# UART command layer and the SEM injection channel.  No MIG, no AXI clock
# converter, no DDR data path -- those IPs and files are simply not in the
# project.  Does NOT overwrite build/vivado/K7_UDP_DDR, the K7_MOD_* builds
# or results/k7_cram/baseline_v1.
#
#   vivado -mode batch -source vivado/create_k7_paper.tcl -tclargs s3 1
#   vivado -mode batch -source vivado/create_k7_paper.tcl -tclargs p1 1
#   vivado -mode batch -source vivado/create_k7_paper.tcl -tclargs p1 1 iso
#
# argv: arch [run_impl] [tag]   arch = s0|s1|s2|s3|p0|p1|p2|s2h
# A tag builds into build/vivado/K7_PAPER_<ARCH>_<TAG> and leaves the
# untagged image of that arch (and its .ebd) in place.
if {$argc < 1} { error "usage: -tclargs s0|s1|s2|s3|p0|p1|p2|s2h|p2h|p0x|s2hr|p2hr ?run_impl? ?tag?" }
set arch [string tolower [lindex $argv 0]]
set run_impl 0
if {$argc >= 2} { set run_impl [lindex $argv 1] }
set tag ""
if {$argc >= 3} { set tag [string toupper [lindex $argv 2]] }
switch -- $arch {
    s0 { set paper_arch 0 }
    s1 { set paper_arch 1 }
    s2 { set paper_arch 2 }
    s3 { set paper_arch 3 }
    p0 { set paper_arch 4 }
    p1 { set paper_arch 5 }
    p2 { set paper_arch 6 }
    s2h { set paper_arch 7 }
    p2h { set paper_arch 8 }
    p0x { set paper_arch 9 }
    s2hr { set paper_arch 10 }
    p2hr { set paper_arch 11 }
    default { error "arch must be s0|s1|s2|s3|p0|p1|p2|s2h|p2h|p0x|s2hr|p2hr, got '$arch'" }
}
set proj "K7_PAPER_[string toupper $arch]"
if {$tag ne ""} { append proj "_$tag" }
set root [file normalize [pwd]]
set out [file join $root build vivado $proj]
if {[file exists $out]} {
    foreach e [glob -nocomplain -directory $out -types {f d} *] {
        if {[file tail $e] ne "ip"} { file delete -force $e }
    }
}
file mkdir $out
create_project -force -part xc7k325tffg900-2 $proj $out
file mkdir [file join $out ip]

# ---- sources: lean shell + one paper kernel -----------------------------
# Only what k7_paper_top elaborates.  The whole rtl/{core,stream,axi,coeff,
# control,fault,ingress,mem,generated,transpose} tree, every csa_*/ddr_*/
# k7_udp_ddr_* file, udp_wr_master, udp_rd_master, the AXI width gears and
# the 3-to-1 mux stay out of the image entirely.
set sources {
    rtl/board/k7_paper_top.sv
    rtl/board/k7_paper_core.sv
    rtl/board/k7_reset_tree.sv
    rtl/board/k7_reset_sync.sv
    rtl/board/uart_rx.sv
    rtl/board/uart_tx.sv
}
foreach f [glob [file join $root rtl board cmd *.v]] { lappend sources $f }
foreach f [glob [file join $root rtl board net *.v] \
                          [file join $root rtl board net *.sv]] {
    set t [file tail $f]
    if {$t in {axi_dwidth_128to256.sv axi_mux_3to1_256.sv udp_wr_master.v
               udp_rd_master.v udp_cmd_stream.v udp_tx_arb.v
               udp_telem_engine.v k7_rgmii_loop_top.sv}} { continue }
    lappend sources $f
}
foreach f [glob -nocomplain [file join $root rtl board sem *.v] \
                          [file join $root rtl board sem *.sv]] {
    lappend sources $f
}
lappend sources [file join $root rtl top paper_fft_stream_engine.sv]

# Paper kernel: paper_fft12 (16-bit in, 27-bit out, growing width) arch dir.
# s2/s2h reuse the s0 family; the WP4-R PFFT designs and historical exchange
# designs reuse p0 arithmetic primitives.  Only s1/s3 use observation wrappers.
set obs_tops {s1 top_s1_kernel.sv}
set kernel_files [glob -nocomplain [file join $root rtl paper_fft12 $arch *.sv]]
if {$arch in {s2 s2h s3 s2hr}} {
    set kernel_files [concat [lsort [glob [file join $root rtl paper_fft12 s0 *.sv]]] $kernel_files]
}
if {$arch in {p1 p2 p0x p2h p2hr}} {
    set kernel_files [concat [lsort [glob [file join $root rtl paper_fft12 p0 *.sv]]] $kernel_files]
}
if {[dict exists $obs_tops $arch]} {
    set obs_name [dict get $obs_tops $arch]
    set kernel_files [lsearch -all -inline -not $kernel_files \
        [file join $root rtl paper_fft12 $arch $obs_name]]
    lappend kernel_files [file join $root rtl paper_fft12 obs $arch $obs_name]
}
if {[llength $kernel_files] == 0} {
    error "no paper kernel files under rtl/paper_fft12/$arch (pg port pending)"
}
set common_files [glob [file join $root rtl paper_fft12 common *.sv]]
set sources [concat $sources $common_files $kernel_files]

add_files -norecurse $sources
add_files -norecurse [file join $root rtl paper_fft12 common twiddle_rom_1024_secded.mem]
if {$arch in {p1 p2 p2hr}} {
    foreach f [glob -nocomplain [file join $root rtl paper_fft12 common pfft_*_wp4r.mem]] {
        add_files -norecurse $f
    }
}
if {$arch in {s2 s3 s2hr}} {
    foreach f [glob -nocomplain [file join $root rtl paper_fft12 common subfft_*_wp4r.mem]] {
        add_files -norecurse $f
    }
}
set_property include_dirs [list [file join $root rtl board cmd]] [current_fileset]
set_property top k7_paper_top [current_fileset]
set_property XPM_LIBRARIES {XPM_CDC XPM_MEMORY XPM_FIFO} [current_project]
set_property verilog_define [list PAPER_ARCH=$paper_arch] [get_filesets sources_1]

add_files -fileset constrs_1 -norecurse [file join $root vivado constraints k7_udp_ddr.xdc]
add_files -fileset constrs_1 -norecurse [file join $root vivado constraints k7_paper_impl.xdc]
set_property used_in_synthesis false \
    [get_files [file join $root vivado constraints k7_paper_impl.xdc]]

file delete -force [file join $out ip clk_wiz_k7]
create_ip -name clk_wiz -vendor xilinx.com -library ip -module_name clk_wiz_k7 -dir [file join $out ip]
set_property -dict [list CONFIG.PRIM_IN_FREQ {100.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {200.000} \
    CONFIG.CLKOUT2_USED {true} CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {125.000} \
    CONFIG.CLKOUT3_USED {true} CONFIG.CLKOUT3_REQUESTED_OUT_FREQ {125.000} \
    CONFIG.CLKOUT3_REQUESTED_PHASE {90.000} CONFIG.NUM_OUT_CLKS {3} \
    CONFIG.USE_LOCKED {true} CONFIG.USE_RESET {true} \
    CONFIG.RESET_TYPE {ACTIVE_HIGH} CONFIG.PRIM_SOURCE {No_buffer}] [get_ips clk_wiz_k7]

file delete -force [file join $out ip sem_0]
create_ip -name sem -vendor xilinx.com -library ip -version 4.1 \
    -module_name sem_0 -dir [file join $out ip]
set_property -dict [list \
    CONFIG.ENABLE_INJECTION      {true} \
    CONFIG.ENABLE_CORRECTION     {true} \
    CONFIG.CORRECTION_METHOD     {repair} \
    CONFIG.ENABLE_CLASSIFICATION {false} \
    CONFIG.VALIDATE_DETECTION    {false} \
    CONFIG.INJECTION_SHIM        {pins} \
    CONFIG.RETRIEVAL_SHIM        {none} \
    CONFIG.CLOCK_FREQ            {100}] [get_ips sem_0]

generate_target all [get_ips]
set_property STEPS.OPT_DESIGN.TCL.POST \
    [file normalize [file join $root vivado post_opt_shell_pblock.tcl]] \
    [get_runs impl_1]
set_property STEPS.WRITE_BITSTREAM.TCL.PRE \
    [file normalize [file join $root vivado pre_write_bitstream_seu.tcl]] \
    [get_runs impl_1]
set_property STEPS.WRITE_BITSTREAM.ARGS.LOGIC_LOCATION_FILE true [get_runs impl_1]
reset_run synth_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "synthesis failed" }
open_run synth_1
report_utilization -file [file join $out util_synth.rpt]
report_utilization -hierarchical -file [file join $out util_synth_hier.rpt]
if {$run_impl} {
    reset_run impl_1
    launch_runs impl_1 -to_step write_bitstream -jobs 4
    wait_on_run impl_1
    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "implementation failed" }
    open_run impl_1
    report_timing_summary -file [file join $out timing_impl.rpt]
    report_cdc -details -file [file join $out cdc_impl.rpt]
    report_drc -file [file join $out drc_impl.rpt]
    report_utilization -hierarchical -file [file join $out util_impl.rpt]
    set wns [get_property STATS.WNS [get_runs impl_1]]
    set whs [get_property STATS.WHS [get_runs impl_1]]
    puts "${proj}_IMPL_DONE WNS=$wns WHS=$whs PAPER_ARCH=$paper_arch"
    if {$wns < 0 || $whs < 0} { error "timing gate failed" }
}
