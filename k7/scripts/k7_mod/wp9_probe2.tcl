lassign $argv dcp
open_checkpoint $dcp
proc p {msg} { puts "PROBE $msg" }
set kname u_core/u_dut/g_k.u_kernel

set c1 [get_cells -hier -filter {IS_PRIMITIVE}]
p "IS_PRIMITIVE=[llength $c1]"
set c2 [get_cells -hier -filter {IS_PRIMITIVE && STATUS == "PLACED"}]
p "placed_status=[llength $c2]"
set c3 [get_cells -hier -filter {IS_PRIMITIVE && STATUS == "FIXED"}]
p "fixed_status=[llength $c3]"
set c4 [get_cells -hier -filter {IS_PRIMITIVE && PRIMITIVE_LEVEL != "INTERNAL"}]
p "non_internal=[llength $c4]"
set c5 [get_cells -hier -filter "IS_PRIMITIVE && NAME =~ $kname/*"]
p "kernel_prims=[llength $c5]"
set c6 [get_cells -hier -filter {IS_PRIMITIVE == 1}]
p "isprim_eq1=[llength $c6]"

if {[llength $c1]} {
    set one [lindex $c1 0]
    p "cell0=[get_property NAME $one] status=[get_property STATUS $one] site=[get_property SITE $one] bel=[get_property BEL $one] pgroup=[get_property PRIMITIVE_GROUP $one] placed=[get_property IS_PLACED $one]"
}
close_design
p "done"
