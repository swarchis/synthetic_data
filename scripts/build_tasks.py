"""Turn seed designs into tasks.

    python3 scripts/build_tasks.py [seed ...] [--cap 10] [--multi 10] [--holes 4] [--jobs 8]

Task types, all graded by the seed's hidden tests:
  bugfix    one injected bug (inject.py)
  multibug  2 or 3 injected bugs at once, each one caught by the tests on
            its own, so fixing only one of them still fails
  complete  a chunk of logic removed and marked TODO (holes.py)
  module    only the module header left; write the whole body

For each seed in designs/ (or just the ones named):
  1. the golden design must pass its own tests, or the seed is skipped
  2. make candidates of every type and run the tests on each
  3. keep only candidates the tests catch (a bug or hole that still passes
     means harmless or redundant code; those are logged in survivors.txt)
  4. cap per seed: --cap per bug type, --multi pairs and --multi triples,
     --holes per hole kind
  5. write each task to tasks/t_<id>/, where the id is a hash of the task's
     contents, so the same task keeps the same id across rebuilds

tasks/ is wiped first, so it always matches the current seeds and code.
Task folders hold hidden files (golden.v, tests/): a harness must only
show the model spec.md and buggy.v.
"""

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from holes import holes
from inject import combine, module_spans, mutate
from sim import run_tests

ROOT = Path(__file__).resolve().parent.parent
DESIGNS = ROOT / "designs"
TASKS = ROOT / "tasks"
GENERATOR = "build_tasks.py v1"


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


def bug_info(m):
    return {"type": m["op"], "line": m["line"], "before": m["before"], "after": m["after"]}


def multibug_candidates(seed, golden, caught, k, n, module_of=None):
    """Up to 2n combinations of k caught bugs: different lines, preferably
    different bug types and at least 3 lines apart. Deterministic per seed.
    With module_of (multi-module seeds), bugs must span at least min(k, 3)
    modules while strict, and at least 2 always."""
    rng = random.Random(f"{seed}-multi-{k}")
    pool = sorted(caught, key=lambda m: (m["line"], m["op"], m["after"]))
    out, seen = [], set()
    attempts, strict_until = (800, 600) if module_of else (400, 300)
    for attempt in range(attempts):
        if len(out) >= 2 * n or len(pool) < k:
            break
        pick = sorted(rng.sample(pool, k), key=lambda m: m["line"])
        lines = [m["line"] for m in pick]
        if len(set(lines)) < k:
            continue
        strict = attempt < strict_until      # relax the spacing rules if we run short
        if strict and (min(b - a for a, b in zip(lines, lines[1:])) < 3 or len({m["op"] for m in pick}) < k):
            continue
        if module_of:
            spread_over = len({module_of(m) for m in pick})
            if spread_over < 2 or (strict and spread_over < min(k, 3)):
                continue
        key = tuple((m["line"], m["after"]) for m in pick)
        if key in seen:
            continue
        src = combine(golden, pick)
        if src is None or src == golden:
            continue
        seen.add(key)
        out.append({"source": src, "bugs": pick, "k": k})
    return out


def build_seed(seed_dir, args):
    meta, design = load_seed(seed_dir)
    seed = seed_dir.name
    row = Counter(seed=seed)
    golden = design.read_text()

    r = run_tests(design, seed_dir)
    if r["status"] != "pass":
        print(f"  SKIP {seed}: golden design {r['status']} {r['failed']}")
        return row, [], []

    def run_all(sources):
        with ThreadPoolExecutor(args.jobs) as pool:
            return list(pool.map(lambda s: test_source(s, seed_dir), sources))

    # Single bugs.
    mutants, row["duplicates"] = mutate(golden)
    # Big designs make hundreds of candidates, each run at several parameter
    # settings; a seed can ask to sample at most N per bug type before simulating.
    presample = meta.get("build", {}).get("presample_per_op")
    if presample:
        by_op = defaultdict(list)
        for m in mutants:
            by_op[m["op"]].append(m)
        mutants = [m for op in sorted(by_op) for m in spread(by_op[op], presample)]
    row["generated"] = len(mutants)
    caught, survivors = defaultdict(list), []
    for m, r in zip(mutants, run_all([m["source"] for m in mutants])):
        row[r["status"]] += 1
        if r["status"] == "fail":
            caught[m["op"]].append((m, r["failed"]))
        elif r["status"] == "pass":
            survivors.append(("bug", m["op"], m["line"], m["before"], m["after"]))

    tasks = []
    for op in sorted(caught):
        chosen = spread(caught[op], args.cap)
        row["capped"] += len(caught[op]) - len(chosen)
        for m, failed in chosen:
            tasks.append({"task_type": "bugfix", "label": m["op"], "source": m["source"],
                          "bug": bug_info(m), "failing_tests": failed})

    # Multiple bugs, built only from bugs the tests catch on their own.
    # A seed's meta.json "build" section can change sizes, counts, and spread.
    build = meta.get("build", {})
    multi_k = build.get("multi_k", [2, 3])
    multi_n = build.get("multi_per_k", args.multi)
    module_of = None
    if build.get("multi_cross_module"):
        spans = module_spans(golden)
        module_of = lambda m: next(i for i, (a, b) in enumerate(spans) if a <= m["edit"][0] < b)
    all_caught = [m for ms in caught.values() for m, _ in ms]
    for k in multi_k:
        cands = multibug_candidates(seed, golden, all_caught, k, multi_n, module_of)
        kept = 0
        for c, r in zip(cands, run_all([c["source"] for c in cands])):
            if r["status"] == "fail" and kept < multi_n:
                kept += 1
                tasks.append({"task_type": "multibug", "label": f"multi{k}", "source": c["source"],
                              "bugs": [bug_info(m) for m in c["bugs"]], "failing_tests": r["failed"]})
            elif r["status"] == "pass":
                survivors.append((f"multi{k}", "cancelled", c["bugs"][0]["line"], "bugs cancel out:",
                                  " + ".join(f"L{m['line']} {m['after']}" for m in c["bugs"])))
        row[f"multi{k}"] = kept

    # Holes: missing logic to write.
    by_kind = defaultdict(list)
    hs = holes(golden)
    for h, r in zip(hs, run_all([h["source"] for h in hs])):
        if r["status"] == "pass":
            survivors.append(("hole", h["kind"], h["line"], "removing this changed nothing:",
                              h["removed"].strip().splitlines()[0]))
        else:
            by_kind[h["kind"]].append((h, r))
    for kind in sorted(by_kind):
        for h, r in spread(by_kind[kind], build.get("holes_per_kind", args.holes)):
            ttype = "module" if kind == "module" else "complete"
            tasks.append({"task_type": ttype, "label": f"hole_{kind}", "source": h["source"],
                          "hole": {"kind": kind, "lines": [h["line"], h["end_line"]], "removed": h["removed"]},
                          "failing_tests": r["failed"]})
            row["complete" if ttype == "complete" else "module"] += 1

    row["kept"] = len(tasks)
    for t in tasks:
        t.update(seed_dir=seed_dir, meta=meta, golden=golden)
        digest = hashlib.sha1(f"{seed}\0{t['task_type']}\0{t['source']}".encode()).hexdigest()
        t["task_id"] = f"t_{digest[:10]}"
    return row, tasks, survivors


def write_task(t):
    task_dir = TASKS / t["task_id"]
    task_dir.mkdir(parents=True)
    seed_dir, meta = t["seed_dir"], t["meta"]
    shutil.copy(seed_dir / "spec.md", task_dir / "spec.md")
    (task_dir / "buggy.v").write_text(t["source"])
    (task_dir / "golden.v").write_text(t["golden"])
    tests = task_dir / "tests"
    tests.mkdir()
    shutil.copy(seed_dir / "Makefile", tests)
    for f in seed_dir.glob("test_*.py"):
        shutil.copy(f, tests)
    if (seed_dir / "configs.json").exists():            # parameter sweep for sim.py
        shutil.copy(seed_dir / "configs.json", tests)
    info = {"task_id": t["task_id"], "task_type": t["task_type"], "label": t["label"],
            "seed": seed_dir.name, "top": meta["top"]}
    for key in ("bug", "bugs", "hole"):
        if key in t:
            info[key] = t[key]
    info.update(failing_tests=t["failing_tests"], source=meta["source"], license=meta["license"],
                generator=GENERATOR)
    (task_dir / "meta.json").write_text(json.dumps(info, indent=2) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("seeds", nargs="*", help="seed folder names (default: all in designs/)")
    ap.add_argument("--cap", type=int, default=10, help="max bugfix tasks per bug type per seed")
    ap.add_argument("--multi", type=int, default=10, help="max 2-bug and max 3-bug tasks per seed")
    ap.add_argument("--holes", type=int, default=4, help="max completion tasks per hole kind per seed")
    ap.add_argument("--jobs", type=int, default=os.cpu_count(), help="simulations run at once")
    args = ap.parse_args()

    seed_dirs = [DESIGNS / s for s in args.seeds] if args.seeds else \
        sorted(p.parent for p in DESIGNS.glob("*/meta.json"))

    if TASKS.exists():
        shutil.rmtree(TASKS)
    TASKS.mkdir()

    rows, all_tasks, survivor_lines = [], [], []
    for seed_dir in seed_dirs:
        print(f"building {seed_dir.name} ...", flush=True)
        row, tasks, survivors = build_seed(seed_dir, args)
        rows.append(row)
        all_tasks += tasks
        survivor_lines += [f"{seed_dir.name:<17} {what:<7} {kind:<13} L{line:<3} {a}\n{'':22}-> {b}"
                           for what, kind, line, a, b in survivors]

    ids = Counter(t["task_id"] for t in all_tasks)
    assert max(ids.values()) == 1, f"duplicate task ids: {[i for i, c in ids.items() if c > 1]}"
    for t in all_tasks:
        write_task(t)

    (TASKS / "survivors.txt").write_text(
        "# Candidates the tests did NOT catch. Each is either harmless (no real behavior\n"
        "# change, redundant code, or bugs that cancel) or a blind spot in the seed's tests.\n\n"
        + ("\n".join(survivor_lines) or "(none)") + "\n")

    cols = ["generated", "compile_error", "timeout", "pass", "capped", "kept"]
    names = ["bugs made", "no_compile", "hang", "survived", "capped", "bugfix"]
    extra = ["multi2", "multi3", "multi4+", "complete", "module"]
    for r in rows:
        r["multi4+"] = sum(v for k, v in r.items() if k.startswith("multi") and k[5:].isdigit() and int(k[5:]) >= 4)
    w = max([len("seed")] + [len(r["seed"]) for r in rows]) + 2
    table = [f"{'seed':<{w}}" + "".join(f"{n:>11}" for n in names + extra + ["total"])]
    for r in rows:
        r["bugfix"] = r["kept"] - sum(r[e] for e in extra)
    total = {"seed": "TOTAL", **{c: sum(r[c] for r in rows) for c in cols + extra + ["bugfix"]}}
    for r in rows + [total]:
        vals = [r[c] for c in cols[:-1]] + [r["bugfix"]] + [r[e] for e in extra] + [r["bugfix"] + sum(r[e] for e in extra)]
        table.append(f"{r['seed']:<{w}}" + "".join(f"{v:>11}" for v in vals))
    by_type = Counter(t["task_type"] for t in all_tasks)
    by_label = Counter(t["label"] for t in all_tasks)
    table += ["", "by task type:  " + ", ".join(f"{k} {v}" for k, v in sorted(by_type.items())),
              "by label:      " + ", ".join(f"{k} {v}" for k, v in sorted(by_label.items()))]
    report = "\n".join(table) + "\n"
    (TASKS / "summary.txt").write_text(report)
    print("\n" + report + f"\nwrote {len(all_tasks)} tasks to tasks/")


if __name__ == "__main__":
    main()
