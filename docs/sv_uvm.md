# Native SystemVerilog UVM testbench (`sv_uvm/`)

**Status: VERIFIED on Verilator 5.052 with Accellera UVM 2020.3.1.** Not run on a commercial simulator (BLOCKED: no licence; see `OWNER_ACTIONS.md`).

## What it is

A compact IEEE 1800.2 UVM environment for `periph_soc`, separate from the main pyuvm testbench:

* `apb_if.sv`: the APB interface.
* `soc_uvm_pkg.sv`:
  * `apb_item`, `apb_driver`, `apb_monitor`, `apb_agent` (with `uvm_sequencer`);
  * `soc_scoreboard`: a register-level reference model built from the register map. It checks decode and PSLVERR, the one UART wait state, reset values, RW masks and the baud clamp. Volatile registers are checked for PSLVERR only;
  * `soc_coverage`: 10 bins (each block read/written, slave error, wait state);
  * sequences `reg_rw_seq` (random writes/reads of the modelled registers) and `decode_err_seq` (unmapped blocks, offsets and decoder holes);
  * tests `soc_reg_test` and `soc_decode_test`.
* `tb_top.sv`: clock, reset, DUT, `run_test()`.
* `run_verilator.sh`: builds with `verilator --binary --timing --assert +define+UVM_NO_DPI`, includes the SVA from `sva/`, runs both tests and fails unless each log has `UVM_ERROR : 0`, `UVM_FATAL : 0` and no `SVA_FAIL` line.

## Versions

From `docs/evidence/2026-10-15/sv_uvm_versions.txt`:

* Simulator: Verilator 5.052 (2026-09-05), official Docker image `verilator/verilator:v5.052`, digest `sha256:a5b73e2fce0b2c483396f3800940f33b6faa802331a061c21694dd27b7352120`.
* UVM: Accellera `uvm-core` tag `2020.3.1`, commit `78c06547a2a0a29b3dc9dcafae62b75b2ff61544`.

Verilator 5.020 (the apt version used by the pyuvm flow) cannot compile the UVM library. A newer Verilator was pulled as a Docker image instead of built from source.

## Results (2026-10-15)

Logs: `docs/evidence/2026-10-15/sv_uvm_clean/`.

| Test | Transfers checked | Mismatches | Coverage | UVM_ERROR | UVM_FATAL | SVA_FAIL |
|---|---|---|---|---|---|---|
| soc_reg_test | 200 | 0 | 9/10 bins | 0 | 0 | 0 |
| soc_decode_test | 250 | 0 | 10/10 bins | 0 | 0 | 0 |

`soc_reg_test` only touches mapped registers, so it never hits the `slverr` bin.

The build took 1089 s wall time (C++ compile 1028 s) on a shared 4-vCPU machine with load average about 20–50 from other agents. Each simulation takes well under a second.

**Failing-first run** (`run_verilator.sh <out> +define+BUG_DECODE`, logs in `docs/evidence/2026-10-15/sv_uvm_bug_decode/`): `soc_decode_test` ends with `UVM_ERROR : 26`. The scoreboard reports e.g. `PSLVERR=0 for R addr=00000204 ... (expected 1)`, and the bound SVA prints `SVA_FAIL SOC_DECODE_HOLE_ERRORS`. `soc_reg_test` stays clean, as expected: it never uses the decoder holes. The script exits non-zero.

## Limits

* `UVM_NO_DPI`: UVM's regex name checks and DPI-based command-line features are off.
* Stimulus uses `$urandom`. A first version used `randomize() with {...}`; under Verilator 5.052 it ended in `UVM_FATAL [RAND] randomize failed`, so constraints are not used.
* No UART line agent, GPIO agent or RAL in the SV environment; those exist only in the pyuvm testbench.
