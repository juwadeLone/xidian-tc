# WP9 F5/F6 attribution pass — read-only. For each unique net in
# <outdir>/f5_nets.txt (and f6_nets.txt), report TYPE, whether any leaf pin
# sits on a kernel primitive (wp4r "crossing" test), and how many of the net's
# pips land in pb_dut tiles, plus a sample of those pips.
#   vivado -mode batch -source wp9_f5_attr.tcl -tclargs <dcp> <outdir>
lassign $argv dcp outdir
open_checkpoint $dcp
set kname [get_property NAME [get_cells -hier -filter {NAME =~ */u_dut/g_k.u_kernel}]]

# rebuild pb_dut tile set from the pblock grid ranges
proc cr_expand {grid} {
    set out {}
    foreach r $grid {
        if {[regexp {CLOCKREGION_X(\d+)Y(\d+)(?::CLOCKREGION_X(\d+)Y(\d+))?} $r -> x1 y1 x2 y2]} {
            if {$x2 eq ""} { set x2 $x1 ; set y2 $y1 }
            for {set x $x1} {$x <= $x2} {incr x} {
                for {set y $y1} {$y <= $y2} {incr y} { lappend out "X${x}Y${y}" }
            }
        }
    }
    return $out
}
foreach crn [cr_expand [get_property GRID_RANGES [get_pblocks pb_dut]]] {
    foreach t [get_tiles -of_objects [get_clock_regions -quiet $crn]] {
        set dut_tiles($t) 1
    }
}

proc attr_file {listfile outfile violside} {
    global kname; upvar dut_tiles dt
    if {![file exists $listfile]} { return }
    set nets {}
    set fh [open $listfile r]
    while {[gets $fh line] >= 0} { if {$line ne ""} { dict set nets $line 1 } }
    close $fh
    set of [open $outfile w]
    puts $of "net\ttype\tpins_total\tcells_in_kernel\tcells_outside_kernel\tpips_in_dut\tpips_out_dut\tsample_pips"
    foreach name [lsort [dict keys $nets]] {
        set n [get_nets -quiet $name]
        if {![llength $n]} { puts $of "$name\tMISSING\t0\t?\t?\t0\t0\t" ; continue }
        set ty [get_property TYPE $n]
        set pins [get_pins -quiet -of_objects $n]
        set np [llength $pins]
        set k 0; set o 0
        if {$np} {
            set pcs [get_cells -quiet -of_objects $pins]
            foreach c $pcs {
                if {[string match "$kname/*" [get_property NAME $c]]} { incr k } else { incr o }
            }
        }
        set hit 0; set outh 0; set sample {}
        foreach p [get_pips -quiet -of_objects $n] {
            set t [string range $p 0 [expr {[string first / $p]-1}]]
            if {[info exists dt($t)]} {
                incr hit
                if {$violside eq "in" && [llength $sample] < 12} { lappend sample $p }
            } else {
                incr outh
                if {$violside eq "out" && [llength $sample] < 12} { lappend sample $p }
            }
        }
        puts $of "$name\t$ty\t$np\t$k\t$o\t$hit\t$outh\t[join $sample {;}]"
    }
    close $of
}
attr_file [file join $outdir f5_nets.txt] [file join $outdir f5_net_attr.tsv] in
attr_file [file join $outdir f6_nets.txt] [file join $outdir f6_net_attr.tsv] out
close_design
puts "WP9ATTR done"
