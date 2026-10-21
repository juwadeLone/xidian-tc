# S1（s2）wp4r2 构建最差路径

来源：`timing_impl_wp4r2.rpt`（布线后）。内核时钟 `clk_out2_clk_wiz_k7`，8.000 ns（125 MHz）。

- Setup：WNS = 0.328 ns（0 个失败端点）。最差路径：`u_core/u_dut/d_in_valid_r_reg/C` → 第 1 级副本 1 第 2 通道 `in_im_q_reg[5]/CE`。
- Hold：WHS = 0.051 ns（0 个失败端点）。最差保持路径：第 6 级副本 0 第 0 通道 `in_re_q_reg[8]/C` 出发。
- 全设计 WNS = 0.328 ns、WHS = 0.051 ns（`check_impl_wp4r2.txt`）。

时序收敛。
