"""Build the hard task set from subtle bugs (inject_subtle.py), plus clean decoys.

    python3 scripts/build_hard.py [seed ...] [--cap 10] [--multi 10] [--decoys 8] [--jobs 8]

Default seeds: ef_tmr32, ef_uart, apb_intc (the small seeds are solved ~99%).
Writes tasks_hard/ (wiped first); tasks/ is never touched.

Every task uses the "review" prompt: the design may have several bugs or none,
with no count given. Task types:
  bugfix    one subtle bug
  multibug  2 or 3 subtle bugs, each caught by the hidden checks on its own
  clean     a decoy with no behavior change: the golden design, or a subtle edit
            the hidden checks prove harmless. A pass means the model did not
            break working code.

Same checks as build_tasks.py: golden must pass, and a bug is kept only if the
hidden checks catch it. Each kept bug task also records whether the agent's
visible checks catch it (visible_catches in meta.json): bugs only the hidden
checks catch are the hardest to find by testing.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_tasks import bug_info, load_seed, multibug_candidates, spread, test_source, write_task
from env import visible_check
from inject import module_spans
from inject_subtle import mutate_subtle
from sim import run_tests

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = "build_hard.py v1"
DEFAULT_SEEDS = ["ef_tmr32", "ef_uart", "apb_intc"]


def build_seed(seed_dir, args):
    meta, design = load_seed(seed_dir)
    seed, golden = seed_dir.name, design.read_text()
    row = Counter(seed=seed)
    r = run_tests(design, seed_dir, golden=design)
    if r["status"] != "pass":
        print(f"  SKIP {seed}: golden design {r['status']} {r['failed']}")
        return row, []

    def run_all(sources):
        with ThreadPoolExecutor(args.jobs) as pool:
            return list(pool.map(lambda s: test_source(s, seed_dir, design), sources))

    mutants, _ = mutate_subtle(golden)
    by_op = defaultdict(list)
    for m in mutants:
        by_op[m["op"]].append(m)
    per_op = meta.get("build", {}).get("presample_per_op", args.presample)
    mutants = [m for op in sorted(by_op) for m in spread(by_op[op], per_op)]
    row["generated"] = len(mutants)
    print(f"  {seed}: simulating {len(mutants)} subtle bugs ...", flush=True)

    caught, harmless = defaultdict(list), []
    for m, r in zip(mutants, run_all([m["source"] for m in mutants])):
        row[r["status"]] += 1
        if r["status"] == "fail":
            caught[m["op"]].append((m, r["failed"]))
        elif r["status"] == "pass":
            harmless.append(m)

    tasks = []
    for op in sorted(caught):
        for m, failed in spread(caught[op], args.cap):
            tasks.append({"task_type": "bugfix", "label": m["op"], "source": m["source"],
                          "bug": bug_info(m), "failing_tests": failed})

    module_of = None
    if meta.get("build", {}).get("multi_cross_module"):
        spans = module_spans(golden)
        module_of = lambda m: next(i for i, (a, b) in enumerate(spans) if a <= m["edit"][0] < b)
    all_caught = [m for ms in caught.values() for m, _ in ms]
    for k in (2, 3):
        cands = multibug_candidates(f"{seed}-subtle", golden, all_caught, k, args.multi, module_of)
        kept = 0
        for c, r in zip(cands, run_all([c["source"] for c in cands])):
            if r["status"] == "fail" and kept < args.multi:
                kept += 1
                tasks.append({"task_type": "multibug", "label": f"subtle{k}", "source": c["source"],
                              "bugs": [bug_info(m) for m in c["bugs"]], "failing_tests": r["failed"]})
        row[f"subtle{k}"] = kept

    # Decoys: the golden design itself, plus harmless edits (spread over bug kinds).
    decoys = [{"task_type": "clean", "label": "clean_golden", "source": golden, "decoy": "golden",
               "failing_tests": []}]
    by_kind = defaultdict(list)
    for m in harmless:
        by_kind[m["op"]].append(m)
    picks = [m for i in range(args.decoys) for op in sorted(by_kind) if i < len(by_kind[op]) for m in [by_kind[op][i]]]
    for m in picks[:args.decoys - 1]:
        decoys.append({"task_type": "clean", "label": "clean_edit", "source": m["source"],
                       "decoy": f"harmless {m['op']}: L{m['line']} {m['before']} -> {m['after']}",
                       "failing_tests": []})
    tasks += decoys
    row["clean"] = len(decoys)
    row["bugfix"] = sum(t["task_type"] == "bugfix" for t in tasks)

    for t in tasks:
        t.update(seed_dir=seed_dir, meta=meta, golden=golden, prompt="review", generator=GENERATOR)
        digest = hashlib.sha1(f"hard\0{seed}\0{t['task_type']}\0{t['source']}".encode()).hexdigest()
        t["task_id"] = f"t_{digest[:10]}"     # t_ like tasks/, so every runner finds them; the hash differs
    return row, tasks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("seeds", nargs="*", default=DEFAULT_SEEDS)
    ap.add_argument("--cap", type=int, default=10, help="max single-bug tasks per bug kind per seed")
    ap.add_argument("--multi", type=int, default=10, help="max 2-bug and max 3-bug tasks per seed")
    ap.add_argument("--decoys", type=int, default=8, help="clean decoys per seed (golden + harmless edits)")
    ap.add_argument("--presample", type=int, default=25, help="max candidates per bug kind to simulate")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--out", default=str(ROOT / "tasks_hard"))
    args = ap.parse_args()

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()

    rows, all_tasks = [], []
    for s in args.seeds:
        print(f"building {s} ...", flush=True)
        row, tasks = build_seed(ROOT / "designs" / s, args)
        rows.append(row)
        all_tasks += tasks
    ids = Counter(t["task_id"] for t in all_tasks)
    assert max(ids.values()) == 1, f"duplicate task ids: {[i for i, c in ids.items() if c > 1]}"
    for t in all_tasks:
        write_task(t, out)

    # Which bugs the agent's own visible checks already reveal.
    bugs = [t for t in all_tasks if t["task_type"] != "clean"]
    print(f"visible checks on {len(bugs)} bug tasks ...", flush=True)
    with ThreadPoolExecutor(args.jobs) as pool:
        vis = list(pool.map(lambda t: visible_check(out / t["task_id"], t["source"])["status"] != "pass", bugs))
    for t, v in zip(bugs, vis):
        p = out / t["task_id"] / "meta.json"
        m = json.loads(p.read_text())
        m["visible_catches"] = v
        p.write_text(json.dumps(m, indent=2) + "\n")
        t["visible_catches"] = v

    lines = [f"{'seed':<10}{'simulated':>10}{'caught':>8}{'harmless':>9}{'bugfix':>8}{'subtle2':>8}{'subtle3':>8}{'clean':>7}"]
    for r in rows:
        lines.append(f"{r['seed']:<10}{r['generated']:>10}{r['fail']:>8}{r['pass']:>9}{r['bugfix']:>8}"
                     f"{r['subtle2']:>8}{r['subtle3']:>8}{r['clean']:>7}")
    lines.append("")
    by_label = defaultdict(lambda: [0, 0])
    for t in bugs:
        by_label[t["label"]][0] += 1
        by_label[t["label"]][1] += t["visible_catches"]
    lines.append("bug tasks by label (visible checks catch / total):")
    lines += [f"  {k:<16} {v[1]:>3}/{v[0]}" for k, v in sorted(by_label.items())]
    hidden_only = sum(not t["visible_catches"] for t in bugs)
    lines.append(f"\n{len(all_tasks)} tasks; {hidden_only}/{len(bugs)} bug tasks caught only by the hidden checks")
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
