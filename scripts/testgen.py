"""Verification tasks: the model writes a cocotb test that catches the bug.

For a bug task (bugfix or multibug), the model sees spec.md, buggy.v, and a
skeleton test file, and returns a test file. The test runs twice, with a fixed
random seed and the design's default parameters:
  1. on golden.v: every test must pass   (the test checks real spec behavior)
  2. on buggy.v:  at least one must fail (the test catches the bug)
Reward 1 only if both hold. A test that always fails scores 0 on step 1.

Model-written code is untrusted. Three layers:
  - static check (check_source): only allowlisted imports; no file/OS/network
    access, no eval/exec/getattr/format, no dunder names. Refused files score 0.
  - the simulator runs with a minimal environment (no API keys) in a fresh
    temp folder holding only design.v and the test
  - run_testgen.py grades in a separate container with no network and no .env

    python3 scripts/testgen.py skeleton <task_dir>          # print the skeleton
    python3 scripts/testgen.py grade <task_dir> <test.py>   # grade a test file
"""

import ast
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from grade import interface
from sim import result, run_once

TIMEOUT = 120          # seconds per run (golden or buggy)
SEED = "12345"         # same random stimulus on golden and buggy

# Each allowed module and the names a test may use from it. Everything else is refused:
# modules expose other modules as attributes (random._os, cocotb.task.inspect, ...),
# so a module name may only appear as the base of an allowlisted attribute.
MODULE_API = {
    "cocotb": {"test", "start_soon", "start", "create_task", "parametrize", "pass_test", "skipif", "xfail",
               "top", "log"},
    "cocotb.clock": {"Clock"},
    "cocotb.triggers": {"ClockCycles", "Combine", "Edge", "Event", "FallingEdge", "First", "Lock", "NextTimeStep",
                        "NullTrigger", "ReadOnly", "ReadWrite", "RisingEdge", "SimTimeoutError", "Timer",
                        "Trigger", "ValueChange", "gather", "select", "wait", "with_timeout"},
    "cocotb.types": {"Array", "Bit", "Logic", "LogicArray", "Range"},
    "cocotb.handle": {"Deposit", "Force", "Freeze", "Release", "Immediate"},
    "cocotb.utils": {"get_sim_time", "get_sim_steps"},
    "cocotb.queue": {"Queue", "LifoQueue", "PriorityQueue", "QueueEmpty", "QueueFull"},
    "cocotb.task": {"Task"},
    "random": {"choice", "choices", "getrandbits", "randint", "random", "randrange", "sample", "seed", "shuffle",
               "uniform", "Random"},
    "math": {"ceil", "floor", "log2", "log10", "log", "sqrt", "isqrt", "gcd", "lcm", "comb", "perm", "prod",
             "factorial", "pow", "exp", "fabs", "trunc", "inf", "pi", "e", "isclose"},
    "itertools": {"accumulate", "batched", "chain", "combinations", "combinations_with_replacement", "compress",
                  "count", "cycle", "dropwhile", "filterfalse", "groupby", "islice", "pairwise", "permutations",
                  "product", "repeat", "starmap", "takewhile", "tee", "zip_longest"},
    "collections": {"ChainMap", "Counter", "OrderedDict", "defaultdict", "deque"},
    "functools": {"cache", "lru_cache", "partial", "reduce", "wraps", "cmp_to_key", "total_ordering"},
}
ALLOWED_IMPORTS = set(MODULE_API)
# Builtins that reach files, code, or attributes by string (format() does through "{0.__class__}").
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "globals", "locals", "vars", "getattr", "setattr",
                   "delattr", "hasattr", "input", "breakpoint", "help", "memoryview", "exit", "quit", "format",
                   "type", "object", "classmethod", "staticmethod", "property"}
# Attributes on any object: string formatting, and frames/code (coroutine.cr_frame.f_globals
# reaches builtins). Names starting with "_" are refused too, except dut._log.
FORBIDDEN_ATTRS = {"format", "format_map", "mro", "cr_frame", "gi_frame", "ag_frame", "cr_code", "gi_code",
                   "ag_code", "cr_await", "gi_yieldfrom", "f_globals", "f_locals", "f_builtins", "f_back",
                   "f_code", "tb_frame", "tb_next", "func_globals"}
ALLOWED_PRIVATE = {"_log"}

MAKEFILE = """SIM ?= icarus
TOPLEVEL_LANG = verilog
VERILOG_SOURCES = $(PWD)/design.v
COCOTB_TOPLEVEL = {top}
COCOTB_TEST_MODULES = test_dut
include $(shell cocotb-config --makefiles)/Makefile.sim
"""


def check_source(src):
    """Reasons the test file is refused (empty list = allowed)."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    bad, modules = [], {}                   # local name -> module path, from import statements
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in ALLOWED_IMPORTS:
                    bad.append(f"import {a.name}")
                elif a.asname:
                    modules[a.asname] = a.name
                else:
                    modules[a.name.split(".")[0]] = a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            if mod not in ALLOWED_IMPORTS:
                bad.append(f"from {mod} import ...")
                continue
            for a in node.names:
                sub = f"{mod}.{a.name}"
                if sub in ALLOWED_IMPORTS:
                    modules[a.asname or a.name] = sub
                elif a.name not in MODULE_API[mod]:
                    bad.append(f"from {mod} import {a.name}")
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES or node.id.startswith("__"):
                bad.append(f"name {node.id}")
            elif node.id in modules:
                bad += _module_use(node, modules[node.id], parents)
        elif isinstance(node, ast.Attribute):
            a = node.attr
            if a in FORBIDDEN_ATTRS or (a.startswith("_") and a not in ALLOWED_PRIVATE):
                bad.append(f"attribute .{a}")
        elif isinstance(node, ast.arg) and node.arg.startswith("__"):
            bad.append(f"argument {node.arg}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
            bad.append("string containing __")
    return sorted(set(bad))


def _module_use(name_node, path, parents):
    """A module name may only be the base of an allowlisted attribute chain
    (cocotb.triggers.RisingEdge), never a value of its own."""
    node = name_node
    while True:
        up = parents.get(node)
        if not isinstance(up, ast.Attribute) or up.value is not node:
            return [f"module {path} used as a value"]
        if f"{path}.{up.attr}" in ALLOWED_IMPORTS:
            path, node = f"{path}.{up.attr}", up
            continue
        return [] if up.attr in MODULE_API[path] else [f"{path}.{up.attr}"]


def _ports(task_dir):
    meta = json.loads((Path(task_dir) / "meta.json").read_text())
    ports, params = interface((Path(task_dir) / "golden.v").read_text())[meta["top"]]
    return meta["top"], ports, params


def skeleton(task_dir):
    """A starting test file: clock, reset, input defaults, and APB helpers when the
    design has an APB port. Built from the top-level ports, so it fits every seed."""
    top, ports, _ = _ports(task_dir)
    inputs = [p for p, (d, _) in ports.items() if d == "input"]
    clk = next((p for p in inputs if p.lower() in ("clk", "pclk", "clock")), None)
    rst = next((p for p in inputs if re.fullmatch(r"(p?resetn?|p?rst_?n?)", p.lower())), None)
    active_low = bool(rst and rst.lower().endswith("n"))
    apb = {"PSEL", "PENABLE", "PWRITE", "PADDR", "PWDATA", "PRDATA"} <= set(ports)
    lines = ["import random", "", "import cocotb", "from cocotb.clock import Clock",
             "from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge, Timer, with_timeout", ""]
    if clk:
        lines += ["CLK_NS = 10", "", "",
                  "async def start(dut):",
                  f'    """Start the clock, drive inputs to idle (0; a UART rx line to 1), {"hold reset for 2 cycles, then release it" if rst else "and wait one cycle"}."""',
                  f'    cocotb.start_soon(Clock(dut.{clk}, CLK_NS, "ns").start())']
        lines += [f"    dut.{p}.value = {1 if p.lower() == 'rx' else 0}" for p in inputs if p not in (clk, rst)]
        if rst:
            lines += [f"    dut.{rst}.value = {0 if active_low else 1}",
                      f"    await ClockCycles(dut.{clk}, 2)",
                      f"    await FallingEdge(dut.{clk})",
                      f"    dut.{rst}.value = {1 if active_low else 0}"]
        else:
            lines += [f"    await FallingEdge(dut.{clk})"]
    else:
        lines += ["", "async def settle():", '    """Combinational design: let outputs settle after changing inputs."""',
                  '    await Timer(1, "ns")']
    if apb and clk:
        lines += ["", "",
                  "async def apb_write(dut, addr, data):",
                  '    """One APB write (setup, then access); returns after the access cycle\'s rising edge."""',
                  f"    await FallingEdge(dut.{clk})",
                  "    dut.PSEL.value, dut.PENABLE.value, dut.PWRITE.value = 1, 0, 1",
                  "    dut.PADDR.value, dut.PWDATA.value = addr, data",
                  f"    await FallingEdge(dut.{clk})",
                  "    dut.PENABLE.value = 1",
                  f"    await RisingEdge(dut.{clk})",
                  f"    await FallingEdge(dut.{clk})",
                  "    dut.PSEL.value, dut.PENABLE.value, dut.PWRITE.value = 0, 0, 0",
                  "", "",
                  "async def apb_read(dut, addr):",
                  '    """One APB read; returns PRDATA sampled in the access cycle (side effects happen at its end)."""',
                  f"    await FallingEdge(dut.{clk})",
                  "    dut.PSEL.value, dut.PENABLE.value, dut.PWRITE.value = 1, 0, 0",
                  "    dut.PADDR.value = addr",
                  f"    await FallingEdge(dut.{clk})",
                  "    dut.PENABLE.value = 1",
                  "    await ReadOnly()",
                  "    value = int(dut.PRDATA.value)",
                  f"    await RisingEdge(dut.{clk})",
                  f"    await FallingEdge(dut.{clk})",
                  "    dut.PSEL.value, dut.PENABLE.value = 0, 0",
                  "    return value"]
    lines += ["", "", "@cocotb.test()", "async def smoke(dut):",
              '    """Replace or extend: check behavior from the specification."""']
    if clk:
        lines += ["    await start(dut)", f"    await ClockCycles(dut.{clk}, 5)"]
    else:
        lines += [f"    dut.{inputs[0]}.value = 0", "    await settle()"]
    return "\n".join(lines) + "\n"


def run_test(design_src, test_src, top, timeout=TIMEOUT):
    """Run a test file on one design in a fresh folder with a minimal environment."""
    work = Path(tempfile.mkdtemp(prefix="tg_"))
    try:
        (work / "Makefile").write_text(MAKEFILE.format(top=top))
        (work / "design.v").write_text(design_src)
        (work / "test_dut.py").write_text(test_src)
        base = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "VIRTUAL_ENV", "PYTHONHOME")
                if k in os.environ}
        env = {"COCOTB_RANDOM_SEED": SEED, "RANDOM_SEED": SEED, "PYTHONDONTWRITEBYTECODE": "1"}
        return run_once(work, [], env, timeout, base_env=base)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def first_failure(log):
    m = re.search(r"(AssertionError[^\n]*|\w+Error: [^\n]*)", log)
    return m.group(1)[:300] if m else ""


def grade_test(task_dir, test_src, timeout=TIMEOUT, check=True):
    """{"status": pass | fails_golden | misses_bug | rejected | error, "reason", "golden", "buggy"}"""
    task_dir = Path(task_dir)
    top, _, _ = _ports(task_dir)
    if check and (bad := check_source(test_src)):
        return {"status": "rejected", "reason": "not allowed: " + ", ".join(bad[:8])}
    g = run_test((task_dir / "golden.v").read_text(), test_src, top, timeout)
    out = {"golden": {k: g[k] for k in ("status", "passed", "failed")}}
    if g["status"] != "pass":
        why = {"compile_error": "test file did not run (import or syntax error, or no tests)",
               "timeout": f"did not finish within {timeout}s on the correct design"}.get(g["status"], "")
        detail = first_failure(g["log"])
        return {**out, "status": "fails_golden",
                "reason": f"fails on the correct design: {why or ', '.join(g['failed'][:5])}"
                          + (f"; {detail}" if detail else "")}
    b = run_test((task_dir / "buggy.v").read_text(), test_src, top, timeout)
    out["buggy"] = {k: b[k] for k in ("status", "passed", "failed")}
    if b["status"] == "fail":
        return {**out, "status": "pass", "reason": f"caught by: {', '.join(b['failed'][:5])}"}
    why = {"pass": "every test passes on the buggy design",
           "timeout": f"timed out on the buggy design (use with_timeout so a hang fails a test)",
           "compile_error": "did not run on the buggy design"}.get(b["status"], b["status"])
    return {**out, "status": "misses_bug", "reason": why}


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "skeleton":
        print(skeleton(sys.argv[2]), end="")
    elif len(sys.argv) == 4 and sys.argv[1] == "grade":
        print(json.dumps(grade_test(sys.argv[2], Path(sys.argv[3]).read_text()), indent=2))
    else:
        sys.exit(__doc__)
