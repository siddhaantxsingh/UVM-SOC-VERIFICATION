"""Environment: agents, the lockstep scoreboard against the reference model,
transaction-level checks independent of the model, and functional coverage."""
import json
import os

import cocotb
from cocotb.triggers import Event, ReadOnly, RisingEdge
from pyuvm import ConfigDB, uvm_component, uvm_env, uvm_subscriber

from agents import ApbAgent, GpioAgent, UartAgent
from refmodel import Inp, SocModel

OUTPUTS = ("prdata", "pready", "pslverr", "uart_tx", "gpio_out", "gpio_oe", "irq")


class LockstepScoreboard(uvm_component):
    """Runs the reference model alongside the DUT and compares every output
    on every cycle (prdata only when a read completes)."""

    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.model = SocModel()
        self.errors = []
        self.cycles = 0
        self.max_errors = 10
        self.failed = Event()  # set on the first mismatch so the test can stop early
        # LOCKSTEP=0 turns the comparison off. Only used to show that another
        # checker (RAL, SVA) catches a bug on its own; never in normal runs.
        self.enabled = os.environ.get("LOCKSTEP", "1") != "0"

    async def run_phase(self):
        d, m = self.dut, self.model
        while True:
            await RisingEdge(d.clk)
            await ReadOnly()
            i = Inp(int(d.rst_n.value), int(d.psel.value), int(d.penable.value), int(d.pwrite.value), int(d.paddr.value),
                    int(d.pwdata.value), int(d.uart_rx.value), int(d.gpio_in.value))
            if i.rst_n and self.enabled:
                exp = m.outputs(i)
                for k in OUTPUTS:
                    got = int(getattr(d, k).value)
                    if k == "prdata" and not (i.psel and i.penable and not i.pwrite and exp["pready"]):
                        continue
                    if got != exp[k] and len(self.errors) < self.max_errors:
                        self.errors.append(f"cycle {self.cycles}: {k} = {got:#x}, model {exp[k]:#x} (addr {i.paddr:#x})")
                        self.logger.error(self.errors[-1])
                        self.failed.set()
                self.cycles += 1
            m.step(i)

    def check_phase(self):
        assert not self.errors, f"{len(self.errors)}+ mismatches; first: {self.errors[0]}"


class UartTxChecker(uvm_component):
    """Model-independent: every byte accepted into the TX FIFO appears on the
    serial line, in order, with correct parity and stop bit."""

    def build_phase(self):
        self.expected, self.seen = [], []

    def accepted(self, byte):
        self.expected.append(byte)

    def write(self, frame):  # from UartTxMonitor
        self.seen.append(frame)

    def check_phase(self):
        got = [b for b, _, _ in self.seen]
        assert all(not p and not f for _, p, f in self.seen), f"bad frames on TX: {[s for s in self.seen if s[1] or s[2]]}"
        assert got == self.expected[: len(got)], f"TX bytes {got[:10]}... differ from accepted {self.expected[:10]}..."
        flush = ConfigDB().get(self, "", "TX_MUST_DRAIN") if ConfigDB().exists(self, "", "TX_MUST_DRAIN") else True
        if flush:
            assert len(got) == len(self.expected), f"{len(self.expected)} bytes accepted, {len(got)} transmitted"


class _Sub(uvm_subscriber):
    def __init__(self, name, parent, fn):
        super().__init__(name, parent)
        self.fn = fn

    def write(self, t):
        self.fn(t)


class Coverage(uvm_component):
    """Functional coverage: named bins, each with a hit count. Bins are
    declared up front so unhit bins show up as holes."""

    BINS = (
        # register map: every register read and written, errors, wait states
        [f"reg.{blk}.{off:#04x}.{rw}" for blk, offs in (("uart", range(0, 0x1C, 4)), ("timer", range(0, 0x14, 4)), ("gpio", range(0, 0x18, 4)),
                                                         ("intc", (0, 4))) for off in offs for rw in ("r", "w")]
        + ["apb.slverr.unmapped_block", "apb.slverr.unmapped_offset", "apb.wait_state", "apb.back_to_back"]
        + [f"uart.cfg.{p}.{b}" for p in ("none", "even", "odd") for b in ("min4", "small", "typical")]
        + ["uart.tx_fifo.full", "uart.tx_fifo.overflow", "uart.rx_fifo.full", "uart.rx.overrun", "uart.rx.parity_err", "uart.rx.frame_err",
           "uart.rx.glitch_rejected", "uart.irq.rx_avail", "uart.irq.tx_empty", "uart.irq.error", "uart.disabled_drop"]
        + ["timer.oneshot_match", "timer.reload_match", "timer.presc0", "timer.presc_gt0", "timer.count_write_running", "timer.irq",
           "timer.compare0"]
        + ["gpio.rise_irq", "gpio.fall_irq", "gpio.w1c", "gpio.edge_during_w1c", "gpio.multi_pin"]
        + [f"intc.{s}.{e}" for s in ("uart", "timer", "gpio") for e in ("masked", "enabled")]
    )

    def build_phase(self):
        self.hits = {b: 0 for b in self.BINS}
        self.dut = ConfigDB().get(self, "", "DUT")
        self.model = None
        self.last_cycle = -10

    def hit(self, b):
        assert b in self.hits, f"unknown coverage bin {b}"
        self.hits[b] += 1

    def apb(self, it):
        blk = {0: "uart", 1: "timer", 2: "gpio", 0xF: "intc"}.get((it.addr >> 12) & 0xF) if (it.addr >> 16) == 0 and ((it.addr >> 8) & 0xF) == 0 else None
        off = it.addr & 0xFF
        key = f"reg.{blk}.{off:#04x}.{'w' if it.write else 'r'}"
        if key in self.hits and not it.slverr:
            self.hit(key)
        if it.slverr:
            self.hit("apb.slverr.unmapped_block" if blk is None else "apb.slverr.unmapped_offset")
        if it.wait_states:
            self.hit("apb.wait_state")
        if it.cycle - self.last_cycle <= 2:
            self.hit("apb.back_to_back")
        m = self.model
        if m is not None and it.write and not it.slverr:
            if blk == "timer" and off == 0x0C and m.timer.enable:
                self.hit("timer.count_write_running")
            if blk == "gpio" and off == 0x14 and it.data & 0xFFFF:
                self.hit("gpio.w1c")
        self.last_cycle = it.cycle

    async def run_phase(self):
        # sample the reference model's state (it mirrors the DUT cycle by cycle)
        d = self.dut
        prev = {}
        while True:
            await RisingEdge(d.clk)
            await ReadOnly()
            m = self.model
            if m is None or d.rst_n.value == 0:
                continue
            u, t, g = m.uart, m.timer, m.gpio
            cur = {
                "uart.tx_fifo.full": len(u.txq) == 8, "uart.tx_fifo.overflow": u.tx_ovf, "uart.rx_fifo.full": len(u.rxq) == 8,
                "uart.rx.overrun": u.rx_ovr, "uart.rx.parity_err": u.par_err, "uart.rx.frame_err": u.frm_err,
                "uart.irq.rx_avail": u.irq_en & 1 and u.irq() and len(u.rxq) > 0, "uart.irq.tx_empty": (u.irq_en >> 1) & 1 and len(u.txq) == 0,
                "uart.irq.error": u.irq_en & 0x3C and u.pending() & 0x3C, "timer.irq": t.irq(), "gpio.rise_irq": g.ist & g.rise_en,
                "gpio.fall_irq": g.ist & g.fall_en, "gpio.multi_pin": bin(g.ist).count("1") > 1,
            }
            for k, v in cur.items():
                if v and not prev.get(k):
                    self.hit(k)
            if u.tx_busy:
                p = "none" if not u.par_en else ("odd" if u.par_odd else "even")
                b = "min4" if u.baud == 4 else "small" if u.baud < 16 else "typical"
                k = f"uart.cfg.{p}.{b}"
                if not prev.get(k):
                    self.hit(k)
                cur[k] = 1
            if t.match and not prev.get("tmatch"):
                self.hit("timer.reload_match" if t.reload else "timer.oneshot_match")
                self.hit("timer.presc0" if t.presc == 0 else "timer.presc_gt0")
                if t.compare == 0:
                    self.hit("timer.compare0")
            cur["tmatch"] = t.match
            pend = m.pending()
            for bit, s in enumerate(("uart", "timer", "gpio")):
                if (pend >> bit) & 1 and not (prev.get("pend", 0) >> bit) & 1:
                    self.hit(f"intc.{s}.{'enabled' if (m.ien >> bit) & 1 else 'masked'}")
            cur["pend"] = pend
            # a new edge flag in the same cycle as a W1C write to IRQ_ST
            if (d.psel.value == 1 and d.penable.value == 1 and d.pwrite.value == 1 and int(d.paddr.value) == 0x2014
                    and ((g.s2 & ~g.prev) & g.rise_en or (~g.s2 & g.prev) & g.fall_en)):
                self.hit("gpio.edge_during_w1c")
            # receiver: a start bit that was a glitch; traffic while disabled
            rx = int(d.uart_rx.value)
            if prev.get("rx_busy") and prev.get("rx_bit") == 0 and not u.rx_busy and not u.rx_valid:
                self.hit("uart.rx.glitch_rejected")
            if not u.enable and prev.get("rx_line", 1) == 1 and rx == 0:
                self.hit("uart.disabled_drop")
            cur.update(rx_busy=u.rx_busy, rx_bit=u.rx_bit, rx_line=rx)
            prev = cur

    def report(self):
        hit = sum(1 for v in self.hits.values() if v)
        return {"bins": len(self.hits), "hit": hit, "percent": 100.0 * hit / len(self.hits), "hits": self.hits}

    def report_phase(self):
        r = self.report()
        self.logger.info(f"functional coverage {r['hit']}/{r['bins']} bins ({r['percent']:.1f} %)")
        out = os.environ.get("COV_DIR")
        if out:
            test = type(self.get_parent().get_parent()).__name__
            with open(os.path.join(out, f"{test}.json"), "w") as f:
                json.dump(r, f, indent=1)


class SvaChecker(uvm_component):
    """Reads the failure counters of the bound SVA modules (sva/soc_sva.sv).
    Any assertion failure fails the test, unless SVA_FATAL=0 (used by the
    bug-injection runs to tell testbench detection and SVA detection apart)."""

    PATHS = ("u_sva", "u_uart.u_sva", "u_tim.u_sva", "u_gpio.u_sva")

    def build_phase(self):
        self.dut = ConfigDB().get(self, "", "DUT")
        self.fatal = os.environ.get("SVA_FATAL", "1") != "0"

    def counts(self):
        out = {}
        for p in self.PATHS:
            h = self.dut
            try:
                for part in p.split("."):
                    h = getattr(h, part)
                out[p] = int(h.fails.value)
            except AttributeError:
                return None  # built with SVA=0
        return out

    def check_phase(self):
        c = self.counts()
        if c is None:
            self.logger.info("SVA not compiled in (SVA=0)")
            return
        total = sum(c.values())
        self.logger.info(f"SVA failures: {total} {c if total else ''}")
        if self.fatal:
            assert total == 0, f"{total} SVA assertion failures {c} (see SVA_FAIL lines in the log)"


class SocEnv(uvm_env):
    def build_phase(self):
        self.apb = ApbAgent("apb", self)
        self.uart = UartAgent("uart", self)
        self.gpio = GpioAgent("gpio", self)
        self.sb = LockstepScoreboard("sb", self)
        self.txchk = UartTxChecker("txchk", self)
        self.cov = Coverage("cov", self)
        self.sva = SvaChecker("sva", self)
        self.cov_apb = _Sub("cov_apb", self, lambda t: self.cov.apb(t))
        self.tx_sub = _Sub("tx_sub", self, lambda t: self.txchk.write(t))

    def connect_phase(self):
        self.cov.model = self.sb.model
        self.apb.mon.ap.connect(self.cov_apb.analysis_export)
        self.uart.mon.ap.connect(self.tx_sub.analysis_export)
