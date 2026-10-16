#!/bin/sh
# Build and run the SV-UVM testbench with Verilator (>= 5.036 needed for UVM;
# verified with 5.052) and the Accellera UVM library.
#
# usage: UVM_HOME=/path/to/uvm-core/src sv_uvm/run_verilator.sh <outdir> [extra verilator args, e.g. +define+BUG_DECODE]
# runs soc_reg_test and soc_decode_test; logs go to <outdir>/<test>.log
set -eu
here=$(cd "$(dirname "$0")" && pwd)
root=$here/..
out=$1; shift
mkdir -p "$out"
: "${UVM_HOME:?set UVM_HOME to the uvm-core src directory}"
verilator --version | tee "$out/verilator_version.txt"
verilator --binary --timing --assert -j "$(nproc)" -Wno-fatal -Wno-lint -Wno-style \
    +define+UVM_NO_DPI +incdir+"$UVM_HOME" "$@" \
    "$UVM_HOME/uvm_pkg.sv" \
    "$root/rtl/periph_soc.sv" "$root/rtl/apb_uart.sv" "$root/rtl/apb_timer.sv" "$root/rtl/apb_gpio.sv" \
    "$root/sva/soc_sva.sv" "$root/sva/sva_bind.sv" \
    "$here/apb_if.sv" "$here/soc_uvm_pkg.sv" "$here/tb_top.sv" \
    --top-module tb_top -Mdir "$out/obj_dir" -o Vtb > "$out/build.log" 2>&1 || { tail -40 "$out/build.log"; exit 1; }
status=0
for t in soc_reg_test soc_decode_test; do
    "$out/obj_dir/Vtb" +UVM_TESTNAME=$t +verilator+seed+1 > "$out/$t.log" 2>&1 || status=1
    errs=$(grep -E '^UVM_(ERROR|FATAL) +: +[0-9]+' "$out/$t.log" | tr -s ' ' | tr '\n' ' ')
    sva=$(grep -c 'SVA_FAIL' "$out/$t.log" || true)
    echo "$t: $errs SVA_FAIL lines: $sva"
    grep -qE '^UVM_ERROR +: +0$' "$out/$t.log" && grep -qE '^UVM_FATAL +: +0$' "$out/$t.log" && [ "$sva" = 0 ] || status=1
done
rm -rf "$out/obj_dir"
exit $status
