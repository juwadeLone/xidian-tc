# Implementation-only constraints for k7_paper_top.
#
# Same role as k7_udp_ddr_impl.xdc, minus the MIG clock groups: the paper
# image has no MIG at all, so there are no MIG clocks to separate from.
# Why this file exists at all, and the propImpl.xdc re-serialisation pitfall
# it works around, are documented in k7_udp_ddr_impl.xdc.

# ---- SEM command CDC (u_core/u_sem_cdc): clk125 <-> icap_clk (sysclk) ------
# sysclk feeds the clk_wiz input, so clk_out2 (125 MHz) and the icap_clk BUFG
# land in the same clock group and every crossing below is otherwise timed.
# The request bundle (flags/timeout/len/payload) is latched in the command
# domain once per SEM transaction and held frozen while cmd_seq waits in
# S_WAIT_SEM; the response bundle (err/len/payload) is latched in the icap
# domain at bridge done and held until the next transaction ends.  Both are
# protocol-stable buses, not timed paths -- only the req/ack toggles matter,
# and those land on ASYNC_REG synchronisers (req_sync/ack_sync).
set_false_path -from [get_cells -hier -filter {NAME =~ *u_sem_cdc/flags_q_reg* || \
    NAME =~ *u_sem_cdc/timeout_q_reg* || NAME =~ *u_sem_cdc/len_q_reg* || \
    NAME =~ *u_sem_cdc/payload_q_reg* || NAME =~ *u_sem_cdc/req_tgl_reg*}] \
    -to [get_clocks sysclk]
set_false_path -from [get_cells -hier -filter {NAME =~ *u_sem_cdc/err_q_reg* || \
    NAME =~ *u_sem_cdc/resp_len_q_reg* || \
    NAME =~ *u_sem_cdc/resp_payload_q_reg* || NAME =~ *u_sem_cdc/ack_tgl_reg*}] \
    -to [get_clocks clk_out2_clk_wiz_k7]
# Quasi-static SEM status bits reach the command domain through the two-flop
# sem_stat_m/sem_stat_s pair (ASYNC_REG); the source regs are the SEM core's
# status pins inside u_sem_wrapper.
set_false_path -from [get_cells -hier -filter {NAME =~ *u_sem_wrapper/*}] \
    -to [get_cells -hier -filter {NAME =~ *u_sem_cdc/*_m_reg* || \
    NAME =~ *sem_stat_m*}]

# ---- Floorplan: kernel and shell in disjoint, hard-contained regions -------
# The FFT kernel (g_k.u_kernel) is one connected block of clock regions:
#   pb_dut    CLOCKREGION_X0Y4:X0Y6 + CLOCKREGION_X1Y3:X1Y6
# It is exclusive (EXCLUDE_PLACEMENT TRUE): every kernel cell is placed
# inside it and no non-kernel cell may be placed in it. CONTAIN_ROUTING
# also constrains nets whose pins are all inside the kernel to this region.
# WP1 had no kernel routing containment: 51 S0 / 364 P0 kernel-only nets
# switched outside pb_dut (results/wp1/REPORT.md, step F 7). WP1b adds it;
# the measured routing counts are recorded in results/wp1b/REPORT.md.
# Nets crossing the kernel boundary (kernel ports and the shared 125 MHz
# clock) are not contained by this property. IS_SOFT is not supported by
# Vivado 2018.3 and is not set.
#
# Everything else (UART, UDP, SEM glue, engine, in_buf/out_buf) is in pb_shell:
#   pb_shell  CLOCKREGION_X0Y0:X0Y3 + CLOCKREGION_X1Y0:X1Y2
# pb_shell takes every primitive outside the kernel except pads and IO
# buffers, clock buffers / MMCM, and the configuration primitives (ICAP,
# FRAME_ECC), which keep their fixed sites.  CONTAIN_ROUTING keeps the
# shell's own nets switching inside pb_shell. Measured in WP1: no net with all its
# pins outside the kernel has a PIP in a pb_dut tile (clock 6, ICAP 121,
# pad 59, other 14943 / 14942 nets); 27 (S0) / 24 (P0) of the "other" nets
# own a wire that reaches into a pb_dut boundary tile without switching
# there -- the BRAM cascade address wires of in_buf/out_buf blocks in the top
# row of X0Y3 / X1Y2, and one or two hex/double wires.
# The exclusion is by the 2018.3 primitive groups IO, CLK (BUFG, MMCM) and
# OTHERS (ICAPE2, FRAME_ECCE2, VCC, GND): with a BUFG inside pb_shell a
# clock net whose loads are all shell cells counts as contained, and its
# route through the clock-spine tiles (no clock region) becomes illegal.
# The X0Y1..X0Y3 band holds the UART pads, both ICAP sites, the PHY reset
# pads and the RGMII pads with IDELAYCTRL_X0Y3, all on the shell side.
#
# The regions must not be changed without a new work package.
create_pblock pb_dut
add_cells_to_pblock pb_dut [get_cells -hier -filter {NAME =~ */u_dut/g_k.u_kernel}]
resize_pblock pb_dut -add {CLOCKREGION_X0Y4:CLOCKREGION_X0Y6 CLOCKREGION_X1Y3:CLOCKREGION_X1Y6}
# Hard containment both ways: kernel cells may not leave the region and no
# non-DUT cell (SEM/UDP/UART/in_buf/out_buf/engine) may be placed inside it.
set_property CONTAIN_ROUTING TRUE [get_pblocks pb_dut]
set_property EXCLUDE_PLACEMENT TRUE [get_pblocks pb_dut]

create_pblock pb_shell
add_cells_to_pblock pb_shell [get_cells -hier -filter {IS_PRIMITIVE && PRIMITIVE_LEVEL != INTERNAL && NAME !~ */u_dut/g_k.u_kernel/* && PRIMITIVE_GROUP != IO && PRIMITIVE_GROUP != CLK && PRIMITIVE_GROUP != OTHERS}]
resize_pblock pb_shell -add {CLOCKREGION_X0Y0:CLOCKREGION_X0Y3 CLOCKREGION_X1Y0:CLOCKREGION_X1Y2}
set_property CONTAIN_ROUTING TRUE [get_pblocks pb_shell]
