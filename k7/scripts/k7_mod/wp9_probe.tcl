# WP9 probe: verify query idioms on one checkpoint before the real export.
#   vivado -mode batch -source wp9_probe.tcl -tclargs <routed.dcp>
lassign $argv dcp
open_checkpoint $dcp
proc p {msg} { puts "PROBE $msg" }

set kh [get_cells -hier -filter {NAME =~ */u_dut/g_k.u_kernel}]
set kname [get_property NAME $kh]
p "kname=$kname"

# cell population
set cells [get_cells -hier -filter {IS_PRIMITIVE && IS_PLACED}]
p "prim_placed=[llength $cells]"
set names [get_property NAME $cells]
p "names_list=[llength $names]"
set sites_l [get_property SITE $cells]
p "sites_list=[llength $sites_l] first=[lindex $sites_l 0]"
set pgrp [get_property PRIMITIVE_GROUP $cells]
p "pgrp_list=[llength $pgrp] first=[lindex $pgrp 0]"

# site object props
set s0 [lindex $sites_l 0]
set sobj [get_sites $s0]
p "site $s0 SITE_TYPE=[get_property SITE_TYPE $sobj] TILE=[get_property TILE $sobj]"

# pblock tiles
set pbt [get_tiles -of_objects [get_pblocks pb_dut]]
p "pb_dut_tiles=[llength $pbt]"
p "pb_dut grid=[get_property GRID_RANGES [get_pblocks pb_dut]] cr=[get_property CONTAIN_ROUTING [get_pblocks pb_dut]] excl=[get_property EXCLUDE_PLACEMENT [get_pblocks pb_dut]]"
p "pb_shell member_cells=[llength [get_cells -of_objects [get_pblocks pb_shell]]]"

# clock region map
set ncr 0
foreach cr [get_clock_regions] {
    set ts [get_tiles -of_objects $cr]
    incr ncr [llength $ts]
}
p "clockregions=[llength [get_clock_regions]] tiles_in_crs=$ncr total_tiles=[llength [get_tiles]]"

# nets
set allnets [get_nets -hierarchical]
p "nets=[llength $allnets]"
p "POWER=[llength [get_nets -hier -filter {TYPE == \"POWER\"}]] GROUND=[llength [get_nets -hier -filter {TYPE == \"GROUND\"}]]"
set kpins [get_pins $kname/*]
p "kernel_pins=[llength $kpins]"
set bnets [get_nets -quiet -of_objects $kpins]
p "boundary_nets=[llength $bnets]"

# pips of a few nets
set sample [lrange $allnets 0 4]
set pp [get_pips -of_objects $sample]
p "sample_pips=[llength $pp] first=[lindex $pp 0]"
if {[llength $pp]} {
    set pn [get_property NAME $pp]
    p "pip_names=[llength $pn] p0=[lindex $pn 0]"
    set one [get_nets -of_objects [lindex $pp 0]]
    p "pip0_net=$one"
}
close_design
p "done"
