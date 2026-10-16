# UVM SoC verification (project 12)

A UVM testbench for an APB peripheral subsystem: address decoder, UART, timer, GPIO and interrupt controller, about 450 lines of SystemVerilog in `rtl/`.

It is written with **pyuvm** (IEEE 1800.2 UVM in Python) on cocotb 1.9 and Verilator 5.020. Verilator 5.020 cannot run the SystemVerilog UVM library and no commercial simulator is installed, so the main testbench uses pyuvm. pyuvm keeps the UVM structure (agents, drivers, monitors, sequencers, sequences, analysis ports, scoreboards, phasing, ConfigDB) on a free simulator. A second, smaller testbench in native SystemVerilog UVM (`sv_uvm/`) runs on Verilator 5.052.

**Status.** The latest regression is `results/2026-10-15/` (10 seeds × 9 tests, all pass). Merged functional coverage is **82 / 82 bins**. **12 / 12 injected RTL bugs are caught** by the Python checks alone. SVA is compiled into every run: **0 assertion failures on clean RTL**, and **21 / 21 testable properties fire on the bug they target**. The RAL tests alone catch both W1C bugs. A compact **native SystemVerilog UVM** testbench runs on Verilator 5.052 with Accellera UVM 2020.3.1 (see [docs/sv_uvm.md](docs/sv_uvm.md) for its status).

## Testbench

```
            ┌────────────── SocEnv ─────────────────────────────────────────────┐
 sequences ─┤ ApbAgent (driver, monitor, sequencer) ──► coverage                 │
            │ UartAgent (RX-line driver, independent TX decoder) ──► TX checker │
            │ GpioAgent (input driver)                                          │
            │ LockstepScoreboard: cycle-accurate reference model vs DUT, every  │
            │   output on every cycle                                           │
            └───────────────────────────────────────────────────────────────────┘
```

* **Lockstep scoreboard** (`tb/refmodel.py`). A cycle-accurate Python model of the whole subsystem, written from the register specification. Every cycle it compares PREADY, PSLVERR, PRDATA (on reads), the UART TX line, the GPIO outputs and the IRQ pin. On the first mismatch the test stops.
* **Model-independent checks.**
  * The TX line is decoded by its own monitor. Every accepted byte must appear, in order, with correct framing.
  * Sequences assert specification behaviour directly: RX data integrity, error flags, overrun keeping the first 8 bytes, glitch rejection, W1C per bit, one-shot vs auto-reload, interrupt routing at the pin.
* **Coverage.** 82 named bins:
  * every register in both directions;
  * slave errors, wait states and back-to-back transfers;
  * UART parity × baud-class crosses, FIFO full and overflow, every RX error, every interrupt cause;
  * timer modes, GPIO edges and W1C races;
  * interrupt-controller routing.
* **Tests.** `RegisterTest`, `UartTxTest`, `UartRxTest`, `TimerTest`, `GpioTest`, `IntcTest` and `StressTest` (all blocks and both UART directions at once). They are constrained-random, with the seed taken from cocotb's `RANDOM_SEED`.

## SystemVerilog assertions (`sva/`)

`sva/soc_sva.sv` holds 23 concurrent properties. `sva/sva_bind.sv` binds them into `periph_soc`, `apb_uart`, `apb_timer` and `apb_gpio`, so the RTL is unchanged. `tb/Makefile` compiles them with `--assert` by default (`SVA=0` turns them off).

* **APB protocol (9):** PENABLE only with PSEL; a transfer starts with a setup cycle; setup is followed by access; access holds until PREADY; PENABLE drops after completion; PADDR/PWRITE and PWDATA are stable during a transfer; PSLVERR only in the last access cycle; PREADY only in the access phase (a rule of this design).
* **Peripherals (12):** UART wait state, FIFO-full depth, baud clamp, W1C of the overrun flag, TX parity, RX mid-bit sampling; timer prescaler period and restart at 0; GPIO W1C clears only written bits and never loses an edge; decoder holes raise PSLVERR; IRQ pin = OR of enabled pending sources.
* **No X (2):** written, but they cannot fail on 2-state Verilator.

Verilator 5.020 rejects `##N`, named sequences and repetition ([probe log](docs/evidence/2026-10-15/verilator_5020_sva_support_probe.log)), so only `|->`, `|=>`, `$past`, `$stable` and `$isunknown` are used.

**Failing-first evidence.** `tools/regress.py --sva-bugs` runs 8 APB protocol bugs: 6 requester bugs injected in the driver (`APB_DRV_BUG=penable_idle|no_setup|abort_setup|drop_wait|addr_change|wdata_change`) and 2 completer bugs in the RTL (`BUG_APB_SLVERR_SETUP`, `BUG_APB_PREADY_SETUP`). The 12 functional bugs cover the peripheral properties. Each of the 21 testable properties fired on its target bug in the 2026-10-15 run.

## Register model (RAL)

`regs/periph_soc.json` is the single register description. `tools/reggen.py` generates:

* `tb/ral_model.py`: a pyuvm register model (`uvm_reg_block`, `uvm_reg`, `uvm_reg_field` with RW/RO/W1C policies);
* `sw/periph_soc_regs.h`: a C header;
* `docs/register_map.md`.

CI fails if the generated files are stale. pyuvm 5.0 has no predictor, backdoor or built-in register sequences, so `tb/ral.py` adds an APB adapter and four sequences in UVM style:

* reset values of every register;
* bit-bash of every RW and W1C bit;
* frontdoor read = backdoor (RTL signal) = mirror;
* per-bit W1C of sticky flags that the hardware set.

`RalRegisterTest` makes 367 register checks per run. With the lockstep scoreboard off (`LOCKSTEP=0`), `RalW1cTest` alone fails on `BUG_UART_W1C` ("W1C of bit 2: uart_irq_st read 0x3c, mirror 0x38") and on `BUG_GPIO_W1C`.

## What bug injection found

`tools/regress.py --bugs` rebuilds the DUT with each of 12 bugs behind `ifdef`s and runs the suite.

**The first run caught 11 of 12.** `BUG_UART_W1C` survived: writing bit 2 of `IRQ_ST` cleared the parity-error flag instead of the overrun flag.

The tests only ever cleared all error flags at once, and the random register test never wrote `IRQ_ST` while a flag was set. The lockstep model could not see a difference that the stimulus never produced. Adding per-bit W1C stimulus with two flags set closed the hole.

The same rerun also closed the last four coverage holes: two UART configuration crosses, plus interrupt routing of the UART and timer while enabled. The fixes were a deterministic sweep of the parity × baud crosses and a directed `IntcTest`.

| Bug | Caught by |
|---|---|
| UART parity inverted | UartTxTest, StressTest |
| TX FIFO one entry short | UartTxTest, StressTest |
| RX samples at the bit edge instead of the middle | UartRxTest, StressTest |
| W1C clears the wrong flag | UartRxTest (after the fix above) |
| UART skips its wait state | RegisterTest, UartTxTest, UartRxTest, IntcTest, StressTest |
| baud divider clamped at 2 instead of 4 | UartTxTest |
| timer reloads to 1 | TimerTest |
| prescaler off by one | IntcTest |
| GPIO W1C clears every flag | GpioTest |
| GPIO edge lost during a W1C | GpioTest |
| interrupt controller ignores the GPIO mask | GpioTest, IntcTest, StressTest |
| decoder aliases address bits 11:8 | RegisterTest |

In the 2026-10-15 run, the RAL tests also failed on 5 of the 12 bug builds (the lockstep scoreboard was on in those runs, so it may be what stopped them), and every bug fired its target SVA property (`results/2026-10-15/regression_full.log`).

## Running

```sh
python3 -m venv venv && venv/bin/pip install -r tb/requirements.txt    # plus verilator >= 5.020
cd tb && PATH=../venv/bin:$PATH make SIM=verilator                    # all tests, random seed
PATH=venv/bin:$PATH python3 tools/regress.py --seeds 10 --bugs --sva-bugs --ral-bugs   # + bug injection, SVA and RAL checks
python3 tools/reggen.py                                                # regenerate RAL model, C header, register docs
UVM_HOME=.../uvm-core/src sv_uvm/run_verilator.sh /tmp/out             # SV-UVM (Verilator >= 5.036; see docs/sv_uvm.md)
```

## Documentation

* [Verification plan](docs/verification_plan.md): features → stimulus → checks → coverage bins.
* [Register map](docs/register_map.md) (generated).
* [SV-UVM testbench](docs/sv_uvm.md).
* [Owner actions](docs/OWNER_ACTIONS.md): commercial-simulator and EDA Playground commands.
* [Limitations](docs/limitations.md).
* [Requirements matrix](docs/requirements.md).

## Scorecard (2026-10-15, self-assessed)

| Score | Value | Why not higher |
|---|---|---|
| Fresher-portfolio strength | 8/10 | The main UVM environment is pyuvm, and the SV-UVM testbench is small and has run only on Verilator, not on an industry simulator. |
| Engineering maturity | 7/10 | Failing-first evidence for every testable check and CI, but the SVA are not formally proven, there is no code coverage, and the reference model shares an author with the RTL. |
| Deployment readiness | 6/10 | Reproducible regression in CI; no sign-off flow (formal, code coverage, commercial simulator) and the DUT is a small peripheral subsystem. |
| Completion of intended scope | 8/10 | Agents, scoreboards, RAL, SVA, coverage and protocol errors are done; the no-X properties and a commercial-simulator run are still open. |
