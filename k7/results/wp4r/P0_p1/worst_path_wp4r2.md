# P0（p1）wp4r2 构建最差路径

来源：`timing_impl_wp4r2.rpt`（布线后）。内核时钟 `clk_out2_clk_wiz_k7`，8.000 ns（125 MHz）。

- Setup：WNS = 0.118 ns（0 个失败端点）。最差路径：`u_core/u_dut/kernel_rst_reg/C` → 第 2 级编码级译码器 `u_decode/sy0i_reg[32]/R`（复位脚），数据路径 7.438 ns，其中布线 96.5%——是复位分发路径，不是译码逻辑路径。批次 1（wp4r）最差路径 WNS = 0.031763 ns，同为 `kernel_rst_reg` 出发的复位路径（第 4 级乘法器复位脚）。
- Hold：WHS = 0.054 ns（0 个失败端点）。
- 全设计 WNS = 0.118 ns、WHS = 0.054 ns（`check_impl_wp4r2.txt` 的 DESIGN 行）。

时序收敛，未触发第 11.7 节的"编码级译码路径加一拍"。
