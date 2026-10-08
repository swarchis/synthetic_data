import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ReadOnly, ClockCycles

@cocotb.test()
async def counts_up(dut):
    cocotb.start_soon(Clock(dut.clk, 10, "ns").start())
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)
    await ReadOnly()
    assert int(dut.count.value) == 0, f"in reset: {int(dut.count.value)}"
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    await ReadOnly()
    prev = int(dut.count.value)
    for _ in range(20):
        await RisingEdge(dut.clk)
        await ReadOnly()
        cur = int(dut.count.value)
        assert cur == (prev + 1) % 16, f"{prev} -> {cur}"
        prev = cur
