"""UVM tests. Each builds the environment, resets the DUT and runs a
scenario; the lockstep scoreboard and the checkers decide pass/fail.
The random seed is cocotb's RANDOM_SEED (set by the regression runner)."""
import os
import random

import cocotb
import pyuvm
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, First, Timer
from pyuvm import ConfigDB, uvm_test

import sequences as S
from env import SocEnv

SCALE = float(os.environ.get("TEST_SCALE", "1"))
LIMIT_US = 3000  # simulated time per test at scale 1


ONLY_TESTS = [t for t in os.environ.get("ONLY_TESTS", "").split(",") if t]


def utest(cls):
    """pyuvm.test(), skipping tests not named in ONLY_TESTS (when set).
    cocotb 1.9 cannot select pyuvm tests by class name through TESTCASE."""
    return pyuvm.test(skip=bool(ONLY_TESTS) and cls.__name__ not in ONLY_TESTS)(cls)


def n(x):
    return max(1, int(x * SCALE))


class BaseTest(uvm_test):
    def build_phase(self):
        self.dut = cocotb.top
        ConfigDB().set(None, "*", "DUT", self.dut)
        ConfigDB().set(None, "*", "UART_LINE", (16, None))
        self.env = SocEnv("env", self)
        self.rng = random.Random(cocotb.RANDOM_SEED)

    async def run_phase(self):
        self.raise_objection()
        d = self.dut
        cocotb.start_soon(Clock(d.clk, 10, units="ns").start())
        d.rst_n.value = 0
        d.uart_rx.value = 1
        d.gpio_in.value = 0
        await ClockCycles(d.clk, 3)
        d.rst_n.value = 1
        await ClockCycles(d.clk, 2)
        self.bus = S.Bus(self.env, self.rng)
        # stop at the first scoreboard mismatch, or when the scenario hangs
        # (a broken DUT can make a polling loop wait forever)
        task = cocotb.start_soon(self.scenario())
        limit = Timer(int(LIMIT_US * SCALE), units="us")
        res = await First(task, self.env.sb.failed.wait(), limit)
        if res is limit:
            task.kill()
            self.env.sb.errors.append(f"scenario did not finish within {LIMIT_US * SCALE:.0f} us of simulated time")
        elif not task.done():
            task.kill()
            await ClockCycles(d.clk, 3)  # let the bound SVA sample the failing cycle too
        else:
            await ClockCycles(d.clk, 20)
        self.drop_objection()

    async def scenario(self):
        raise NotImplementedError


@utest
class RegisterTest(BaseTest):
    async def scenario(self):
        await S.register_scenario(self.bus, self.rng, n(300))


@utest
class UartTxTest(BaseTest):
    async def scenario(self):
        await S.uart_tx_scenario(self.bus, self.rng, n(4), 12)


@utest
class UartRxTest(BaseTest):
    async def scenario(self):
        await S.uart_rx_scenario(self.bus, self.rng, n(3), 10)


@utest
class TimerTest(BaseTest):
    async def scenario(self):
        await S.timer_scenario(self.bus, self.rng, n(8))


@utest
class GpioTest(BaseTest):
    async def scenario(self):
        await S.gpio_scenario(self.bus, self.rng, n(60))


@utest
class IntcTest(BaseTest):
    async def scenario(self):
        await S.intc_directed(self.bus, self.rng)


@utest
class StressTest(BaseTest):
    """Everything at once: UART TX and RX, timer, GPIO and the interrupt
    controller compete for the bus."""

    async def scenario(self):
        await S.intc_scenario(self.bus, self.rng)
        await S.uart_config(self.bus, self.rng.randint(4, 12), self.rng.choice((None, "even", "odd")), irq_en=0x3F)
        line = ConfigDB().get(None, "", "UART_LINE")
        tx = cocotb.start_soon(S.uart_send(self.bus, [self.rng.randrange(256) for _ in range(n(10))]))
        rx = cocotb.start_soon(self._rx(line))
        await S.gpio_scenario(self.bus, self.rng, n(30))
        await tx
        await rx
        await S.intc_scenario(self.bus, self.rng)
        await S.uart_wait_tx_idle(self.bus)

    async def _rx(self, line):
        baud, parity = line
        for _ in range(n(6)):
            await self.bus.frame(S.UartFrame(byte=self.rng.randrange(256), baud=baud, parity=parity, idle_bits=self.rng.randint(0, 2)))


class _RalBase(BaseTest):
    async def ral(self):
        import ral
        chk = ral.RalChecker(ral.build_model(self.env.apb.seqr), self.dut, self.logger)
        return chk

    def finish(self, chk, what):
        self.logger.info(f"RAL {what}: {chk.checked} register checks, {len(chk.errors)} mismatches")
        assert not chk.errors, f"RAL {what}: {len(chk.errors)} mismatches; first: {chk.errors[0]}"


@utest
class RalRegisterTest(_RalBase):
    """pyuvm register model: reset values, bit-bash, frontdoor vs backdoor."""

    async def scenario(self):
        chk = await self.ral()
        await chk.reset_check()
        await chk.bit_bash()
        await chk.backdoor_check(self.rng)
        self.finish(chk, "reset + bit-bash + backdoor")


@utest
class RalW1cTest(_RalBase):
    """pyuvm register model: per-bit W1C of hardware-set sticky flags."""

    async def scenario(self):
        chk = await self.ral()
        await chk.w1c_check(self.bus, self.rng)
        self.finish(chk, "W1C")
