"""Turn seed designs into bug-fix tasks.

    python3 scripts/build_tasks.py [seed ...] [--cap 10] [--jobs 8]

For each seed in designs/ (or just the ones named):
  1. the golden design must pass its own tests, or the seed is skipped
  2. inject.py makes every single-bug variant
  3. sim.py runs the tests on each; only bugs the tests catch are kept
  4. at most --cap tasks per bug type per seed, spread across the file
  5. each kept bug is written to tasks/task_NNNN/

tasks/ is wiped first, so it always matches the current seeds and code.
Task folders hold hidden files (golden.v, tests/): a harness must only
show the model spec.md and buggy.v.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from inject import mutate
from sim import run_tests

ROOT = Path(__file__).resolve().parent.parent
DESIGNS = ROOT / "designs"
TASKS = ROOT / "tasks"
GENERATOR = "inject.py v0"


def load_seed(seed_dir):
    meta = json.loads((seed_dir / "meta.json").read_text())
    design = seed_dir / f"{meta['top']}.v"
    return meta, design


def test_source(source, seed_dir):
    """Run the seed's tests on Verilog text."""
    with tempfile.NamedTemporaryFile("w", suffix=".v", delete=False) as f:
        f.write(source)
    try:
        return run_tests(f.name, seed_dir, timeout=30)
    finally:
        os.unlink(f.name)


def spread(items, n):
    """Pick n items evenly across the list, so capped tasks don't all come from one line."""
    if len(items) <= n:
        return items
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)] if n > 1 else items[:1]


def build_seed(seed_dir, cap, jobs):
    meta, design = load_seed(seed_dir)
    row = Counter(seed=seed_dir.name)
    golden = design.read_text()

    r = run_tests(design, seed_dir)
    if r["status"] != "pass":
        print(f"  SKIP {seed_dir.name}: golden design {r['status']} {r['failed']}")
        return row, [], []

    mutants, row["duplicates"] = mutate(golden)
    row["generated"] = len(mutants)
    with ThreadPoolExecutor(jobs) as pool:
        results = list(pool.map(lambda m: test_source(m["source"], seed_dir), mutants))

    caught, survivors = defaultdict(list), []
    for m, r in zip(mutants, results):
        row[r["status"]] += 1
        if r["status"] == "fail":
            caught[m["op"]].append((m, r["failed"]))
        elif r["status"] == "pass":
            survivors.append(m)

    kept = []
    for op in sorted(caught):
        chosen = spread(caught[op], cap)
        row["capped"] += len(caught[op]) - len(chosen)
        kept += chosen
    kept.sort(key=lambda k: (k[0]["line"], k[0]["op"]))
    row["kept"] = len(kept)
    return row, [(seed_dir, meta, golden, m, failed) for m, failed in kept], survivors


def write_task(task_dir, seed_dir, meta, golden, m, failed):
    task_dir.mkdir(parents=True)
    shutil.copy(seed_dir / "spec.md", task_dir / "spec.md")
    (task_dir / "buggy.v").write_text(m["source"])
    (task_dir / "golden.v").write_text(golden)
    tests = task_dir / "tests"
    tests.mkdir()
    shutil.copy(seed_dir / "Makefile", tests)
    for f in seed_dir.glob("test_*.py"):
        shutil.copy(f, tests)
    (task_dir / "meta.json").write_text(json.dumps({
        "task_id": task_dir.name,
        "seed": seed_dir.name,
        "top": meta["top"],
        "bug": {"type": m["op"], "line": m["line"], "before": m["before"], "after": m["after"]},
        "failing_tests": failed,
        "source": meta["source"],
        "license": meta["license"],
        "generator": GENERATOR,
    }, indent=2) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("seeds", nargs="*", help="seed folder names (default: all in designs/)")
    ap.add_argument("--cap", type=int, default=10, help="max tasks per bug type per seed")
    ap.add_argument("--jobs", type=int, default=os.cpu_count(), help="simulations run at once")
    args = ap.parse_args()

    seed_dirs = [DESIGNS / s for s in args.seeds] if args.seeds else \
        sorted(p.parent for p in DESIGNS.glob("*/meta.json"))

    if TASKS.exists():
        shutil.rmtree(TASKS)
    TASKS.mkdir()

    rows, all_tasks, survivor_lines = [], [], []
    for seed_dir in seed_dirs:
        print(f"building {seed_dir.name} ...")
        row, tasks, survivors = build_seed(seed_dir, args.cap, args.jobs)
        rows.append(row)
        all_tasks += tasks
        survivor_lines += [f"{seed_dir.name}  {m['op']:<13} L{m['line']:<3} {m['before']}\n"
                           f"{'':22}-> {m['after']}" for m in survivors]

    for n, task in enumerate(all_tasks, 1):
        write_task(TASKS / f"task_{n:04d}", *task)

    (TASKS / "survivors.txt").write_text(
        "# Bugs the tests did NOT catch. Each is either harmless (no real behavior change)\n"
        "# or a blind spot in the seed's tests that should be fixed.\n\n"
        + ("\n".join(survivor_lines) or "(none)") + "\n")

    cols = ["generated", "duplicates", "compile_error", "timeout", "pass", "capped", "kept"]
    names = ["generated", "dupes", "no_compile", "hang", "survived", "capped", "kept"]
    table = [f"{'seed':<10}" + "".join(f"{n:>11}" for n in names)]
    total = {"seed": "TOTAL", **{c: sum(r[c] for r in rows) for c in cols}}
    for row in rows + [total]:
        table.append(f"{row['seed']:<10}" + "".join(f"{row[c]:>11}" for c in cols))
    by_type = Counter(json.loads((TASKS / f"task_{n:04d}" / "meta.json").read_text())["bug"]["type"]
                      for n in range(1, len(all_tasks) + 1))
    table += ["", "kept by bug type: " + ", ".join(f"{k} {v}" for k, v in sorted(by_type.items()))]
    report = "\n".join(table) + "\n"
    (TASKS / "summary.txt").write_text(report)
    print("\n" + report + f"\nwrote {len(all_tasks)} tasks to tasks/")


if __name__ == "__main__":
    main()
