"""Check a test-writing failure on the golden design: is it the model's mistake or a spec gap?

    python3 scripts/patch_rerun.py <results_dir> <patches.json>

patches.json: {"<task_id>|<test name>": [["old text", "new text"], ...], ...}. Each
pair replaces one exact, unique piece of the model's suite (a wrong expected value,
a missing PADDR set, ...) with what the spec says. The patched test is then run alone
on the golden design. "pass" means the suite was right everywhere else, so the failure
is fully explained by the patched lines; each patch still needs a spec sentence that
states the patched value (otherwise the failure is a spec gap, not the model's mistake).
Run it in the rtl-env container with --network none (model-written code).
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sim import run_once
from testgen import MAKEFILE, SEED, _ports

ROOT = Path(__file__).resolve().parent.parent


def rerun(answer, test, pairs):
    code = answer["code"]
    for old, new in pairs:
        assert code.count(old) == 1, f"{test}: patch text found {code.count(old)} times: {old[:60]!r}"
        code = code.replace(old, new)
    task = ROOT / "tasks" / answer["prompt_task"]
    top, _, _ = _ports(task)
    work = Path(tempfile.mkdtemp())
    (work / "Makefile").write_text(MAKEFILE.format(top=top))
    (work / "design.v").write_text((task / "golden.v").read_text())
    (work / "test_dut.py").write_text(code)
    env = {"COCOTB_RANDOM_SEED": SEED, "RANDOM_SEED": SEED, "COCOTB_TEST_FILTER": f"'(^|\\.)({test})\\Z'"}
    return run_once(work, [], env, 120, base_env={k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ})


def main():
    d, patches = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())
    answers = {json.loads(l)["task_id"]: json.loads(l) for l in (d / "answers.jsonl").read_text().splitlines()}
    bad = 0
    for key, pairs in patches.items():
        uid, test = key.split("|")
        r = rerun(answers[uid], test, pairs)
        bad += r["status"] != "pass"
        print(f"{r['status']:5} {uid:16} {test:40} {len(pairs)} patch(es)")
        if r["status"] != "pass":
            i = r["log"].find("Error")
            print("      ", r["log"][max(0, i - 300):i + 300].replace("\n", "\n       "))
    print(f"\n{len(patches) - bad}/{len(patches)} failing tests pass on golden after their patches")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
