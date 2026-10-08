import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

WIDTH = 8
MASK = (1 << WIDTH) - 1


class ShiftModel:
    """Python reference model: what the register should do, per spec.md."""

    def __init__(self):
        self.q = 0

    def step(self, rst, load, din, shift_en, dir, sin):
        if rst:
            self.q = 0
        elif load:
            self.q = din
        elif shift_en:
            if dir:
                self.q = (sin << (WIDTH - 1)) | (self.q >> 1)
            else:
                self.q = ((self.q << 1) | sin) & MASK

    def sout(self, dir):
        return self.q & 1 if dir else self.q >> (WIDTH - 1)


def read(sig, name, cycle):
    """Read a signal as an int, failing cleanly if it holds X or Z (unknown) bits."""
    v = sig.value
    assert v.is_resolvable, f"cycle {cycle}: {name} is unknown ({v})"
    return int(v)


def drive(dut, rst=0, load=0, din=0, shift_en=0, dir=0, sin=0):
    dut.rst.value = rst
    dut.load.value = load
    dut.din.value = din
    dut.shift_en.value = shift_en
    dut.dir.value = dir
    dut.sin.value = sin


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    drive(dut, rst=1, load=1, din=0xA5)       # reset must win over load
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)


async def run(dut, model, stimulus):
    """Each item is a dict of inputs. Inputs change on the falling edge,
    state moves on the rising edge, outputs are checked in between."""
    for cycle, s in enumerate(stimulus):
        q = read(dut.q, "q", cycle)
        assert q == model.q, f"cycle {cycle}: q {q:0{WIDTH}b}, expected {model.q:0{WIDTH}b}"
        drive(dut, **s)
        await ReadOnly()
        sout = read(dut.sout, "sout", cycle)
        want = model.sout(s.get("dir", 0))
        assert sout == want, f"cycle {cycle}: sout {sout} (dir={s.get('dir', 0)}), expected {want}"
        await RisingEdge(dut.clk)
        model.step(**{k: s.get(k, 0) for k in ("rst", "load", "din", "shift_en", "dir", "sin")})
        await FallingEdge(dut.clk)
    q = read(dut.q, "q", len(stimulus))
    assert q == model.q, f"end: q {q:0{WIDTH}b}, expected {model.q:0{WIDTH}b}"


@cocotb.test()
async def reset_state(dut):
    await start(dut)
    await run(dut, ShiftModel(), [{}])


@cocotb.test()
async def shift_left_then_right(dut):
    await start(dut)
    model = ShiftModel()
    stim = [{"load": 1, "din": 0b1011_0010}]
    stim += [{"shift_en": 1, "dir": 0, "sin": b} for b in (1, 0, 1, 1)]
    stim += [{"shift_en": 1, "dir": 1, "sin": b} for b in (0, 1, 1, 0, 0, 1)]
    stim += [{"dir": d} for d in (0, 1, 0)]               # hold, sout follows dir
    await run(dut, model, stim)


@cocotb.test()
async def priority(dut):
    """load beats shift; rst beats both."""
    await start(dut)
    model = ShiftModel()
    stim = [{"load": 1, "din": 0x3C, "shift_en": 1, "dir": 1, "sin": 1},
            {"shift_en": 1, "dir": 0, "sin": 1},
            {"rst": 1, "load": 1, "din": 0xFF, "shift_en": 1},
            {"load": 1, "din": 0x81}]
    await run(dut, model, stim)


@cocotb.test()
async def random_traffic(dut):
    await start(dut)
    model = ShiftModel()
    rng = random.Random(1234)
    stim = []
    for _ in range(600):
        stim.append({"rst": int(rng.random() < 0.02), "load": int(rng.random() < 0.15),
                     "din": rng.randrange(MASK + 1), "shift_en": int(rng.random() < 0.7),
                     "dir": rng.randrange(2), "sin": rng.randrange(2)})
    await run(dut, model, stim)
