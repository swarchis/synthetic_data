"""Show where a test-writing suite fails on the golden design: rerun each failing test alone on golden
and print the error plus the test code around the outermost and innermost frames in the test file.

    docker run --rm --network none -v "$(pwd)":/work:ro -w /work rtl-env \\
        python3 scripts/failure_context.py results/<run> <suite_id> [lines_before]

Model-written code: run it only in the container with --network none.
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sim import run_once
from testgen import MAKEFILE, SEED, _ports

ROOT = Path(__file__).resolve().parent.parent


def main():
    run, uid = Path(sys.argv[1]), sys.argv[2]
    before = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    ans = {json.loads(l)["task_id"]: json.loads(l) for l in (run / "answers.jsonl").read_text().splitlines()}[uid]
    res = {json.loads(l)["task_id"]: json.loads(l) for l in (run / "results.jsonl").read_text().splitlines()}[uid]
    task = ROOT / "tasks" / ans["prompt_task"]
    top, _, _ = _ports(task)
    code = ans["code"].splitlines()
    for test in res.get("golden_failed", []):
        test = test.split(":")[-1]
        work = Path(tempfile.mkdtemp())
        (work / "Makefile").write_text(MAKEFILE.format(top=top))
        (work / "design.v").write_text((task / "golden.v").read_text())
        (work / "test_dut.py").write_text(ans["code"])
        r = run_once(work, [], {"COCOTB_RANDOM_SEED": SEED, "RANDOM_SEED": SEED,
                                "COCOTB_TEST_FILTER": f"'(^|\\.)({test})\\Z'"}, 120,
                     base_env={k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ})
        lines = [int(x) for x in re.findall(r'test_dut\.py", line (\d+)', r["log"])]
        msg = next(iter(re.findall(r"(\w+Error: [^\n]*|AssertionError: [^\n]*)", r["log"])), "")
        print(f"=== {uid} :: {test}  status={r['status']}  frames={lines}")
        print("    ", msg[:300])
        for n in ([lines[0]] + ([lines[-1]] if len(lines) > 1 else [])) if lines else []:
            print(f"    --- line {n}")
            for k in range(max(1, n - before), n + 1):
                print(f"{k:5}{'>' if k == n else ' '} {code[k - 1][:150]}")


if __name__ == "__main__":
    main()
