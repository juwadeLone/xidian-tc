#!/usr/bin/env bash
# Sequential Vivado builds for the 5 remaining paper_fft12 arches.
# s0/p0 bitstreams already exist and are unchanged.
set -u
VIVADO=/c/Xilinx/Vivado/2018.3/bin/vivado.bat
cd "$(dirname "$0")/../.."
mkdir -p results/build_logs
for a in "$@"; do
    A=$(echo "$a" | tr a-z A-Z)
    log="results/build_logs/${a}_build.log"
    echo "=== $a build start $(date -Is) ===" | tee "$log"
    "$VIVADO" -mode batch -source vivado/create_k7_paper.tcl -tclargs "$a" 1 \
        >> "$log" 2>&1
    rc=$?
    # evidence: bitstream exists + timing WNS from the log
    bit="build/vivado/K7_PAPER_${A}/K7_PAPER_${A}.runs/impl_1/k7_paper_top.bit"
    wns=$(grep -o "WNS=[-0-9.]*" "$log" | tail -1)
    if [ -f "$bit" ]; then
        sha=$(sha256sum "$bit" | cut -d' ' -f1)
        echo "=== $a BUILD_OK rc=$rc $wns bit_sha256=$sha $(date -Is) ===" | tee -a "$log"
    else
        echo "=== $a BUILD_FAIL rc=$rc $wns $(date -Is) ===" | tee -a "$log"
    fi
done
