# IEEE TC Submission Release (October 2026)

Code and summary tables for the IEEE Transactions on Computers (TC) paper
*"Arithmetic-Efficient Multi-Stage Error Tolerance in Parallel SDF FFTs via Operator Homogeneity"* (submitted October 2026).

The release covers six kernels of the 1024-point, four-lane, ten-stage
radix-2 SDF FFT family on a Kintex-7 (xc7k325tffg900-2): four injected
designs (P0, P1, S0, S1) and two resource-only designs (S1h, P1h). All
kernels are fixed-point; per-stage component widths and the twiddle
fractional point are in `k7/rtl/paper_fft12/common/fft_wg_pkg.sv`
(`IN_W = 16`, twiddle `Q2.16`, growing-width `S<s>_W -> S<s>_WO` datapath).
As in the paper, delay memories are mapped to distributed RAM and twiddle
tables to LUTs by the same rules (kernel RAMB counts are zero in the
wp4r2 resource tables; delay storage appears as LUTRAM).

## Designs in the paper

| Paper name | Directory | `PAPER_ARCH` | Build commit (fault_tolerant_SDF_FFT_designs, `resubmit-k7`) | Bitstream SHA-256 | Injected |
|---|---|---|---|---|---|
| P0 | `k7/rtl/paper_fft12/p1` | 5 | `a97b03b59bd3569e9688cffaf721eb1391b40347` | `53735c3a40e044b55bd41aa57f2b228bbfeb58c34be6b8c328e6457ba1c47707` | yes |
| P1 | `k7/rtl/paper_fft12/p2` | 6 | `a97b03b59bd3569e9688cffaf721eb1391b40347` | `9cb4a3ccb18d9ff7dd573df62aa293f62947b9f0e73f7292837ce95aeac45848` | yes |
| S0 | `k7/rtl/paper_fft12/s3` | 3 | `a292d7d8841738734959993736e84a135f7affd4` | `c970ff7881186f0f21578d791f6d965790f1cc4d09221637f363a70a62e858fe` | yes |
| S1 | `k7/rtl/paper_fft12/s2` | 1 | `20afd6810150caae73cf062fc857efd4430692e4` | `1cb7c6591e6539e7e3631bba06efa8eadd7731c5293b7a047591d2b18309e818` | yes |
| S1h | `k7/rtl/paper_fft12/s2hr` | 10 | `f999f79efaccf6f8c3471fa154dbf7d02b602820` (built as `29a3e54`; identical RTL after a local rebase) | `18af9143e19a353fa3c884d4eaf6950467b3a7c58318d3cfe545b369b3f8a183` | no (resource only) |
| P1h | `k7/rtl/paper_fft12/p2hr` | 11 | `f999f79efaccf6f8c3471fa154dbf7d02b602820` (built as `29a3e54`; identical RTL after a local rebase) | `81debbd01e4922461aa8cb714ec025955d3acb2a7014dccc7fab41f905f8d9a9` | no (resource only) |

The build commits are in the authors' working repository, which is not
public; this release carries the RTL of its later commit `115ae6b`.

Directories of earlier designs that are not in the paper: `s0 s1 p0 s2h
p2h p0x obs`. The paper designs reuse the arithmetic primitives in `s0`
(SubFFT) and `p0` (PFFT), which `k7/vivado/create_k7_paper.tcl` adds to
their builds.

## Reproducing the paper

### Rebuilding a bitstream

Each bitstream was built from the commit listed in the table. This
release carries the RTL of a later commit, `115ae6b`. A file-by-file
audit found that every source change between each build commit and
`115ae6b` only adds code that the listed design does not elaborate:
generate branches selected by `PAPER_ARCH` values that the design does
not take, or a module that only such a branch instantiates. A design
built from this release therefore elaborates the same RTL as its build
commit. To build one, run Vivado 2018.3 from `k7/`:

```
vivado -mode batch -source vivado/create_k7_paper.tcl -tclargs p1 1 wp4r2
```

where the first argument is the directory name from the table (`p1`,
`p2`, `s3`, `s2`, `s2hr`, `p2hr`) and the last names the build; the
injected bitstreams were built as `wp4r2`, S1h and P1h as `wp10b`. The
script generates the clock and SEM IP cores itself. The SHA-256 in the
table identifies each bitstream used in the paper.

### Fault injection settings

Injection runs against the four injected designs use the Xilinx SEM IP:
while the SEM controller is idle, the chosen configuration bits, one for
a single upset and two for a pair, are flipped through the ICAP and held
while ten input frames stream through; the kernel output is
compared word-for-word against the fixed-point golden model, and the SEM
repair is confirmed afterwards. Host-side scripts:

- `k7/scripts/k7_mod/wp6_sem_campaign.py` — campaign driver
  (`prereg`, `singles`, `repeat`, `pairs`, `resume` subcommands);
- `k7/scripts/k7_cram/k7_sem_common.py` — SEM command channel, FAR/LFA
  packing, run/compare/repair primitives;
- `k7/host/gui/board_if.py`, `k7/host/gui/protocol.py` — UART/UDP board
  transport used by the campaign.

Sample sizes, seeds and the outcome classification were fixed before
injection; see `k7/results/wp6/prereg.json`, `prereg_batch4.json` and
`prereg_addendum_1.md`.

## Summary tables in `k7/results/`

| File | Supports |
|---|---|
| `wp6/MAIN_TABLES.md` | Table II (injection outcomes) and per-shot counts in the single-shot section |
| `wp6/S27_tns_tables.md` | Mechanism classification of single-shot failures (454 -> 432 + 18 + 4) |
| `wp6/prereg.json`, `wp6/prereg_batch4.json`, `wp6/prereg_addendum_1.md` | Sample sizes, seeds and classification fixed before injection |
| `wp4r/<design>/functional_breakdown_wp4r2.md` (P0_p1, P1_p2, S0_s3, S1_s2) | Per-function Logic LUT quoted in the resource-cost section (encoders, syndromes, decoders and SECDED) |
| `wp4r/<design>/worst_path_wp4r2.md` | Slack column of Table III |
| `wp4r/<design>/build_summary_wp4r2.json` | Table III kernel resource totals (`kernel` section) |
| `wp10/REPORT.md`, `wp10/S1h_s2hr/functional_breakdown_wp10b.md`, `wp10/P1h_p2hr/functional_breakdown_wp10b.md` | S1h/P1h resource results |
| `wp7b/REPORT.md` | Dynamic power estimates (not reported in the paper) |

## Licenses

This release is under the MIT License (`LICENSE`).
`k7/third_party/prjxray-db` is data from Project X-Ray and keeps its own
license (`k7/third_party/prjxray-db/LICENSE`). `k7/third_party/
layout_ecc_sim` is a vendored subset of the authors' own SEM LFA-packing
module (constants from Xilinx UG470/PG036 and Project X-Ray
documentation), required by `k7/scripts/k7_cram/k7_sem_common.py`.
