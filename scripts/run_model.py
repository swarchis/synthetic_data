"""Run a model on the bug-fix tasks and grade every answer.

Local models (free, through Ollama on the Mac):
    python3 scripts/run_model.py --model qwen2.5-coder:7b
    python3 scripts/run_model.py --model qwen3.5:9b --think on --limit 5

Paid models (OpenAI). Prices are per 1M tokens, copied from OpenAI's pricing
page; nothing is sent until --yes is given:
    python3 scripts/run_model.py --model openai:gpt-5-mini \\
        --price-in 0.25 --price-out 2.00 --budget 5 --limit 10          # estimate only
    python3 scripts/run_model.py ... --yes                              # actually run

    Inside Docker, pass the key through from your shell (never type it here):
        docker run --rm -e OPENAI_API_KEY -v "$(pwd)":/work rtl-env python3 scripts/run_model.py ...

Resume an interrupted run (spending so far counts against the same budget):
    python3 scripts/run_model.py --resume results/<run_dir> [--yes]

The model sees only spec.md and buggy.v. One attempt per task (pass@1).
Ollama runs use temperature 0; OpenAI reasoning models use their default.

Spending safeguards for paid models:
  - prices and a budget are required; there are no built-in prices to go stale
  - without --yes, only the cost estimate is printed and nothing is sent
  - before every request: if spent + this request's worst case (prompt plus a
    full --max-output answer) would exceed the budget, the run stops first
  - --max-output caps every answer (enforced by OpenAI)
  - no automatic retries; 3 errors in a row stop the run
  - a request that fails with unknown usage is charged at its worst case

Each run writes results/<model>_<settings>_<time>/:
  config.json     model and settings
  results.jsonl   one line per task: grade, timing, tokens, cost, full response
  summary.txt     pass rates overall, by bug type, by seed; total cost
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from grade import grade
from llm import Budget, actual_cost, ask_ollama, ask_openai, is_paid, run_pool, worst_case_cost

ROOT = Path(__file__).resolve().parent.parent
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
PROMPT_VERSION = "v2"   # v2 adds prompts for new task types; the bugfix prompt is unchanged from v1
MAX_ERRORS_IN_A_ROW = 3

SYSTEM = "You are an expert digital design and verification engineer."

INSTRUCTIONS = {
    "bugfix": ("The Verilog module below does not match its specification. It contains exactly one bug.\n"
               "Find the bug and return the complete corrected file.",
               "- Change only what is needed to fix the bug.", "Buggy file"),
    "multibug": ("The Verilog module below does not match its specification. It contains one or more bugs.\n"
                 "Find and fix all of them, and return the complete corrected file.",
                 "- Change only what is needed to fix the bugs.", "Buggy file"),
    "complete": ("The Verilog module below is incomplete: some logic was removed and replaced with a\n"
                 "`// TODO: missing logic` comment. Write the missing logic so the module matches its\n"
                 "specification, and return the complete file.",
                 "- Keep the existing code; add what is missing.", "Incomplete file"),
    "module": ("The Verilog module below has only its interface. Write the body so the module matches\n"
               "its specification, and return the complete file.",
               "- Declare any internal signals you need.", "Incomplete file"),
}

PROMPT = """{task}

Rules:
- Keep the module name, ports, and parameters exactly the same.
{rule}
- Reply with the full corrected file in a single ```verilog code block.

## Specification

{spec}

## {label}: {top}.v

```verilog
{buggy}
```
"""


def extract(answer, top):
    """Pull the corrected module out of the reply. Models often add extra code
    blocks (e.g. a testbench), so take the last block that defines `module <top>`."""
    blocks = re.findall(r"```[a-zA-Z]*\s*\n(.*?)```", answer, re.S)
    has_top = re.compile(rf"\bmodule\s+{re.escape(top)}\b")
    matches = [b for b in blocks if has_top.search(b)]
    if matches:
        return matches[-1]
    m = re.search(rf"\bmodule\s+{re.escape(top)}\b.*?\bendmodule\b", answer, re.S)   # no code fences
    return m.group(0) + "\n" if m else None


def build_prompt(task_dir):
    meta = json.loads((task_dir / "meta.json").read_text())
    task, rule, label = INSTRUCTIONS[meta.get("task_type", "bugfix")]
    prompt = PROMPT.format(task=task, rule=rule, label=label, spec=(task_dir / "spec.md").read_text().strip(),
                           top=meta["top"], buggy=(task_dir / "buggy.v").read_text().rstrip())
    return meta, prompt


def task_label(meta):
    return meta.get("label") or meta["bug"]["type"]


def run_task(task_dir, cfg):
    meta, prompt = build_prompt(task_dir)
    paid = is_paid(cfg["model"])
    t0 = time.time()
    try:
        if paid:
            answer, thinking, stats = ask_openai(cfg["model"].split(":", 1)[1], SYSTEM, prompt,
                                                 cfg["max_output"], cfg["effort"])
        else:
            on = cfg["think"] == "on"
            answer, thinking, stats = ask_ollama(OLLAMA_URL, cfg["model"], SYSTEM, prompt, cfg["think"],
                                                 16384 if on else 8192, 12288 if on else 4096)
    except Exception as e:
        row = {"task_id": task_dir.name, "status": "error", "reason": f"model request failed: {e}"}
        if paid:
            row["cost"] = worst(prompt, cfg)    # unknown usage: assume the worst
        return row
    seconds = round(time.time() - t0, 1)

    code = extract(answer, meta["top"])
    if code is None:
        g = {"status": "no_answer", "reason": "no code block defining the module", "passed": [], "failed": []}
    else:
        g = grade(task_dir, code)
    row = {"task_id": task_dir.name, "seed": meta["seed"], "task_type": meta.get("task_type", "bugfix"),
           "bug_type": task_label(meta),
           "status": g["status"], "reason": g["reason"], "failed_tests": g["failed"],
           "seconds": seconds, **stats, "code": code, "answer": answer, "thinking": thinking}
    if paid:
        row["cost"] = actual_cost(stats, cfg["price_in"], cfg["price_out"])
    return row


def worst(prompt, cfg):
    return worst_case_cost(SYSTEM, prompt, cfg["price_in"], cfg["price_out"], cfg["max_output"])


def safe_run_task(task_dir, cfg):
    """run_task, but never raises: a crash becomes an error row (charged at worst case if paid)."""
    try:
        return run_task(task_dir, cfg)
    except Exception as e:
        row = {"task_id": task_dir.name, "status": "error", "reason": f"crashed: {type(e).__name__}: {e}"}
        if is_paid(cfg["model"]):
            row["cost"] = worst(build_prompt(task_dir)[1], cfg)
        return row


def summarize(run_dir, rows, config, spent=None):
    n = len(rows)
    passed = sum(r["status"] == "pass" for r in rows)
    settings = (f"effort: {config['effort']}" if is_paid(config["model"]) else f"think: {config['think']}")
    lines = [f"model      {config['model']}  ({settings}, prompt {config['prompt_version']})",
             f"started    {config['started']}",
             f"pass@1     {passed}/{n} = {100 * passed / max(n, 1):.1f}%", ""]
    lines.append("outcomes   " + ", ".join(f"{k} {v}" for k, v in Counter(r["status"] for r in rows).most_common()))
    timed = [r for r in rows if "seconds" in r]
    if timed:
        lines.append(f"time       {sum(r['seconds'] for r in timed) / 60:.1f} min total, "
                     f"{sum(r['seconds'] for r in timed) / len(timed):.0f}s per task, "
                     f"{sum(r.get('output_tokens', 0) for r in timed) // len(timed)} output tokens per task")
        cut = sum(r.get("hit_token_limit", False) for r in timed)
        if cut:
            lines.append(f"WARNING    {cut} answers hit the token limit and were cut off")
    if spent is not None:
        lines.append(f"cost       ${spent:.4f} of ${config['budget']:.2f} budget "
                     f"(prices in/out per 1M: ${config['price_in']} / ${config['price_out']})")
    for key, title in (("task_type", "by task type"), ("bug_type", "by label"), ("seed", "by seed")):
        groups = defaultdict(list)
        for r in rows:
            groups[r.get(key, "?")].append(r["status"] == "pass")
        lines += ["", title]
        lines += [f"  {g:<14} {sum(v)}/{len(v)}" for g, v in sorted(groups.items())]
    text = "\n".join(lines) + "\n"
    (run_dir / "summary.txt").write_text(text)
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="Ollama model (qwen2.5-coder:7b) or openai:<model> (openai:gpt-5-mini)")
    ap.add_argument("--think", choices=["on", "off", "default"], default="off",
                    help="Ollama only: reasoning mode for models that have one (default: off)")
    ap.add_argument("--effort", choices=["minimal", "low", "medium", "high", "default"], default="medium",
                    help="OpenAI only: reasoning effort (default: medium; 'default' omits it)")
    ap.add_argument("--price-in", type=float, help="OpenAI: USD per 1M input tokens (from OpenAI's pricing page)")
    ap.add_argument("--price-out", type=float, help="OpenAI: USD per 1M output tokens (from OpenAI's pricing page)")
    ap.add_argument("--budget", type=float, help="OpenAI: hard spending cap in USD for this run (all resumes)")
    ap.add_argument("--max-output", type=int, default=8000, help="OpenAI: max output tokens per answer")
    ap.add_argument("--yes", action="store_true", help="OpenAI: actually send requests (otherwise estimate only)")
    ap.add_argument("--jobs", type=int, help="requests at once (default: 8 for OpenAI, 1 for local Ollama)")
    ap.add_argument("--limit", type=int, help="only the first N tasks (for quick checks)")
    ap.add_argument("--task-types", help="comma-separated: bugfix,multibug,complete,module (default: all)")
    ap.add_argument("--tasks", default=str(ROOT / "tasks"))
    ap.add_argument("--results-dir", default=str(ROOT / "results"))
    ap.add_argument("--resume", help="continue an interrupted run directory")
    args = ap.parse_args()

    if args.resume:
        run_dir = Path(args.resume)
        config = json.loads((run_dir / "config.json").read_text())
        config.setdefault("effort", "medium")
        config.setdefault("max_output", 8000)
        if args.budget is not None:            # allow raising (or lowering) the cap on resume
            config["budget"] = args.budget
    else:
        if not args.model:
            ap.error("--model is required")
        config = {"model": args.model, "think": args.think, "effort": args.effort,
                  "prompt_version": PROMPT_VERSION, "temperature": None if is_paid(args.model) else 0,
                  "started": datetime.now().isoformat(timespec="seconds"),
                  "tasks": args.tasks, "limit": args.limit, "task_types": args.task_types,
                  "max_output": args.max_output,
                  "price_in": args.price_in, "price_out": args.price_out, "budget": args.budget}
        run_dir = None

    paid = is_paid(config["model"])
    if paid:
        missing = [f for f, k in (("--price-in", "price_in"), ("--price-out", "price_out"), ("--budget", "budget"))
                   if config.get(k) is None]
        if missing:
            ap.error(f"paid model needs {', '.join(missing)} (copy prices from OpenAI's pricing page)")
        if config["budget"] <= 0 or config["price_in"] <= 0 or config["price_out"] <= 0:
            ap.error("--budget, --price-in and --price-out must be positive")
        if args.yes and not os.environ.get("OPENAI_API_KEY"):
            ap.error("OPENAI_API_KEY is not set (in Docker, add: -e OPENAI_API_KEY)")

    tasks = sorted(Path(config["tasks"]).glob("t_*")) or sorted(Path(config["tasks"]).glob("task_*"))
    if config.get("task_types"):
        wanted = set(config["task_types"].split(","))
        tasks = [t for t in tasks if json.loads((t / "meta.json").read_text()).get("task_type", "bugfix") in wanted]
    tasks = tasks[:config["limit"]]
    rows_on_disk = []
    if run_dir and (run_dir / "results.jsonl").exists():
        rows_on_disk = [json.loads(l) for l in (run_dir / "results.jsonl").read_text().splitlines()]
    done = {r["task_id"]: r for r in rows_on_disk if r["status"] != "error"}   # errors get retried
    spent = sum(r.get("cost", 0) for r in rows_on_disk)                      # ...but their cost still counts
    todo = [t for t in tasks if t.name not in done]

    if paid:
        worst_total = sum(worst(build_prompt(t)[1], config) for t in todo)
        est_in = sum(len(SYSTEM + build_prompt(t)[1]) / 4 for t in todo)
        typical = (est_in * config["price_in"] + len(todo) * 2000 * config["price_out"]) / 1e6
        print(f"{config['model']} (effort {config['effort']}): {len(todo)} tasks to run")
        print(f"  likely cost   ~${typical:.2f}  (guess: 2,000 output tokens per task incl. reasoning)")
        print(f"  worst case     ${worst_total:.2f}  (every answer uses all {config['max_output']} output tokens)")
        print(f"  budget         ${config['budget']:.2f}, already spent ${spent:.4f}")
        if worst_total + spent > config["budget"]:
            print(f"  -> the run will stop early if spending gets close to the budget")
        if not args.yes:
            print("\nEstimate only: nothing was sent. Re-run with --yes to start.")
            return

    if run_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        settings = f"effort-{config['effort']}" if paid else f"think-{config['think']}"
        base = Path(args.results_dir) / f"{re.sub(r'[^A-Za-z0-9.]+', '-', config['model'])}_{settings}_{stamp}"
        run_dir, n = base, 2
        while run_dir.exists():                 # two runs started in the same minute
            run_dir, n = base.with_name(f"{base.name}-{n}"), n + 1
        run_dir.mkdir(parents=True)
        (run_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    else:
        (run_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")

    jobs = args.jobs or (8 if paid else 1)
    print(f"{config['model']}: {len(todo)} to run, {len(done)} already done, {jobs} at a time -> {run_dir}",
          flush=True)
    budget = Budget(config["budget"], spent) if paid else None
    finished = [0]

    def reserve(task_dir):
        if not paid:
            return True, None
        w = worst(build_prompt(task_dir)[1], config)
        return (True, w) if budget.reserve(w) else (False, budget.why_not(w))

    with (run_dir / "results.jsonl").open("a") as out:
        def on_done(task_dir, r, w):
            if paid:
                budget.settle(w, r.get("cost", 0))
            if r["status"] != "error":
                done[r["task_id"]] = r
            out.write(json.dumps(r) + "\n")
            out.flush()
            finished[0] += 1
            score = sum(x["status"] == "pass" for x in done.values())
            cost = f"  ${budget.spent:.4f} spent" if paid else ""
            print(f"  [{finished[0]}/{len(todo)}] {r['task_id']} {r.get('bug_type', ''):<13} {r['status']:<13} "
                  f"{r.get('seconds', '-')}s  | running total {score}/{len(done)}{cost}", flush=True)
            if r["status"] == "error":
                print(f"      {r['reason'][:200]}", flush=True)
            return r["status"] == "error"

        stopped = run_pool(todo, lambda t: safe_run_task(t, config), jobs, reserve, on_done, MAX_ERRORS_IN_A_ROW)
    spent = budget.spent if paid else 0

    if stopped:
        print(f"\nSTOPPED: {stopped}. Resume later with --resume {run_dir}")
    rows = [done[t.name] for t in tasks if t.name in done]
    print("\n" + summarize(run_dir, rows, config, spent if paid else None))


if __name__ == "__main__":
    main()
