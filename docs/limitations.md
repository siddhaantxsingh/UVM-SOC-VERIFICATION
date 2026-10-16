# Limitations

* **Two testbenches, different depth.**
  * The main testbench is **pyuvm** (UVM structure in Python) on cocotb + Verilator 5.020. It has the lockstep scoreboard, 82 coverage bins, RAL and the bug-injection flow.
  * The native **SystemVerilog UVM** testbench in `sv_uvm/` is small: one APB agent, a register-level scoreboard, 10 coverage bins, 2 sequences, 2 tests. It does not drive the UART RX line or GPIO inputs and has no RAL.
  * The SV-UVM testbench was run on Verilator 5.052 (official Docker image) with Accellera UVM 2020.3.1 and `UVM_NO_DPI`. It has not been run on a commercial simulator. With `UVM_NO_DPI`, UVM's regex-based component name checks and `+uvm_set_*` command-line features are off.
  * SV-UVM stimulus uses `$urandom`, not `randomize()` with constraints. A first attempt with inline constraints hit `randomize failed` under Verilator 5.052, so the constraint solver path is not used.
  * The SV-UVM coverage is a bin counter, not covergroups.
* **SVA.**
  * Only the SVA subset Verilator 5.020 accepts is used (`|->`, `|=>`, `$past`, `$stable`, `$isunknown`). Multi-cycle rules are one-cycle implications. There are no `##N` sequences.
  * The two `$isunknown` (no-X) properties can never fail on Verilator, which is 2-state. They are untested.
  * Several peripheral properties restate the intended RTL behaviour at the signal level (e.g. the RX start-bit counter load). They share an author with the RTL, like the reference model.
  * The properties are checked in simulation only. There is no formal proof.
  * Assertion failures are counted by the testbench through a `fails` counter in each bound module. A run without the cocotb check phase would only print `SVA_FAIL` lines.
* **RAL.**
  * pyuvm 5.0 ships `uvm_reg_block`, `uvm_reg`, `uvm_reg_field`, `uvm_reg_map` and frontdoor access through an adapter. Its `uvm_reg_predictor`, backdoor access and built-in register sequences raise `NotImplementedError`.
  * So: prediction uses the map's auto-predict. "Backdoor" reads the RTL signals named in `regs/periph_soc.json` through cocotb handles; there is no backdoor write. Reset, bit-bash, mirror and W1C sequences are written in `tb/ral.py` in the style of the UVM built-ins.
  * The RTL is hand-written; only the model, the C header and the docs are generated from the JSON. The reset and bit-bash tests check that RTL and JSON agree.
* **The DUT is a peripheral subsystem, not a CPU SoC.** The testbench is the APB master, standing in for the CPU.
* **The reference model and the RTL share an author and a specification.** A misreading common to both would pass the lockstep check. The TX decoder, sequence assertions, RAL and SVA reduce that risk but do not remove it.
* **No code coverage.** Verilator line and toggle coverage was not collected.
* **Clean-run regression uses 10 seeds.** Bins are counted as hit or not.
* **Run times were measured on a shared 4-vCPU machine** with other agents' builds running, so they vary.
