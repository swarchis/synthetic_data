import json
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

# Parameters come from sim.py's sweep (configs.json); defaults match the RTL.
P = json.loads(os.environ.get("RTL_PARAMS", "{}"))
N = P.get("NUM_SRC", 8)
PB = P.get("PRIO_BITS", 3)
MASK = (1 << N) - 1
PMAX = (1 << PB) - 1
PRIO_WORDS = (N + 7) // 8
CTRL, ENABLE, TRIGGER, PENDING, FORCE, THRESH, CLAIM, INSERVICE, STATUS, ID = range(0, 0x28, 4)
PRIO0 = 0x40
ID_VALUE = 0x1C0D0000 | (PB << 8) | N
MAPPED = set(range(0, 0x28, 4)) | {PRIO0 + 4 * k for k in range(PRIO_WORDS)}
READ_ONLY = {INSERVICE, STATUS, ID}


class IntcModel:
    """Python reference model of the whole controller, per spec.md."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.gen = self.enable = self.trigger = self.thresh = 0
        self.prio = [0] * N
        self.pending = self.inserv = 0
        self.meta = self.s = self.s_prev = 0

    def best(self):
        idx, bp = -1, 0
        elig = self.pending & self.enable & ~self.inserv
        for k in range(N):
            if elig >> k & 1 and self.prio[k] > bp:
                idx, bp = k, self.prio[k]
        return idx, bp

    def irq(self):
        idx, bp = self.best()
        return int(bool(self.gen) and idx >= 0 and bp > self.thresh)

    def irq_id(self):
        return self.best()[0] + 1 if self.irq() else 0

    @staticmethod
    def err(addr, write):
        return addr % 4 != 0 or addr not in MAPPED or (write and addr in READ_ONLY)

    def read_value(self, addr):
        if addr >= PRIO0:
            k = (addr - PRIO0) // 4
            return sum(self.prio[8 * k + j] << (4 * j) for j in range(8) if 8 * k + j < N)
        return {CTRL: self.gen, ENABLE: self.enable, TRIGGER: self.trigger, PENDING: self.pending,
                FORCE: 0, THRESH: self.thresh, CLAIM: self.irq_id(), INSERVICE: self.inserv,
                STATUS: (self.irq_id() << 8) | self.irq(), ID: ID_VALUE}[addr]

    def apb_outputs(self, c):
        """(PRDATA, PSLVERR) during this cycle, before the clock edge."""
        if not (c["PSEL"] and c["PENABLE"]):
            return 0, 0
        e = self.err(c["PADDR"], c["PWRITE"])
        data = 0 if (c["PWRITE"] or e) else self.read_value(c["PADDR"])
        return data, int(e)

    def step(self, c):
        """Advance one rising edge with this cycle's inputs."""
        if not c["PRESETn"]:
            self.reset()
            return
        gen, enable, trigger, thresh, prio, inserv = (self.gen, self.enable, self.trigger, self.thresh,
                                                      list(self.prio), self.inserv)
        clear = force = 0
        addr, data = c["PADDR"], c["PWDATA"]
        if c["PSEL"] and c["PENABLE"] and not self.err(addr, c["PWRITE"]):
            if c["PWRITE"]:
                if addr == CTRL:
                    gen = data & 1
                elif addr == ENABLE:
                    enable = data & MASK
                elif addr == TRIGGER:
                    trigger = data & MASK
                elif addr == PENDING:
                    clear |= data & MASK
                elif addr == FORCE:
                    force = data & MASK
                elif addr == THRESH:
                    thresh = data & PMAX
                elif addr == CLAIM:
                    if 1 <= data <= N:
                        inserv &= ~(1 << (data - 1))
                elif addr >= PRIO0:
                    k = (addr - PRIO0) // 4
                    for j in range(8):
                        if 8 * k + j < N:
                            prio[8 * k + j] = (data >> (4 * j)) & PMAX
            elif addr == CLAIM and self.irq():
                idx = self.irq_id() - 1
                inserv |= 1 << idx
                clear |= 1 << idx
        rise = self.s & ~self.s_prev
        set_ = (self.trigger & rise) | (~self.trigger & MASK & self.s) | force
        self.pending = (self.pending & ~clear) | set_
        self.s_prev, self.s, self.meta = self.s, self.meta, c["irq_src"] & MASK
        self.gen, self.enable, self.trigger, self.thresh, self.prio, self.inserv = \
            gen, enable, trigger, thresh, prio, inserv


class Stim:
    """Builds cycle-by-cycle inputs. Each APB transfer is a setup and an access cycle."""

    def __init__(self, rng=None):
        self.cycles, self.src, self.rng = [], 0, rng

    def _cycle(self, **kw):
        c = {"PRESETn": 1, "PSEL": 0, "PENABLE": 0, "PWRITE": 0, "PADDR": 0, "PWDATA": 0, "irq_src": self.src}
        if self.rng:     # junk on the bus when not selected: must be ignored
            c.update(PADDR=self.rng.randrange(256), PWDATA=self.rng.getrandbits(32), PWRITE=self.rng.randrange(2),
                     PENABLE=self.rng.randrange(2))
        c.update(kw)
        self.cycles.append(c)

    def idle(self, n=1):
        for _ in range(n):
            self._cycle()

    def irq(self, value, hold=0):
        self.src = value & MASK
        self.idle(hold)

    def write(self, addr, data):
        self._cycle(PSEL=1, PENABLE=0, PWRITE=1, PADDR=addr, PWDATA=data)
        self._cycle(PSEL=1, PENABLE=1, PWRITE=1, PADDR=addr, PWDATA=data)

    def read(self, addr):
        self._cycle(PSEL=1, PENABLE=0, PWRITE=0, PADDR=addr)
        self._cycle(PSEL=1, PENABLE=1, PWRITE=0, PADDR=addr, tag="read")

    def reset(self, n=2):
        for _ in range(n):
            self._cycle(PRESETn=0)


def read(sig, name, cycle):
    v = sig.value
    assert v.is_resolvable, f"cycle {cycle}: {name} is unknown ({v})"
    return int(v)


async def start(dut):
    cocotb.start_soon(Clock(dut.PCLK, 10, "ns").start())
    dut.PRESETn.value = 0
    for s in ("PSEL", "PENABLE", "PWRITE", "PADDR", "PWDATA", "irq_src"):
        getattr(dut, s).value = 0
    await ClockCycles(dut.PCLK, 2)
    await FallingEdge(dut.PCLK)
    return IntcModel()


async def run(dut, model, stim):
    """Drive every cycle, compare all outputs with the model, step on the rising edge.
    Returns [(addr, PRDATA, PSLVERR)] for each read access, for explicit checks."""
    reads = []
    for cycle, c in enumerate(stim.cycles):
        for k in ("PRESETn", "PSEL", "PENABLE", "PWRITE", "PADDR", "PWDATA", "irq_src"):
            getattr(dut, k).value = c[k]
        await ReadOnly()
        ctx = f"cycle {cycle} (PADDR=0x{c['PADDR']:02X} {'W' if c['PWRITE'] else 'R'} sel={c['PSEL']} en={c['PENABLE']})"
        if c["PRESETn"]:
            assert read(dut.PREADY, "PREADY", ctx) == 1, f"{ctx}: PREADY must always be 1"
            assert read(dut.irq, "irq", ctx) == model.irq(), f"{ctx}: irq {int(dut.irq.value)}, expected {model.irq()}"
            assert read(dut.irq_id, "irq_id", ctx) == model.irq_id(), \
                f"{ctx}: irq_id {int(dut.irq_id.value)}, expected {model.irq_id()}"
            want_data, want_err = model.apb_outputs(c)
            got_data, got_err = read(dut.PRDATA, "PRDATA", ctx), read(dut.PSLVERR, "PSLVERR", ctx)
            assert got_err == want_err, f"{ctx}: PSLVERR {got_err}, expected {want_err}"
            assert got_data == want_data, f"{ctx}: PRDATA 0x{got_data:08X}, expected 0x{want_data:08X}"
            if c.get("tag") == "read":
                reads.append((c["PADDR"], got_data, got_err))
        await RisingEdge(dut.PCLK)
        model.step(c)
        await FallingEdge(dut.PCLK)
    return reads


def setup(stim, prios, enable=MASK, trigger=0, thresh=0, gen=1):
    """Program priorities {source: prio}, then ENABLE, TRIGGER, THRESHOLD, CTRL."""
    for k in range(PRIO_WORDS):
        stim.write(PRIO0 + 4 * k, sum((prios.get(8 * k + j, 0) & PMAX) << (4 * j) for j in range(8)))
    stim.write(ENABLE, enable)
    stim.write(TRIGGER, trigger)
    stim.write(THRESH, thresh)
    stim.write(CTRL, gen)


@cocotb.test()
async def reset_values(dut):
    model = await start(dut)
    st = Stim()
    for a in sorted(MAPPED):
        st.read(a)
    reads = await run(dut, model, st)
    for addr, data, err in reads:
        want = ID_VALUE if addr == ID else 0
        assert (data, err) == (want, 0), f"after reset 0x{addr:02X} = 0x{data:08X} err {err}, expected 0x{want:08X}"


@cocotb.test()
async def register_read_write(dut):
    model = await start(dut)
    st = Stim()
    regs = [CTRL, ENABLE, TRIGGER, THRESH] + [PRIO0 + 4 * k for k in range(PRIO_WORDS)]
    for a in regs:
        st.write(a, 0xFFFFFFFF)
        st.read(a)
    for a in regs:
        st.write(a, 0)
        st.read(a)
    reads = await run(dut, model, st)
    full_prio = lambda k: sum(PMAX << (4 * j) for j in range(8) if 8 * k + j < N)
    want = {CTRL: 1, ENABLE: MASK, TRIGGER: MASK, THRESH: PMAX, **{PRIO0 + 4 * k: full_prio(k) for k in range(PRIO_WORDS)}}
    for (addr, data, _), expect in zip(reads, [want[a] for a in regs] + [0] * len(regs)):
        assert data == expect, f"0x{addr:02X} read 0x{data:08X}, expected 0x{expect:08X}"


@cocotb.test()
async def error_responses(dut):
    model = await start(dut)
    st = Stim()
    bad = [0x01, 0x02, 0x03, 0x05, 0x28, 0x2C, 0x3C, PRIO0 + 4 * PRIO_WORDS, 0xFC]
    for a in bad:
        st.write(a, 0xFFFFFFFF)
        st.read(a)
    for a in sorted(READ_ONLY):
        st.write(a, 0xFFFFFFFF)
    st.write(CTRL + 1, 1)                       # misaligned write to CTRL must not enable
    st.read(CTRL)
    # Misaligned reads next to CLAIM while an interrupt is active must not claim.
    setup(st, {0: 1})
    st.write(FORCE, 1)
    for a in (CLAIM + 1, CLAIM + 2, CLAIM + 3):
        st.read(a)
    st.read(INSERVICE)
    reads = await run(dut, model, st)
    n_bad = len(bad)
    for addr, data, err in reads[:n_bad]:
        assert (data, err) == (0, 1), f"0x{addr:02X}: read 0x{data:08X} err {err}, expected error"
    assert reads[n_bad][1:] == (0, 0), f"CTRL changed by an erroring write: {reads[n_bad]}"
    for addr, data, err in reads[n_bad + 1:n_bad + 4]:
        assert (data, err) == (0, 1), f"misaligned CLAIM read 0x{addr:02X} must error and return 0"
    assert reads[-1][1] == 0, "a misaligned CLAIM read must not claim (INSERVICE changed)"


@cocotb.test()
async def level_interrupt_flow(dut):
    """Highest-numbered source, level triggered: claim, complete, re-pend while held."""
    model = await start(dut)
    top = N - 1
    st = Stim()
    setup(st, {top: 1})
    st.irq(1 << top, hold=4)
    st.read(STATUS)             # irq active: bit 0 and the id in [15:8]
    st.read(CLAIM)              # claims top
    st.read(INSERVICE)
    st.idle(2)
    st.read(CLAIM)              # nothing: in service
    st.write(CLAIM, top + 1)    # complete; still high so it re-pends
    st.idle(1)
    st.read(CLAIM)
    st.irq(0, hold=3)
    st.write(CLAIM, top + 1)
    st.write(PENDING, MASK)
    st.read(PENDING)
    reads = await run(dut, model, st)
    status = reads.pop(0)[1]
    assert status == ((top + 1) << 8) | 1, f"STATUS 0x{status:08X}, expected 0x{((top + 1) << 8) | 1:08X}"
    assert reads[0][1] == top + 1, f"first claim returned {reads[0][1]}, expected {top + 1}"
    assert reads[1][1] == 1 << top, f"INSERVICE 0x{reads[1][1]:X}, expected 0x{1 << top:X}"
    assert reads[2][1] == 0, "claim while in service must return 0"
    assert reads[3][1] == top + 1, "level source still high must be claimable again after complete"
    assert reads[4][1] == 0, "pending must clear once the line is low and W1C is written"


@cocotb.test()
async def edge_interrupt_flow(dut):
    """1-cycle pulses, back-to-back pulses, and a pulse while in service."""
    model = await start(dut)
    st = Stim()
    setup(st, {1: 2}, trigger=1 << 1)
    st.irq(0b10, hold=1)
    st.irq(0, hold=4)
    st.read(CLAIM)                          # claims source 1
    st.irq(0b10, hold=1)                    # pulse while in service: becomes pending again
    st.irq(0, hold=3)
    st.read(CLAIM)                          # in service: 0
    st.write(CLAIM, 2)
    st.idle(1)
    st.read(CLAIM)                          # pending again: 2
    st.write(CLAIM, 2)
    for _ in range(3):                      # back-to-back pulses
        st.irq(0b10, hold=1)
        st.irq(0, hold=1)
    st.irq(0b10, hold=6)                    # held high: one edge only
    st.read(CLAIM)
    st.write(CLAIM, 2)
    st.idle(2)
    st.read(CLAIM)                          # no new edge while held: 0
    st.irq(0, hold=3)
    reads = await run(dut, model, st)
    assert [r[1] for r in reads] == [2, 0, 2, 2, 0], f"edge claims {[r[1] for r in reads]}"


@cocotb.test()
async def priority_and_threshold(dut):
    model = await start(dut)
    st = Stim()
    prios = {0: 1, 1: PMAX, 2: PMAX, 3: 0}
    if N > 8:
        prios[N - 1] = PMAX                 # lives in the second PRIO word
    setup(st, prios)
    st.write(FORCE, MASK)
    st.idle(1)
    want = [2, 3] + ([N] if N > 8 else []) + [1, 0]
    for _ in want:
        st.read(CLAIM)
    reads = await run(dut, model, st)
    got = [r[1] for r in reads]
    assert got == want, f"claim order {got}, expected {want} (ties: lowest number first; priority 0 never)"

    st = Stim()
    for k in range(1, N + 1):
        st.write(CLAIM, k)
    st.write(PENDING, MASK)
    st.write(THRESH, PMAX)                  # nothing exceeds the max threshold
    st.write(FORCE, 0b1)                    # source 0 has priority 1
    st.read(CLAIM)
    st.write(THRESH, 1)                     # equal to the priority: still blocked (strictly greater)
    st.read(CLAIM)
    st.write(THRESH, 0)
    st.read(CLAIM)
    reads = await run(dut, model, st)
    assert [r[1] for r in reads] == [0, 0, 1], f"threshold claims {[r[1] for r in reads]}"


@cocotb.test()
async def w1c_and_force(dut):
    model = await start(dut)
    st = Stim()
    pattern = 0b1011 & MASK
    st.write(FORCE, pattern)
    st.read(PENDING)
    st.write(PENDING, 0b0010)
    st.read(PENDING)
    st.irq(0b0001, hold=3)                  # level source 0 high
    st.write(PENDING, 0b0001)               # set wins: stays pending
    st.read(PENDING)
    st.read(FORCE)
    reads = await run(dut, model, st)
    assert reads[0][1] == pattern, f"FORCE: PENDING 0x{reads[0][1]:X}, expected 0x{pattern:X}"
    assert reads[1][1] == pattern & ~0b0010, f"W1C cleared too much or too little: 0x{reads[1][1]:X}"
    assert reads[2][1] & 1, "level source held high must stay pending after W1C"
    assert reads[3][1] == 0, "FORCE reads as 0"


@cocotb.test()
async def claim_edge_cases(dut):
    model = await start(dut)
    st = Stim()
    setup(st, {0: 1, 1: 1}, gen=0)
    st.write(FORCE, 0b11)
    st.read(CLAIM)                          # GEN off: 0, no side effect
    st.read(INSERVICE)
    st.write(CTRL, 1)
    for bad in (0, N + 1, 0xFFFFFFFF):
        st.write(CLAIM, bad)                # ignored, no error
    st.read(CLAIM)                          # 1
    st.write(CLAIM, 2)                      # completing a source not in service: no effect
    st.read(INSERVICE)
    st.read(CLAIM)                          # 2
    st.read(CLAIM)                          # 0
    st.read(STATUS)
    reads = await run(dut, model, st)
    assert [r[1] for r in reads] == [0, 0, 1, 1, 2, 0, 0], f"claim edge cases {[r[1] for r in reads]}"


@cocotb.test()
async def reset_mid_operation(dut):
    model = await start(dut)
    st = Stim()
    setup(st, {0: 1, N - 1: PMAX}, trigger=MASK)
    st.irq(MASK, hold=4)
    st.read(CLAIM)
    st._cycle(PSEL=1, PENABLE=0, PWRITE=1, PADDR=CTRL, PWDATA=0)
    st.reset(2)                             # reset in the middle of a transfer
    st.idle(2)
    for a in (CTRL, ENABLE, PENDING, INSERVICE, PRIO0, STATUS):
        st.read(a)
    reads = await run(dut, model, st)
    # Lines are still high and reset makes every source level triggered again,
    # so PENDING refills two cycles after reset; everything else stays at 0.
    got = {r[0]: r[1] for r in reads[1:]}
    assert got.pop(PENDING) == MASK, "held-high lines must be pending again after reset (level mode)"
    assert all(v == 0 for v in got.values()), f"state survived reset: {got}"


@cocotb.test()
async def random_traffic(dut):
    model = await start(dut)
    rng = random.Random(1234 + N)
    st = Stim(rng)
    setup(st, {k: rng.randrange(PMAX + 1) for k in range(N)}, trigger=rng.getrandbits(N), thresh=0)
    addrs = (sorted(MAPPED) + [CLAIM] * 8 + [PENDING, STATUS] * 3 + [0x2C, PRIO0 + 4 * PRIO_WORDS]
             + [a + rng.randrange(1, 4) for a in sorted(MAPPED)])        # misaligned neighbors
    for _ in range(700):
        r = rng.random()
        if r < 0.15:
            st.irq(st.src ^ (1 << rng.randrange(N)))
        if r < 0.03:
            st.reset(1)
        a = rng.choice(addrs)
        roll = rng.random()
        if roll < 0.45:
            st.read(a)
        elif roll < 0.85:
            if a == CLAIM:
                data = rng.randrange(N + 2)
            elif a >= PRIO0:
                data = rng.getrandbits(32)
            elif a == CTRL:
                data = int(rng.random() < 0.85)     # keep GEN on most of the time
            elif a == THRESH:
                data = rng.randrange(PMAX + 1) if rng.random() < 0.3 else 0
            else:
                data = rng.choice([rng.getrandbits(32), MASK, 0, 1 << rng.randrange(N)])
            st.write(a, data)
        else:
            st.idle(rng.randrange(1, 4))
    await run(dut, model, st)
