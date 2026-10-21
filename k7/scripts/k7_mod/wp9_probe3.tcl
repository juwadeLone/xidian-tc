lassign $argv dcp
open_checkpoint $dcp
proc p {msg} { puts "PROBE $msg" }
proc try {label script} { if {[catch {uplevel $script} r]} { puts "PROBE $label ERROR $r" } else { puts "PROBE $label $r" } }

set cells [get_cells -hier -filter {IS_PRIMITIVE && (STATUS == "PLACED" || STATUS == "FIXED")}]
set names [get_property NAME $cells]
set sites_l [get_property SITE $cells]
p "cells=[llength $cells] names=[llength $names] sites=[llength $sites_l] s0=[lindex $sites_l 0]"

set sobj [get_sites [lindex $sites_l 0]]
try "site_props" {list props=[get_property SITE_TYPE $sobj] tile=[get_property TILE $sobj]}

try "pb_dut_tiles" {list [llength [get_tiles -of_objects [get_pblocks pb_dut]]]}
try "pb_dut_sites" {list [llength [get_sites -of_objects [get_pblocks pb_dut]]]}
try "cr_tiles" {list crmap=[expr 0]; foreach cr [get_clock_regions] { set ts [get_tiles -of_objects $cr]; incr crmap [llength $ts] }; list total=$crmap crs=[llength [get_clock_regions]]}
try "tile_cr" {list [get_property CLOCK_REGION [lindex [get_tiles] 100]]}
try "total_tiles" {list [llength [get_tiles]]}

p "pb_dut grid=[get_property GRID_RANGES [get_pblocks pb_dut]] contain=[get_property CONTAIN_ROUTING [get_pblocks pb_dut]] excl=[get_property EXCLUDE_PLACEMENT [get_pblocks pb_dut]]"
p "pb_dut members=[get_property NAME [get_cells -of_objects [get_pblocks pb_dut]]]"

set allnets [get_nets -hierarchical]
p "nets=[llength $allnets]"
try "power" {list [llength [get_nets -hier -filter {TYPE == "POWER"}]]}
try "ground" {list [llength [get_nets -hier -filter {TYPE == "GROUND"}]]}
try "netprops" {list [list_property [lindex $allnets 0]]}

set kname u_core/u_dut/g_k.u_kernel
set kpins [get_pins $kname/*]
p "kernel_pins=[llength $kpins]"
set bnets [get_nets -quiet -of_objects $kpins]
p "boundary_nets=[llength $bnets]"

set some [lrange $allnets 0 99]
set pp [get_pips -of_objects $some]
p "pips_100nets=[llength $pp] p0=[lindex $pp 0]"
if {[llength $pp]} {
    p "pip_name=[get_property NAME [lindex $pp 0]]"
    p "pip_net=[get_property NAME [get_nets -of_objects [lindex $pp 0]]]"
    p "pip_tile=[get_property TILE [lindex $pp 0]]"
}
close_design
p "done"
