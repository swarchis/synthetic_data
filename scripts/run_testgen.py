"""Verification tasks, one-shot: ask a model for a bug-catching test, then grade it.

Two steps, so model-written code never runs where the API key or network is:

1. ask (network, the key; never runs model code). Same spending safeguards as
   run_model.py: prices and a budget are required, nothing is sent without --yes.
     docker run --rm --env-file .env -v "$(pwd)":/work rtl-env python3 scripts/run_testgen.py ask \\
         --model openai:gpt-6-luna --price-in 0.2 --price-out 1 --budget 2 --per-seed 5 [--yes]

2. grade (no network, no .env: only scripts/, tasks/ and results/ are mounted):
     docker run --rm --network none -v "$(pwd)/scripts":/work/scripts:ro -v "$(pwd)/tasks":/work/tasks:ro \\
         -v "$(pwd)/results":/work/results rtl-env python3 scripts/run_testgen.py grade results/<run_dir>

Two modes:
  --mode bug    (default) --per-seed bug tasks per seed. The model sees spec.md, buggy.v
                and a skeleton test, and is told the design has at least one bug.
                Pass = its test passes on golden and fails on buggy (testgen.grade_test).
  --mode suite  spec-only: --samples answers per seed. The model sees spec.md, the
                module header and the skeleton, never an implementation. Score = share
                of --mutants hidden buggy versions caught, if the suite passes on golden
                (testgen.grade_suite). Mutants are chosen at grading time, the same for
                every answer on a seed, and saved to mutants.json.

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
from testgen import TIMEOUT, grade_suite, grade_test, header, observable_at_defaults, skeleton

ROOT = Path(__file__).resolve().parent.parent
PROMPT_VERSION = "tg2"   # bug mode (tg2: self._x allowed in the rules text); suite mode below
SUITE_PROMPT_VERSION = "ts1"
MAX_ERRORS_IN_A_ROW = 3
SYSTEM = "You are an expert digital design and verification engineer."

RULES = """Rules:
- cocotb 2.1 with Icarus Verilog. The top-level module is `{top}`; use its ports as `dut.<port>`.
  Internal signal names are not part of the specification: use top-level ports only.
- Allowed imports: cocotb, cocotb.clock, cocotb.triggers, cocotb.types, cocotb.handle, cocotb.utils,
  cocotb.queue, random, math, itertools, collections, functools (common public names only).
  No file, OS, or network access; no eval/exec/getattr/format(); no names or strings with "__";
  no attributes starting with "_" except dut._log and your own self._x. Files that break
  these rules score 0.
- Each run must finish within {timeout} seconds of wall-clock time (keep each test to tens of
  thousands of clock cycles). Use with_timeout or cycle limits so a hang fails a test instead.
- Start from the skeleton below; keep, change, or replace its helpers.
- Reply with the complete test file in a single ```python code block.
"""

PROMPT = """Write cocotb tests for the Verilog design below.

The design contains at least one bug: somewhere it does not match its specification.
Your test file is run twice, with the same fixed random seed and the design's default parameters:
  1. on a correct design that matches the specification: every test must pass;
  2. on the design below: at least one test must fail.
Both are needed to score. A test that checks behavior the specification does not define,
or checks it wrongly, fails on the correct design and scores 0.

{rules}
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


SUITE_PROMPT = """Write a cocotb test suite for the Verilog module specified below. You get its
specification and its module header, not its implementation.

The suite is run, with a fixed random seed and the default parameters, on a correct
implementation and on many hidden versions that each contain one or more bugs (wrong
operators, constants, conditions, timing, resets, and similar). Your score is the share of
buggy versions in which at least one test fails, but only if every test passes on the correct
implementation; otherwise the score is 0. So check as much of the specified behavior as
precisely as you can, corner cases included, and never check behavior the specification
leaves undefined.

{rules}
## Specification

{spec}

## Module header: {top}

```verilog
{header}```

## Skeleton: test_dut.py

```python
{skeleton}```
"""


def build_prompt(unit, config):
    """unit = (id, task_dir). Suite mode uses the task only for its spec, golden ports and header."""
    _, task_dir = unit
    meta = json.loads((task_dir / "meta.json").read_text())
    rules = RULES.format(top=meta["top"], timeout=TIMEOUT)
    spec = (task_dir / "spec.md").read_text().strip()
    if config.get("mode") == "suite":
        return meta, SUITE_PROMPT.format(rules=rules, spec=spec, top=meta["top"], header=header(task_dir),
                                         skeleton=skeleton(task_dir))
    return meta, PROMPT.format(rules=rules, top=meta["top"], spec=spec,
                               design=(task_dir / "buggy.v").read_text().rstrip(), skeleton=skeleton(task_dir))


def extract(answer):
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", answer, re.S)
    tests = [b for b in blocks if "cocotb.test" in b]
    return (tests or blocks or [None])[-1]


def bug_tasks(config):
    """seed -> its bug tasks (bugfix + multibug), sorted by id."""
    by_seed = defaultdict(list)
    for t in sorted(Path(config["tasks"]).glob("t_*")):
        m = json.loads((t / "meta.json").read_text())
        if m["task_type"] in ("bugfix", "multibug") and (not config.get("seeds") or m["seed"] in config["seeds"].split(",")):
            by_seed[m["seed"]].append(t)
    return by_seed


def pick_units(config):
    """(id, task_dir) per answer to ask for: a bug task, or a seed sample in suite mode."""
    by_seed = bug_tasks(config)
    if config.get("mode") == "suite":
        return [(f"{s}#{k}", by_seed[s][0]) for s in sorted(by_seed) for k in range(config["samples"])]
    return [(t.name, t) for s in sorted(by_seed) for t in spread(by_seed[s], config["per_seed"])]


def worst(prompt, config):
    return worst_case_cost(SYSTEM, prompt, config["price_in"], config["price_out"], config["max_output"])


def ask_one(unit, config):
    meta, prompt = build_prompt(unit, config)
    suite = config.get("mode") == "suite"
    row = {"task_id": unit[0], "seed": meta["seed"], "task_type": "suite" if suite else meta["task_type"],
           "label": "suite" if suite else meta["label"], "prompt_task": unit[1].name}
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
        suite = args.mode == "suite"
        config = {"model": args.model, "effort": args.effort, "mode": args.mode,
                  "prompt_version": SUITE_PROMPT_VERSION if suite else PROMPT_VERSION,
                  "started": datetime.now().isoformat(timespec="seconds"), "tasks": args.tasks,
                  "seeds": args.seeds, "per_seed": args.per_seed, "samples": args.samples,
                  "max_output": args.max_output,
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

    units = pick_units(config)
    rows = read_rows(run_dir / "answers.jsonl") if run_dir else []
    done = {r["task_id"]: r for r in rows if r["status"] != "error"}
    spent = sum(r.get("cost", 0) for r in rows)
    todo = [u for u in units if u[0] not in done]
    prompts = {u[0]: build_prompt(u, config)[1] for u in todo}

    worst_total = sum(worst(prompts[u[0]], config) for u in todo)
    est_in = sum(len(SYSTEM + prompts[u[0]]) / 4 for u in todo)
    typical = (est_in * config["price_in"] + len(todo) * 8000 * config["price_out"]) / 1e6
    print(f"{config['model']} (effort {config['effort']}, mode {config.get('mode', 'bug')}): {len(todo)} answers "
          f"({Counter(json.loads((u[1] / 'meta.json').read_text())['seed'] for u in todo).most_common()})")
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

    def reserve(u):
        w = worst(prompts[u[0]], config)
        return (True, w) if budget.reserve(w) else (False, budget.why_not(w))

    with (run_dir / "answers.jsonl").open("a") as out:
        def on_done(u, r, w):
            budget.settle(w, r.get("cost", 0))
            out.write(json.dumps(r) + "\n")
            out.flush()
            count[0] += 1
            print(f"  [{count[0]}/{len(todo)}] {r['task_id']} {r['seed']:<16} {r['status']}"
                  f"{'' if r.get('code') or r['status'] == 'error' else ' (no code block)'}  ${budget.spent:.4f} spent",
                  flush=True)
            return r["status"] == "error"

        stopped = run_pool(todo, lambda u: ask_one(u, config), args.jobs, reserve, on_done, MAX_ERRORS_IN_A_ROW)
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
    if config.get("mode") == "suite":
        return grade_suites(run_dir, config, answers, graded, todo, args)

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


def pick_mutants(run_dir, config, n, jobs):
    """seed -> hidden mutant task ids, the same for every answer. Saved to mutants.json on
    first use, so a resumed or repeated grade uses the same ones. Spread evenly over each
    seed's bug tasks; for seeds with a parameter sweep, only bugs that show at the
    default parameters (the suite runs there)."""
    path = run_dir / "mutants.json"
    if path.exists():
        return json.loads(path.read_text())
    out = {}
    by_seed = bug_tasks(config)
    with ThreadPoolExecutor(jobs) as pool:
        for seed in sorted(by_seed):
            cands = spread(by_seed[seed], 3 * n)
            if (cands[0] / "tests" / "configs.json").exists():
                ok = list(pool.map(observable_at_defaults, cands))
                print(f"  {seed}: {sum(ok)}/{len(cands)} candidate bugs show at default parameters", flush=True)
                cands = [c for c, o in zip(cands, ok) if o]
            out[seed] = [c.name for c in spread(cands, n)]
    path.write_text(json.dumps(out, indent=1) + "\n")
    return out


def grade_suites(run_dir, config, answers, graded, todo, args):
    mutants = pick_mutants(run_dir, config, args.mutants, args.jobs)
    tasks = Path(config["tasks"])
    with (run_dir / "results.jsonl").open("a") as out, ThreadPoolExecutor(args.jobs) as pool:
        for n, a in enumerate(todo, 1):
            base = {k: a[k] for k in ("task_id", "seed", "task_type", "label")}
            if not a.get("code"):
                r = {**base, "status": "no_answer", "score": 0.0, "reason": "no python code block"}
            else:
                try:
                    r = {**base, **grade_suite(tasks / a["prompt_task"], [tasks / m for m in mutants[a["seed"]]],
                                                a["code"], pool)}
                except Exception as e:
                    r = {**base, "status": "error", "score": 0.0, "reason": f"grader crashed: {type(e).__name__}: {e}"}
            graded[r["task_id"]] = r
            out.write(json.dumps(r) + "\n")
            out.flush()
            print(f"  [{n}/{len(todo)}] {r['task_id']:<20} {r['status']:<12} score {r['score']:.2f}  "
                  f"{r.get('reason', '')[:100]}", flush=True)
    rows = [graded[t] for t in answers if t in graded]
    print("\n" + summarize_suites(run_dir, rows, config, answers, mutants))


def summarize_suites(run_dir, rows, config, answers, mutants):
    cost = sum(a.get("cost", 0) for a in answers.values())
    mean = sum(r["score"] for r in rows) / max(len(rows), 1)
    lines = [f"model      {config['model']} (effort {config['effort']}, prompt {config['prompt_version']})",
             f"task       spec-only test suite; score = share of hidden buggy versions caught, if it passes golden",
             f"score      {100 * mean:.1f}% mean over {len(rows)} suites",
             f"outcomes   " + ", ".join(f"{k} {v}" for k, v in Counter(r["status"] for r in rows).most_common()),
             f"cost       ${cost:.4f} (prices in/out per 1M: ${config['price_in']} / ${config['price_out']})",
             "", "by seed (mean score; each suite's score; mutants)"]
    by_seed = defaultdict(list)
    for r in rows:
        by_seed[r["seed"]].append(r)
    for seed, rs in sorted(by_seed.items()):
        sc = [r["score"] for r in rs]
        each = " ".join("FG" if r["status"] == "fails_golden" else "RJ" if r["status"] == "rejected"
                        else f"{r['score']:.2f}" for r in rs)
        lines.append(f"  {seed:<16} {100 * sum(sc) / len(sc):5.1f}%   {each:<24} {len(mutants.get(seed, []))}")
    labels = defaultdict(lambda: [0, 0])          # mutant label -> [caught, tried], suites that passed golden
    tasks = Path(config["tasks"])
    label_of = {}
    for r in rows:
        if r["status"] != "graded":
            continue
        for tid, hit in [(t, 1) for t in r["killed"]] + [(t, 0) for t in r["missed"]]:
            if tid not in label_of:
                label_of[tid] = json.loads((tasks / tid / "meta.json").read_text())["label"]
            labels[label_of[tid]][0] += hit
            labels[label_of[tid]][1] += 1
    lines += ["", "bugs caught by label (over suites that passed golden)"]
    lines += [f"  {k:<16} {v[0]}/{v[1]}" for k, v in sorted(labels.items())]
    text = "\n".join(lines) + "\n"
    (run_dir / "summary.txt").write_text(text)
    return text


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
    a.add_argument("--mode", choices=["bug", "suite"], default="bug")
    a.add_argument("--per-seed", type=int, default=5, help="bug mode: tasks per seed")
    a.add_argument("--samples", type=int, default=3, help="suite mode: answers per seed")
    a.add_argument("--seeds")
    a.add_argument("--jobs", type=int, default=8)
    a.add_argument("--tasks", default=str(ROOT / "tasks"))
    a.add_argument("--results-dir", default=str(ROOT / "results"))
    a.add_argument("--resume")
    a.add_argument("--yes", action="store_true")
    g = sub.add_parser("grade")
    g.add_argument("run_dir")
    g.add_argument("--jobs", type=int, default=os.cpu_count())
    g.add_argument("--mutants", type=int, default=40, help="suite mode: hidden buggy versions per seed")
    args = ap.parse_args()
    (cmd_ask if args.cmd == "ask" else cmd_grade)(args, ap)


if __name__ == "__main__":
    main()
