# Owner actions

Steps only the owner can do, because they need a commercial licence or an online account.

## 1. Run the SV-UVM testbench on a commercial simulator

Needed to: check `sv_uvm/` on a 4-state simulator, where the two no-X assertions in `sva/soc_sva.sv` become meaningful. All three simulators ship UVM, so no download is needed.

From `uvm-soc-verification/`:

```sh
SRC="rtl/periph_soc.sv rtl/apb_uart.sv rtl/apb_timer.sv rtl/apb_gpio.sv sva/soc_sva.sv sva/sva_bind.sv sv_uvm/apb_if.sv sv_uvm/soc_uvm_pkg.sv sv_uvm/tb_top.sv"

# Cadence Xcelium
xrun -uvm -uvmhome CDNS-1.2 -sv -timescale 1ns/1ps $SRC -top tb_top +UVM_TESTNAME=soc_reg_test    -l xrun_reg.log
xrun -uvm -uvmhome CDNS-1.2 -sv -timescale 1ns/1ps $SRC -top tb_top +UVM_TESTNAME=soc_decode_test -l xrun_decode.log

# Siemens Questa
vlog -sv -timescale 1ns/1ps -L uvm $SRC && vsim -c tb_top -L uvm +UVM_TESTNAME=soc_reg_test -do "run -all; quit" -l questa_reg.log

# Synopsys VCS
vcs -full64 -sverilog -ntb_opts uvm-1.2 -timescale=1ns/1ps $SRC -top tb_top -o simv && ./simv +UVM_TESTNAME=soc_reg_test -l vcs_reg.log
```

Expected: each log ends with `UVM_ERROR :    0` and `UVM_FATAL :    0`, and `SCB ... transfers checked, 0 mismatches`. There must be no `SVA_FAIL` line.

Failing-first check: add `+define+BUG_DECODE` to the compile and run `soc_decode_test`. Expected: `UVM_ERROR` > 0 and `SVA_FAIL SOC_DECODE_HOLE_ERRORS`.

Save: the logs to `docs/evidence/<date>/<simulator>_*.log`, and the simulator version (`xrun -version`, `vsim -version`, `vcs -ID`). Then update `docs/requirements.md` ("SystemVerilog UVM on a commercial simulator").

## 2. EDA Playground (free account, commercial simulators in the browser)

1. Log in at https://www.edaplayground.com.
2. Testbench + Design: choose "SystemVerilog/Verilog"; UVM / OVM: "UVM 1.2"; Tools & Simulators: "Aldec Riviera Pro" or "Synopsys VCS".
3. Paste `sv_uvm/tb_top.sv`, `sv_uvm/soc_uvm_pkg.sv`, `sv_uvm/apb_if.sv`, `sva/soc_sva.sv`, `sva/sva_bind.sv` into the testbench pane (one tab per file, add `` `include `` lines in `testbench.sv` for the others), and the four `rtl/*.sv` files into the design pane.
4. Run options: `+UVM_TESTNAME=soc_reg_test`.
5. Expected and what to save: as in section 1. Save the share link and the log.

## 3. Formal proof of the SVA (optional)

Any formal tool (Jasper, Questa Formal, VC Formal, or SymbiYosys with a commercial Verific front end) can prove the properties in `sva/soc_sva.sv` on `periph_soc`. Save the proof log. This is not done here.
