# Verification plan

**DUT.** `periph_soc` (`rtl/`): an APB peripheral subsystem with an address decoder, a UART, a timer, GPIO and an interrupt controller. The register maps are in the header comment of each RTL file.

**Methodology.** UVM (IEEE 1800.2) through **pyuvm** on cocotb 1.9 + Verilator 5.020. No commercial SystemVerilog simulator with UVM support is available here, and Verilator cannot run the SystemVerilog UVM library.

## Checking strategy

| Checker | How it works | Independent of the reference model? |
|---|---|---|
| **Lockstep scoreboard** (`env.LockstepScoreboard`) | A cycle-accurate Python model (`tb/refmodel.py`), written from the register specification, steps alongside the DUT. Every cycle it compares `pready`, `pslverr`, `uart_tx`, `gpio_out`, `gpio_oe` and `irq`, and `prdata` on completed reads. | no |
| **UART TX checker** (`env.UartTxChecker`) | An independent serial decoder samples the TX line mid-bit. Every byte the sequences got accepted (flow-controlled pushes) must appear in order, with correct parity and stop bit, and nothing else may appear. | yes |
| **Sequence assertions** (`tb/sequences.py`) | Directed expectations from the specification: RX data equals the frames driven; error flags are set by bad parity or stop bits; an overrun keeps the first 8 bytes; a glitch is rejected; one-shot vs auto-reload; W1C semantics; overflow flags. | yes |

The lockstep model and the RTL were written by the same author from the same specification, so a misreading shared by both would go unnoticed. The model-independent checks exist to limit that risk.

## Features, stimulus and coverage

| Feature | Stimulus (test / sequence) | Coverage bins |
|---|---|---|
| Address decode and every register, read and write | `RegisterTest` / `register_scenario` (random legal and illegal accesses) | `reg.<block>.<offset>.<r/w>` for all 40 register directions |
| PSLVERR for unmapped blocks and offsets | `register_scenario` | `apb.slverr.unmapped_block`, `apb.slverr.unmapped_offset` |
| APB wait states, back-to-back transfers | all tests (UART has one wait state; random idle 0–2) | `apb.wait_state`, `apb.back_to_back` |
| UART TX framing: 3 parity modes × baud classes, divider clamp at 4 | `UartTxTest` | `uart.cfg.<parity>.<min4/small/typical>` (9 bins) |
| TX FIFO full, overflow, W1C | `uart_tx_scenario` (disabled-TX overflow) | `uart.tx_fifo.full`, `uart.tx_fifo.overflow` |
| RX data path, FIFO, overrun | `UartRxTest` | `uart.rx_fifo.full`, `uart.rx.overrun` |
| RX parity and frame errors, glitch rejection, disabled receiver | `uart_rx_scenario` | `uart.rx.parity_err`, `uart.rx.frame_err`, `uart.rx.glitch_rejected`, `uart.disabled_drop` |
| UART interrupts | random `IRQ_EN` in all UART scenarios | `uart.irq.rx_avail`, `uart.irq.tx_empty`, `uart.irq.error` |
| Timer: prescaler, compare, one-shot / auto-reload, count writes while running | `TimerTest` | `timer.*` (7 bins) |
| GPIO edges, W1C, an edge in the same cycle as a W1C | `GpioTest` | `gpio.*` (5 bins) |
| Interrupt controller routing and masking | `StressTest` / `intc_scenario` | `intc.<source>.<masked/enabled>` (6 bins) |
| Concurrency: all blocks and both UART directions at once | `StressTest` | — |

## Bug-injection plan

The RTL contains 12 known bugs behind `ifdef BUG_*`. `tools/regress.py --bugs` builds the DUT with each one and runs the suite; a bug counts as caught if any test fails. Each bug has a different class of failure:

* data-path errors: UART parity, RX sampling point;
* off-by-one capacity: FIFO depth;
* register semantics: W1C on the wrong bit, baud clamp, W1C that clears everything;
* protocol: a missing wait state;
* counter behaviour: timer reload, prescaler;
* event loss: a GPIO edge during W1C;
* routing and decode: interrupt-controller mask, address aliasing.
