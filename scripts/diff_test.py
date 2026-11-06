"""Generic differential test: the candidate (u_dut) and the golden design (u_ref)
get identical inputs every cycle, and every output must match.

Settings come from diff_run.json in the working directory (written by diffcheck.py
from a seed's diff.json). The golden design defines expected behavior, with two
exceptions: outputs can be compared only when a condition holds ("compare"; the condition
sees this cycle's inputs and golden outputs by name, and past(n, "input") for inputs n cycles ago;
a condition can also be {"when": ..., "mask": ...} to compare only some bits), and a
golden output that is X/Z (undefined) is not checked that cycle.
"""

import json
import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge, Timer

from drivers import AhbLiteDriver, ApbDriver, InputGen, WishboneDriver, make_rng

CFG = json.loads(Path("diff_run.json").read_text())
PARAMS = json.loads(os.environ.get("RTL_PARAMS", "{}"))
INPUTS, OUTPUTS = CFG["inputs"], CFG["outputs"]
CLOCK, RESET = CFG.get("clock"), CFG.get("reset")
CYCLES = CFG.get("cycles", 2000)


class Mismatch(AssertionError):
    pass


def value_of(sig):
    v = sig.value
    return int(v) if v.is_resolvable else None


_CODE = {}


def _code(expr):
    """Compile each compare expression once (they run on every output, every cycle)."""
    if expr not in _CODE:
        _CODE[expr] = compile(expr, "<compare>", "eval")
    return _CODE[expr]


def compare_now(dut, cycle, env, run, history=()):
    for o in OUTPUTS:
        cond = CFG.get("compare", {}).get(o, "always")
        past = lambda n, name: history[-n].get(name) if 0 < n <= len(history) else None   # inputs n cycles ago
        scope = {"past": past, **env}
        mask = None
        if isinstance(cond, dict):          # {"when": condition, "mask": expression giving the bits to compare}
            mask, cond = cond.get("mask"), cond.get("when", "always")
        if cond == "never" or (cond != "always" and not eval(_code(cond), scope)):
            continue
        ref = value_of(getattr(dut, f"ref_{o}"))
        if ref is None:          # golden output undefined here: don't care
            continue
        got = value_of(getattr(dut, f"dut_{o}"))
        if mask is not None and got is not None:        # evaluated only once the golden output is defined
            m = eval(_code(mask), scope)
            got, ref = got & m, ref & m
        if got != ref:
            shown = {k: v for k, v in env.items() if k in INPUTS}
            raise Mismatch(f"run {run} cycle {cycle}: output {o} = "
                           f"{'X' if got is None else hex(got)}, expected {hex(ref)}; inputs {shown}")


async def run_one(dut, run):
    salt = f"-{CFG['salt']}" if CFG.get("salt") else ""      # visible runs: different stimulus
    rng = make_rng(f"{CFG['top']}-{json.dumps(PARAMS, sort_keys=True)}-{run}{salt}")
    rules = CFG.get("inputs_rules", {})
    special = {RESET["name"]} if RESET else set()
    if CLOCK:
        special.add(CLOCK["name"])
    apb = (AhbLiteDriver(CFG["ahb"], rng) if CFG.get("ahb") else ApbDriver(CFG["apb"], rng) if CFG.get("apb")          # the bus driver, if the design has a bus
           else WishboneDriver(CFG["wishbone"], rng) if CFG.get("wishbone") else None)
    bus = apb.signals if apb else set()
    gens = {n: InputGen(rules.get(n, CFG.get("default_input", {"kind": "random"})), len(getattr(dut, n)), rng)
            for n in INPUTS if n not in special | bus}
    phases = CFG.get("phases") or [{"cycles": CYCLES, "inputs": {}}]

    if CLOCK:
        cocotb.start_soon(Clock(getattr(dut, CLOCK["name"]), CLOCK.get("period_ns", 10), "ns").start())
    rst = getattr(dut, RESET["name"]) if RESET else None
    active = RESET.get("active", 1) if RESET else 1
    reset_left = RESET.get("cycles", 2) if RESET else 0
    for n in INPUTS:
        if n not in special:
            getattr(dut, n).value = 0
    if CLOCK:
        await FallingEdge(getattr(dut, CLOCK["name"]))

    started = False          # compare only after the first reset has taken effect
    for cycle in range(CYCLES):
        # Which phase are we in? Phases repeat.
        t, ph = cycle % sum(p["cycles"] for p in phases), None
        for p in phases:
            if t < p["cycles"]:
                ph = p
                break
            t -= p["cycles"]
        env = {}
        if cycle == 0:
            history = []
        if rst is not None:
            if reset_left == 0 and started and rng.random() < RESET.get("random_rate", 0.0):
                reset_left = rng.randrange(1, 3)
            in_reset = reset_left > 0
            rst.value = active if in_reset else 1 - active
            env[RESET["name"]] = active if in_reset else 1 - active
            if in_reset:
                reset_left -= 1
                if apb:
                    apb.reset()
        else:
            in_reset = False
        for n, g in gens.items():
            v = g.update(ph["inputs"].get(n))
            getattr(dut, n).value = v
            env[n] = v
        if apb:
            fb = {o: value_of(getattr(dut, f"ref_{o}")) for o in OUTPUTS} if getattr(apb, "wants_feedback", False) else None
            for n, v in (apb.step(fb) if fb is not None else apb.step()).items():
                getattr(dut, n).value = v
                env[n] = v
        if CLOCK:
            await ReadOnly()
        else:
            await Timer(1, "ns")
        for o in OUTPUTS:
            env[o] = value_of(getattr(dut, f"ref_{o}"))
        if started or rst is None:
            compare_now(dut, cycle, env, run, history)
        history.append({k: v for k, v in env.items() if k in INPUTS})
        del history[:-16]
        if CLOCK:
            await RisingEdge(getattr(dut, CLOCK["name"]))
            if in_reset:
                started = True
            await FallingEdge(getattr(dut, CLOCK["name"]))


def _make(run):
    async def test(dut):
        await run_one(dut, run)
    test.__name__ = test.__qualname__ = f"diff_run_{run}"
    return cocotb.test()(test)


for _k in range(CFG.get("runs", 3)):
    globals()[f"diff_run_{_k}"] = _make(_k)
