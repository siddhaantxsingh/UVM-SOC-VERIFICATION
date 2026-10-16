// SystemVerilog assertions for periph_soc, attached with `bind` (sva_bind.sv)
// so the RTL files stay untouched.
//
// Only property forms that Verilator 5.020 accepts are used: |->, |=>,
// $past, $stable, $isunknown and `disable iff` (see verilator_sva_probe.sv).
// There are no ##N delays or named sequences; multi-cycle rules are written
// as one-cycle implications.
//
// Every failure prints one "SVA_FAIL <name>" line (the first 5 per property)
// and increments `fails`. The cocotb testbench reads `fails` of every bound
// instance in its check phase and fails the test if it is non-zero.
// tools/regress.py collects the SVA_FAIL names from the simulator log.
//
// Each property name is listed in tools/regress.py (SVA_TARGETS) together
// with the injected bug that must make it fire.

/* verilator lint_off BLKSEQ */
`define SVA_CHECK(name, prop) \
    name: assert property (@(posedge clk) disable iff (!rst_n) prop) \
        else begin \
            if (fails < 5) $display("[%0t] SVA_FAIL %s (%m)", $time, `"name`"); \
            fails = fails + 1; \
        end

// ---------------------------------------------------------------- APB bus
// Bound to periph_soc: the APB port of the subsystem, the decoder and the
// interrupt controller.
module apb_soc_sva (
    input logic        clk, rst_n,
    input logic        psel, penable, pwrite,
    input logic [31:0] paddr, pwdata, prdata,
    input logic        pready, pslverr,
    input logic        uart_tx, irq,
    input logic [15:0] gpio_out, gpio_oe,
    input logic [2:0]  pend, ien
);
    int unsigned fails = 0;

    // ---- master side (what the APB requester must do)
    // PENABLE is only ever high together with PSEL.
    `SVA_CHECK(APB_PENABLE_NEEDS_PSEL, penable |-> psel)
    // A transfer starts with a setup cycle (PSEL high, PENABLE low).
    `SVA_CHECK(APB_FIRST_CYCLE_IS_SETUP, psel && !$past(psel) |-> !penable)
    // The setup cycle is always followed by an access cycle.
    `SVA_CHECK(APB_SETUP_THEN_ACCESS, psel && !penable |=> psel && penable)
    // The access phase holds while the slave inserts wait states.
    `SVA_CHECK(APB_ACCESS_HOLDS_UNTIL_READY, psel && penable && !pready |=> psel && penable)
    // After a completed transfer PENABLE drops (next is idle or a new setup).
    `SVA_CHECK(APB_PENABLE_DROPS_AFTER_READY, psel && penable && pready |=> !penable)
    // PADDR and PWRITE are stable from setup until the transfer completes.
    `SVA_CHECK(APB_ADDR_CTRL_STABLE, psel && !(penable && pready) |=> $stable(paddr) && $stable(pwrite))
    // PWDATA is stable from setup until a write completes.
    `SVA_CHECK(APB_WDATA_STABLE, psel && pwrite && !(penable && pready) |=> $stable(pwdata))
    // The master's control signals are never X/Z while PSEL is high.
    `SVA_CHECK(APB_NO_X_ON_REQUEST, psel |-> !$isunknown({penable, pwrite, paddr}))

    // ---- completer side (what the subsystem must do)
    // PSLVERR is only valid in the last cycle of the access phase.
    `SVA_CHECK(APB_SLVERR_ONLY_IN_ACCESS, pslverr |-> psel && penable && pready)
    // Design rule of this subsystem: PREADY is only driven in the access phase.
    `SVA_CHECK(APB_PREADY_ONLY_IN_ACCESS, pready |-> psel && penable)
    // No X/Z on any output after reset. Verilator is a 2-state simulator, so
    // this property can never fail under Verilator; it is meaningful on a
    // 4-state simulator only.
    `SVA_CHECK(SOC_NO_X_ON_OUTPUTS, !$isunknown({prdata, pready, pslverr, uart_tx, irq, gpio_out, gpio_oe}))

    // ---- decoder and interrupt controller
    // Any access with address bits 11:8 non-zero inside the low 64 KiB is
    // unmapped and must end with PSLVERR.
    `SVA_CHECK(SOC_DECODE_HOLE_ERRORS, psel && penable && pready && paddr[31:16] == 16'h0 && paddr[11:8] != 4'h0 |-> pslverr)
    // The IRQ pin is the OR of the enabled pending sources, every cycle.
    `SVA_CHECK(SOC_IRQ_IS_MASKED_OR, irq == |(pend & ien))
endmodule

// ---------------------------------------------------------------- UART
module apb_uart_sva #(parameter int DEPTH = 8) (
    input logic        clk, rst_n,
    input logic        psel, penable, pwrite, pready, fire,
    input logic [7:0]  paddr,
    input logic [31:0] pwdata,
    input logic        tx_full, rx_full, rx_valid, rx_ovr, tx_take, par_odd, enable, rx_busy, rx_s2, tx_par,
    input logic [$clog2(DEPTH):0] tx_cnt,
    input logic [15:0] baud, rx_div,
    input logic [7:0]  tx_sh
);
    int unsigned fails = 0;
    // Every UART access has one wait state: PREADY is low in the first
    // access cycle.
    `SVA_CHECK(UART_FIRST_ACCESS_CYCLE_WAITS, psel && penable && !$past(psel && penable) |-> !pready)
    // "TX FIFO full" means exactly DEPTH entries.
    `SVA_CHECK(UART_TX_FULL_AT_DEPTH, tx_full |-> tx_cnt == DEPTH[$clog2(DEPTH):0])
    // The baud divider never holds a value below 4 (smaller writes clamp).
    `SVA_CHECK(UART_BAUD_AT_LEAST_4, baud >= 16'd4)
    // Writing 1 to IRQ_ST[2] clears the overrun flag (unless a new overrun
    // happens in the same cycle).
    `SVA_CHECK(UART_W1C_CLEARS_OVERRUN, fire && pwrite && paddr == 8'h14 && pwdata[2] && !(rx_valid && rx_full) |=> !rx_ovr)
    // The parity bit loaded with a TX byte makes the total number of ones
    // even (even parity) or odd (odd parity).
    `SVA_CHECK(UART_TX_PARITY_CORRECT, $past(tx_take) |-> (^tx_sh ^ tx_par) == $past(par_odd))
    // A detected start bit is next sampled half a bit period later.
    `SVA_CHECK(UART_RX_SAMPLES_MID_BIT, $past(!rx_busy && enable && !rx_s2) |-> rx_div == {1'b0, $past(baud[15:1])} - 16'd1)
endmodule

// ---------------------------------------------------------------- timer
module apb_timer_sva (
    input logic        clk, rst_n,
    input logic        fire, pwrite, tick,
    input logic [7:0]  paddr,
    input logic [15:0] pcnt, presc,
    input logic [31:0] count, compare
);
    int unsigned fails = 0;
    // The counter advances once every PRESC+1 clocks.
    `SVA_CHECK(TIMER_TICK_EVERY_PRESC_PLUS_1, tick |-> pcnt == presc)
    // On a compare match the counter restarts at 0 (unless software writes
    // COUNT in the same cycle).
    `SVA_CHECK(TIMER_MATCH_RESTARTS_AT_0, tick && count == compare && !(fire && pwrite && paddr == 8'h0C) |=> count == 32'h0)
endmodule

// ---------------------------------------------------------------- GPIO
module apb_gpio_sva (
    input logic        clk, rst_n,
    input logic        fire, pwrite,
    input logic [7:0]  paddr,
    input logic [31:0] pwdata,
    input logic [15:0] ist, rise, fall, rise_en, fall_en
);
    int unsigned fails = 0;
    // A W1C write to IRQ_ST clears only the bits written as 1.
    `SVA_CHECK(GPIO_W1C_CLEARS_ONLY_WRITTEN_BITS, fire && pwrite && paddr == 8'h14 |=> ($past(ist & ~pwdata[15:0]) & ~ist) == 16'h0)
    // Every enabled edge is captured in IRQ_ST, even during a W1C write.
    `SVA_CHECK(GPIO_EDGE_ALWAYS_CAPTURED, ($past((rise & rise_en) | (fall & fall_en)) & ~ist) == 16'h0)
endmodule

`undef SVA_CHECK
