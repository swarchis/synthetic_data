import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

N = 4


class ArbiterModel:
    """Python reference model: what the arbiter should do, per spec.md."""

    def __init__(self):
        self.grant, self.last = 0, N - 1

    def step(self, rst, req):
        if rst:
            self.grant, self.last = 0, N - 1
            return
        for k in range(1, N + 1):
            i = (self.last + k) % N
            if req >> i & 1:
                self.grant, self.last = 1 << i, i
                return
        self.grant = 0


def read(sig, name, cycle):
    """Read a signal as an int, failing cleanly if it holds X or Z (unknown) bits."""
    v = sig.value
    assert v.is_resolvable, f"cycle {cycle}: {name} is unknown ({v})"
    return int(v)


def check(dut, model, cycle):
    g = read(dut.grant, "grant", cycle)
    assert g == model.grant, f"cycle {cycle}: grant {g:0{N}b}, expected {model.grant:0{N}b}"
    li = read(dut.last_idx, "last_idx", cycle)
    assert li == model.last, f"cycle {cycle}: last_idx {li}, expected {model.last}"


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    dut.rst.value = 1
    dut.req.value = (1 << N) - 1       # requests during reset must be ignored
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)


async def run(dut, model, stimulus):
    """Each item is (rst, req). Inputs change on the falling edge,
    state moves on the rising edge, outputs are checked on the falling edge."""
    for cycle, (rst, req) in enumerate(stimulus):
        check(dut, model, cycle)
        dut.rst.value = rst
        dut.req.value = req
        await RisingEdge(dut.clk)
        model.step(rst, req)
        await FallingEdge(dut.clk)
    check(dut, model, len(stimulus))


@cocotb.test()
async def reset_state(dut):
    await start(dut)
    await run(dut, ArbiterModel(), [(0, 0)])


@cocotb.test()
async def all_requesting(dut):
    """Everyone asks every cycle: grants rotate 0, 1, 2, 3, 0, ..."""
    await start(dut)
    await run(dut, ArbiterModel(), [(0, 0b1111)] * 12)


@cocotb.test()
async def each_alone(dut):
    """One requester at a time, including holding the grant several cycles."""
    await start(dut)
    stim = []
    for i in list(range(N)) + list(reversed(range(N))):
        stim += [(0, 1 << i)] * 3 + [(0, 0)]
    await run(dut, ArbiterModel(), stim)


@cocotb.test()
async def pairs_alternate(dut):
    """Two requesters alternate; priority must skip the idle ones and wrap."""
    await start(dut)
    stim = [(0, 0b1010)] * 6 + [(0, 0b1001)] * 6 + [(0, 0b0110)] * 6 + [(0, 0b1100)] * 5
    await run(dut, ArbiterModel(), stim)


@cocotb.test()
async def last_winner_checked_last(dut):
    """After 3 wins, only 3 and 0 asking: 0 must win (3 is checked last)."""
    await start(dut)
    stim = [(0, 0b1000), (0, 0b1001), (0, 0b1001), (0, 0b1000), (0, 0b1000), (0, 0b0001)]
    await run(dut, ArbiterModel(), stim)


@cocotb.test()
async def reset_mid_rotation(dut):
    await start(dut)
    stim = [(0, 0b1111)] * 3 + [(1, 0b1111)] + [(0, 0b1110)] * 4 + [(1, 0)] + [(0, 0b0101)] * 4
    await run(dut, ArbiterModel(), stim)


@cocotb.test()
async def random_traffic(dut):
    await start(dut)
    rng = random.Random(1234)
    stim = [(int(rng.random() < 0.01), rng.randrange(1 << N)) for _ in range(1500)]
    await run(dut, ArbiterModel(), stim)
