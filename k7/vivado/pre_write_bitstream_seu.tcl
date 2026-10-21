# Emit the essential-bits mask (.ebd) alongside the bitstream so the SEM
# campaign can attribute injected (LFA, word, bit) positions to essential
# configuration bits offline.  Same switch the A7 SEM build used
# (vivado/integrate_a7_sem.tcl wrote this inline).
set_property BITSTREAM.SEU.ESSENTIALBITS Yes [current_design]
