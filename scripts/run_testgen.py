"""Verification tasks, one-shot: ask a model for a bug-catching test, then grade it.

Two steps, so model-written code never runs where the API key or network is:

1. ask (network, the key; never runs model code). Same spending safeguards as
   run_model.py: prices and a budget are required, nothing is sent without --yes.
     docker run --rm --env-file .env -v "$(pwd)":/work rtl-env python3 scripts/run_testgen.py ask \\
         --model openai:gpt-6-luna --price-in 0.2 --price-out 1 --budget 2 --per-seed 5 [--yes]

2. grade (no network, no .env: only scripts/, tasks/ and results/ are mounted):
     docker run --rm --network none -v "$(pwd)/scripts":/work/scripts:ro -v "$(pwd)/tasks":/work/tasks:ro \\
         -v "$(pwd)/results":/work/results rtl-env python3 scripts/run_testgen.py grade results/<run_dir>

Tasks: --per-seed bug tasks (bugfix + multibug) per seed, spread evenly, from tasks/.
The model sees spec.md, buggy.v and a skeleton test (testgen.py), and is told the
design has at least one bug. Grading is testgen.grade_test.

Writes results/testgen_<model>_<time>/: config.json, answers.jsonl (ask),
results.jsonl and summary.txt (grade). Both steps resume where they stopped.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_tasks import spread
from llm import Budget, actual_cost, ask_openai, is_paid, run_pool, worst_case_cost
from testgen import TIMEOUT, grade_test, skeleton

ROOT = Path(__file__).resolve().parent.parent
PROMPT_VERSION = "tg1"
MAX_ERRORS_IN_A_ROW = 3
SYSTEM = "You are an expert digital design and verification engineer."

PROMPT = """Write cocotb tests for the Verilog design below.

The design contains at least one bug: somewhere it does not match its specification.
Your test file is run twice, with the same fixed random seed and the design's default parameters:
  1. on a correct design that matches the specification: every test must pass;
  2. on the design below: at least one test must fail.
Both are needed to score. A test that checks behavior the specification does not define,
or checks it wrongly, fails on the correct design and scores 0.

Rules:
- cocotb 2.1 with Icarus Verilog. The top-level module is `{top}`; use its ports as `dut.<port>`.
  Internal signal names are not part of the specification: use top-level ports only.
- Allowed imports: cocotb, cocotb.clock, cocotb.triggers, cocotb.types, cocotb.handle, cocotb.utils,
  cocotb.queue, random, math, itertools, collections, functools (common public names only).
  No file, OS, or network access; no eval/exec/getattr/format(); no names or strings with "__";
  no attributes starting with "_" except dut._log. Files that break these rules score 0.
- Each run must finish within {timeout} seconds of wall-clock time (keep each test to tens of
  thousands of clock cycles). Use with_timeout or cycle limits so a hang fails a test instead.
- Start from the skeleton below; keep, change, or replace its helpers.
- Reply with the complete test file in a single ```python code block.

## Specification

{spec}

## Design under test: {top}.v

```verilog
{design}
```

## Skeleton: test_dut.py

```python
{skeleton}```
"""


def build_prompt(task_dir):
    meta = json.loads((task_dir / "meta.json").read_text())
    return meta, PROMPT.format(top=meta["top"], timeout=TIMEOUT, spec=(task_dir / "spec.md").read_text().strip(),
                               design=(task_dir / "buggy.v").read_text().rstrip(), skeleton=skeleton(task_dir))


def extract(answer):
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", answer, re.S)
    tests = [b for b in blocks if "cocotb.test" in b]
    return (tests or blocks or [None])[-1]


def pick_tasks(config):
    by_seed = defaultdict(list)
    for t in sorted(Path(config["tasks"]).glob("t_*")):
        m = json.loads((t / "meta.json").read_text())
        if m["task_type"] in ("bugfix", "multibug") and (not config.get("seeds") or m["seed"] in config["seeds"].split(",")):
            by_seed[m["seed"]].append(t)
    return [t for s in sorted(by_seed) for t in spread(by_seed[s], config["per_seed"])]


def worst(prompt, config):
    return worst_case_cost(SYSTEM, prompt, config["price_in"], config["price_out"], config["max_output"])


def ask_one(task_dir, config):
    meta, prompt = build_prompt(task_dir)
    row = {"task_id": task_dir.name, "seed": meta["seed"], "task_type": meta["task_type"], "label": meta["label"]}
    try:
        answer, thinking, stats = ask_openai(config["model"].split(":", 1)[1], SYSTEM, prompt,
                                             config["max_output"], config["effort"])
    except Exception as e:
        return {**row, "status": "error", "reason": f"model request failed: {e}", "cost": worst(prompt, config)}
    return {**row, "status": "answered", **stats, "answer": answer, "thinking": thinking, "code": extract(answer),
            "cost": actual_cost(stats, config["price_in"], config["price_out"])}


def read_rows(path):
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def cmd_ask(args, ap):
    if args.resume:
        run_dir = Path(args.resume)
        config = json.loads((run_dir / "config.json").read_text())
        if args.budget is not None:
            config["budget"] = args.budget
    else:
        if not args.model or not is_paid(args.model):
            ap.error("--model openai:<name> is required")
        config = {"model": args.model, "effort": args.effort, "prompt_version": PROMPT_VERSION,
                  "started": datetime.now().isoformat(timespec="seconds"), "tasks": args.tasks,
                  "seeds": args.seeds, "per_seed": args.per_seed, "max_output": args.max_output,
                  "price_in": args.price_in, "price_out": args.price_out, "budget": args.budget}
        run_dir = None
    missing = [f for f, k in (("--price-in", "price_in"), ("--price-out", "price_out"), ("--budget", "budget"))
               if config.get(k) is None]
    if missing:
        ap.error(f"paid model needs {', '.join(missing)} (copy prices from OpenAI's pricing page)")
    if min(config["budget"], config["price_in"], config["price_out"]) <= 0:
        ap.error("--budget, --price-in and --price-out must be positive")
    if args.yes and not os.environ.get("OPENAI_API_KEY"):
        ap.error("OPENAI_API_KEY is not set (in Docker, add: --env-file .env)")

    tasks = pick_tasks(config)
    rows = read_rows(run_dir / "answers.jsonl") if run_dir else []
    done = {r["task_id"]: r for r in rows if r["status"] != "error"}
    spent = sum(r.get("cost", 0) for r in rows)
    todo = [t for t in tasks if t.name not in done]

    worst_total = sum(worst(build_prompt(t)[1], config) for t in todo)
    est_in = sum(len(SYSTEM + build_prompt(t)[1]) / 4 for t in todo)
    typical = (est_in * config["price_in"] + len(todo) * 8000 * config["price_out"]) / 1e6
    print(f"{config['model']} (effort {config['effort']}): {len(todo)} test-writing tasks "
          f"({Counter(json.loads((t / 'meta.json').read_text())['seed'] for t in todo).most_common()})")
    print(f"  likely cost   ~${typical:.2f}  (guess: 8,000 output tokens per task incl. reasoning)")
    print(f"  worst case     ${worst_total:.2f}  (every answer uses all {config['max_output']} output tokens)")
    print(f"  budget         ${config['budget']:.2f}, already spent ${spent:.4f}")
    if worst_total + spent > config["budget"]:
        print("  -> the run will stop early if spending gets close to the budget")
    if not args.yes:
        print("\nEstimate only: nothing was sent. Re-run with --yes to start.")
        return

    if run_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        run_dir = Path(args.results_dir) / f"testgen_{re.sub(r'[^A-Za-z0-9.]+', '-', config['model'])}_{stamp}"
        run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    budget = Budget(config["budget"], spent)
    count = [0]
    print(f"{len(todo)} to ask, {len(done)} already answered, {args.jobs} at a time -> {run_dir}", flush=True)

    def reserve(t):
        w = worst(build_prompt(t)[1], config)
        return (True, w) if budget.reserve(w) else (False, budget.why_not(w))

    with (run_dir / "answers.jsonl").open("a") as out:
        def on_done(t, r, w):
            budget.settle(w, r.get("cost", 0))
            out.write(json.dumps(r) + "\n")
            out.flush()
            count[0] += 1
            print(f"  [{count[0]}/{len(todo)}] {r['task_id']} {r['seed']:<16} {r['status']}"
                  f"{'' if r.get('code') or r['status'] == 'error' else ' (no code block)'}  ${budget.spent:.4f} spent",
                  flush=True)
            return r["status"] == "error"

        stopped = run_pool(todo, lambda t: ask_one(t, config), args.jobs, reserve, on_done, MAX_ERRORS_IN_A_ROW)
    if stopped:
        print(f"\nSTOPPED: {stopped}. Resume with: ask --resume {run_dir}")
    print(f"\nanswers in {run_dir}/answers.jsonl, ${budget.spent:.4f} spent. Now grade them (no network):")
    print(f'docker run --rm --network none -v "$(pwd)/scripts":/work/scripts:ro -v "$(pwd)/tasks":/work/tasks:ro '
          f'-v "$(pwd)/results":/work/results rtl-env python3 scripts/run_testgen.py grade {run_dir.relative_to(ROOT)}')


def cmd_grade(args, ap):
    if os.environ.get("OPENAI_API_KEY") or (ROOT / ".env").exists():
        ap.error("refusing to run model-written tests where the API key is reachable. Mount only scripts/, "
                 "tasks/ and results/, without --env-file, and with --network none (see the docstring).")
    run_dir = Path(args.run_dir)
    config = json.loads((run_dir / "config.json").read_text())
    answers = {r["task_id"]: r for r in read_rows(run_dir / "answers.jsonl") if r["status"] != "error"}
    graded = {r["task_id"]: r for r in read_rows(run_dir / "results.jsonl")}
    todo = [a for t, a in answers.items() if t not in graded]
    print(f"grading {len(todo)} answers ({len(graded)} already graded), {args.jobs} at a time", flush=True)

    def one(a):
        base = {k: a[k] for k in ("task_id", "seed", "task_type", "label")}
        if not a.get("code"):
            return {**base, "status": "no_answer", "reason": "no python code block"}
        try:
            g = grade_test(Path(config["tasks"]) / a["task_id"], a["code"])
        except Exception as e:
            g = {"status": "error", "reason": f"grader crashed: {type(e).__name__}: {e}"}
        return {**base, **g}

    with (run_dir / "results.jsonl").open("a") as out, ThreadPoolExecutor(args.jobs) as pool:
        for n, r in enumerate(pool.map(one, todo), 1):
            graded[r["task_id"]] = r
            out.write(json.dumps(r) + "\n")
            out.flush()
            print(f"  [{n}/{len(todo)}] {r['task_id']} {r['seed']:<16} {r['status']:<12} {r.get('reason', '')[:100]}",
                  flush=True)
    rows = [graded[t] for t in answers if t in graded]
    print("\n" + summarize(run_dir, rows, config, answers))


def summarize(run_dir, rows, config, answers):
    n, ok = len(rows), sum(r["status"] == "pass" for r in rows)
    cost = sum(a.get("cost", 0) for a in answers.values())
    lines = [f"model      {config['model']} (effort {config['effort']}, prompt {config['prompt_version']})",
             f"task       write a cocotb test that passes on golden and fails on the buggy design",
             f"score      {ok}/{n} = {100 * ok / max(n, 1):.1f}%",
             f"outcomes   " + ", ".join(f"{k} {v}" for k, v in Counter(r["status"] for r in rows).most_common()),
             f"cost       ${cost:.4f} (prices in/out per 1M: ${config['price_in']} / ${config['price_out']})"]
    for key, title in (("task_type", "by task type"), ("seed", "by seed")):
        groups = defaultdict(list)
        for r in rows:
            groups[r[key]].append(r["status"] == "pass")
        lines += ["", title] + [f"  {g:<16} {sum(v)}/{len(v)}" for g, v in sorted(groups.items())]
    text = "\n".join(lines) + "\n"
    (run_dir / "summary.txt").write_text(text)
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask")
    a.add_argument("--model")
    a.add_argument("--effort", choices=["minimal", "low", "medium", "high", "default"], default="medium")
    a.add_argument("--price-in", type=float)
    a.add_argument("--price-out", type=float)
    a.add_argument("--budget", type=float)
    a.add_argument("--max-output", type=int, default=24000)
    a.add_argument("--per-seed", type=int, default=5)
    a.add_argument("--seeds")
    a.add_argument("--jobs", type=int, default=8)
    a.add_argument("--tasks", default=str(ROOT / "tasks"))
    a.add_argument("--results-dir", default=str(ROOT / "results"))
    a.add_argument("--resume")
    a.add_argument("--yes", action="store_true")
    g = sub.add_parser("grade")
    g.add_argument("run_dir")
    g.add_argument("--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args()
    (cmd_ask if args.cmd == "ask" else cmd_grade)(args, ap)


if __name__ == "__main__":
    main()
