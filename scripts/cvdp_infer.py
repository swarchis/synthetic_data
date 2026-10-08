"""Answer CVDP's exported prompts with a local Ollama model.

CVDP's local-inference flow is: export prompts -> answer them yourself ->
import the answers for grading. This script is the middle step. It sends
CVDP's own system and user messages unchanged, so the prompt is NVIDIA's,
not ours. Runs on the Mac host (talks to Ollama on localhost).

    python3 scripts/cvdp_infer.py --prompts prompts.jsonl --out responses.jsonl \\
        --model qwen2.5-coder:7b [--limit 5]

Answers are appended as soon as each finishes, so re-running the same
command resumes where it stopped. Output lines: {"id", "completion"} as CVDP
expects, plus timing and token counts (CVDP ignores extra fields).
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")


def ask(model, system, user, num_ctx, num_predict):
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    body = {"model": model, "messages": messages, "stream": False,
            "options": {"temperature": 0, "seed": 0, "num_ctx": num_ctx, "num_predict": num_predict}}
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as resp:
        return json.load(resp)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompts", required=True, help="prompts.jsonl from CVDP's local_export")
    ap.add_argument("--out", required=True, help="responses.jsonl for CVDP's local_import")
    ap.add_argument("--model", required=True, help="Ollama model, e.g. qwen2.5-coder:7b")
    ap.add_argument("--limit", type=int, help="only the first N prompts")
    ap.add_argument("--num-ctx", type=int, default=16384, help="context window (prompt + answer)")
    ap.add_argument("--num-predict", type=int, default=6000, help="max answer tokens")
    args = ap.parse_args()

    prompts = [json.loads(l) for l in Path(args.prompts).read_text().splitlines() if l.strip()][:args.limit]
    out = Path(args.out)
    done = set()
    if out.exists():
        done = {json.loads(l)["id"] for l in out.read_text().splitlines() if l.strip()}
    todo = [p for p in prompts if p["id"] not in done]
    print(f"{args.model}: {len(todo)} to answer, {len(done)} already done -> {out}")

    with out.open("a") as f:
        for k, p in enumerate(todo, 1):
            t0 = time.time()
            r = ask(args.model, p.get("system", ""), p.get("user") or p["prompt"], args.num_ctx, args.num_predict)
            row = {"id": p["id"], "completion": r.get("message", {}).get("content", ""),
                   "seconds": round(time.time() - t0, 1), "prompt_tokens": r.get("prompt_eval_count", 0),
                   "output_tokens": r.get("eval_count", 0), "hit_token_limit": r.get("done_reason") == "length"}
            f.write(json.dumps(row) + "\n")
            f.flush()
            cut = "  (CUT OFF at token limit)" if row["hit_token_limit"] else ""
            print(f"  [{k}/{len(todo)}] {p['id']:<45} {row['seconds']:>6}s  "
                  f"{row['prompt_tokens']} in / {row['output_tokens']} out{cut}")


if __name__ == "__main__":
    main()
