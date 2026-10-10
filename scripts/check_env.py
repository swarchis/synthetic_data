"""Scripted agents that check the agent environment behaves before any model touches it.

    docker run --rm -v "$(pwd)":/work rtl-env python3 scripts/check_env.py [--seeds fifo,ef_tmr32] [--per-seed 2]

For each picked task:
  oracle     writes the golden design, compiles, runs tests, submits     -> reward 1
  patcher    fixes a bugfix task with replace() edits only               -> reward 1
  lazy       submits the untouched task design                           -> reward 0
  peeker     tries to read/write outside the workspace, then submits      -> every attempt refused, reward 0
  looper     reads the spec forever                                      -> stopped at the step cap, reward 0
Then, over all picked tasks: how often the visible checks already catch the bug
(they should catch most, and the hidden grade must catch every one).
"""

import argparse
import difflib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env import RTLEnv, visible_check

ROOT = Path(__file__).resolve().parent.parent


def run(env, calls):
    env.reset()
    outs = []
    for tool, args in calls:
        obs, reward, done = env.step(tool, args)
        outs.append(obs)
        if done:
            break
    return outs, env.reward, env.done


def oracle(env):
    gold = (env.task_dir / "golden.v").read_text()
    return [("read_file", {"path": "spec.md"}), ("write_file", {"path": env.design_name, "content": gold}),
            ("compile", {}), ("run_tests", {}), ("submit", {})]


def patcher(env):
    """replace() edits that turn buggy.v into golden.v, one changed block at a time."""
    buggy = (env.task_dir / "buggy.v").read_text().splitlines(keepends=True)
    gold = (env.task_dir / "golden.v").read_text().splitlines(keepends=True)
    calls = []
    sm = difflib.SequenceMatcher(None, buggy, gold, autojunk=False)
    for op, a1, a2, b1, b2 in reversed(sm.get_opcodes()):    # bottom up so context stays valid
        if op == "equal":
            continue
        lo, hi = a1, a2
        while True:                                          # grow context until unique
            old = "".join(buggy[lo:hi])
            if old and "".join(buggy).count(old) == 1:
                break
            lo, hi = max(0, lo - 1), min(len(buggy), hi + 1)
        new = "".join(buggy[lo:a1]) + "".join(gold[b1:b2]) + "".join(buggy[a2:hi])
        calls.append(("replace", {"path": env.design_name, "old": old, "new": new}))
    return calls + [("compile", {}), ("submit", {})]


def peeker(env):
    t = str(env.task_dir)
    return [("read_file", {"path": "../golden.v"}), ("read_file", {"path": t + "/golden.v"}),
            ("read_file", {"path": "golden.v"}), ("read_file", {"path": "/etc/passwd"}),
            ("search", {"text": "module", "path": "../golden.v"}),
            ("write_file", {"path": "../evil.v", "content": "x"}),
            ("write_file", {"path": "spec.md", "content": "x"}),
            ("write_file", {"path": "sub/" + env.design_name, "content": "x"}),
            ("replace", {"path": "/etc/hosts", "old": "a", "new": "b"}),
            ("submit", {})]


def check_task(task_dir, max_steps):
    env = RTLEnv(task_dir, max_steps=max_steps)
    ok, notes = True, []

    def expect(name, cond, detail=""):
        nonlocal ok
        if not cond:
            ok = False
            notes.append(f"FAIL {name} {detail}")

    try:
        outs, r, _ = run(env, oracle(env))
        expect("oracle", r == 1.0, f"{env.final} {outs[3][:300]!r}")
        expect("oracle-visible", outs[3].startswith("Visible checks passed"), outs[3][:300])
        expect("oracle-compile", outs[2].startswith("Compiled OK"), outs[2][:300])

        if env.meta["task_type"] in ("bugfix", "multibug"):
            calls = patcher(env)
            outs, r, _ = run(env, calls)
            expect("patcher", r == 1.0, f"{env.final} {[o[:120] for o in outs]}")

        outs, r, _ = run(env, [("submit", {})])
        expect("lazy", r == 0.0, str(env.final))

        outs, r, _ = run(env, peeker(env))
        refused = all(o.startswith(("Error:", "Not replaced")) for o in outs[:-1])
        expect("peeker", refused and r == 0.0, f"{[o[:100] for o in outs[:-1]]}")
        leaked = [o for o in outs if "endmodule" in o or "root:" in o]
        expect("peeker-leak", not leaked, leaked[:1])

        outs, r, done = run(env, [("read_file", {"path": "spec.md"})] * (max_steps + 5))
        expect("looper", done and r == 0.0 and len(outs) == max_steps and env.final["status"] == "no_submit",
               f"{len(outs)} {env.final}")
        expect("looper-after", env.step("submit", {})[0] == "The task is already finished.")

        vis = visible_check(env.task_dir, (env.task_dir / "buggy.v").read_text())
    finally:
        env.close()
    return ok, notes, vis["status"] != "pass"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks-dir", default=str(ROOT / "tasks"))
    ap.add_argument("--seeds", help="comma list (default: all)")
    ap.add_argument("--per-seed", type=int, default=2)
    ap.add_argument("--max-steps", type=int, default=12)
    a = ap.parse_args()

    by_seed = {}
    for m in sorted(Path(a.tasks_dir).glob("t_*/meta.json")):
        j = json.loads(m.read_text())
        by_seed.setdefault(j["seed"], {}).setdefault(j["task_type"], []).append(m.parent)
    seeds = a.seeds.split(",") if a.seeds else sorted(by_seed)
    rng = random.Random(0)
    picked = []
    for s in seeds:
        for tt in ("bugfix", "multibug", "complete", "module"):
            pool = by_seed.get(s, {}).get(tt, [])
            picked += rng.sample(pool, min(a.per_seed, len(pool)))

    bad, caught = 0, 0
    for t in picked:
        meta = json.loads((t / "meta.json").read_text())
        ok, notes, vis_caught = check_task(t, a.max_steps)
        bad += not ok
        caught += vis_caught
        print(f"{'ok  ' if ok else 'BAD '} {t.name} {meta['seed']:16} {meta['task_type']:9} "
              f"visible {'catches' if vis_caught else 'misses '} the bug", flush=True)
        for n in notes:
            print("     ", n[:600])
    print(f"\n{len(picked) - bad}/{len(picked)} tasks behave; visible checks catch the starting bug in "
          f"{caught}/{len(picked)} (hidden grade catches all by construction)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
