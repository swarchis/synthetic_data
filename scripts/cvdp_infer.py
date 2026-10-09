"""Answer CVDP's exported prompts with a model.

CVDP's local-inference flow is: export prompts -> answer them yourself ->
import the answers for grading. This script is the middle step. It sends
CVDP's own system and user messages unchanged, so the prompt is NVIDIA's,
not ours. Runs on the Mac host.

Local (Ollama on localhost, free):
    python3 scripts/cvdp_infer.py --prompts prompts.jsonl --out responses.jsonl \\
        --model qwen2.5-coder:7b [--limit 5]

Paid (OpenAI), with the same safeguards as run_model.py (see llm.py):
prices from OpenAI's pricing page and a budget are required, and nothing
is sent until --yes:
    python3 scripts/cvdp_infer.py --prompts prompts.jsonl --out responses.jsonl \\
        --model openai:gpt-5-mini --price-in 0.25 --price-out 2.00 --budget 5 [--yes]

Answers are appended as soon as each finishes, so re-running the same
command resumes where it stopped. responses.jsonl gets {"id", "completion"}
as CVDP expects, plus timing, tokens, and cost (CVDP ignores extra fields).
Failed requests go to <out>.errors.jsonl, not to CVDP; their cost (worst
case, when unknown) still counts toward the budget.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from llm import Budget, actual_cost, ask_ollama, ask_openai, is_paid, run_pool, worst_case_cost

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompts", required=True, help="prompts.jsonl from CVDP's local_export")
    ap.add_argument("--out", required=True, help="responses.jsonl for CVDP's local_import")
    ap.add_argument("--model", required=True, help="Ollama model (qwen2.5-coder:7b) or openai:<model>")
    ap.add_argument("--limit", type=int, help="only the first N prompts")
    ap.add_argument("--jobs", type=int, help="requests at once (default: 8 for OpenAI, 1 for local Ollama)")
    ap.add_argument("--num-ctx", type=int, default=16384, help="Ollama: context window (prompt + answer)")
    ap.add_argument("--num-predict", type=int, default=6000, help="Ollama: max answer tokens")
    ap.add_argument("--effort", choices=["minimal", "low", "medium", "high", "default"], default="medium",
                    help="OpenAI: reasoning effort")
    ap.add_argument("--price-in", type=float, help="OpenAI: USD per 1M input tokens")
    ap.add_argument("--price-out", type=float, help="OpenAI: USD per 1M output tokens")
    ap.add_argument("--budget", type=float, help="OpenAI: hard spending cap in USD, across resumes of this --out")
    ap.add_argument("--max-output", type=int, default=12000, help="OpenAI: max output tokens per answer")
    ap.add_argument("--yes", action="store_true", help="OpenAI: actually send requests (otherwise estimate only)")
    args = ap.parse_args()

    paid = is_paid(args.model)
    if paid:
        missing = [f for f, v in (("--price-in", args.price_in), ("--price-out", args.price_out),
                                  ("--budget", args.budget)) if v is None]
        if missing:
            ap.error(f"paid model needs {', '.join(missing)} (copy prices from OpenAI's pricing page)")
        if min(args.budget, args.price_in, args.price_out) <= 0:
            ap.error("--budget, --price-in and --price-out must be positive")
        if args.yes and not os.environ.get("OPENAI_API_KEY"):
            ap.error("OPENAI_API_KEY is not set")

    prompts = [json.loads(l) for l in Path(args.prompts).read_text().splitlines() if l.strip()][:args.limit]
    out, err_path = Path(args.out), Path(str(args.out) + ".errors.jsonl")
    old = [json.loads(l) for l in out.read_text().splitlines() if l.strip()] if out.exists() else []
    old_err = [json.loads(l) for l in err_path.read_text().splitlines() if l.strip()] if err_path.exists() else []
    done = {r["id"] for r in old}
    spent = sum(r.get("cost", 0) for r in old + old_err)
    todo = [p for p in prompts if p["id"] not in done]

    def user_of(p):
        return p.get("user") or p["prompt"]

    def worst(p):
        return worst_case_cost(p.get("system", ""), user_of(p), args.price_in, args.price_out, args.max_output)

    if paid:
        worst_total = sum(worst(p) for p in todo)
        typical = sum((len(p.get("system", "") + user_of(p)) / 4 * args.price_in + 3000 * args.price_out) / 1e6
                      for p in todo)
        print(f"{args.model} (effort {args.effort}): {len(todo)} prompts to answer")
        print(f"  likely cost   ~${typical:.2f}  (guess: 3,000 output tokens per answer incl. reasoning)")
        print(f"  worst case     ${worst_total:.2f}  (every answer uses all {args.max_output} output tokens)")
        print(f"  budget         ${args.budget:.2f}, already spent ${spent:.4f}")
        if worst_total + spent > args.budget:
            print("  -> the run will stop early if spending gets close to the budget")
        if not args.yes:
            print("\nEstimate only: nothing was sent. Re-run with --yes to start.")
            return

    jobs = args.jobs or (8 if paid else 1)
    print(f"{args.model}: {len(todo)} to answer, {len(done)} already done, {jobs} at a time -> {out}", flush=True)
    budget = Budget(args.budget, spent) if paid else None

    def work(p):
        t0 = time.time()
        try:
            if paid:
                answer, _, stats = ask_openai(args.model.split(":", 1)[1], p.get("system", ""), user_of(p),
                                              args.max_output, args.effort)
            else:
                answer, _, stats = ask_ollama(OLLAMA_URL, args.model, p.get("system", ""), user_of(p),
                                              "default", args.num_ctx, args.num_predict)
        except Exception as e:
            row = {"id": p["id"], "error": f"{type(e).__name__}: {e}"}
            if paid:
                row["cost"] = worst(p)                  # unknown usage: assume the worst
            return row
        row = {"id": p["id"], "completion": answer, "seconds": round(time.time() - t0, 1), **stats}
        if paid:
            row["cost"] = actual_cost(stats, args.price_in, args.price_out)
        return row

    def reserve(p):
        if not paid:
            return True, None
        w = worst(p)
        return (True, w) if budget.reserve(w) else (False, budget.why_not(w))

    finished = [0]
    with out.open("a") as f, err_path.open("a") as ef:
        def on_done(p, row, w):
            if paid:
                budget.settle(w, row.get("cost", 0))
            finished[0] += 1
            is_err = "error" in row
            (ef if is_err else f).write(json.dumps(row) + "\n")
            (ef if is_err else f).flush()
            if is_err:
                print(f"  [{finished[0]}/{len(todo)}] {p['id']:<45} ERROR {row['error'][:150]}", flush=True)
            else:
                cut = "  (CUT OFF at token limit)" if row["hit_token_limit"] else ""
                cost = f"  ${budget.spent:.4f} spent" if paid else ""
                print(f"  [{finished[0]}/{len(todo)}] {p['id']:<45} {row['seconds']:>6}s  "
                      f"{row['prompt_tokens']} in / {row['output_tokens']} out{cut}{cost}", flush=True)
            return is_err

        stopped = run_pool(todo, work, jobs, reserve, on_done)
    if err_path.exists() and err_path.stat().st_size == 0:
        err_path.unlink()
    if stopped:
        print(f"\nSTOPPED: {stopped}. Re-run the same command to resume.")
    answered = len(done) + finished[0] - (sum(1 for _ in open(err_path)) - len(old_err) if err_path.exists() else 0)
    print(f"\n{answered}/{len(prompts)} answered" + (f", ${budget.spent:.4f} spent of ${args.budget:.2f}" if paid else ""))


if __name__ == "__main__":
    main()
