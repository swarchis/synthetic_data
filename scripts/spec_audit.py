"""Find likely spec gaps from failed model answers.

    docker run --rm -v "$(pwd)":/work -w /work rtl-env \\
        python3 scripts/spec_audit.py --seed ef_uart results/<run_dir> [results/<run_dir> ...]

For every failed answer on the seed (one-shot results.jsonl or agent episodes.jsonl),
each module of the answer is put into the golden design on its own, and the result is
run through the seed's differential check. A module whose answer version breaks the
golden design behaves differently from golden. If the task injected no bug in that
module, the answer changed correct behavior there: an "extra" change.

When many independent answers change the same correct module, and fail the same way,
the spec probably does not pin that behavior down (or states it wrongly). A human then
reads those modules against the golden code. The report lists, per module, how many
answers broke it and their first mismatches (which output, grouped).
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from diffcheck import diff_check, first_mismatch
from inject import module_spans

ROOT = Path(__file__).resolve().parent.parent


def modules(src):
    out = {}
    for s, e in module_spans(src):
        m = re.match(r"\s*module\s+(\w+)", src[s:e])
        if m:
            out[m.group(1)] = (s, e)
    return out


def load_failed(run_dirs, seed):
    rows = {}
    for d in map(Path, run_dirs):
        f = d / "results.jsonl" if (d / "results.jsonl").exists() else d / "episodes.jsonl"
        for l in f.read_text().splitlines():
            r = json.loads(l)
            if r.get("seed") == seed:
                rows[(d.name, r["task_id"])] = r            # last row per task wins (resumes)
    return [(k, r, r.get("code") or r.get("final_code")) for k, r in sorted(rows.items())
            if r["status"] in ("fail", "timeout") and (r.get("code") or r.get("final_code"))]


def graft_check(answer, golden, cfg_dir, name, gm):
    am = modules(answer)
    if name not in am:
        return "missing", ""
    s, e = am[name]
    gs, ge = gm[name]
    if answer[s:e].split() == golden[gs:ge].split():
        return "same", ""
    trial = golden[:gs] + answer[s:e] + golden[ge:]
    d = diff_check(trial, golden, cfg_dir, timeout=120)
    return d["status"], first_mismatch(d["log"]) if d["status"] == "fail" else ""


def audit(seed, run_dirs, tasks_dir, jobs, out=None):
    cfg_dir = ROOT / "designs" / seed
    failed = load_failed(run_dirs, seed)
    work = []
    golden = None
    for (run, tid), r, code in failed:
        t = Path(tasks_dir) / tid
        if not t.exists():
            continue
        golden = golden or (t / "golden.v").read_text()
        buggy = (t / "buggy.v").read_text()
        gm, bm = modules(golden), modules(buggy)
        for name in gm:
            # Did the task touch this module (injected bug or removed logic)?
            touched = name not in bm or buggy[slice(*bm[name])].split() != golden[slice(*gm[name])].split()
            work.append((f"{tid}@{run[-4:]}", name, touched, code, gm))

    def one(w):
        ans_id, name, touched, code, gm = w
        status, why = graft_check(code, golden, cfg_dir, name, gm)
        return ans_id, name, touched, status, why

    results = list(ThreadPoolExecutor(jobs).map(one, work))
    answers = sorted({a for a, *_ in results})
    print(f"== {seed}: {len(answers)} failed answers, {len(work)} module checks\n")

    extra, bugmod = defaultdict(list), Counter()
    for ans_id, name, touched, status, why in results:
        if status in ("pass", "same", "missing"):
            continue
        if touched:
            bugmod[name] += 1
        else:
            extra[name].append((ans_id, status, why))
    print("Modules where answers changed CORRECT code and broke it (likely spec gaps or model errors):")
    for name, hits in sorted(extra.items(), key=lambda kv: -len(kv[1])):
        outs = Counter(re.sub(r"run \d+ cycle \d+: output (\w+).*", r"\1", w) if w else s for _, s, w in hits)
        print(f"  {name:24} {len(hits):3} answers   first mismatch on: {dict(outs.most_common(4))}")
    print("\nModules with the task's own bug that the answer did not get right:")
    for name, n in bugmod.most_common():
        print(f"  {name:24} {n:3} answers")
    none = [a for a in answers if not any(x[0] == a and x[3] not in ("pass", "same", "missing") for x in results)]
    print(f"\n{len(none)} answers fail only in combination (no single module breaks golden on its own)")
    if out:
        Path(out).write_text(json.dumps([dict(zip(("answer", "module", "touched", "status", "why"), x))
                                         for x in results]))
        print(f"details: {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="result directories (one-shot or agent)")
    ap.add_argument("--seed", required=True)
    ap.add_argument("--tasks", default=str(ROOT / "tasks"))
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--out", help="write every module check to this JSON file")
    a = ap.parse_args()
    audit(a.seed, a.runs, a.tasks, a.jobs, a.out)


if __name__ == "__main__":
    main()
