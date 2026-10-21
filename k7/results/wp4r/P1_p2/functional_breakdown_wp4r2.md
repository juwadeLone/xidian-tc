# P1 功能分项（批次 2 wp4r2 构建，布线后 `util_impl_wp4r2.rpt`，层次利用率表）

计数口径：只统计 `g_k.u_kernel` 子树；括号行（模块自身）和叶行计全量，有子层的父行跳过，每行只计一次。归类规则（WP4-R 第 11.3 节）：旋转器外壳与 `fft_complex_mul_full_wp4r` 计入旋转器；各路/通道/副本模块的括号行与合并级内核（`pfft_stage9_exchange_wg`、`pfft_stage10_wg`、S 族合并级外壳）计入蝶形加减与级内寄存器；存储外壳与 `ecc643_secded_delay6_wg` 括号行（晚写/转发寄存器）计入延迟存储；SECDED 编码、译码外壳计入 SECDED；`ecc643_encode_wp4r`、`ecc643_decode_wg`（不含标志子模块）计入校验子与译码器；表决外壳计入表决器；`pfft_sdf_control_wp4r` 与指数生成外壳计入控制；`pfft_rom_wp4r`（含 `pfft_hom_rom_wp4r` 外壳行）计入 ROM；`ecc643_flags_wp4r`、`tmr_stage_flag_wg` 计入标志；其余括号行计入其他并逐项列出。若 Vivado 把被包模块保留为外壳的子行，子行按同名规则计入同一类别，不会重复计数。跨子模块边界合并造成的父行与子行差值集中在对账行，逐项列出。

| 类别 | Logic LUT | LUTRAM | FF | DSP |
| --- | ---: | ---: | ---: | ---: |
| 旋转器 | 1617 | 0 | 603 | 336 |
| 蝶形加减与级内寄存器 | 13188 | 0 | 13809 | 0 |
| 延迟存储 | 210 | 3672 | 1008 | 0 |
| SECDED | 0 | 0 | 0 | 0 |
| 校验子与译码器 | 0 | 0 | 0 | 0 |
| 表决器 | 2968 | 0 | 0 | 0 |
| 控制 | 453 | 0 | 459 | 0 |
| ROM | 1947 | 0 | 906 | 0 |
| 标志 | 20 | 0 | 10 | 0 |
| 其他（级/流水线/顶层自身行） | 12 | 0 | 0 | 0 |
| 跨层次合并差值（对账行） | -7 | 0 | 0 | 0 |
| 合计 | 20408 | 3672 | 16795 | 336 |
| 内核总数（`g_k.u_kernel` 行） | 20408 | 3672 | 16795 | 336 |

其他明细（实例路径（模块）：Logic LUT / LUTRAM / FF / DSP）：

- `(g_k.u_kernel)`（top_p2_pfft_tmr_no_framebuf_v1）：12 / 0 / 0 / 0

对账行明细（父行总数减已计子行之和，Logic LUT / LUTRAM / FF / DSP）：

- `u_pipeline`（p2_pfft_real_tmr_pipeline_wp4r）：-7 / 0 / 0 / 0
- `g_stage[3].u_stage`（pfft_real_tmr_stage_wg__parameterized1）：-2 / 0 / 0 / 0
- `g_replica[1].g_sdf.u_copy`（pfft_sdf4_wp4r__parameterized1__2）：-1 / 0 / 0 / 0
- `g_replica[2].g_sdf.u_copy`（pfft_sdf4_wp4r__parameterized1）：-1 / 0 / 0 / 0
- `g_stage[5].u_stage`（pfft_real_tmr_stage_wg__parameterized3）：-3 / 0 / 0 / 0
- `g_replica[1].g_sdf.u_copy`（pfft_sdf4_wp4r__parameterized3__2）：-2 / 0 / 0 / 0
- `g_replica[2].g_sdf.u_copy`（pfft_sdf4_wp4r__parameterized3）：-1 / 0 / 0 / 0
- `g_stage[9].u_stage`（pfft_real_tmr_stage_wg__parameterized7）：-1 / 0 / 0 / 0
- `g_replica[1].g_s9.u_copy`（pfft_stage9_replica_wp4r__2）：-1 / 0 / 0 / 0
- `u_stage9`（pfft_stage9_exchange_wg__2）：-1 / 0 / 0 / 0

注：跨层次合并差值 -7 LUT 不为 0，按第 11.3 节逐项解释见上。

自检：各类加总等于内核总数；各类非空约束满足；其他未超过 3%；合并差值为 0。
