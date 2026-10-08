"""Run a model on the bug-fix tasks and grade every answer.

    python3 scripts/run_model.py --model qwen2.5-coder:7b
    python3 scripts/run_model.py --model qwen3.5:9b --think on --limit 5
    python3 scripts/run_model.py --resume results/<run_dir>

The model sees only spec.md and buggy.v. One attempt per task, temperature 0
(pass@1). Talks to Ollama (default http://host.docker.internal:11434, which
is the Mac host as seen from inside Docker; override with OLLAMA_URL).

Each run writes results/<model>_<think>_<time>/:
  config.json     model and settings
  results.jsonl   one line per task: grade, timing, full response
  summary.txt     pass rates overall, by bug type, by seed
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from grade import grade

ROOT = Path(__file__).resolve().parent.parent
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
PROMPT_VERSION = "v1"

SYSTEM = "You are an expert digital design and verification engineer."

PROMPT = """The Verilog module below does not match its specification. It contains exactly one bug.
Find the bug and return the complete corrected file.

Rules:
- Keep the module name, ports, and parameters exactly the same.
- Change only what is needed to fix the bug.
- Reply with the full corrected file in a single ```verilog code block.

## Specification

{spec}

## Buggy file: {top}.v

```verilog
{buggy}
```
"""


def ask(model, prompt, think):
    """Send one chat request to Ollama. Returns (answer, thinking, stats)."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0, "seed": 0, "num_ctx": 16384 if think == "on" else 8192,
                    "num_predict": 12288 if think == "on" else 4096},
    }
    if think != "default":
        body["think"] = think == "on"
    try:
        r = post(body)
    except urllib.error.HTTPError as e:
        # Models without a thinking mode reject the `think` field; retry without it.
        if "think" in body and "think" in e.read().decode(errors="replace").lower():
            body.pop("think")
            r = post(body)
        else:
            raise
    msg = r.get("message", {})
    answer = msg.get("content", "")
    thinking = msg.get("thinking", "")
    # Some models put their reasoning inline instead of in a separate field.
    inline = re.findall(r"<think>(.*?)</think>", answer, re.S)
    if inline:
        thinking += "\n".join(inline)
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S)
    stats = {"prompt_tokens": r.get("prompt_eval_count", 0), "output_tokens": r.get("eval_count", 0),
             "hit_token_limit": r.get("done_reason") == "length"}
    return answer, thinking, stats


def post(body):
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.load(resp)


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


def run_task(task_dir, model, think):
    meta = json.loads((task_dir / "meta.json").read_text())
    prompt = PROMPT.format(spec=(task_dir / "spec.md").read_text().strip(),
                           top=meta["top"], buggy=(task_dir / "buggy.v").read_text().rstrip())
    t0 = time.time()
    try:
        answer, thinking, stats = ask(model, prompt, think)
    except Exception as e:
        return {"task_id": task_dir.name, "status": "error", "reason": f"model request failed: {e}"}
    seconds = round(time.time() - t0, 1)

    code = extract(answer, meta["top"])
    if code is None:
        g = {"status": "no_answer", "reason": "no code block defining the module", "passed": [], "failed": []}
    else:
        g = grade(task_dir, code)
    return {"task_id": task_dir.name, "seed": meta["seed"], "bug_type": meta["bug"]["type"],
            "status": g["status"], "reason": g["reason"], "failed_tests": g["failed"],
            "seconds": seconds, **stats, "code": code, "answer": answer, "thinking": thinking}


def summarize(run_dir, rows, config):
    n = len(rows)
    passed = sum(r["status"] == "pass" for r in rows)
    lines = [f"model      {config['model']}  (think: {config['think']}, prompt {config['prompt_version']})",
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
    for key, title in (("bug_type", "by bug type"), ("seed", "by seed")):
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
    ap.add_argument("--model", help="Ollama model name, e.g. qwen2.5-coder:7b")
    ap.add_argument("--think", choices=["on", "off", "default"], default="off",
                    help="reasoning mode for models that have one (default: off)")
    ap.add_argument("--limit", type=int, help="only the first N tasks (for quick checks)")
    ap.add_argument("--tasks", default=str(ROOT / "tasks"))
    ap.add_argument("--resume", help="continue an interrupted run directory")
    args = ap.parse_args()

    if args.resume:
        run_dir = Path(args.resume)
        config = json.loads((run_dir / "config.json").read_text())
    else:
        if not args.model:
            ap.error("--model is required")
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        run_dir = ROOT / "results" / f"{re.sub(r'[^A-Za-z0-9.]+', '-', args.model)}_think-{args.think}_{stamp}"
        run_dir.mkdir(parents=True)
        config = {"model": args.model, "think": args.think, "prompt_version": PROMPT_VERSION,
                  "temperature": 0, "started": datetime.now().isoformat(timespec="seconds"),
                  "tasks": args.tasks, "limit": args.limit}
        (run_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")

    tasks = sorted(Path(config["tasks"]).glob("task_*"))[:config["limit"]]
    results_path = run_dir / "results.jsonl"
    done = {}
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            r = json.loads(line)
            if r["status"] != "error":      # retry requests that failed
                done[r["task_id"]] = r
    todo = [t for t in tasks if t.name not in done]
    print(f"{config['model']} (think {config['think']}): {len(todo)} to run, {len(done)} already done -> {run_dir}")

    with results_path.open("a") as out:
        for k, task_dir in enumerate(todo, 1):
            r = run_task(task_dir, config["model"], config["think"])
            done[r["task_id"]] = r
            out.write(json.dumps(r) + "\n")
            out.flush()
            score = sum(x["status"] == "pass" for x in done.values())
            print(f"  [{k}/{len(todo)}] {r['task_id']} {r.get('bug_type', ''):<13} {r['status']:<13} "
                  f"{r.get('seconds', '-')}s  | running total {score}/{len(done)}")

    rows = [done[t.name] for t in tasks if t.name in done]
    print("\n" + summarize(run_dir, rows, config))


if __name__ == "__main__":
    main()
