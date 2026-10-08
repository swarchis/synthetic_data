import random

import cocotb
from cocotb.triggers import Timer

WIDTH = 8
MASK = (1 << WIDTH) - 1
TOP = WIDTH - 1


def model(a, b, op):
    """Python reference model: (y, carry) per spec.md."""
    if op == 0:
        s = a + b
        return s & MASK, s >> WIDTH
    if op == 1:
        return (a - b) & MASK, int(a < b)
    if op == 2:
        return a & b, 0
    if op == 3:
        return a | b, 0
    if op == 4:
        return a ^ b, 0
    if op == 5:
        return (a << 1) & MASK, (a >> TOP) & 1
    if op == 6:
        return a >> 1, a & 1
    return int(a < b), 0


def read(sig, name, ctx):
    """Read a signal as an int, failing cleanly if it holds X or Z (unknown) bits."""
    v = sig.value
    assert v.is_resolvable, f"{ctx}: {name} is unknown ({v})"
    return int(v)


async def check(dut, a, b, op):
    dut.a.value = a
    dut.b.value = b
    dut.op.value = op
    await Timer(1, "ns")
    ctx = f"op={op} a={a} b={b}"
    y, carry = model(a, b, op)
    got_y = read(dut.y, "y", ctx)
    got_c = read(dut.carry, "carry", ctx)
    got_z = read(dut.zero, "zero", ctx)
    assert got_y == y, f"{ctx}: y {got_y}, expected {y}"
    assert got_c == carry, f"{ctx}: carry {got_c}, expected {carry}"
    assert got_z == (y == 0), f"{ctx}: zero {got_z} with y {y}"


EDGES = [0, 1, 2, 0x7F, 0x80, 0xFE, MASK]


@cocotb.test()
async def edge_cases(dut):
    """Every op on boundary values: 0, max, top bit set, equal operands."""
    for op in range(8):
        for a in EDGES:
            for b in EDGES:
                await check(dut, a, b, op)


@cocotb.test()
async def random_values(dut):
    rng = random.Random(1234)
    for _ in range(3000):
        await check(dut, rng.randrange(MASK + 1), rng.randrange(MASK + 1), rng.randrange(8))


@cocotb.test()
async def op_changes_alone(dut):
    """Same operands, only op changes: catches outputs that don't react to op."""
    for a, b in [(0x5A, 0x3C), (0xFF, 0x01), (0x10, 0x10), (0x00, 0x80)]:
        for op in list(range(8)) + list(range(7, -1, -1)):
            await check(dut, a, b, op)
