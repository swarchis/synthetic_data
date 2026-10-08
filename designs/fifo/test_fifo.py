import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles

DEPTH = 4
WIDTH = 8


class FifoModel:
    """Python reference model: what the FIFO should do, per spec.md."""

    def __init__(self):
        self.q = deque()

    def reset(self):
        self.q.clear()

    def step(self, push, din, pop):
        # Decide using flags from before the edge (spec rule 4).
        full = len(self.q) == DEPTH
        empty = len(self.q) == 0
        if pop and not empty:
            self.q.popleft()
        if push and not full:
            self.q.append(din)


def read(sig, name, cycle):
    """Read a signal as an int, failing cleanly if it holds X or Z (unknown) bits."""
    v = sig.value
    assert v.is_resolvable, f"cycle {cycle}: {name} is unknown ({v})"
    return int(v)


def check(dut, model, cycle):
    n = len(model.q)
    count = read(dut.count, "count", cycle)
    empty = read(dut.empty, "empty", cycle)
    full = read(dut.full, "full", cycle)
    assert count == n, f"cycle {cycle}: count {count}, expected {n}"
    assert empty == (n == 0), f"cycle {cycle}: empty {empty} with {n} words"
    assert full == (n == DEPTH), f"cycle {cycle}: full {full} with {n} words"
    if n:
        dout = read(dut.dout, "dout", cycle)
        assert dout == model.q[0], f"cycle {cycle}: dout {dout}, expected {model.q[0]}"


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    dut.rst.value = 1
    dut.push.value = 0
    dut.pop.value = 0
    dut.din.value = 0
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)
    dut.rst.value = 0


async def run(dut, model, stimulus):
    """Each item is (push, din, pop). Inputs change on the falling edge,
    outputs are checked on the falling edge, state moves on the rising edge."""
    for cycle, (push, din, pop) in enumerate(stimulus):
        check(dut, model, cycle)
        dut.push.value = push
        dut.din.value = din
        dut.pop.value = pop
        await RisingEdge(dut.clk)
        model.step(push, din, pop)
        await FallingEdge(dut.clk)
    check(dut, model, len(stimulus))


@cocotb.test()
async def reset_state(dut):
    await start(dut)
    check(dut, FifoModel(), 0)


@cocotb.test()
async def fill_and_drain(dut):
    await start(dut)
    model = FifoModel()
    fill = [(1, 0x10 + i, 0) for i in range(DEPTH + 2)]   # last 2 pushes hit a full queue
    drain = [(0, 0, 1) for _ in range(DEPTH + 2)]          # last 2 pops hit an empty queue
    await run(dut, model, fill + drain)


@cocotb.test()
async def push_pop_same_cycle(dut):
    await start(dut)
    model = FifoModel()
    stim = [(1, 0xA0, 1)]                                   # empty: only push happens
    stim += [(1, 0xB0 + i, 0) for i in range(DEPTH - 1)]    # fill to full
    stim += [(1, 0xEE, 1)]                                  # full: only pop happens
    stim += [(1, 0xC0 + i, 1) for i in range(6)]            # partly full: both happen
    await run(dut, model, stim)


@cocotb.test()
async def random_traffic(dut):
    await start(dut)
    model = FifoModel()
    rng = random.Random(1234)
    stim = []
    # Phases bias toward filling, draining, then balanced, so full and empty both get hit often.
    for p_push, p_pop in [(0.9, 0.2), (0.2, 0.9), (0.5, 0.5), (0.8, 0.8)] * 5:
        for _ in range(50):
            stim.append((int(rng.random() < p_push), rng.randrange(1 << WIDTH), int(rng.random() < p_pop)))
    await run(dut, model, stim)


@cocotb.test()
async def reset_mid_traffic(dut):
    await start(dut)
    model = FifoModel()
    await run(dut, model, [(1, 0x55 + i, 0) for i in range(3)])
    dut.rst.value = 1
    dut.push.value = 1
    dut.pop.value = 0
    await RisingEdge(dut.clk)
    model.reset()
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    await run(dut, model, [(1, 0x77, 0), (0, 0, 1)])
