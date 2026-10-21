# Project X-Ray database subset (kintex7)

Source: https://github.com/f4pga/prjxray-db, commit
`0a0addedd73e7e4139d52a6d8db4258763e0f1f3` (2021-12-14), directory `kintex7/`,
fetched 2026-10-16 from `raw.githubusercontent.com/f4pga/prjxray-db/<commit>/kintex7/`.
License: CC0-1.0 (`LICENSE`, unchanged).

Only the tile types of the core regions are kept: `segbits_`, `ppips_` and
`mask_` files for CLBLL_L/R, CLBLM_L/R, INT_L/R (no mask file upstream),
DSP_L/R and BRAM_L/R (BRAM content files `*.block_ram.db` omitted).  The files
are byte-for-byte copies.  The kintex7 database was built on xc7k70t; only
the tile-type bit tables are used here, with the board-measured frame table v2
for the xc7k325t addresses; its validity on our bitstreams is decided by the
WP6-A self-checks.  Used by `scripts/k7_mod/wp6a_bitmap.py`.
