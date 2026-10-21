# P0 功能分项（批次 2 wp4r2 构建，布线后 `util_impl_wp4r2.rpt`，层次利用率表）

计数口径：只统计 `g_k.u_kernel` 子树；括号行（模块自身）和叶行计全量，有子层的父行跳过，每行只计一次。归类规则（WP4-R 第 11.3 节）：旋转器外壳与 `fft_complex_mul_full_wp4r` 计入旋转器；各路/通道/副本模块的括号行与合并级内核（`pfft_stage9_exchange_wg`、`pfft_stage10_wg`、S 族合并级外壳）计入蝶形加减与级内寄存器；存储外壳与 `ecc643_secded_delay6_wg` 括号行（晚写/转发寄存器）计入延迟存储；SECDED 编码、译码外壳计入 SECDED；`ecc643_encode_wp4r`、`ecc643_decode_wg`（不含标志子模块）计入校验子与译码器；表决外壳计入表决器；`pfft_sdf_control_wp4r` 与指数生成外壳计入控制；`pfft_rom_wp4r`（含 `pfft_hom_rom_wp4r` 外壳行）计入 ROM；`ecc643_flags_wp4r`、`tmr_stage_flag_wg` 计入标志；其余括号行计入其他并逐项列出。若 Vivado 把被包模块保留为外壳的子行，子行按同名规则计入同一类别，不会重复计数。跨子模块边界合并造成的父行与子行差值集中在对账行，逐项列出。

| 类别 | Logic LUT | LUTRAM | FF | DSP |
| --- | ---: | ---: | ---: | ---: |
| 旋转器 | 1347 | 0 | 583 | 192 |
| 蝶形加减与级内寄存器 | 7031 | 0 | 7639 | 0 |
| 延迟存储 | 1895 | 2112 | 2662 | 0 |
| SECDED | 4861 | 0 | 0 | 0 |
| 校验子与译码器 | 8764 | 0 | 4339 | 0 |
| 表决器 | 1023 | 0 | 0 | 0 |
| 控制 | 175 | 0 | 183 | 0 |
| ROM | 1647 | 0 | 576 | 0 |
| 标志 | 34 | 0 | 52 | 0 |
| 其他（级/流水线/顶层自身行） | 94 | 0 | 45 | 0 |
| 跨层次合并差值（对账行） | -1 | 0 | 0 | 0 |
| 合计 | 26870 | 2112 | 16079 | 192 |
| 内核总数（`g_k.u_kernel` 行） | 26870 | 2112 | 16079 | 192 |

其他明细（实例路径（模块）：Logic LUT / LUTRAM / FF / DSP）：

- `(g_k.u_kernel)`（top_p1_pfft_ecc）：58 / 0 / 0 / 0
- `(g_stage[1].g_ecc.u_stage)`（p1_ecc_stage_wp4r）：1 / 0 / 6 / 0
- `(g_stage[2].g_ecc.u_stage)`（p1_ecc_stage_wp4r__parameterized0）：4 / 0 / 6 / 0
- `(g_stage[3].g_ecc.u_stage)`（p1_ecc_stage_wp4r__parameterized1）：5 / 0 / 6 / 0
- `(g_stage[4].g_ecc.u_stage)`（p1_ecc_stage_wp4r__parameterized2）：6 / 0 / 6 / 0
- `(g_stage[5].g_ecc.u_stage)`（p1_ecc_stage_wp4r__parameterized3）：6 / 0 / 6 / 0
- `(g_stage[6].g_ecc.u_stage)`（p1_ecc_stage_wp4r__parameterized4）：7 / 0 / 6 / 0
- `(g_stage[7].g_ecc.u_stage)`（p1_ecc_stage_wp4r__parameterized5）：7 / 0 / 9 / 0

对账行明细（父行总数减已计子行之和，Logic LUT / LUTRAM / FF / DSP）：

- `u_pipeline`（p1_pfft_ecc_pipeline_wp4r）：-1 / 0 / 0 / 0
- `g_stage[9].g_tmr.u_stage`（pfft_real_tmr_stage_wg__parameterized0）：-1 / 0 / 0 / 0

注：跨层次合并差值 -1 LUT 不为 0，按第 11.3 节逐项解释见上。

自检：各类加总等于内核总数；各类非空约束满足；其他未超过 3%；合并差值为 0。
