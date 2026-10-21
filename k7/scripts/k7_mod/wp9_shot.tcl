# WP9 optional GUI capture — P0 (P1_WP4R2), read-only (open_checkpoint only,
# no design edits, no write_checkpoint). Two-phase: wait for flag files so the
# caller can capture the window between layout and routing states.
# Run: vivado -mode gui -source wp9_shot.tcl
open_checkpoint /home/xdu/frozen/wp6/P1_WP4R2/k7_paper_top_routed.dcp

# group colours: kernel blue, SEM red; show pblock outlines by selecting them
set kcells [get_cells -hier -filter {NAME =~ u_core/u_dut/g_k.u_kernel/* && IS_PRIMITIVE}]
highlight_objects -color blue $kcells
set semcells [get_cells -hier -filter {(NAME =~ u_core/u_sem_wrapper/* || NAME =~ u_core/u_sem_cdc/* || NAME =~ u_core/u_sem_bridge/*) && IS_PRIMITIVE}]
mark_objects -color red $semcells
select_objects [get_pblocks]
puts "WP9SHOT_LAYOUT_READY"
file delete -force /tmp/wp9shot_go1 /tmp/wp9shot_go2
while {![file exists /tmp/wp9shot_go1]} { after 500 }

# show routing: select all nets so device view draws routed paths
select_objects [get_nets]
select_objects -add [get_pblocks]
puts "WP9SHOT_ROUTING_READY"
while {![file exists /tmp/wp9shot_go2]} { after 500 }
exit
