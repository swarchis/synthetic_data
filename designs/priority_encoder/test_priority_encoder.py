import cocotb
from cocotb.triggers import Timer

N = 8


def model(req):
    """Python reference model: (idx, valid, onehot) per spec.md."""
    if req == 0:
        return 0, 0, 0
    idx = req.bit_length() - 1
    return idx, 1, 1 << idx


def read(sig, name, ctx):
    """Read a signal as an int, failing cleanly if it holds X or Z (unknown) bits."""
    v = sig.value
    assert v.is_resolvable, f"{ctx}: {name} is unknown ({v})"
    return int(v)


async def check(dut, req):
    dut.req.value = req
    await Timer(1, "ns")
    ctx = f"req={req:0{N}b}"
    idx, valid, onehot = model(req)
    got = (read(dut.idx, "idx", ctx), read(dut.valid, "valid", ctx), read(dut.onehot, "onehot", ctx))
    assert got[1] == valid, f"{ctx}: valid {got[1]}, expected {valid}"
    assert got[0] == idx, f"{ctx}: idx {got[0]}, expected {idx}"
    assert got[2] == onehot, f"{ctx}: onehot {got[2]:0{N}b}, expected {onehot:0{N}b}"


@cocotb.test()
async def every_input(dut):
    """All 2^N request patterns, in order."""
    for req in range(1 << N):
        await check(dut, req)


@cocotb.test()
async def every_input_reversed(dut):
    """Same patterns, descending: catches logic that depends on the previous input."""
    for req in reversed(range(1 << N)):
        await check(dut, req)


@cocotb.test()
async def single_requests(dut):
    """Each line alone, with no request in between."""
    for i in range(N):
        await check(dut, 1 << i)
        await check(dut, 0)
