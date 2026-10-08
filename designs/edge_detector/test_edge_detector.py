import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge


class EdgeModel:
    """Python reference model: what the detector should do, per spec.md."""

    def __init__(self):
        self.prev = 0
        self.rise = 0
        self.fall = 0

    def step(self, rst, sig):
        if rst:
            self.prev, self.rise, self.fall = 0, 0, 0
        else:
            self.rise = int(sig and not self.prev)
            self.fall = int(not sig and self.prev)
            self.prev = sig


def read(sig, name, cycle):
    """Read a signal as an int, failing cleanly if it holds X or Z (unknown) bits."""
    v = sig.value
    assert v.is_resolvable, f"cycle {cycle}: {name} is unknown ({v})"
    return int(v)


def check(dut, model, cycle):
    rise = read(dut.rise, "rise", cycle)
    fall = read(dut.fall, "fall", cycle)
    any_edge = read(dut.any_edge, "any_edge", cycle)
    assert rise == model.rise, f"cycle {cycle}: rise {rise}, expected {model.rise}"
    assert fall == model.fall, f"cycle {cycle}: fall {fall}, expected {model.fall}"
    assert any_edge == (model.rise | model.fall), f"cycle {cycle}: any_edge {any_edge}, rise {rise} fall {fall}"


async def start(dut, sig=0):
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    dut.rst.value = 1
    dut.sig.value = sig
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)


async def run(dut, model, stimulus):
    """Each item is (rst, sig). Inputs change on the falling edge,
    state moves on the rising edge, outputs are checked on the falling edge."""
    for cycle, (rst, sig) in enumerate(stimulus):
        check(dut, model, cycle)
        dut.rst.value = rst
        dut.sig.value = sig
        await RisingEdge(dut.clk)
        model.step(rst, sig)
        await FallingEdge(dut.clk)
    check(dut, model, len(stimulus))


@cocotb.test()
async def reset_state(dut):
    await start(dut, sig=1)          # sig high during reset: still no pulses
    check(dut, EdgeModel(), 0)


@cocotb.test()
async def pattern(dut):
    await start(dut)
    sigs = [0, 1, 1, 1, 0, 0, 1, 0, 1, 0, 0, 1, 1, 0]
    await run(dut, EdgeModel(), [(0, s) for s in sigs])


@cocotb.test()
async def high_right_after_reset(dut):
    """Previous sample resets to 0, so sig=1 at the first edge is a rise."""
    await start(dut, sig=1)
    await run(dut, EdgeModel(), [(0, 1), (0, 1), (0, 0), (1, 0), (0, 1)])


@cocotb.test()
async def reset_mid_pulse(dut):
    await start(dut)
    model = EdgeModel()
    await run(dut, model, [(0, 0), (0, 1), (1, 1), (0, 1), (0, 0), (1, 0), (0, 0), (0, 1)])


@cocotb.test()
async def random_traffic(dut):
    await start(dut)
    rng = random.Random(1234)
    stim = []
    for _ in range(800):
        stim.append((int(rng.random() < 0.03), int(rng.random() < 0.5)))
    await run(dut, EdgeModel(), stim)
