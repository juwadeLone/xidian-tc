`timescale 1ns/1ps

// Word widths of the growing-width paper_fft12 kernels (sim/fixed_fft_wgrow.py).
// Per component: stage s takes S<s>_W bits and emits S<s>_WO bits.  No stage
// shifts; the twiddle multiply keeps the low WO bits of product >>> TW_FRAC.
package fft_wg_pkg;
    localparam integer IN_W    = 16;   // input component width
    localparam integer TW_W    = 18;   // twiddle width, Q2.16
    localparam integer TW_FRAC = 16;

    localparam integer S1_W   = 16, S1_WO  = 18;
    localparam integer S2_W   = 18, S2_WO  = 19;
    localparam integer S3_W   = 19, S3_WO  = 20;
    localparam integer S4_W   = 20, S4_WO  = 21;
    localparam integer S5_W   = 21, S5_WO  = 22;
    localparam integer S6_W   = 22, S6_WO  = 23;
    localparam integer S7_W   = 23, S7_WO  = 24;
    localparam integer S8_W   = 24, S8_WO  = 25;
    localparam integer S9_W   = 25, S9_WO  = 26;
    localparam integer S10_W  = 26, S10_WO = 27;

    localparam integer OUT_W  = S10_WO;
endpackage
