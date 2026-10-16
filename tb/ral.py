"""Register abstraction layer on pyuvm 5.0's register classes.

What pyuvm 5.0 provides and is used here: uvm_reg_block, uvm_reg,
uvm_reg_field (with the UVM access policies, e.g. W1C prediction),
uvm_reg_map frontdoor read/write through an adapter and a sequencer, and
auto-prediction of the mirror.

What pyuvm 5.0 does not provide (its methods raise NotImplementedError), so
this file adds small replacements:
  * uvm_reg_predictor        -> the map's auto-prediction is used instead;
  * backdoor access          -> `peek()` reads the RTL signals named in
                                regs/periph_soc.json through cocotb handles;
  * built-in sequences (uvm_reg_hw_reset_seq, uvm_reg_bit_bash_seq, ...)
                             -> `reset_check`, `bit_bash`, `backdoor_check`
                                and `w1c_check` below, in the same style.
"""
from cocotb.triggers import ClockCycles
from pyuvm import ConfigDB, uvm_reg_adapter, uvm_reg_bus_op, uvm_status_e

import sequences as S
from agents import ApbItem, UartFrame
from ral_model import REG_META, periph_soc_reg_block

try:
    from pyuvm import uvm_access_e
except ImportError:  # pragma: no cover
    from pyuvm._reg.uvm_reg_model import uvm_access_e


class ApbRegAdapter(uvm_reg_adapter):
    """Register operation <-> APB item."""

    def reg2bus(self, rw: uvm_reg_bus_op):
        write = rw.kind == uvm_access_e.UVM_WRITE
        return ApbItem(addr=rw.addr, write=write, data=rw.data if write else 0, idle=0)

    def bus2reg(self, item, rw: uvm_reg_bus_op):
        if not item.write:
            rw.data = item.rdata
        rw.status = uvm_status_e.UVM_NOT_OK if item.slverr else uvm_status_e.UVM_IS_OK


def build_model(seqr):
    model = periph_soc_reg_block("regmodel")
    model.default_map.set_sequencer(seqr, ApbRegAdapter("apb_adapter"))
    model.default_map.set_auto_predict(True)
    return model


def regs(model):
    return [(name, getattr(model, name)) for name in REG_META]


def check_mask(reg):
    """Bits compared on a read: non-volatile fields."""
    m = 0
    for f in reg.get_fields():
        if not f.is_volatile():
            m |= ((1 << f.get_n_bits()) - 1) << f.get_lsb_pos()
    return m


class RalChecker:
    """Runs the register sequences and collects mismatches."""

    def __init__(self, model, dut, log):
        self.model, self.dut, self.log = model, dut, log
        self.errors = []
        self.checked = 0

    def error(self, msg):
        if len(self.errors) < 20:
            self.log.error(msg)
        self.errors.append(msg)

    async def read(self, name, reg):
        status, value = await reg.read()
        if status != uvm_status_e.UVM_IS_OK:
            self.error(f"{name}: read returned PSLVERR")
        return value

    async def write(self, name, reg, value):
        status = await reg.write(value)
        if status != uvm_status_e.UVM_IS_OK:
            self.error(f"{name}: write returned PSLVERR")

    async def mirror_check(self, name, reg, what):
        """Frontdoor read compared with the mirror (non-volatile fields)."""
        exp = reg.get_mirrored_value()
        got = await self.read(name, reg)  # auto-predict updates the mirror afterwards
        m = check_mask(reg)
        self.checked += 1
        if (got ^ exp) & m:
            self.error(f"{what}: {name} read {got & m:#x}, mirror {exp & m:#x} (mask {m:#x})")

    # ------------------------------------------------------------ sequences
    async def reset_check(self):
        """Like uvm_reg_hw_reset_seq, but compares every field (also the
        volatile ones, which are stable right after reset) with the reset
        value from regs/periph_soc.json."""
        self.model.reset()
        for name, reg in regs(self.model):
            meta = REG_META[name]
            got = await self.read(name, reg)
            self.checked += 1
            if (got ^ meta["reset"]) & meta["mask"]:
                self.error(f"reset: {name} read {got & meta['mask']:#x}, expected {meta['reset']:#x}")

    async def bit_bash(self):
        """Like uvm_reg_bit_bash_seq: for every bit of every RW or W1C field,
        write it flipped (other bits at their mirrored value) and check the
        read-back against the mirror, which pyuvm predicts from the field
        access policy (RW: takes the value; W1C: 1 clears, 0 keeps). RO and
        volatile fields are written too but not compared. Each register is
        put back to its reset value afterwards."""
        for name, reg in regs(self.model):
            meta = REG_META[name]
            if "bit_bash" in meta["skip"]:
                continue
            if meta["bit_bash_background"]:
                await self.write(name, reg, meta["bit_bash_background"])
            for f in reg.get_fields():
                if f.get_access() not in ("RW", "W1C") or f.is_volatile():
                    continue
                for bit in range(f.get_lsb_pos(), f.get_lsb_pos() + f.get_n_bits()):
                    for _ in range(2):  # flip, then flip back
                        await self.write(name, reg, reg.get_mirrored_value() ^ (1 << bit))
                        await self.mirror_check(name, reg, f"bit-bash bit {bit}")
            await self.write(name, reg, meta["reset"])

    def peek(self, name):
        """Backdoor read: assemble the register value from the RTL signals."""
        value = 0
        reg = getattr(self.model, name)
        for f in reg.get_fields():
            path = REG_META[name]["hdl"].get(f.get_name())
            if not path:
                continue
            h = self.dut
            for part in path.split("."):
                h = getattr(h, part)
            value |= (int(h.value) & ((1 << f.get_n_bits()) - 1)) << f.get_lsb_pos()
        return value

    def peek_mask(self, name):
        reg = getattr(self.model, name)
        return sum(((1 << f.get_n_bits()) - 1) << f.get_lsb_pos() for f in reg.get_fields() if f.get_name() in REG_META[name]["hdl"])

    async def backdoor_check(self, rng):
        """Writes random legal values through the frontdoor, then checks for
        every register that frontdoor read == backdoor peek == mirror."""
        for name, reg in regs(self.model):
            meta = REG_META[name]
            if "bit_bash" in meta["skip"] or name in ("timer_ctrl",):
                continue  # FIFO port; keep the timer stopped so COUNT stays still
            v = rng.getrandbits(32)
            if name == "uart_baud":
                v = rng.randint(4, 0xFFFF)
            await self.write(name, reg, v)
        for name, reg in regs(self.model):
            m = self.peek_mask(name)
            if not m:
                continue
            bd = self.peek(name)
            exp = reg.get_mirrored_value()
            fd = await self.read(name, reg)
            self.checked += 1
            if (fd ^ bd) & m:
                self.error(f"backdoor: {name} frontdoor {fd & m:#x} != backdoor {bd & m:#x}")
            cm = check_mask(reg) & m
            if (bd ^ exp) & cm:
                self.error(f"backdoor: {name} backdoor {bd & cm:#x} != mirror {exp & cm:#x}")
        for name, reg in regs(self.model):  # back to reset values
            if "bit_bash" not in REG_META[name]["skip"] and REG_META[name]["mask"] and name not in ("timer_ctrl",):
                await self.write(name, reg, REG_META[name]["reset"])

    async def w1c_check(self, bus, rng):
        """Sets several sticky W1C flags at once in hardware, then clears them
        one bit at a time through the register model. After each write the
        read-back must equal the mirror, in which pyuvm's W1C policy has
        cleared exactly the written bit."""
        m, clk = self.model, self.dut.clk
        # UART: overrun + parity error + frame error together
        baud = rng.randint(6, 10)
        await S.uart_config(bus, baud, "even")
        for i in range(9):  # 9 frames into an 8-deep FIFO: overrun
            await bus.frame(UartFrame(byte=rng.randrange(256), baud=baud, parity="even", bad_parity=(i == 3), bad_stop=(i == 5), idle_bits=1))
        await ClockCycles(clk, baud * 4)
        # TX overflow with the UART disabled (nothing is transmitted)
        ConfigDB().set(None, "*", "TX_MUST_DRAIN", False)
        await bus.write(S.UART + 0x08, 0)
        for i in range(9):
            await bus.write(S.UART + 0x00, i)
            if i < 8:
                bus.env.txchk.accepted(i)
        await self.mirror_flags_then_clear("uart_irq_st", [2, 3, 4, 5])
        # GPIO: two edge flags, then clear one at a time
        await bus.write(S.GPIO + 0x0C, 0xFFFF)
        m.gpio_rise_en.predict(0xFFFF)
        pins = rng.sample(range(16), 3)
        await bus.gpio(sum(1 << p for p in pins), 6)
        await self.mirror_flags_then_clear("gpio_irq_st", pins)
        await bus.write(S.GPIO + 0x0C, 0)
        m.gpio_rise_en.predict(0)
        # timer: match flag
        await bus.write(S.TIMER + 0x08, 3)
        await bus.write(S.TIMER + 0x0C, 0)
        await bus.write(S.TIMER + 0x00, 1)
        await ClockCycles(clk, 10)
        await self.mirror_flags_then_clear("timer_status", [0])

    async def mirror_flags_then_clear(self, name, bits):
        reg = getattr(self.model, name)
        await self.read(name, reg)  # predict the hardware-set flags into the mirror
        flags = reg.get_mirrored_value()
        missing = [b for b in bits if not (flags >> b) & 1]
        if missing:
            self.error(f"w1c: {name} = {flags:#x}; stimulus did not set bits {missing}")
            return
        for b in bits:
            await self.write(name, reg, 1 << b)
            await self.mirror_check(name, reg, f"W1C of bit {b}")
