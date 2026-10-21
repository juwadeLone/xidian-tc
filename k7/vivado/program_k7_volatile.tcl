# User-authorized SRAM configuration only; never accesses configuration flash.
if {$argc != 1} {error "usage: -tclargs bitfile"}
set bitfile [file normalize [lindex $argv 0]]
if {![file exists $bitfile]} {error "missing bitfile: $bitfile"}
open_hw
connect_hw_server
open_hw_target
set devices [get_hw_devices -filter {PART == xc7k325t}]
if {[llength $devices] != 1} {error "expected exactly one xc7k325t"}
set device [lindex $devices 0]
current_hw_device $device
set_property PROGRAM.FILE $bitfile $device
program_hw_devices $device
refresh_hw_device $device
puts "K7_VOLATILE_PROGRAMMED device=$device bitfile=$bitfile"
catch { close_hw_target }
catch { disconnect_hw_server }
