# WP9 floorplan data export — read-only: opens the routed checkpoint, never
# modifies the design, never writes a checkpoint.
#
#   vivado -mode batch -source wp9_export.tcl \
#       -tclargs <routed.dcp> <outdir> <tiles_out|-> <design>
#
# Outputs in <outdir>: pblocks.tsv, sites.tsv.gz, route_tiles.tsv.gz,
# summary.json, f5_shell_in_dut.tsv, f6_core_out_dut.tsv .
# If <tiles_out> != "-", also writes the device-wide tile table there
# (the four designs share one device; only one run passes a real path).
#
# Cell groups (WP9 §2, first match wins):
#   1 core  : NAME under u_core/u_dut/g_k.u_kernel/
#   2 sem   : NAME under u_core/u_sem_wrapper|u_sem_cdc|u_sem_bridge/
#   3 ioclk : PRIMITIVE_GROUP in {IO,CLK,OTHERS}
#   4 shell : everything else
# Net classes (precedence const > boundary > core-name > shell; overlap of
# boundary and core-named nets is counted and reported):
#   const    : TYPE POWER or GROUND
#   boundary : nets on pins of u_core/u_dut/g_k.u_kernel
#   core     : NAME starts with u_core/u_dut/g_k.u_kernel/
#   shell    : the rest

lassign $argv dcp outdir tiles_out design
set T0 [clock milliseconds]
open_checkpoint $dcp
file mkdir $outdir

set sha [lindex [exec sha256sum $dcp] 0]

proc log {m} { puts "WP9 $m" }

# ---- clock-region helpers --------------------------------------------------
proc cr_expand {grid} {
    set out {}
    foreach r $grid {
        if {[regexp {CLOCKREGION_X(\d+)Y(\d+)(?::CLOCKREGION_X(\d+)Y(\d+))?} $r -> x1 y1 x2 y2]} {
            if {$x2 eq ""} { set x2 $x1 ; set y2 $y1 }
            for {set x $x1} {$x <= $x2} {incr x} {
                for {set y $y1} {$y <= $y2} {incr y} { lappend out "X${x}Y${y}" }
            }
        } else {
            log "WARN unparsed grid range: $r"
        }
    }
    return $out
}

# tile -> clock region map (covers tiles that belong to a clock region)
foreach cr [get_clock_regions] {
    set crn [get_property NAME $cr]
    foreach t [get_tiles -of_objects $cr] { set tilecr($t) $crn }
}
log "tilecr entries [array size tilecr]"

# ---- pblocks -----------------------------------------------------------------
set pbinfo {}
array unset dut_sites
array unset dut_tiles
foreach pb [get_pblocks] {
    set nm [get_property NAME $pb]
    set gr [get_property GRID_RANGES $pb]
    set sites_in [get_sites -of_objects $pb]
    set members [get_cells -of_objects $pb]
    dict set pbinfo $nm [list grid $gr \
        contain [get_property CONTAIN_ROUTING $pb] \
        excl [get_property EXCLUDE_PLACEMENT $pb] \
        members [llength $members] sites [llength $sites_in]]
    if {$nm eq "pb_dut"} {
        foreach s $sites_in { set dut_sites($s) 1 }
        foreach crn [cr_expand $gr] {
            foreach t [get_tiles -of_objects [get_clock_regions -quiet $crn]] {
                set dut_tiles($t) 1
            }
        }
    }
}
log "pb_dut sites=[array size dut_sites] tiles=[array size dut_tiles]"

# ---- cells -------------------------------------------------------------------
set kh [get_cells -hier -filter {NAME =~ */u_dut/g_k.u_kernel}]
set kname [get_property NAME $kh]
set kref [get_property REF_NAME $kh]
log "kernel $kname ref=$kref"

set cells [get_cells -hier -filter {IS_PRIMITIVE && (STATUS == "PLACED" || STATUS == "FIXED")}]
set cnames [get_property NAME $cells]
set csites [get_property SITE $cells]
set cgrp   [get_property PRIMITIVE_GROUP $cells]
set cplev  [get_property PRIMITIVE_LEVEL $cells]
log "leaf cells [llength $cells]"

array unset sgcnt          ;# site -> group -> n
array unset seen_site
set g1 0; set g2 0; set g3 0; set g4 0
set core_noninternal 0
set f2_out 0; set f2_sites {}
set f3_in 0; set f3_g2 0; set f3_g3 0; set f3_g4 0
set sem_sites {}
foreach n $cnames s $csites pg $cgrp pl $cplev {
    if {[string match "$kname/*" $n]} {
        set g 1; incr g1
        if {$pl ne "INTERNAL"} { incr core_noninternal }
        if {![info exists dut_sites($s)]} { incr f2_out; lappend f2_sites $s }
    } elseif {[string match "u_core/u_sem_wrapper/*" $n] ||
              [string match "u_core/u_sem_cdc/*" $n] ||
              [string match "u_core/u_sem_bridge/*" $n]} {
        set g 2; incr g2
        lappend sem_sites $s
        if {[info exists dut_sites($s)]} { incr f3_in; incr f3_g2 }
    } elseif {$pg eq "IO" || $pg eq "CLK" || $pg eq "OTHERS"} {
        set g 3; incr g3
        if {[info exists dut_sites($s)]} { incr f3_in; incr f3_g3 }
    } else {
        set g 4; incr g4
        if {[info exists dut_sites($s)]} { incr f3_in; incr f3_g4 }
    }
    incr sgcnt($s,$g)
    set seen_site($s) 1
}
log "groups core=$g1 sem=$g2 ioclk=$g3 shell=$g4 ; f2_out=$f2_out f3_in=$f3_in"

# site -> type, tile
proc net_of_pip {po} {
    set pobj $po
    if {[catch {get_pips -quiet $po} r] || $r eq ""} { set pobj $po } else { set pobj $r }
    if {![catch {get_nets -quiet -of_objects $pobj} n] && $n ne ""} { return $n }
    if {![catch {get_nodes -quiet -of_objects $pobj} nd] && $nd ne ""} {
        if {![catch {get_nets -quiet -of_objects [lindex $nd 0]} n2] && $n2 ne ""} { return $n2 }
    }
    return "?"
}

foreach s [array names seen_site] {
    set so [get_sites -quiet $s]
    if {[catch {get_property SITE_TYPE $so} st]} { set st "?" }
    set site_type($s) $st
    if {[catch {get_property NAME [get_tiles -of_objects $so]} ti]} { set ti "?" }
    set site_tile($s) $ti
}
log "sites mapped [array size site_tile]"

# SEM clock regions (F4)
set sem_crs {}
foreach s [lsort -unique $sem_sites] {
    set t $site_tile($s)
    if {[info exists tilecr($t)]} { lappend sem_crs $tilecr($t) } else { lappend sem_crs "NONE:$t" }
}
set sem_crs [lsort -unique $sem_crs]
set sem_in_dut $f3_g2

# ---- pblocks.tsv --------------------------------------------------------------
set fh [open [file join $outdir pblocks.tsv] w]
puts $fh "pblock\tgrid_ranges\tcontain_routing\texclude_placement\tmember_cells\tsites_in\tleaf_cells_in"
foreach pb [get_pblocks] {
    set nm [get_property NAME $pb]
    set d [dict get $pbinfo $nm]
    set leaf_in 0
    set psites {}
    if {$nm eq "pb_dut"} {
        foreach s [array names dut_sites] {
            foreach g {1 2 3 4} { if {[info exists sgcnt($s,$g)]} { incr leaf_in $sgcnt($s,$g) } }
        }
    } else {
        foreach s [get_sites -of_objects $pb] {
            foreach g {1 2 3 4} { if {[info exists sgcnt($s,$g)]} { incr leaf_in $sgcnt($s,$g) } }
        }
    }
    puts $fh "$nm\t[join [dict get $d grid] {;}]\t[dict get $d contain]\t[dict get $d excl]\t[dict get $d members]\t[dict get $d sites]\t$leaf_in"
}
close $fh

# ---- sites.tsv.gz -------------------------------------------------------------
set fh [open [file join $outdir sites.tsv] w]
puts $fh "site\tsite_type\ttile\tn_core\tn_sem\tn_io_clk\tn_shell"
foreach s [lsort [array names seen_site]] {
    set a 0; set b 0; set c 0; set d 0
    catch { set a $sgcnt($s,1) }; catch { set b $sgcnt($s,2) }
    catch { set c $sgcnt($s,3) }; catch { set d $sgcnt($s,4) }
    puts $fh "$s\t$site_type($s)\t$site_tile($s)\t$a\t$b\t$c\t$d"
}
close $fh
exec gzip -f [file join $outdir sites.tsv]
log "sites.tsv.gz written"

# ---- device tiles (one run only) ----------------------------------------------
if {$tiles_out ne "-"} {
    set fh [open $tiles_out w]
    puts $fh "tile\ttile_type\tgrid_x\tgrid_y\tclock_region"
    set allt [get_tiles]
    set tx [get_property GRID_POINT_X $allt]
    set ty [get_property GRID_POINT_Y $allt]
    foreach t $allt gx $tx gy $ty {
        set i [string last "_X" $t]
        set ttype [expr {$i > 0 ? [string range $t 0 $i-1] : "?"}]
        set cr [expr {[info exists tilecr($t)] ? $tilecr($t) : ""}]
        puts $fh "$t\t$ttype\t$gx\t$gy\t$cr"
    }
    close $fh
    exec gzip -f $tiles_out
    log "tiles.tsv.gz written ([llength $allt] tiles)"
}

# ---- nets ---------------------------------------------------------------------
# Classify PHYSICAL nets (deduplicated via PARENT): hierarchical segments of one
# physical net would each return the full pip list and inflate every count.
set bnd [get_nets -quiet -of_objects [get_pins $kname/*]]
array unset isbnd
foreach n [get_property NAME $bnd] { set isbnd($n) 1 }
foreach n [get_property PARENT $bnd] { if {$n ne ""} { set isbnd($n) 1 } }
set cnets [get_nets -hier -filter {TYPE == "POWER" || TYPE == "GROUND"}]
array unset isconst
foreach n [get_property NAME $cnets] { set isconst($n) 1 }
foreach n [get_property PARENT $cnets] { if {$n ne ""} { set isconst($n) 1 } }

proc rank {c} { return [dict get {shell 0 core 1 boundary 2 const 3} $c] }
set allnets [get_nets -hierarchical]
set nnames [get_property NAME $allnets]
set pnames [get_property PARENT $allnets]
array unset pcls; array unset prep
set nb_overlap 0
foreach net $allnets nm $nnames pp $pnames {
    set key [expr {$pp ne "" ? $pp : $nm}]
    if {[string match "$kname/*" $nm] || [string match "$kname/*" $key]} { set sc core } else { set sc shell }
    if {[info exists isbnd($nm)] || [info exists isbnd($key)]} {
        set sc boundary
        if {[string match "$kname/*" $nm]} { incr nb_overlap }
    }
    if {[info exists isconst($nm)] || [info exists isconst($key)]} { set sc const }
    if {![info exists pcls($key)]} {
        set pcls($key) $sc; set prep($key) $net
    } elseif {[rank $sc] > [rank $pcls($key)]} {
        set pcls($key) $sc
    }
}
array unset CL
foreach {key c} [array get pcls] { lappend CL($c) $prep($key) }
set kn [expr {[info exists CL(core)] ? $CL(core) : {}}]
set bn [expr {[info exists CL(boundary)] ? $CL(boundary) : {}}]
set sn [expr {[info exists CL(shell)] ? $CL(shell) : {}}]
set cn [expr {[info exists CL(const)] ? $CL(const) : {}}]
log "physnets total=[array size pcls] core=[llength $kn] bnd=[llength $bn] shell=[llength $sn] const=[llength $cn] overlap_bnd_core=$nb_overlap"

# ---- pips per net class, per tile ----------------------------------------------
array unset RC
set f5_pips {}; set f6_pips {}
set bnd_in 0; set bnd_out 0; set const_in 0
foreach {cls nlist} [list core $kn boundary $bn shell $sn const $cn] {
    for {set i 0} {$i < [llength $nlist]} {incr i 50000} {
        set pips [get_pips -quiet -of_objects [lrange $nlist $i [expr {$i+49999}]]]
        if {![llength $pips]} { continue }
        set pnames [get_property NAME $pips]
        foreach po $pips pn $pnames {
            set t [string range $pn 0 [expr {[string first / $pn]-1}]]
            incr RC($cls,$t)
            if {$cls eq "shell" && [info exists dut_tiles($t)]} { lappend f5_pips $po }
            if {$cls eq "core" && ![info exists dut_tiles($t)]} { lappend f6_pips $po }
            if {$cls eq "boundary"} {
                if {[info exists dut_tiles($t)]} { incr bnd_in } else { incr bnd_out }
            }
            if {$cls eq "const" && [info exists dut_tiles($t)]} { incr const_in }
        }
    }
    log "class $cls done"
}

# route_tiles.tsv.gz
set fh [open [file join $outdir route_tiles.tsv] w]
puts $fh "tile\tn_core\tn_boundary\tn_shell\tn_const"
array unset tset
foreach k [array names RC] { set tset([lindex [split $k ,] 1]) 1 }
foreach t [lsort [array names tset]] {
    set a 0; set b 0; set c 0; set d 0
    catch { set a $RC(core,$t) }; catch { set b $RC(boundary,$t) }
    catch { set c $RC(shell,$t) }; catch { set d $RC(const,$t) }
    puts $fh "$t\t$a\t$b\t$c\t$d"
}
close $fh
exec gzip -f [file join $outdir route_tiles.tsv]
log "route_tiles.tsv.gz written"

# ---- violation lists ------------------------------------------------------------
# pip,tile always; net resolved per-pip only when the set is small (otherwise the
# offending net names go to *_nets.txt via one bulk nodes->nets query).
proc dump_violations {pips path netsfile} {
    set resolve [expr {[llength $pips] <= 2000}]
    set fh [open $path w]
    puts $fh "pip\ttile\tnet"
    foreach po $pips {
        set pn [get_property NAME $po]
        set t [string range $pn 0 [expr {[string first / $pn]-1}]]
        set nn ""
        if {$resolve} { set nn [net_of_pip $po] }
        puts $fh "$pn\t$t\t$nn"
    }
    close $fh
    set nets_l {}
    if {![catch {get_nodes -quiet -of_objects $pips} nds] && [llength $nds]} {
        if {![catch {get_nets -quiet -of_objects $nds} ns] && [llength $ns]} {
            set nets_l [lsort -unique [get_property NAME $ns]]
        }
    }
    if {![llength $nets_l]} {
        foreach po [lrange $pips 0 199] { lappend nets_l [net_of_pip $po] }
        set nets_l [lsort -unique $nets_l]
    }
    set nfh [open $netsfile w]
    foreach n $nets_l { puts $nfh $n }
    close $nfh
}
dump_violations $f5_pips [file join $outdir f5_shell_in_dut.tsv] [file join $outdir f5_nets.txt]
set f6_n [llength $f6_pips]
dump_violations [lrange $f6_pips 0 4999] [file join $outdir f6_core_out_dut.tsv] [file join $outdir f6_nets.txt]

# ---- summary.json -----------------------------------------------------------------
proc jl {l} {
    set o {}
    foreach e $l { lappend o "\"$e\"" }
    return "\[[join $o ,]\]"
}
set f2_sites [lsort -unique $f2_sites]
set t1 [expr {([clock milliseconds] - $T0)/1000.0}]
set fh [open [file join $outdir summary.json] w]
puts $fh "{"
puts $fh "  \"design\": \"$design\","
puts $fh "  \"dcp\": \"$dcp\","
puts $fh "  \"sha256\": \"$sha\","
puts $fh "  \"kernel\": \"$kname\","
puts $fh "  \"kernel_ref\": \"$kref\","
puts $fh "  \"cells\": {\"core\": $g1, \"core_noninternal\": $core_noninternal, \"sem\": $g2, \"io_clk\": $g3, \"shell\": $g4, \"total\": [llength $cells]},"
puts $fh "  \"f2_core_cells_outside_dut\": $f2_out,"
puts $fh "  \"f2_sites\": [jl $f2_sites],"
puts $fh "  \"f3_noncore_cells_in_dut\": $f3_in,"
puts $fh "  \"f3_by_group\": {\"sem\": $f3_g2, \"io_clk\": $f3_g3, \"shell\": $f3_g4},"
puts $fh "  \"f4_sem_in_dut\": $sem_in_dut,"
puts $fh "  \"f4_sem_clock_regions\": [jl $sem_crs],"
puts $fh "  \"f5_shell_pips_in_dut\": [llength $f5_pips],"
puts $fh "  \"f6_core_pips_outside_dut\": $f6_n,"
puts $fh "  \"boundary_pips_in_dut\": $bnd_in,"
puts $fh "  \"boundary_pips_out_dut\": $bnd_out,"
puts $fh "  \"const_pips_in_dut\": $const_in,"
puts $fh "  \"nets\": {\"total\": [llength $allnets], \"core\": [llength $kn], \"boundary\": [llength $bn], \"shell\": [llength $sn], \"const\": [llength $cn], \"boundary_corename_overlap\": $nb_overlap},"
puts $fh "  \"elapsed_s\": $t1"
puts $fh "}"
close $fh

close_design
log "done ${t1}s"
