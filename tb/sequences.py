"""Sequences: single-item helpers and the stimulus scenarios used by tests."""
import random

import cocotb
from cocotb.triggers import ClockCycles
from pyuvm import ConfigDB, uvm_sequence

from agents import ApbItem, GpioItem, UartFrame

UART, TIMER, GPIO, INTC = 0x0000, 0x1000, 0x2000, 0xF000


class _One(uvm_sequence):
    def __init__(self, item):
        super().__init__("one")
        self.item = item

    async def body(self):
        await self.start_item(self.item)
        await self.finish_item(self.item)


class Bus:
    """Register access through the APB agent (what a CPU driver would do)."""

    def __init__(self, env, rng):
        self.env, self.rng = env, rng

    async def write(self, addr, data, idle=None):
        it = ApbItem(addr=addr, write=True, data=data & 0xFFFF_FFFF, idle=self.rng.choice((0, 0, 1, 2)) if idle is None else idle)
        await _One(it).start(self.env.apb.seqr)
        return it

    async def read(self, addr, idle=None):
        it = ApbItem(addr=addr, write=False, idle=self.rng.choice((0, 0, 1, 2)) if idle is None else idle)
        await _One(it).start(self.env.apb.seqr)
        return it.rdata

    async def frame(self, fr):
        await _One(fr).start(self.env.uart.seqr)

    async def gpio(self, value, hold):
        await _One(GpioItem(value=value, hold=hold)).start(self.env.gpio.seqr)


# ---------------------------------------------------------------- UART

def line(env, baud, parity):
    ConfigDB().set(None, "*", "UART_LINE", (max(4, baud), parity))


async def uart_config(bus, baud, parity, irq_en=0):
    await bus.write(UART + 0x0C, baud)
    ctrl = 1 | ((parity is not None) << 1) | ((parity == "odd") << 2)
    line(bus.env, baud, parity)
    await bus.write(UART + 0x10, irq_en)
    await bus.write(UART + 0x08, ctrl)


async def uart_wait_tx_idle(bus, limit=200000):
    for _ in range(limit):
        st = await bus.read(UART + 0x04)
        if st & 0b10010 == 0b00010:  # tx_empty and not tx_busy
            return
    raise TimeoutError("UART TX never drained")


async def uart_send(bus, data):
    """Push bytes with flow control (poll tx_full); each accepted byte is
    reported to the model-independent TX checker."""
    for b in data:
        while (await bus.read(UART + 0x04)) & 1:
            pass
        await bus.write(UART + 0x00, b)
        bus.env.txchk.accepted(b)


async def uart_tx_scenario(bus, rng, n_cfg=4, n_bytes=12):
    # every parity mode x baud class once (in random order), then random ones
    combos = [(p, c) for p in (None, "even", "odd") for c in ("min4", "small", "typical")]
    rng.shuffle(combos)
    combos += [(rng.choice((None, "even", "odd")), rng.choice(("min4", "small", "typical"))) for _ in range(n_cfg)]
    for parity, cls in combos:
        baud = {"min4": rng.choice((2, 3, 4)), "small": rng.randint(5, 15), "typical": rng.randint(16, 40)}[cls]  # 2, 3 clamp to 4
        await uart_wait_tx_idle(bus)
        await uart_config(bus, baud, parity, irq_en=rng.randrange(64))
        await uart_send(bus, [rng.randrange(256) for _ in range(rng.randint(1, n_bytes))])
    await uart_wait_tx_idle(bus)
    # overflow: transmitter disabled, 9 pushes into an 8-deep FIFO
    await bus.write(UART + 0x08, 0)
    data = [rng.randrange(256) for _ in range(9)]
    for b in data:
        await bus.write(UART + 0x00, b)
    assert (await bus.read(UART + 0x04)) & 1, "TX FIFO should be full"
    assert (await bus.read(UART + 0x14)) & 0x20, "TX overflow flag should be set"
    await bus.write(UART + 0x14, 0x20)
    assert not (await bus.read(UART + 0x14)) & 0x20, "W1C should clear TX overflow"
    for b in data[:8]:
        bus.env.txchk.accepted(b)
    baud = rng.randint(4, 20)
    await uart_config(bus, baud, None)
    await uart_wait_tx_idle(bus)


async def uart_rx_scenario(bus, rng, n_cfg=3, n_frames=10):
    for _ in range(n_cfg):
        baud = rng.choice((4, rng.randint(5, 15), rng.randint(16, 32)))
        parity = rng.choice((None, "even", "odd"))
        await uart_config(bus, baud, parity, irq_en=rng.randrange(64))
        sent, got = [], []
        frames = [UartFrame(byte=rng.randrange(256), baud=baud, parity=parity, idle_bits=rng.randint(0, 3)) for _ in range(rng.randint(1, n_frames))]

        async def sender():
            for f in frames:
                await bus.frame(f)
                sent.append(f.byte)

        task = cocotb.start_soon(sender())
        # reader drains the FIFO fast enough that nothing is lost
        while len(got) < len(frames):
            n = (await bus.read(UART + 0x18)) & 0xF
            for _ in range(n):
                got.append(await bus.read(UART + 0x00))
            await ClockCycles(bus.env.apb.drv.dut.clk, rng.randint(0, baud * 4))
        await task
        assert got == sent, f"RX data {got} != sent {sent}"
        pend = await bus.read(UART + 0x14)
        assert not pend & 0x1C, f"unexpected RX error flags {pend:#x}"
    # errors: bad parity, bad stop bit, a glitch, then overrun
    baud = rng.randint(8, 16)
    await uart_config(bus, baud, "even")
    await bus.frame(UartFrame(byte=0x5A, baud=baud, parity="even", bad_parity=True))
    await bus.frame(UartFrame(byte=0xA5, baud=baud, parity="even", bad_stop=True, idle_bits=2))
    await bus.frame(UartFrame(baud=baud, glitch=max(1, baud // 4)))
    await ClockCycles(bus.env.apb.drv.dut.clk, baud * 4)
    pend = await bus.read(UART + 0x14)
    assert pend & 0x18 == 0x18, f"parity and frame error flags expected, got {pend:#x}"
    assert (await bus.read(UART + 0x18)) == 2, "both damaged bytes are still delivered; the glitch is not"
    # W1C one flag at a time: only the written bit clears
    await bus.write(UART + 0x14, 0x08)
    assert (await bus.read(UART + 0x14)) & 0x18 == 0x10, "W1C of parity_err must leave frame_err"
    await bus.write(UART + 0x14, 0x10)
    assert not (await bus.read(UART + 0x14)) & 0x18
    for _ in range(2):
        await bus.read(UART + 0x00)
    for i in range(10):
        await bus.frame(UartFrame(byte=i, baud=baud, parity="even", idle_bits=0))
    await ClockCycles(bus.env.apb.drv.dut.clk, baud * 4)
    assert (await bus.read(UART + 0x14)) & 0x04, "overrun expected"
    assert [await bus.read(UART + 0x00) for _ in range(8)] == list(range(8)), "the first 8 bytes survive an overrun"
    # a bad-parity frame on top of the overrun, then clear only the overrun
    await bus.frame(UartFrame(byte=0x33, baud=baud, parity="even", bad_parity=True))
    await ClockCycles(bus.env.apb.drv.dut.clk, baud * 4)
    await bus.write(UART + 0x14, 0x04)
    pend = await bus.read(UART + 0x14)
    assert pend & 0x0C == 0x08, f"W1C of rx_overrun must leave parity_err, got {pend:#x}"
    await bus.write(UART + 0x14, 0x08)
    await bus.read(UART + 0x00)
    # traffic while disabled is ignored
    await bus.write(UART + 0x08, 0)
    await bus.frame(UartFrame(byte=0x77, baud=baud, parity=None))
    assert (await bus.read(UART + 0x18)) == 0


# ---------------------------------------------------------------- timer / GPIO / INTC

async def timer_scenario(bus, rng, rounds=6):
    clk = bus.env.apb.drv.dut.clk
    for _ in range(rounds):
        presc = rng.choice((0, 0, rng.randint(1, 4)))
        compare = rng.choice((0, rng.randint(1, 30)))
        reload = rng.randrange(2)
        await bus.write(TIMER + 0x00, 0)
        await bus.write(TIMER + 0x10, 1)
        await bus.write(TIMER + 0x04, presc)
        await bus.write(TIMER + 0x08, compare)
        await bus.write(TIMER + 0x0C, 0)
        await bus.write(TIMER + 0x00, 1 | (reload << 1) | (rng.randrange(2) << 2))
        for _ in range(rng.randint(2, 8)):
            await bus.read(TIMER + 0x0C)
            if rng.random() < 0.15:
                await bus.write(TIMER + 0x0C, rng.randint(0, compare))
        await ClockCycles(clk, (presc + 1) * (compare + 2) + 4)
        assert (await bus.read(TIMER + 0x10)) & 1, "compare match expected"
        en = (await bus.read(TIMER + 0x00)) & 1
        assert en == reload, "one-shot stops, auto-reload keeps running"
        await bus.write(TIMER + 0x10, 1)


async def gpio_scenario(bus, rng, rounds=40):
    await bus.write(GPIO + 0x0C, rng.randrange(1 << 16))
    await bus.write(GPIO + 0x10, rng.randrange(1 << 16))

    async def toggler():
        v = 0
        for _ in range(rounds):
            v ^= rng.randrange(1 << 16) & rng.choice((0x1, 0xF, 0xFFFF, 0x8001))
            await bus.gpio(v, rng.randint(1, 6))

    t = cocotb.start_soon(toggler())
    for _ in range(rounds):
        op = rng.randrange(6)
        if op == 0:
            await bus.write(GPIO + 0x00, rng.randrange(1 << 16))
        elif op == 1:
            await bus.write(GPIO + 0x04, rng.randrange(1 << 16))
        elif op == 2:
            await bus.write(GPIO + 0x14, await bus.read(GPIO + 0x14), idle=0)
        elif op == 3:
            await bus.write(GPIO + rng.choice((0x0C, 0x10)), rng.randrange(1 << 16))
        else:
            await bus.read(GPIO + rng.choice((0x00, 0x04, 0x08, 0x0C, 0x10, 0x14)))
    await t


async def intc_scenario(bus, rng):
    for _ in range(6):
        await bus.write(INTC + 0x04, rng.randrange(8))
        await bus.read(INTC + 0x00)
        await bus.read(INTC + 0x04)


async def register_scenario(bus, rng, n=200):
    """Random accesses over the whole map, legal and illegal."""
    regs = [UART + o for o in range(0, 0x1C, 4) if o != 0] + [TIMER + o for o in range(0, 0x14, 4)] + \
           [GPIO + o for o in range(0, 0x18, 4)] + [INTC, INTC + 4]
    bad = [UART + 0x1C, UART + 0x02, TIMER + 0x14, GPIO + 0x18, INTC + 8, 0x3000, 0x0000_0100, 0x0001_0000, 0x8000_1000]
    for _ in range(n):
        a = rng.choice(regs) if rng.random() < 0.85 else rng.choice(bad)
        if a == TIMER + 0x00 and rng.random() < 0.5:
            continue  # keep the timer mostly idle so reads are not all counting
        if rng.random() < 0.5:
            await bus.write(a, rng.getrandbits(32))
        else:
            await bus.read(a)
    # the UART data register last: reading it pops, writing it transmits
    await bus.write(UART + 0x08, 0)
    await bus.read(UART + 0x00)


async def intc_directed(bus, rng):
    """Raise each interrupt source with its INTC enable on and then off,
    checking the irq pin through PENDING and the output."""
    dut = bus.env.apb.drv.dut
    clk = dut.clk

    async def check(src_bit, raise_fn, clear_fn):
        for enabled in (1, 0):
            await bus.write(INTC + 0x04, (1 << src_bit) if enabled else 0)
            await raise_fn()
            assert (await bus.read(INTC + 0x00)) >> src_bit & 1, f"source {src_bit} should be pending"
            await ClockCycles(clk, 1)
            assert int(dut.irq.value) == enabled, f"irq pin for source {src_bit}, enable={enabled}"
            await clear_fn()

    async def uart_raise():
        await bus.write(UART + 0x10, 0x02)  # tx_empty interrupt (the FIFO is empty)

    async def uart_clear():
        await bus.write(UART + 0x10, 0)

    async def timer_raise():
        await bus.write(TIMER + 0x08, rng.randint(0, 5))
        await bus.write(TIMER + 0x0C, 0)
        await bus.write(TIMER + 0x00, 0b101)
        await ClockCycles(clk, 12)

    async def timer_clear():
        await bus.write(TIMER + 0x00, 0)
        await bus.write(TIMER + 0x10, 1)

    async def gpio_raise():
        await bus.write(GPIO + 0x0C, 1)
        await bus.gpio(1, 5)
        await bus.gpio(0, 1)

    async def gpio_clear():
        await bus.write(GPIO + 0x14, 0xFFFF)

    await check(0, uart_raise, uart_clear)
    await check(1, timer_raise, timer_clear)
    await check(2, gpio_raise, gpio_clear)
