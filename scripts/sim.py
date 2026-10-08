"""Run a design's cocotb tests against any Verilog file.

Used by the filter (does the bug get caught?) and later by the grader
(does the model's fix pass?), so both always judge the same way.

    python3 scripts/sim.py <design.v> <test_dir>

<test_dir> holds the Makefile and test_*.py. The Makefile's VERILOG_SOURCES
is overridden, so <design.v> can be any file: golden, buggy, or a model's fix.
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

DEFAULT_TIMEOUT = 60  # seconds


def run_tests(design, test_dir, timeout=DEFAULT_TIMEOUT):
    """Returns {"status", "passed", "failed", "log"}.

    status is one of:
      pass           every test passed
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

        env = dict(os.environ, PWD=str(work))  # make reads $(PWD), not the real cwd
        cmd = ["make", f"VERILOG_SOURCES={work / 'design.v'}"]
        # New session so a timeout can kill make and the simulator under it.
        proc = subprocess.Popen(cmd, cwd=work, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            log, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            log, _ = proc.communicate()
            return result("timeout", [], [], log)

        results = work / "results.xml"
        if not results.exists():
            return result("compile_error", [], [], log)

        passed, failed = [], []
        for case in ET.parse(results).iter("testcase"):
            bad = case.find("failure") is not None or case.find("error") is not None
            (failed if bad else passed).append(case.get("name"))
        if not passed and not failed:
            return result("compile_error", [], [], log)
        return result("fail" if failed else "pass", passed, failed, log)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def result(status, passed, failed, log):
    return {"status": status, "passed": passed, "failed": failed, "log": log[-3000:]}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    r = run_tests(sys.argv[1], sys.argv[2])
    print(json.dumps({k: v for k, v in r.items() if k != "log"}))
    if r["status"] in ("compile_error", "timeout"):
        print(r["log"][-1500:], file=sys.stderr)
