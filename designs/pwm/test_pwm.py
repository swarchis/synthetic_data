import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

WIDTH = 8
MASK = (1 << WIDTH) - 1


class PwmModel:
    """Python reference model: what the generator should do, per spec.md."""

    def __init__(self):
        self.count = 0

    def last(self, period):
        return period == 0 or self.count >= period - 1

    def outputs(self, en, period, duty):
        return int(bool(en) and self.count < duty), int(bool(en) and self.last(period))

    def step(self, rst, en, period, duty):
        if rst or not en or self.last(period):
            self.count = 0
        else:
            self.count += 1


def read(sig, name, cycle):
    """Read a signal as an int, failing cleanly if it holds X or Z (unknown) bits."""
    v = sig.value
    assert v.is_resolvable, f"cycle {cycle}: {name} is unknown ({v})"
    return int(v)


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    dut.rst.value = 1
    dut.en.value = 1
    dut.period.value = 5
    dut.duty.value = 2
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)


async def run(dut, model, stimulus):
    """Each item is (rst, en, period, duty). Inputs change on the falling edge,
    the counter moves on the rising edge, outputs are checked in between."""
    for cycle, (rst, en, period, duty) in enumerate(stimulus):
        cnt = read(dut.cnt, "cnt", cycle)
        assert cnt == model.count, f"cycle {cycle}: cnt {cnt}, expected {model.count}"
        dut.rst.value = rst
        dut.en.value = en
        dut.period.value = period
        dut.duty.value = duty
        await ReadOnly()
        want_out, want_wrap = model.outputs(en, period, duty)
        ctx = f"cycle {cycle} (cnt={cnt} en={en} period={period} duty={duty})"
        out = read(dut.out, "out", ctx)
        wrap = read(dut.wrap, "wrap", ctx)
        assert out == want_out, f"{ctx}: out {out}, expected {want_out}"
        assert wrap == want_wrap, f"{ctx}: wrap {wrap}, expected {want_wrap}"
        await RisingEdge(dut.clk)
        model.step(rst, en, period, duty)
        await FallingEdge(dut.clk)


def steady(period, duty, cycles, en=1):
    return [(0, en, period, duty)] * cycles


@cocotb.test()
async def reset_state(dut):
    await start(dut)
    await run(dut, PwmModel(), [(1, 1, 5, 2), (0, 1, 5, 2)])


@cocotb.test()
async def basic_periods(dut):
    await start(dut)
    model = PwmModel()
    await run(dut, model, steady(5, 2, 17) + steady(8, 7, 20) + steady(3, 1, 10))


@cocotb.test()
async def duty_extremes(dut):
    """duty 0 (always low), duty == period and duty > period (always high)."""
    await start(dut)
    model = PwmModel()
    await run(dut, model, steady(6, 0, 13) + steady(6, 6, 13) + steady(6, 200, 13) + steady(MASK, MASK, 10))


@cocotb.test()
async def tiny_periods(dut):
    """period 0 and 1 hold the counter at 0."""
    await start(dut)
    model = PwmModel()
    await run(dut, model, steady(1, 1, 6) + steady(0, 1, 6) + steady(2, 1, 8) + steady(1, 0, 4))


@cocotb.test()
async def period_shrinks_mid_count(dut):
    """Lower period below the current count: the counter must still return to 0."""
    await start(dut)
    model = PwmModel()
    await run(dut, model, steady(20, 10, 12) + steady(4, 2, 12) + steady(200, 100, 5) + steady(3, 2, 6))


@cocotb.test()
async def enable_toggle(dut):
    await start(dut)
    model = PwmModel()
    await run(dut, model, steady(6, 3, 9) + steady(6, 3, 3, en=0) + steady(6, 3, 9) + [(0, 0, 6, 3), (0, 1, 6, 3)])


@cocotb.test()
async def long_period(dut):
    """Full-width period: the counter must reach 254 and wrap."""
    await start(dut)
    model = PwmModel()
    await run(dut, model, steady(MASK, 128, 2 * MASK + 5))


@cocotb.test()
async def random_traffic(dut):
    await start(dut)
    model = PwmModel()
    rng = random.Random(1234)
    stim, period, duty = [], 7, 3
    for _ in range(1500):
        if rng.random() < 0.03:
            period = rng.choice([0, 1, 2, 3, rng.randrange(MASK + 1)])
        if rng.random() < 0.05:
            duty = rng.choice([0, period, period + 1 if period < MASK else period, rng.randrange(MASK + 1)])
        stim.append((int(rng.random() < 0.01), int(rng.random() > 0.03), period, duty))
    await run(dut, model, stim)
