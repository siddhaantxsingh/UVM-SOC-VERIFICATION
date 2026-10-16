"""UVM agents: APB master, UART serial (RX driver, independent TX monitor),
GPIO input driver. All drive right after a rising clock edge and sample in
the read-only phase of a cycle."""
import os

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge
from pyuvm import (ConfigDB, uvm_analysis_port, uvm_agent, uvm_driver, uvm_monitor, uvm_sequence_item,
                   uvm_sequencer)


class ApbItem(uvm_sequence_item):
    def __init__(self, name="apb", addr=0, write=False, data=0, idle=0):
        super().__init__(name)
        self.addr, self.write, self.data, self.idle = addr, write, data, idle
        self.rdata, self.slverr, self.wait_states, self.cycle = 0, 0, 0, 0

    def __str__(self):
        return f"{'W' if self.write else 'R'} {self.addr:#010x} {'data' if self.write else 'rdata'}={(self.data if self.write else self.rdata):#x} err={self.slverr}"


class ApbDriver(uvm_driver):
    """APB requester. APB_DRV_BUG (environment) injects one protocol
    violation per transfer, to show that the SVA protocol checks fire:
    penable_idle, no_setup, abort_setup, drop_wait, addr_change, wdata_change.
    Unset (the default) means a correct APB requester."""

    BUGS = ("penable_idle", "no_setup", "abort_setup", "drop_wait", "addr_change", "wdata_change")

    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.bug = os.environ.get("APB_DRV_BUG", "")
        assert self.bug in ("",) + self.BUGS, f"unknown APB_DRV_BUG {self.bug}"

    async def run_phase(self):
        d = self.dut
        bug = self.bug
        d.psel.value = 0
        d.penable.value = 0
        while True:
            item = await self.seq_item_port.get_next_item()
            for _ in range(item.idle):
                await RisingEdge(d.clk)
            if bug == "penable_idle":  # PENABLE high without PSEL for one cycle
                d.psel.value, d.penable.value = 0, 1
                await RisingEdge(d.clk)
            if bug == "abort_setup":  # a setup cycle not followed by an access cycle
                d.psel.value, d.penable.value, d.pwrite.value, d.paddr.value = 1, 0, 0, item.addr
                await RisingEdge(d.clk)
                d.psel.value = 0
                await RisingEdge(d.clk)
            # setup phase
            d.psel.value = 1
            d.penable.value = int(bug == "no_setup")  # bug: skip the setup cycle
            d.pwrite.value = int(item.write)
            d.paddr.value = item.addr
            d.pwdata.value = item.data if item.write else 0
            if bug != "no_setup":
                await RisingEdge(d.clk)
            d.penable.value = 1
            if bug == "wdata_change" and item.write:  # PWDATA changes between setup and access
                d.pwdata.value = item.data ^ 1
            waits = 0
            while True:
                await ReadOnly()
                if d.pready.value == 1:
                    item.rdata, item.slverr = int(d.prdata.value), int(d.pslverr.value)
                    break
                waits += 1
                await RisingEdge(d.clk)
                if bug == "drop_wait" and waits == 1:  # PENABLE drops during a wait state
                    d.penable.value = 0
                    await RisingEdge(d.clk)
                    d.penable.value = 1
                if bug == "addr_change" and waits == 1:  # PADDR changes during a wait state
                    d.paddr.value = item.addr ^ 0x4
            item.wait_states = waits
            await RisingEdge(d.clk)  # the transfer completes on this edge
            d.psel.value = 0
            d.penable.value = 0
            self.seq_item_port.item_done()


class ApbMonitor(uvm_monitor):
    def build_phase(self):
        self.ap = uvm_analysis_port("ap", self)
        self.dut = ConfigDB().get(self, "", "DUT")

    async def run_phase(self):
        d = self.dut
        cycle, waits = 0, 0
        while True:
            await RisingEdge(d.clk)
            await ReadOnly()
            cycle += 1
            if d.psel.value == 1 and d.penable.value == 1:
                if d.pready.value == 1:
                    it = ApbItem(addr=int(d.paddr.value), write=bool(d.pwrite.value), data=int(d.pwdata.value))
                    it.rdata, it.slverr, it.wait_states, it.cycle = int(d.prdata.value), int(d.pslverr.value), waits, cycle
                    self.ap.write(it)
                    waits = 0
                else:
                    waits += 1


class ApbAgent(uvm_agent):
    def build_phase(self):
        self.seqr = uvm_sequencer("seqr", self)
        self.drv = ApbDriver("drv", self)
        self.mon = ApbMonitor("mon", self)

    def connect_phase(self):
        self.drv.seq_item_port.connect(self.seqr.seq_item_export)


class UartFrame(uvm_sequence_item):
    """One serial frame on the DUT's RX line."""

    def __init__(self, name="frame", byte=0, baud=16, parity=None, bad_parity=False, bad_stop=False, idle_bits=1, glitch=0):
        super().__init__(name)
        self.glitch = glitch  # >0: only a low pulse of this many cycles (not a frame)
        self.byte, self.baud, self.parity = byte, baud, parity  # parity: None, "even", "odd"
        self.bad_parity, self.bad_stop, self.idle_bits = bad_parity, bad_stop, idle_bits

    def bits(self):
        b = [0] + [(self.byte >> i) & 1 for i in range(8)]
        if self.parity:
            p = (bin(self.byte).count("1") & 1) ^ (self.parity == "odd")
            b.append(p ^ int(self.bad_parity))
        b.append(0 if self.bad_stop else 1)
        return b


class UartRxDriver(uvm_driver):
    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")

    async def run_phase(self):
        d = self.dut
        d.uart_rx.value = 1
        while True:
            fr = await self.seq_item_port.get_next_item()
            if fr.glitch:
                d.uart_rx.value = 0
                for _ in range(fr.glitch):
                    await RisingEdge(d.clk)
                d.uart_rx.value = 1
                for _ in range(2 * fr.baud):
                    await RisingEdge(d.clk)
                self.seq_item_port.item_done()
                continue
            for bit in fr.bits():
                d.uart_rx.value = bit
                for _ in range(fr.baud):
                    await RisingEdge(d.clk)
            d.uart_rx.value = 1
            for _ in range(fr.idle_bits * fr.baud):
                await RisingEdge(d.clk)
            self.seq_item_port.item_done()


class UartTxMonitor(uvm_monitor):
    """Decodes the DUT's TX line on its own (independently of the reference
    model), using the line configuration the test publishes in ConfigDB."""

    def build_phase(self):
        self.ap = uvm_analysis_port("ap", self)
        self.dut = ConfigDB().get(self, "", "DUT")

    async def run_phase(self):
        d = self.dut
        while True:
            await RisingEdge(d.clk)
            await ReadOnly()
            if d.uart_tx.value != 0 or d.rst_n.value == 0:
                continue
            cfg = ConfigDB().get(self, "", "UART_LINE")  # (baud, parity)
            baud, parity = cfg
            # sample each bit in its middle
            vals = []
            nbits = 1 + 8 + (1 if parity else 0) + 1
            for k in range(nbits):
                target = baud // 2 if k == 0 else baud
                for _ in range(target):
                    await RisingEdge(d.clk)
                await ReadOnly()
                vals.append(int(d.uart_tx.value))
            byte = sum(v << i for i, v in enumerate(vals[1:9]))
            perr = bool(parity) and vals[9] != ((bin(byte).count("1") & 1) ^ (parity == "odd"))
            ferr = vals[-1] != 1 or vals[0] != 0
            self.ap.write((byte, perr, ferr))
            # wait for the end of the stop bit before looking for the next start
            for _ in range(baud - baud // 2):
                await RisingEdge(d.clk)


class UartAgent(uvm_agent):
    def build_phase(self):
        self.seqr = uvm_sequencer("seqr", self)
        self.drv = UartRxDriver("drv", self)
        self.mon = UartTxMonitor("mon", self)

    def connect_phase(self):
        self.drv.seq_item_port.connect(self.seqr.seq_item_export)


class GpioItem(uvm_sequence_item):
    def __init__(self, name="gpio", value=0, hold=1):
        super().__init__(name)
        self.value, self.hold = value, hold


class GpioDriver(uvm_driver):
    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")

    async def run_phase(self):
        d = self.dut
        d.gpio_in.value = 0
        while True:
            it = await self.seq_item_port.get_next_item()
            d.gpio_in.value = it.value
            for _ in range(it.hold):
                await RisingEdge(d.clk)
            self.seq_item_port.item_done()


class GpioAgent(uvm_agent):
    def build_phase(self):
        self.seqr = uvm_sequencer("seqr", self)
        self.drv = GpioDriver("drv", self)

    def connect_phase(self):
        self.drv.seq_item_port.connect(self.seqr.seq_item_export)
