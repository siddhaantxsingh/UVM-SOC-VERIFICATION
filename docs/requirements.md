# Requirements matrix — Project 12 (UVM SoC verification)

Evidence for the 2026-10-15 rows: `results/2026-10-15/regression.json` and `regression_full.log` (command: `python3 tools/regress.py --seeds 10 --bugs --bug-seeds 2 --sva-bugs --ral-bugs --out results/2026-10-15`), `docs/evidence/2026-10-15/`.

| Requirement | Status | Evidence |
|---|---|---|
| UVM environment (pyuvm): agents, drivers, monitors, sequencers, sequences, scoreboard, phasing | VERIFIED | `tb/` (pyuvm 5.0); 9 tests pass on 10 seeds (2026-10-15) |
| Reference-model scoreboard | VERIFIED | lockstep cycle-accurate model; catches 12/12 injected bugs together with the other checks |
| Constrained-random stimulus with seeds | VERIFIED | `RANDOM_SEED`, `tools/regress.py --seeds` |
| Functional coverage and closure | VERIFIED | 82/82 bins merged over 10 seeds (2026-10-15 regression) |
| Bug injection / testbench effectiveness | VERIFIED | 12/12 caught by the cocotb/pyuvm checks with SVA set non-fatal (2026-10-15 regression) |
| Regression automation | VERIFIED | `tools/regress.py`, CI workflow |
| SVA: APB protocol assertions (PSEL/PENABLE sequencing, stable address/control/data, PSLVERR only in the access phase) | VERIFIED | `sva/soc_sva.sv`, bound by `sva/sva_bind.sv`, compiled with `--assert` in every run. 0 failures on clean RTL over 10 seeds. Each of the 9 protocol properties fires on its injected protocol bug (6 requester bugs in the driver, 2 completer bugs in the RTL) |
| SVA: peripheral properties (UART, timer, GPIO, decoder, interrupt controller) | VERIFIED | 12 properties; each fires on its target bug among the 12 functional bugs. Total 21/21 testable properties fire on their target |
| SVA: no X on outputs / requests after reset | IMPLEMENTED BUT UNVERIFIED | 2 `$isunknown` properties are written, but Verilator is 2-state, so they cannot fail here. Needs a 4-state simulator (`docs/OWNER_ACTIONS.md`) |
| SVA forms used are supported by the simulator | VERIFIED | `docs/evidence/2026-10-15/verilator_5020_sva_support_probe.log`: 5.020 rejects `##N`, named sequences, `[*N]`, `##[m:n]`; only `|->`, `|=>`, `$past`, `$stable`, `$isunknown` are used |
| RAL: register model generated from one description | VERIFIED | `regs/periph_soc.json` → `tools/reggen.py` → `tb/ral_model.py` (pyuvm `uvm_reg_block`/`uvm_reg`/`uvm_reg_field`), `sw/periph_soc_regs.h`, `docs/register_map.md`; CI runs `reggen.py --check` |
| RAL: reset-value, bit-bash, frontdoor/backdoor mirror sequences | VERIFIED | `RalRegisterTest`: 367 register checks per run, passes on 10 seeds. pyuvm 5.0 has no predictor, no backdoor and no built-in sequences; `tb/ral.py` adds small replacements (see limitations) |
| RAL catches the W1C bug | VERIFIED | `--ral-bugs`: with the lockstep scoreboard off and SVA non-fatal, `RalW1cTest` alone fails on `BUG_UART_W1C` ("W1C of bit 2: uart_irq_st read 0x3c, mirror 0x38") and `BUG_GPIO_W1C` |
| Native SystemVerilog UVM environment | VERIFIED (on Verilator only) | `sv_uvm/` (agent, driver, monitor, scoreboard, coverage, 2 sequences, 2 tests). Verilator 5.052 + Accellera UVM 2020.3.1: both tests UVM_ERROR 0 / UVM_FATAL 0 locally (`docs/evidence/2026-10-15/sv_uvm_clean/`) and in CI run 38822290359. With `+define+BUG_DECODE`: UVM_ERROR 26 (`sv_uvm_bug_decode/`) |
| SystemVerilog UVM on a commercial simulator | BLOCKED | no licence here; commands in `docs/OWNER_ACTIONS.md` |
| CPU-level SoC (core + peripherals) verification | NOT IMPLEMENTED | — |
| Formal verification of the SVA | NOT IMPLEMENTED | properties are checked in simulation only |
| CI | VERIFIED | `.github/workflows/uvm-soc-verification.yml`: run 38822290359 green (regression job with SVA/RAL checks, and the sv-uvm job) |
