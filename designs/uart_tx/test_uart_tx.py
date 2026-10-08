import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

C = 4                       # CLKS_PER_BIT
IDLE, START, DATA, STOP = range(4)


class UartModel:
    """Python reference model: what the transmitter should do, per spec.md."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.state, self.cnt, self.idx, self.sh, self.tx, self.done = IDLE, 0, 0, 0, 1, 0

    def step(self, rst, start, data):
        if rst:
            self.reset()
            return
        self.done = 0
        if self.state == IDLE:
            self.tx = 1
            if start:
                self.sh, self.state, self.tx, self.cnt = data, START, 0, 0
        elif self.cnt != C - 1:
            self.cnt += 1
        elif self.state == START:
            self.cnt, self.idx, self.state, self.tx = 0, 0, DATA, self.sh & 1
        elif self.state == DATA:
            self.cnt = 0
            if self.idx == 7:
                self.state, self.tx = STOP, 1
            else:
                self.idx += 1
                self.tx = self.sh >> self.idx & 1
        else:
            self.cnt, self.state, self.done = 0, IDLE, 1

    @property
    def busy(self):
        return int(self.state != IDLE)


def read(sig, name, cycle):
    """Read a signal as an int, failing cleanly if it holds X or Z (unknown) bits."""
    v = sig.value
    assert v.is_resolvable, f"cycle {cycle}: {name} is unknown ({v})"
    return int(v)


def check(dut, model, cycle):
    for name, want in (("tx", model.tx), ("busy", model.busy), ("done", model.done)):
        got = read(getattr(dut, name), name, cycle)
        assert got == want, f"cycle {cycle}: {name} {got}, expected {want}"


async def start_clock(dut):
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    dut.rst.value = 1
    dut.start.value = 1                # start during reset must be ignored
    dut.data.value = 0xFF
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)


async def run(dut, model, stimulus, decoded=None):
    """Each item is (rst, start, data). Inputs change on the falling edge,
    state moves on the rising edge, outputs are checked on the falling edge.
    If `decoded` is a list, also decode the tx line like a real receiver would."""
    rx = {"state": "idle", "n": 0, "bits": []}
    for cycle, (rst, start, data) in enumerate(stimulus):
        check(dut, model, cycle)
        if decoded is not None and not rst:
            receive(rx, read(dut.tx, "tx", cycle), decoded)
        dut.rst.value = rst
        dut.start.value = start
        dut.data.value = data
        await RisingEdge(dut.clk)
        model.step(rst, start, data)
        await FallingEdge(dut.clk)
    check(dut, model, len(stimulus))


def receive(rx, bit, decoded):
    """Minimal UART receiver: sample each bit in the middle of its C-cycle window."""
    if rx["state"] == "idle":
        if bit == 0:
            rx.update(state="frame", n=1, bits=[])
        return
    rx["n"] += 1                           # n-th cycle of the frame, start bit is cycles 1..C
    if rx["n"] > C and (rx["n"] - 1) % C == C // 2:   # middle of each data bit, then the stop bit
        rx["bits"].append(bit)
        if len(rx["bits"]) == 9:
            *data_bits, stop = rx["bits"]
            assert stop == 1, f"stop bit was 0 while receiving {data_bits}"
            decoded.append(sum(b << i for i, b in enumerate(data_bits)))
            rx["state"] = "idle"


def send(byte, gap=0, hold_start=1, scramble=None):
    """Stimulus to send one byte: pulse start, then wait out the frame (+ gap)."""
    frame = 10 * C
    first = [(0, 1, byte)] * hold_start
    rest = [(0, 0, scramble(i) if scramble else byte) for i in range(frame + 1 - hold_start + gap)]
    return first + rest


@cocotb.test()
async def reset_state(dut):
    await start_clock(dut)
    await run(dut, UartModel(), [(0, 0, 0)] * 3)


@cocotb.test()
async def one_byte_decodes(dut):
    """Send bytes through a receiver: LSB first, framing, stop bit."""
    await start_clock(dut)
    got = []
    stim = []
    for b in (0x55, 0xA3, 0x00, 0xFF, 0x81):
        stim += send(b, gap=3)
    await run(dut, UartModel(), stim, decoded=got)
    assert got == [0x55, 0xA3, 0x00, 0xFF, 0x81], f"received {[hex(x) for x in got]}"


@cocotb.test()
async def data_changes_while_busy(dut):
    """The byte is captured at start; later changes to data must not leak out."""
    await start_clock(dut)
    got = []
    rng = random.Random(7)
    stim = send(0x3C, gap=2, scramble=lambda i: rng.randrange(256))
    await run(dut, UartModel(), stim, decoded=got)
    assert got == [0x3C], f"received {[hex(x) for x in got]}"


@cocotb.test()
async def start_ignored_while_busy(dut):
    """Holding start the whole time: back-to-back bytes, but no restart mid-frame."""
    await start_clock(dut)
    got = []
    stim = [(0, 1, 0x96)] * (10 * C * 3 + 6)
    await run(dut, UartModel(), stim, decoded=got)
    assert got == [0x96, 0x96, 0x96], f"received {[hex(x) for x in got]}"


@cocotb.test()
async def back_to_back(dut):
    """start right on the done cycle begins the next frame immediately."""
    await start_clock(dut)
    model = UartModel()
    stim = send(0x12, hold_start=1)
    stim += [(0, 1, 0x34)] + [(0, 0, 0x34)] * (10 * C + 3)
    await run(dut, model, stim)


@cocotb.test()
async def reset_mid_frame(dut):
    await start_clock(dut)
    stim = send(0xC5)[:17] + [(1, 0, 0)] + [(0, 0, 0)] * 3 + send(0x5A, gap=2)
    await run(dut, UartModel(), stim)


@cocotb.test()
async def random_traffic(dut):
    await start_clock(dut)
    rng = random.Random(1234)
    stim = [(int(rng.random() < 0.004), int(rng.random() < 0.1), rng.randrange(256)) for _ in range(2500)]
    await run(dut, UartModel(), stim)
