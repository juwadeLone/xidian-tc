# WP9 F6 BRKH attribution — read-only. Each f6 pip sits on a tile without a
# clock region (BRKH_INT). Following wp4r's rule for unassigned tiles, resolve
# each pip through its routing nodes' tile clock regions and classify as
# kernel/shell/boundary (kernel = both adjacent nodes' regions inside DUT_CR).
#   vivado -mode batch -source wp9_brkh.tcl -tclargs <dcp> <outdir>
lassign $argv dcp outdir
open_checkpoint $dcp

set DUT_CR {X0Y4 X0Y5 X0Y6 X1Y3 X1Y4 X1Y5 X1Y6}
set SHELL_CR {X0Y0 X0Y1 X0Y2 X0Y3 X1Y0 X1Y1 X1Y2}
set tile_regions [dict create]
foreach region [get_clock_regions] {
    foreach tile [get_tiles -of_objects $region] {
        dict set tile_regions $tile [get_property NAME $region]
    }
}
proc clock_regions_of_tile {tile} {
    global tile_regions
    return [expr {[dict exists $tile_regions $tile] ? [dict get $tile_regions $tile] : {}}]
}

set infile [file join $outdir f6_core_out_dut.tsv]
set fh [open $infile r]; gets $fh line
set of [open [file join $outdir f6_brkh_attr.tsv] w]
puts $of "pip\ttile\tregions_of_node_tiles\tclass"
while {[gets $fh line] >= 0} {
    set f [split $line "\t"]
    if {[llength $f] < 2} { continue }
    set pip [lindex $f 0]
    set nodes [get_nodes -quiet -of_objects [get_pips -quiet $pip]]
    set regions {}
    foreach nd $nodes {
        foreach t [get_tiles -quiet -of_objects $nd] {
            foreach r [clock_regions_of_tile $t] { lappend regions $r }
        }
    }
    set regions [lsort -unique $regions]
    set kernel 0; set shell 0; set other 0
    foreach r $regions {
        if {[lsearch -exact $DUT_CR $r] >= 0} { set kernel 1
        } elseif {[lsearch -exact $SHELL_CR $r] >= 0} { set shell 1
        } else { set other 1 }
    }
    set cls [expr {$kernel && !$shell && !$other ? "kernel" : ($shell && !$kernel ? "shell" : ($kernel && $shell ? "boundary" : "unresolved"))}]
    puts $of "$pip\t[lindex $f 1]\t[join $regions ,]\t$cls"
}
close $of
close_design
puts "WP9BRKH done"
