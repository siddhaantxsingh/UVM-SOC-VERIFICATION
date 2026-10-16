"""Cycle-accurate reference model of periph_soc, written from the register
specification in the RTL headers. The scoreboard runs it in lockstep with
the DUT and compares every output on every cycle.

Convention: outputs(inp) gives the combinational outputs for the current
state and this cycle's inputs; step(inp) advances to the state after the
next rising clock edge."""
from dataclasses import dataclass, field

M32 = 0xFFFF_FFFF


@dataclass
class Inp:
    rst_n: int = 0
    psel: int = 0
    penable: int = 0
    pwrite: int = 0
    paddr: int = 0
    pwdata: int = 0
    uart_rx: int = 1
    gpio_in: int = 0


@dataclass
class Uart:
    DEPTH = 8
    enable: int = 0
    par_en: int = 0
    par_odd: int = 0
    baud: int = 16
    irq_en: int = 0
    rx_ovr: int = 0
    par_err: int = 0
    frm_err: int = 0
    tx_ovf: int = 0
    txq: list = field(default_factory=list)
    rxq: list = field(default_factory=list)
    waited: int = 0
    # transmitter
    tx_busy: int = 0
    tx: int = 1
    tx_div: int = 0
    tx_bit: int = 0
    tx_sh: int = 0
    tx_par: int = 0
    # receiver
    rx_s1: int = 1
    rx_s2: int = 1
    rx_busy: int = 0
    rx_div: int = 0
    rx_bit: int = 0
    rx_sh: int = 0
    rx_par: int = 0
    rx_valid: int = 0
    rx_byte: int = 0
    rx_perr: int = 0
    rx_ferr: int = 0

    def pending(self):
        return (self.tx_ovf << 5) | (self.frm_err << 4) | (self.par_err << 3) | (self.rx_ovr << 2) | ((len(self.txq) == 0) << 1) | (len(self.rxq) > 0)

    def irq(self):
        return int((self.pending() & self.irq_en) != 0)

    def comb(self, sel, i):
        """(prdata, pready, pslverr) for an access in this cycle."""
        access = sel and i.penable
        fire = access and self.waited
        a = i.paddr & 0xFF
        mapped = (a & 3) == 0 and a <= 0x18
        rd = 0
        if fire and not i.pwrite:
            rd = {
                0x00: self.rxq[0] if self.rxq else 0,
                0x04: (self.tx_busy << 4) | ((len(self.rxq) == self.DEPTH) << 3) | ((len(self.rxq) == 0) << 2) | ((len(self.txq) == 0) << 1) | (len(self.txq) == self.DEPTH),
                0x08: (self.par_odd << 2) | (self.par_en << 1) | self.enable,
                0x0C: self.baud,
                0x10: self.irq_en,
                0x14: self.pending(),
                0x18: len(self.rxq),
            }.get(a, 0)
        return rd, int(bool(fire)), int(bool(fire and not mapped))

    def step(self, sel, i):
        access = sel and i.penable
        fire = access and self.waited
        a = i.paddr & 0xFF
        nxt_waited = int(bool(access and not self.waited))
        push = fire and i.pwrite and a == 0x00
        pop = fire and not i.pwrite and a == 0x00 and len(self.rxq) > 0
        take = self.enable and not self.tx_busy and len(self.txq) > 0
        head = self.txq[0] if self.txq else 0
        # --- transmitter (uses current state)
        if take:
            self.tx_busy, self.tx_sh, self.tx_par, self.tx_bit, self.tx_div, self.tx = 1, head, (bin(head).count("1") & 1) ^ self.par_odd, 0, self.baud - 1, 0
        elif self.tx_busy:
            last = 10 if self.par_en else 9
            if self.tx_div != 0:
                self.tx_div -= 1
            elif self.tx_bit == last:
                self.tx_busy = 0
            else:
                b = self.tx_bit
                self.tx_bit += 1
                self.tx_div = self.baud - 1
                if b < 8:
                    self.tx = self.tx_sh & 1
                    self.tx_sh >>= 1
                elif b == 8 and self.par_en:
                    self.tx = self.tx_par
                else:
                    self.tx = 1
        # --- FIFOs and registers (rx_valid etc. are the registered values from the last edge)
        rx_valid, rx_byte, rx_perr, rx_ferr = self.rx_valid, self.rx_byte, self.rx_perr, self.rx_ferr
        push_ok = push and len(self.txq) < self.DEPTH  # fullness before this edge
        if push and not push_ok:
            self.tx_ovf = 1
        if take:
            self.txq.pop(0)
        if push_ok:
            self.txq.append(i.pwdata & 0xFF)
        if rx_valid and len(self.rxq) == self.DEPTH:
            self.rx_ovr = 1
        if rx_valid and rx_perr:
            self.par_err = 1
        if rx_valid and rx_ferr:
            self.frm_err = 1
        full_before = len(self.rxq) == self.DEPTH
        if pop:
            self.rxq.pop(0)
        if rx_valid and not full_before:
            self.rxq.append(rx_byte)
        if fire and i.pwrite:
            d = i.pwdata
            if a == 0x08:
                self.enable, self.par_en, self.par_odd = d & 1, (d >> 1) & 1, (d >> 2) & 1
            elif a == 0x0C:
                v = d & 0xFFFF
                self.baud = 4 if v < 4 else v
            elif a == 0x10:
                self.irq_en = d & 0x3F
            elif a == 0x14:
                if d & 4: self.rx_ovr = 0
                if d & 8: self.par_err = 0
                if d & 16: self.frm_err = 0
                if d & 32: self.tx_ovf = 0
        # --- receiver (uses the old synchronised value rx_s2)
        s2 = self.rx_s2
        self.rx_valid = 0
        if not self.rx_busy:
            if self.enable_before and not s2:
                self.rx_busy, self.rx_bit, self.rx_div = 1, 0, ((self.baud_before >> 1) - 1) & 0xFFFF
        elif self.rx_div != 0:
            self.rx_div -= 1
        else:
            self.rx_div = (self.baud_before - 1) & 0xFFFF
            b = self.rx_bit
            self.rx_bit = (b + 1) & 0xF
            if b == 0:
                if s2:
                    self.rx_busy = 0
            elif b <= 8:
                self.rx_sh = (self.rx_sh >> 1) | (s2 << 7)
            elif b == 9 and self.par_en_before:
                self.rx_par = s2
            else:
                self.rx_busy = 0
                self.rx_valid = 1
                self.rx_byte = self.rx_sh
                self.rx_ferr = int(not s2)
                self.rx_perr = int(bool(self.par_en_before and self.rx_par != ((bin(self.rx_sh).count("1") & 1) ^ self.par_odd_before)))
        self.rx_s2 = self.rx_s1
        self.rx_s1 = i.uart_rx
        self.waited = nxt_waited

    def snapshot_cfg(self):
        # the receiver sees the configuration registers as they were before this edge
        self.enable_before, self.baud_before, self.par_en_before, self.par_odd_before = self.enable, self.baud, self.par_en, self.par_odd


@dataclass
class Timer:
    enable: int = 0
    reload: int = 0
    irq_en: int = 0
    match: int = 0
    presc: int = 0
    pcnt: int = 0
    compare: int = M32
    count: int = 0

    def comb(self, sel, i):
        fire = sel and i.penable
        a = i.paddr & 0xFF
        rd = 0
        if fire and not i.pwrite:
            rd = {0x00: (self.irq_en << 2) | (self.reload << 1) | self.enable, 0x04: self.presc, 0x08: self.compare,
                  0x0C: self.count, 0x10: self.match}.get(a, 0)
        return rd, int(bool(fire)), int(bool(fire and not ((a & 3) == 0 and a <= 0x10)))

    def irq(self):
        return self.match & self.irq_en

    def step(self, sel, i):
        fire = sel and i.penable
        a = i.paddr & 0xFF
        tick = self.enable and self.pcnt == self.presc
        enable0 = self.enable
        if enable0:
            self.pcnt = 0 if tick else (self.pcnt + 1) & 0xFFFF
        if tick:
            if self.count == self.compare:
                self.match = 1
                self.count = 0
                if not self.reload:
                    self.enable = 0
            else:
                self.count = (self.count + 1) & M32
        if fire and i.pwrite:
            d = i.pwdata
            if a == 0x00:
                self.enable, self.reload, self.irq_en = d & 1, (d >> 1) & 1, (d >> 2) & 1
                self.pcnt = 0
            elif a == 0x04:
                self.presc = d & 0xFFFF
            elif a == 0x08:
                self.compare = d & M32
            elif a == 0x0C:
                self.count = d & M32
            elif a == 0x10 and d & 1:
                self.match = 0


@dataclass
class Gpio:
    s1: int = 0
    s2: int = 0
    prev: int = 0
    out: int = 0
    oe: int = 0
    rise_en: int = 0
    fall_en: int = 0
    ist: int = 0

    def comb(self, sel, i):
        fire = sel and i.penable
        a = i.paddr & 0xFF
        rd = 0
        if fire and not i.pwrite:
            rd = {0x00: self.out, 0x04: self.oe, 0x08: self.s2, 0x0C: self.rise_en, 0x10: self.fall_en, 0x14: self.ist}.get(a, 0)
        return rd, int(bool(fire)), int(bool(fire and not ((a & 3) == 0 and a <= 0x14)))

    def irq(self):
        return int(self.ist != 0)

    def step(self, sel, i):
        fire = sel and i.penable
        a = i.paddr & 0xFF
        rise = self.s2 & ~self.prev & 0xFFFF
        fall = ~self.s2 & self.prev & 0xFFFF
        clr = (i.pwdata & 0xFFFF) if (fire and i.pwrite and a == 0x14) else 0
        self.ist = (self.ist & ~clr & 0xFFFF) | (rise & self.rise_en) | (fall & self.fall_en)
        self.prev, self.s2, self.s1 = self.s2, self.s1, i.gpio_in & 0xFFFF
        if fire and i.pwrite:
            d = i.pwdata & 0xFFFF
            if a == 0x00: self.out = d
            elif a == 0x04: self.oe = d
            elif a == 0x0C: self.rise_en = d
            elif a == 0x10: self.fall_en = d


class SocModel:
    def __init__(self):
        self.reset()

    def reset(self):
        self.uart, self.timer, self.gpio, self.ien = Uart(), Timer(), Gpio(), 0

    def decode(self, i):
        hi_ok = (i.paddr >> 16) == 0 and ((i.paddr >> 8) & 0xF) == 0
        sel = (i.paddr >> 12) & 0xF
        p = i.psel
        return (p and hi_ok and sel == 0, p and hi_ok and sel == 1, p and hi_ok and sel == 2, p and hi_ok and sel == 0xF)

    def pending(self):
        return (self.gpio.irq() << 2) | (self.timer.irq() << 1) | self.uart.irq()

    def outputs(self, i):
        """Expected combinational and registered outputs for this cycle."""
        su, st, sg, si = self.decode(i)
        ru, yu, eu = self.uart.comb(su, i)
        rt, yt, et = self.timer.comb(st, i)
        rg, yg, eg = self.gpio.comb(sg, i)
        fire_i = si and i.penable
        a = i.paddr & 0xFF
        ri = 0
        if fire_i and not i.pwrite:
            ri = self.pending() if a == 0 else self.ien if a == 4 else 0
        ei = int(bool(fire_i and a not in (0, 4)))
        none = i.psel and not (su or st or sg or si)
        pready = yu if su else yt if st else yg if sg else int(bool(i.psel and i.penable))
        pslverr = eu if su else et if st else eg if sg else ei if si else int(bool(none and i.penable))
        return {"prdata": ru | rt | rg | ri, "pready": pready, "pslverr": pslverr, "uart_tx": self.uart.tx,
                "gpio_out": self.gpio.out, "gpio_oe": self.gpio.oe, "irq": int((self.pending() & self.ien) != 0)}

    def step(self, i):
        if not i.rst_n:
            self.reset()
            return
        su, st, sg, si = self.decode(i)
        self.uart.snapshot_cfg()
        self.uart.step(su, i)
        self.timer.step(st, i)
        self.gpio.step(sg, i)
        if si and i.penable and i.pwrite and (i.paddr & 0xFF) == 4:
            self.ien = i.pwdata & 7
