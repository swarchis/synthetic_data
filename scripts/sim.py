"""Run a design's cocotb tests against any Verilog file.

Used by the filter (does the bug get caught?) and later by the grader
(does the model's fix pass?), so both always judge the same way.

    python3 scripts/sim.py <design.v> <test_dir>

<test_dir> holds the Makefile and test_*.py. The Makefile's VERILOG_SOURCES
is overridden, so <design.v> can be any file: golden, buggy, or a model's fix.

Parameter sweeps: if <test_dir> has a configs.json like
    {"top": "apb_intc", "configs": [{"name": "n4", "params": {"NUM_SRC": 4}}, ...]}
the tests run once per config, with the parameters passed to the simulator
(-P<top>.<name>=<value>) and to the tests as JSON in $RTL_PARAMS. The design
passes only if every config passes; the first config that does not pass ends
the run, and failed test names are prefixed with the config name.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_TIMEOUT = 60  # seconds, per config


def run_tests(design, test_dir, timeout=DEFAULT_TIMEOUT):
    """Returns {"status", "passed", "failed", "log"}.

    status is one of:
      pass           every test passed (in every config)
      fail           at least one test failed
      compile_error  the simulator could not build the design
      timeout        simulation ran longer than `timeout` seconds
    """
    design = Path(design).resolve()
    test_dir = Path(test_dir).resolve()

    # Fresh folder per run, so parallel runs never share build files.
    work = Path(tempfile.mkdtemp(prefix="sim_"))
    try:
        shutil.copy(test_dir / "Makefile", work)
        for f in test_dir.glob("*.py"):
            shutil.copy(f, work)
        shutil.copy(design, work / "design.v")

        sweep = test_dir / "configs.json"
        if not sweep.exists():
            return run_once(work, [], {}, timeout)

        spec = json.loads(sweep.read_text())
        passed, logs = [], []
        for cfg in spec["configs"]:
            name, params = cfg["name"], cfg["params"]
            args = " ".join(f"-P{spec['top']}.{k}={v}" for k, v in params.items())
            # COMPILE_ARGS goes in the environment, not on the make command line: cocotb's
            # makefile appends its own flags (timescale etc.) with +=, which a command-line
            # value would silently override.
            env = {"RTL_PARAMS": json.dumps(params), "COMPILE_ARGS": args}
            r = run_once(work, [f"SIM_BUILD=sim_build_{name}"], env, timeout)
            logs.append(f"===== config {name} {params}: {r['status']}\n{r['log']}")
            if r["status"] != "pass":
                return result(r["status"], passed + [f"{name}:{t}" for t in r["passed"]],
                              [f"{name}:{t}" for t in r["failed"]], "\n".join(logs))
            passed += [f"{name}:{t}" for t in r["passed"]]
        return result("pass", passed, [], "\n".join(logs))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_once(work, make_args, extra_env, timeout):
    results = work / "results.xml"
    if results.exists():
        results.unlink()
    env = dict(os.environ, PWD=str(work), **extra_env)   # make reads $(PWD), not the real cwd
    cmd = ["make", f"VERILOG_SOURCES={work / 'design.v'}"] + make_args
    # New session so a timeout can kill make and the simulator under it.
    proc = subprocess.Popen(cmd, cwd=work, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        log, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        log, _ = proc.communicate()
        return result("timeout", [], [], log)

    if not results.exists():
        return result("compile_error", [], [], log)

    passed, failed = [], []
    for case in ET.parse(results).iter("testcase"):
        bad = case.find("failure") is not None or case.find("error") is not None
        (failed if bad else passed).append(case.get("name"))
    if not passed and not failed:
        return result("compile_error", [], [], log)
    return result("fail" if failed else "pass", passed, failed, log)


def result(status, passed, failed, log):
    return {"status": status, "passed": passed, "failed": failed, "log": log[-3000:]}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    r = run_tests(sys.argv[1], sys.argv[2])
    print(json.dumps({k: v for k, v in r.items() if k != "log"}))
    if r["status"] in ("compile_error", "timeout"):
        print(r["log"][-1500:], file=sys.stderr)
