import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

GREEN_TIME, MIN_GREEN, YELLOW_TIME, RED_TIME = 8, 3, 3, 6
GREEN, YELLOW, RED = 0, 1, 2


class LightModel:
    """Python reference model: what the controller should do, per spec.md."""

    def __init__(self):
        self.state, self.timer, self.pending = GREEN, 0, 0

    def step(self, rst, ped):
        if rst:
            self.state, self.timer, self.pending = GREEN, 0, 0
            return
        pending = self.pending
        if self.state == RED:
            self.pending = 0
        elif ped:
            self.pending = 1
        if self.state == GREEN:
            done = self.timer == GREEN_TIME - 1 or (pending and self.timer >= MIN_GREEN - 1)
            nxt = YELLOW
        elif self.state == YELLOW:
            done, nxt = self.timer == YELLOW_TIME - 1, RED
        else:
            done, nxt = self.timer == RED_TIME - 1, GREEN
        if done:
            self.state, self.timer = nxt, 0
        else:
            self.timer += 1


def read(sig, name, cycle):
    """Read a signal as an int, failing cleanly if it holds X or Z (unknown) bits."""
    v = sig.value
    assert v.is_resolvable, f"cycle {cycle}: {name} is unknown ({v})"
    return int(v)


def check(dut, model, cycle):
    s = read(dut.state, "state", cycle)
    assert s == model.state, f"cycle {cycle}: state {s}, expected {model.state}"
    for name, want in (("green", s == GREEN), ("yellow", s == YELLOW), ("red", s == RED), ("walk", s == RED)):
        got = read(getattr(dut, name), name, cycle)
        assert got == want, f"cycle {cycle}: {name} {got} in state {s}"


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    dut.rst.value = 1
    dut.ped.value = 1                  # button held during reset: must not stay pending
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)


async def run(dut, model, stimulus):
    """Each item is (rst, ped). Inputs change on the falling edge,
    state moves on the rising edge, outputs are checked on the falling edge."""
    for cycle, (rst, ped) in enumerate(stimulus):
        check(dut, model, cycle)
        dut.rst.value = rst
        dut.ped.value = ped
        await RisingEdge(dut.clk)
        model.step(rst, ped)
        await FallingEdge(dut.clk)
    check(dut, model, len(stimulus))


FULL = GREEN_TIME + YELLOW_TIME + RED_TIME


@cocotb.test()
async def reset_state(dut):
    await start(dut)
    await run(dut, LightModel(), [(0, 0)])


@cocotb.test()
async def normal_cycles(dut):
    """No pedestrians: exact phase lengths for three full cycles."""
    await start(dut)
    await run(dut, LightModel(), [(0, 0)] * (3 * FULL + 2))


@cocotb.test()
async def ped_early_in_green(dut):
    """Press at cycle 1 of green: green must still last MIN_GREEN cycles."""
    await start(dut)
    await run(dut, LightModel(), [(0, 1)] + [(0, 0)] * (FULL + 4))


@cocotb.test()
async def ped_late_in_green(dut):
    """Press after MIN_GREEN: green ends one cycle after the request registers."""
    await start(dut)
    await run(dut, LightModel(), [(0, 0)] * 5 + [(0, 1)] + [(0, 0)] * (FULL + 3))


@cocotb.test()
async def ped_in_yellow_and_red(dut):
    """Press during yellow (pending, then cleared by red) and during red (ignored)."""
    await start(dut)
    stim = [(0, 0)] * (GREEN_TIME + 1) + [(0, 1)] + [(0, 0)] * (YELLOW_TIME + 1)
    stim += [(0, 1)] * 3 + [(0, 0)] * (FULL + 3)
    await run(dut, LightModel(), stim)


@cocotb.test()
async def ped_held(dut):
    """Button held down the whole time."""
    await start(dut)
    await run(dut, LightModel(), [(0, 1)] * (3 * FULL))


@cocotb.test()
async def reset_mid_phase(dut):
    await start(dut)
    stim = [(0, 0)] * (GREEN_TIME + 2) + [(1, 1)] + [(0, 0)] * 4 + [(0, 1)] + [(0, 0)] * (FULL + 2)
    await run(dut, LightModel(), stim)


@cocotb.test()
async def random_traffic(dut):
    await start(dut)
    rng = random.Random(1234)
    stim = [(int(rng.random() < 0.01), int(rng.random() < 0.12)) for _ in range(1500)]
    await run(dut, LightModel(), stim)
