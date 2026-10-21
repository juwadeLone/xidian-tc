`timescale 1ns/1ps
// Paper-FFT K7 SEM board top: UDP payload -> paper FFT kernel -> UDP,
// UART command layer and the SEM injection channel, with the DDR3
// subsystem removed outright -- no MIG, no AXI clock converter, no DDR
// pins on the module.  k7_udp_ddr_top's port set minus ddr3_*.
//
// There is no calibration to wait for, so the core's calib_done is the
// processing domain's own reset release.
module k7_paper_top (
    input  wire        sysclk,        // AD12, 100 MHz, SSTL135
    output wire        uart_tx_o,     // Y26, LVCMOS18
    input  wire uart_rx_i,
    output wire phy_rst_a_o, phy_rst_b_o,
    input wire PHYD_rgmii_rxc, PHYD_rgmii_rx_ctl,
    input wire [3:0] PHYD_rgmii_rxd,
    output wire PHYD_rgmii_txc, PHYD_rgmii_tx_ctl,
    output wire [3:0] PHYD_rgmii_txd
);
    // Power-on reset: no reset pin is assigned, because the board's button
    // pin is not established and guessing an IO location is worse than doing
    // without one.  Power cycling or reprogramming restarts the run.
    reg [15:0] por_cnt = 16'd0;
    always @(posedge sysclk)
        if (!por_cnt[15]) por_cnt <= por_cnt + 16'd1;
    wire por_rst_n = por_cnt[15];

    wire clk200, clk125, clk125_90, mmcm_locked;
    // ICAPE2 (inside the SEM subsystem) caps at 100 MHz, so it cannot share
    // clk125.  sysclk is already the right frequency; buffer it out in
    // parallel with the clk_wiz input and hand it to the core.
    wire icap_clk;
    BUFG u_icap_bufg (.I(sysclk), .O(icap_clk));
    clk_wiz_k7 u_clk (
        .clk_in1(sysclk),
        .clk_out1(clk200), .clk_out2(clk125), .clk_out3(clk125_90),
        .reset(~por_rst_n),
        .locked(mmcm_locked)
    );

    // Reset distribution lives in k7_reset_tree so it can be simulated.
    // There is no MIG: ui_clk takes clk125 (the aresetn output is unused) and
    // ui_clk_sync_rst gets its released value -- without a MIG there is no
    // upstream reset for the processing domain to wait on, so it comes up as
    // soon as the MMCM locks.
    wire proc_rst_n, rx_rst_n, ref_rst_n;
    k7_reset_tree u_rst (
        .sysclk(sysclk), .por_rst_n(por_rst_n), .mmcm_locked(mmcm_locked),
        .ui_clk(clk125), .ui_clk_sync_rst(1'b0),
        .clk125(clk125), .clk200(clk200), .rgmii_rxc(PHYD_rgmii_rxc),
        .aresetn(), .proc_rst_n(proc_rst_n),
        .rx_rst_n(rx_rst_n), .ref_rst_n(ref_rst_n)
    );
    assign phy_rst_a_o = por_rst_n;
    assign phy_rst_b_o = por_rst_n;
    reg [4:0] tap_boot;
    always @(posedge clk200) begin
        if (!ref_rst_n) tap_boot <= 0;
        else if (tap_boot != 31) tap_boot <= tap_boot + 1'b1;
    end
    wire gmii_rxc, gmii_rx_dv, gmii_tx_dv;
    wire [7:0] gmii_rd, gmii_td;
    rgmii_to_gmii u_rx (
        .idelay_clk(clk200), .tap_value(5'd16), .tap_load(tap_boot == 16),
        .rst(!rx_rst_n), .rgmii_rxc(PHYD_rgmii_rxc),
        .rgmii_rx_ctl(PHYD_rgmii_rx_ctl), .rgmii_rd(PHYD_rgmii_rxd),
        .gmii_rxc(gmii_rxc), .gmii_rx_dv(gmii_rx_dv), .gmii_rx_err(), .gmii_rd(gmii_rd)
    );
    gmii_to_rgmii u_tx (
        .rst(!proc_rst_n), .idelay_clk(clk200), .gmii_txc(clk125),
        .txc_fwd_clk(clk125_90), .gmii_tx_dv(gmii_tx_dv),
        .gmii_tx_err(1'b0), .gmii_td(gmii_td),
        .rgmii_txc(PHYD_rgmii_txc), .rgmii_tx_ctl(PHYD_rgmii_tx_ctl),
        .rgmii_td(PHYD_rgmii_txd)
    );

`ifdef PAPER_ARCH
    localparam integer PAPER_ARCH_SEL = `PAPER_ARCH;
`else
    localparam integer PAPER_ARCH_SEL = 3;      // S3 when unspecified
`endif
`ifdef ARITH_THRESHOLD
    localparam integer ARITH_THRESHOLD_SEL = `ARITH_THRESHOLD;
`else
    localparam integer ARITH_THRESHOLD_SEL = 0; // kernel default (16/4/8)
`endif

    k7_paper_core #(
        .PAPER_ARCH(PAPER_ARCH_SEL),
        .ARITH_THRESHOLD(ARITH_THRESHOLD_SEL)
    ) u_core (
        .clk(clk125),
        .rst_n(proc_rst_n),
        .icap_clk(icap_clk),
        .icap_rst_n(por_rst_n),
        .uart_rx_i(uart_rx_i),
        .uart_tx_o(uart_tx_o),
        // No MIG calibration exists; out-of-reset is the ready signal.
        .calib_done(proc_rst_n),
        .rx_clk(gmii_rxc),
        .rx_rst(!rx_rst_n),
        .gmii_rx_dv(gmii_rx_dv),
        .gmii_rd(gmii_rd),
        .gmii_tx_dv(gmii_tx_dv),
        .gmii_td(gmii_td)
    );
endmodule
