set shell_candidates [get_cells -hier -filter {IS_PRIMITIVE && PRIMITIVE_LEVEL != INTERNAL && NAME !~ */u_dut/g_k.u_kernel/* && PRIMITIVE_GROUP != IO && PRIMITIVE_GROUP != CLK && PRIMITIVE_GROUP != OTHERS}]
set shell_added {}
foreach cell $shell_candidates {
    if {[lsearch -exact [get_pblocks -quiet -of_objects $cell] pb_shell] < 0} {
        lappend shell_added $cell
    }
}
if {[llength $shell_added]} {
    add_cells_to_pblock pb_shell $shell_added
}
puts "POST_OPT_SHELL_PBLOCK added=[llength $shell_added] eligible=[llength $shell_candidates]"
foreach cell [lsort $shell_added] {
    puts "POST_OPT_SHELL_ADDED_CELL $cell ref=[get_property REF_NAME $cell]"
}
