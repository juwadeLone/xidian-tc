# PHYD RGMII loopback constraints.
#
# PHYD, not PHYA: measured 2026-09-09 by enabling the vendor throughput design's
# channels one at a time -- cha/chb/chc gave 0 MB/s, chd gave 111.1 MB/s.  Pin
# locations copied from 02_udp_txspeed_rgmii/uisrc/04_pin/fpga_pin.xdc.  The
# mapping follows the cable, so re-measure if the port changes.
# See contracts/k7_board_facts.md.

create_clock -period 10.000 -name sysclk [get_ports sysclk]
set_property PACKAGE_PIN AD12 [get_ports sysclk]
set_property IOSTANDARD SSTL135 [get_ports sysclk]

# 125 MHz recovered from the PHY.
create_clock -period 8.000 -name rgmii_rxc [get_ports PHYD_rgmii_rxc]

set_property PACKAGE_PIN L25 [get_ports PHYD_rgmii_rxc]
set_property PACKAGE_PIN J21 [get_ports PHYD_rgmii_rx_ctl]
set_property PACKAGE_PIN J22 [get_ports {PHYD_rgmii_rxd[0]}]
set_property PACKAGE_PIN L21 [get_ports {PHYD_rgmii_rxd[1]}]
set_property PACKAGE_PIN K21 [get_ports {PHYD_rgmii_rxd[2]}]
set_property PACKAGE_PIN L22 [get_ports {PHYD_rgmii_rxd[3]}]
set_property PACKAGE_PIN L23 [get_ports PHYD_rgmii_txc]
set_property PACKAGE_PIN K25 [get_ports PHYD_rgmii_tx_ctl]
set_property PACKAGE_PIN M22 [get_ports {PHYD_rgmii_txd[0]}]
set_property PACKAGE_PIN M23 [get_ports {PHYD_rgmii_txd[1]}]
set_property PACKAGE_PIN N21 [get_ports {PHYD_rgmii_txd[2]}]
set_property PACKAGE_PIN N22 [get_ports {PHYD_rgmii_txd[3]}]

set_property IOSTANDARD LVCMOS33 [get_ports PHYD_rgmii_*]
set_property SLEW FAST [get_ports PHYD_rgmii_txc]
set_property SLEW FAST [get_ports PHYD_rgmii_tx_ctl]
set_property SLEW FAST [get_ports {PHYD_rgmii_txd[*]}]

set_property PACKAGE_PIN W21 [get_ports phy_rst_a_o]
set_property PACKAGE_PIN T20 [get_ports phy_rst_b_o]
set_property IOSTANDARD LVCMOS33 [get_ports phy_rst_a_o]
set_property IOSTANDARD LVCMOS33 [get_ports phy_rst_b_o]

# Every IDELAYE2 and the IDELAYCTRL driving them must share this group; the
# attribute is on the instances inside rgmii_to_gmii.
# Only the Ethernet controller. MIG has two separate delay groups/refclocks.
set_property IODELAY_GROUP rgmii_rx_delay [get_cells u_rx/IDELAYCTRL_inst]

# sysclk and the PHY's recovered rxc are unrelated: one is the board oscillator,
# the other comes back from the link.  Without this the tool times the reset
# crossing between them as a real path.  The crossing itself is handled by the
# synchroniser in k7_rgmii_loop_top; this only stops the tool from inventing a
# requirement between two clocks that never had a phase relationship.
# -include_generated_clocks: without it this only groups the primary clock and
# leaves the MMCM outputs (clk200, the 90-degree txc) timed against rgmii_rxc.
set_clock_groups -asynchronous -group [get_clocks -include_generated_clocks sysclk] -group [get_clocks -include_generated_clocks rgmii_rxc]

# The MIG's clocks are grouped asynchronously in k7_udp_ddr_impl.xdc, not here.
# This file is used in synthesis, and Vivado re-serialises a synthesis-used XDC
# into propImpl.xdc for implementation to read -- dropping every "set" and
# substituting each $variable with empty text as it writes.  A constraint built
# from variables therefore arrives at implementation as
#     if {[llength ] && [llength ]} { set_clock_groups -group  -group  }
# which is not an error, just nothing.  XDC is not a Tcl script; keep the
# queries inline and single-command, or put the constraint where it is read
# directly.  See results/k7_port/PHASE3_TIMING.md.

# A reset synchroniser exists precisely to take an asynchronous assertion; the
# path into its clear pin is not a timed path and reporting it as one is noise.
# This covers the sysclk -> clk125 recovery check that the async clock groups
# do not, because base_rst_n and the domains it resets all descend from sysclk.
#
# Scoped to u_rst/*, not */k7_reset_sync*/*: a pin's NAME is its instance path,
# and the module name never appears there, so that filter matched nothing.
# Written as one command with the query inline -- an earlier version assigned
# the pins to a variable first and was silently reduced to "set_false_path -to"
# with no argument by the propagation described above.
set_false_path -to [get_pins -hier -filter {NAME =~ u_rst/*/sync_reg[*]/CLR}]

set_property DCI_CASCADE {32 34} [get_iobanks 33]

set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property BITSTREAM.GENERAL.COMPRESS true [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE 50 [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]
set_property BITSTREAM.CONFIG.SPI_FALL_EDGE Yes [current_design]

set_property PACKAGE_PIN Y26 [get_ports uart_tx_o]
set_property PACKAGE_PIN AA26 [get_ports uart_rx_i]
set_property IOSTANDARD LVCMOS18 [get_ports {uart_tx_o uart_rx_i}]

