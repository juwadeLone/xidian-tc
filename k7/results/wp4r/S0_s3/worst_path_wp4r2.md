# S0（s3）wp4r2 构建最差路径

来源：`timing_impl_wp4r2.rpt`（布线后）。内核时钟 `clk_out2_clk_wiz_k7`，8.000 ns（125 MHz）。

- Setup：WNS = 0.323 ns（0 个失败端点）。最差路径：`u_core/u_dut/kernel_rst_reg/C` → 第 6 级编码级第 1 路 `u_control/out_count_reg[0]/R`（复位脚），复位分发路径。
- Hold：WHS = 0.056 ns（0 个失败端点）。最差保持路径：第 1 级第 4 路 `u_control/pending_wr_addr_reg[5]/C` 出发。
- 全设计 WNS = 0.323 ns、WHS = 0.056 ns（`check_impl_wp4r2.txt`）。

时序收敛，未触发加拍。
