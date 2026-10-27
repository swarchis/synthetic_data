"""Scripted answers that check the verification-task grader before any model runs.

    docker run --rm -e OPENAI_API_KEY=fake-for-check -v "$(pwd)":/work -w /work rtl-env \\
        python3 scripts/check_testgen.py [--per-seed 1]

For bug tasks of every seed:
  skeleton    the starting test file: runs on every seed, catches nothing   -> misses_bug
  reference   the seed's own hand tests (seeds that have them)              -> pass if they catch this bug
  always_fail one test that asserts False                                   -> fails_golden
  hang        a test that never finishes                                    -> fails_golden (timeout)
Once (static check + environment):
  file/os/format/frame/module-attribute tricks                              -> rejected
  env_peek    (static check off) fails if OPENAI_API_KEY is visible        -> pass: the key is not passed on
"""

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from testgen import grade_test, skeleton

ROOT = Path(__file__).resolve().parent.parent

ALWAYS_FAIL = """import cocotb

@cocotb.test()
async def nope(dut):
    assert False, "always fails"
"""

HANG = """import cocotb
from cocotb.triggers import Timer

@cocotb.test()
async def forever(dut):
    while True:
        await Timer(1, "ns")
"""

ENV_PEEK = """import os
import cocotb

@cocotb.test()
async def no_key(dut):
    assert "OPENAI_API_KEY" not in os.environ, "the API key reached the test"
"""

CHEATS = {
    "read_design": 'import cocotb\n@cocotb.test()\nasync def t(dut):\n    assert "BUG" not in open("design.v").read()\n',
    "import_os": "import os\nimport cocotb\n@cocotb.test()\nasync def t(dut):\n    os.listdir('.')\n",
    "random_os": "import random\nimport cocotb\n@cocotb.test()\nasync def t(dut):\n    random._os.listdir('.')\n",
    "module_attr": "import cocotb.task\nimport cocotb\n@cocotb.test()\nasync def t(dut):\n    cocotb.task.inspect.getsource(t)\n",
    "module_alias": "import cocotb\nimport random\nm = random\n@cocotb.test()\nasync def t(dut):\n    pass\n",
    "frame": ("import cocotb\nfrom cocotb.clock import Clock\n@cocotb.test()\nasync def t(dut):\n"
              "    c = Clock(dut.clk, 10, 'ns').start()\n    b = c.cr_frame.f_globals\n"),
    "format": 'import cocotb\n@cocotb.test()\nasync def t(dut):\n    "{0.x}".format(dut)\n',
    "dunder": "import cocotb\n@cocotb.test()\nasync def t(dut):\n    (1).__class__\n",
    "getattr": "import cocotb\n@cocotb.test()\nasync def t(dut):\n    getattr(dut, 'clk')\n",
    "eval": "import cocotb\n@cocotb.test()\nasync def t(dut):\n    eval('1')\n",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks-dir", default=str(ROOT / "tasks"))
    ap.add_argument("--per-seed", type=int, default=1)
    a = ap.parse_args()

    by_seed = {}
    for m in sorted(Path(a.tasks_dir).glob("t_*/meta.json")):
        j = json.loads(m.read_text())
        if j["task_type"] in ("bugfix", "multibug"):
            by_seed.setdefault(j["seed"], []).append(m.parent)
    rng = random.Random(0)
    picked = [t for s in sorted(by_seed) for t in rng.sample(by_seed[s], min(a.per_seed, len(by_seed[s])))]

    fails = []

    def expect(name, got, want):
        ok = got["status"] in want
        print(f"{'ok  ' if ok else 'FAIL'} {name:<44} {got['status']:<12} {got.get('reason', '')[:110]}", flush=True)
        if not ok:
            fails.append(name)

    for t in picked:
        seed = json.loads((t / "meta.json").read_text())["seed"]
        expect(f"{seed} {t.name} skeleton", grade_test(t, skeleton(t), timeout=60), {"misses_bug"})
        hand = sorted((t / "tests").glob("test_*.py"))
        if hand:   # hand tests read $RTL_PARAMS via os: grade without the static check
            r = grade_test(t, hand[0].read_text(), timeout=120, check=False)
            failing = json.loads((t / "meta.json").read_text())["failing_tests"]
            caught_by_hand = any(not f.startswith("diff:") for f in failing)
            expect(f"{seed} {t.name} reference", r, {"pass"} if caught_by_hand else {"pass", "misses_bug"})
        expect(f"{seed} {t.name} always_fail", grade_test(t, ALWAYS_FAIL, timeout=60), {"fails_golden"})
    t = picked[0]
    expect(f"hang ({t.name})", grade_test(t, HANG, timeout=15), {"fails_golden"})
    for name, src in CHEATS.items():
        expect(f"cheat {name}", grade_test(t, src, timeout=30), {"rejected"})
    expect("env_peek (static check off)", grade_test(t, ENV_PEEK, timeout=30, check=False), {"misses_bug"})

    print("\nALL OK" if not fails else f"\nFAILED: {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
