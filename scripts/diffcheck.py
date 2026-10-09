"""Differential checker: does a candidate design behave like the golden one?

    python3 scripts/diffcheck.py <candidate.v> <golden.v> <seed_or_test_dir>

<seed_or_test_dir> holds diff.json (and optionally configs.json for parameter
sweeps). The golden design's modules are renamed (<name>__ref) so both versions
live in one simulation; a generated top module (diff_top) feeds them identical
inputs, and scripts/diff_test.py compares every output every cycle.

No hand-written reference model is needed: the golden design is the reference.

diff.json fields:
  top             top module name
  clock           {"name": "clk", "period_ns": 10}, or omit for combinational designs
  reset           {"name": "rst", "active": 1, "cycles": 2, "random_rate": 0.005}
  rules           {"<input>": rule} (see drivers.py); unlisted inputs use default_input
  default_input   rule for unlisted inputs (default: random)
  apb             optional legal APB driver: {"addresses": [...], ...}
  phases          optional [{"cycles": N, "inputs": {"<input>": overrides}}], repeated
  compare         {"<output>": "always" | "never" | python expression over signal values}
  cycles, runs    stimulus length per run, number of runs (different random seeds)
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from grade import interface
from inject import tokenize
from sim import result, run_once

HERE = Path(__file__).parent

MAKEFILE = """SIM ?= icarus
TOPLEVEL_LANG = verilog
VERILOG_SOURCES = $(PWD)/design.v $(PWD)/ref.v $(PWD)/diff_top.v
COCOTB_TOPLEVEL = diff_top
COCOTB_TEST_MODULES = diff_test
include $(shell cocotb-config --makefiles)/Makefile.sim
"""


def rename_modules(src, suffix="__ref"):
    """Rename every module (definitions and instantiations) so the golden copy can
    sit next to the candidate in one simulation."""
    names = set(interface(src))
    out, last = [], 0
    for t in tokenize(src):
        if t.kind == "ident" and t.text in names:
            out.append(src[last:t.start] + t.text + suffix)
            last = t.end
    return "".join(out) + src[last:]


def wrapper(golden_src, top):
    """diff_top: same ports as `top`, inputs shared, outputs doubled as dut_* / ref_*."""
    ports, params = interface(golden_src)[top]
    plist = ", ".join(f"parameter {k} = {v}" for k, v in params.items())
    decl, conn_dut, conn_ref = [], [], []
    for name, (direction, width) in ports.items():
        if direction == "input":
            decl.append(f"  input wire {width} {name}")
            conn_dut.append(f".{name}({name})")
            conn_ref.append(f".{name}({name})")
        elif direction == "output":
            decl += [f"  output wire {width} dut_{name}", f"  output wire {width} ref_{name}"]
            conn_dut.append(f".{name}(dut_{name})")
            conn_ref.append(f".{name}(ref_{name})")
        else:
            raise ValueError(f"inout port {name} is not supported")
    pov = f" #({', '.join(f'.{k}({k})' for k in params)})" if params else ""
    # Seeds may leave `default_nettype none active; the wrapper declares every net anyway.
    head = "`default_nettype wire\n" + (f"module diff_top #({plist}) (\n" if params else "module diff_top (\n")
    return (head + ",\n".join(decl) + "\n);\n"
            f"  {top}{pov} u_dut ({', '.join(conn_dut)});\n"
            f"  {top}__ref{pov} u_ref ({', '.join(conn_ref)});\n"
            "endmodule\n")


def diff_check(candidate_src, golden_src, cfg_dir, timeout=120):
    """Returns {"status", "passed", "failed", "log"} like sim.run_tests."""
    cfg_dir = Path(cfg_dir)
    cfg = json.loads((cfg_dir / "diff.json").read_text())
    top = cfg["top"]
    ports, _ = interface(golden_src)[top]
    cfg["inputs"] = [n for n, (d, _) in ports.items() if d == "input"]
    cfg["outputs"] = [n for n, (d, _) in ports.items() if d == "output"]
    cfg["inputs_rules"] = cfg.get("rules", {})
    sweep = cfg_dir / "configs.json"
    configs = json.loads(sweep.read_text())["configs"] if sweep.exists() else [{"name": "default", "params": {}}]

    work = Path(tempfile.mkdtemp(prefix="diff_"))
    try:
        (work / "design.v").write_text(candidate_src)
        (work / "ref.v").write_text(rename_modules(golden_src))
        (work / "diff_top.v").write_text(wrapper(golden_src, top))
        (work / "diff_run.json").write_text(json.dumps(cfg))
        (work / "Makefile").write_text(MAKEFILE)
        for f in ("diff_test.py", "drivers.py"):
            shutil.copy(HERE / f, work)
        passed, logs = [], []
        for c in configs:
            args = " ".join(f"-Pdiff_top.{k}={v}" for k, v in c["params"].items())
            env = {"RTL_PARAMS": json.dumps(c["params"]), "COMPILE_ARGS": args}
            r = run_once(work, [f"SIM_BUILD=sim_build_{c['name']}"], env, timeout,
                         sources=("design.v", "ref.v", "diff_top.v"))
            logs.append(f"===== diff config {c['name']} {c['params']}: {r['status']}\n{r['log']}")
            if r["status"] != "pass":
                return result(r["status"], passed + [f"{c['name']}:{t}" for t in r["passed"]],
                              [f"{c['name']}:{t}" for t in r["failed"]], "\n".join(logs))
            passed += [f"{c['name']}:{t}" for t in r["passed"]]
        return result("pass", passed, [], "\n".join(logs))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def first_mismatch(log):
    """The first 'run R cycle C: output O = X, expected Y; inputs {...}' message."""
    import re
    m = re.search(r"Mismatch: (run \d+ cycle \d+: output .*)", log)
    return m.group(1).strip() if m else ""


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    r = diff_check(Path(sys.argv[1]).read_text(), Path(sys.argv[2]).read_text(), sys.argv[3])
    print(json.dumps({k: v for k, v in r.items() if k != "log"}))
    if r["status"] != "pass":
        print(first_mismatch(r["log"]) or r["log"][-1500:], file=sys.stderr)
