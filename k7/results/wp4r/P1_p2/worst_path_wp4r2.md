# P1（p2）wp4r2 构建最差路径

来源：`timing_impl_wp4r2.rpt`（布线后）。内核时钟 `clk_out2_clk_wiz_k7`，8.000 ns（125 MHz）。

- Setup：WNS = 0.320 ns（0 个失败端点）。最差路径：`u_core/u_dut/kernel_rst_reg/C` → 第 2 级副本 1 `lane_re_reg[2][17]/R`（复位脚），是复位分发路径。
- Hold：WHS = 0.049 ns（0 个失败端点）。最差保持路径：第 8 级副本 2 存储 `u_memory/u/g_async_distributed.mem_reg[99]/C` → 第 1 路平凡旋转 `g_trivial.r1_reg[24]/D`。
- 全设计 WNS = 0.320 ns、WHS = 0.049 ns（`check_impl_wp4r2.txt` 的 DESIGN 行）。

时序收敛。批次 1（wp4r）WNS = 0.385838 ns、WHS = 0.039638 ns。
