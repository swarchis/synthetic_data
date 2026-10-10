"""Run a model as an agent on tasks: it gets tools (read, search, edit, compile, run
visible tests, submit) instead of answering in one shot. Reward = hidden grade on submit.

Local (free, Ollama):
    python3 scripts/run_agent.py --model qwen2.5-coder:7b --limit 3

Paid (OpenAI). Nothing is sent until --yes:
    docker run --rm --env-file .env -v "$(pwd)":/work rtl-env python3 scripts/run_agent.py \\
        --model openai:gpt-5.6-luna --price-in 1 --price-out 6 --budget 10 --task-ids pilot.txt [--yes]

Resume (finished episodes are never re-run; spending so far counts):
    python3 scripts/run_agent.py --resume results/<run_dir> [--yes]

Spending safeguards (on top of run_model.py's, which this shares through llm.py):
  - every model turn reserves its own worst case before it is sent: the previous
    turn's billed input + output (the whole conversation so far) plus the new tool
    output at 2 chars/token, plus a full --max-output answer. Turn 1 uses the prompt
    estimate. So the budget holds across all turns of all episodes in flight.
  - if a turn's reservation does not fit, it waits for in-flight turns to settle;
    if it still does not fit, the episode stops as an error (resume retries it; its
    cost so far still counts) and no new episodes start
  - --max-steps caps tool calls per episode (default 12), so an episode has at most
    max-steps + 2 model turns
  - a failed request is charged at its worst case; 3 failed episodes in a row stop the run

Writes results/agent_<model>_<settings>_<time>/:
  config.json, episodes.jsonl (one line per episode: grade, steps, tools used,
  tokens, cost, full transcript, final design), summary.txt
"""

import argparse
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env import TOOLS, RTLEnv
from llm import OPENAI_URL, Budget, RequestFailed, is_paid, post, run_pool
import urllib.error

ROOT = Path(__file__).resolve().parent.parent
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
PROMPT_VERSION = "agent-v1"
MAX_ERRORS_IN_A_ROW = 3
MAX_TEXT_TURNS = 2       # turns without a tool call (each also uses a step)

SYSTEM = ("You are an expert digital design and verification engineer working in a small workspace "
          "through tools. Make one tool call per turn. Read the design, find the problem, edit with "
          "replace (small exact edits; use write_file only for large rewrites), check with compile and "
          "run_tests, then call submit. Passing run_tests does not guarantee the final grade, which runs "
          "more checks, so make sure the design matches the specification.")
NUDGE = "Use one of the tools. Call submit when the design is ready."


def tool_schemas():
    out = []
    for name, t in TOOLS.items():
        props, req = {}, []
        for p, ty in t["params"].items():
            props[p] = {"type": ty.rstrip("?")}
            if not ty.endswith("?"):
                req.append(p)
        out.append({"name": name, "description": t["description"],
                    "parameters": {"type": "object", "properties": props, "required": req}})
    return out


SCHEMAS = tool_schemas()
SCHEMA_CHARS = len(json.dumps(SCHEMAS))


# ----- backends ----------------------------------------------------------------
class OpenAIChat:
    """Responses API, one function call per turn, conversation kept server-side
    with previous_response_id."""

    def __init__(self, model, max_output, effort):
        self.model, self.max_output, self.effort = model, max_output, effort
        self.prev = None
        self.tools = [{"type": "function", "strict": False, **s} for s in SCHEMAS]   # optional params

    def send(self, inputs, first=False):
        key = os.environ.get("OPENAI_API_KEY", "")
        body = {"model": self.model, "input": inputs, "tools": self.tools, "parallel_tool_calls": False,
                "max_output_tokens": self.max_output}
        if first:
            body["instructions"] = SYSTEM
        else:
            body["previous_response_id"] = self.prev
        if self.effort != "default":
            body["reasoning"] = {"effort": self.effort}
        try:
            r = post(f"{OPENAI_URL}/responses", body, {"Authorization": f"Bearer {key}"}, 600)
        except urllib.error.HTTPError as e:
            raise RequestFailed(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
        except Exception as e:
            raise RequestFailed(f"{type(e).__name__}: {e}")
        usage = r.get("usage")
        if not usage:
            raise RequestFailed("response had no usage information")
        self.prev = r.get("id")
        text = "".join(c.get("text", "") for it in r.get("output", []) if it.get("type") == "message"
                       for c in it.get("content", []) if c.get("type") == "output_text")
        calls = [{"id": it.get("call_id"), "name": it.get("name"), "arguments": it.get("arguments", "")}
                 for it in r.get("output", []) if it.get("type") == "function_call"]
        stats = {"prompt_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
                 "cached_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens", 0),
                 "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
                 "hit_token_limit": r.get("status") == "incomplete"}
        return text, calls, stats

    def tool_result(self, call, output):
        return {"type": "function_call_output", "call_id": call["id"], "output": output}

    def user(self, text):
        return {"role": "user", "content": text}


class OllamaChat:
    """Ollama /api/chat with tools; the full message list is resent each turn."""

    def __init__(self, model, num_ctx=32768, num_predict=4096):
        self.model, self.num_ctx, self.num_predict = model, num_ctx, num_predict
        self.messages = [{"role": "system", "content": SYSTEM}]
        self.tools = [{"type": "function", "function": s} for s in SCHEMAS]

    def send(self, inputs, first=False):
        self.messages += inputs
        body = {"model": self.model, "messages": self.messages, "tools": self.tools, "stream": False,
                "options": {"temperature": 0, "seed": 0, "num_ctx": self.num_ctx, "num_predict": self.num_predict}}
        try:
            r = post(f"{OLLAMA_URL}/api/chat", body, {}, 1800)
        except Exception as e:
            raise RequestFailed(f"{type(e).__name__}: {e}")
        msg = r.get("message", {})
        self.messages.append(msg)
        calls = [{"id": None, "name": c["function"]["name"], "arguments": c["function"].get("arguments", {})}
                 for c in msg.get("tool_calls") or []]
        stats = {"prompt_tokens": r.get("prompt_eval_count", 0), "output_tokens": r.get("eval_count", 0),
                 "hit_token_limit": r.get("done_reason") == "length"}
        return msg.get("content", ""), calls, stats

    def tool_result(self, call, output):
        return {"role": "tool", "content": output, "tool_name": call["name"]}

    def user(self, text):
        return {"role": "user", "content": text}


# ----- one episode ---------------------------------------------------------------
def parse_args(raw):
    if isinstance(raw, dict):
        return raw, None
    try:
        a = json.loads(raw or "{}")
        return (a, None) if isinstance(a, dict) else ({}, "arguments must be a JSON object")
    except json.JSONDecodeError as e:
        return {}, f"arguments are not valid JSON ({e})"


def first_turn_worst(obs, cfg):
    est_in = math.ceil((len(SYSTEM) + len(obs) + SCHEMA_CHARS) / 2) + 200
    return (est_in * cfg["price_in"] + cfg["max_output"] * cfg["price_out"]) / 1e6


def run_episode(task_dir, cfg, budget=None, first_reservation=None):
    """Returns an episode row. Never raises."""
    paid = is_paid(cfg["model"])
    env = RTLEnv(task_dir, max_steps=cfg["max_steps"])
    row = {"task_id": task_dir.name, "seed": env.meta["seed"], "task_type": env.meta.get("task_type", "bugfix"),
           "label": env.meta.get("label", ""), "cost": 0.0, "prompt_tokens": 0, "output_tokens": 0,
           "cached_tokens": 0, "turns": 0, "transcript": []}
    t0 = time.time()
    reservation = first_reservation
    try:
        obs = env.reset()
        chat = (OpenAIChat(cfg["model"].split(":", 1)[1], cfg["max_output"], cfg["effort"]) if paid
                else OllamaChat(cfg["model"]))
        inputs, first, text_turns, last_ctx = [chat.user(obs)], True, 0, None
        while not env.done:
            if paid:
                if reservation is None:
                    if last_ctx is None:
                        reservation = first_turn_worst(obs, cfg)
                    else:
                        pending = sum(len(json.dumps(i)) for i in inputs)
                        ctx = last_ctx + math.ceil(pending / 2) + 200
                        reservation = (ctx * cfg["price_in"] + cfg["max_output"] * cfg["price_out"]) / 1e6
                    if not budget.reserve_wait(reservation):
                        why = budget.why_not(reservation)
                        reservation = None
                        raise BudgetStop(why)
                try:
                    text, calls, stats = chat.send(inputs, first)
                except RequestFailed:
                    budget.settle(reservation, reservation)       # unknown usage: assume the worst
                    row["cost"] += reservation
                    reservation = None
                    raise
                cost = (stats["prompt_tokens"] * cfg["price_in"] + stats["output_tokens"] * cfg["price_out"]) / 1e6
                budget.settle(reservation, cost)
                reservation = None
                row["cost"] += cost
                last_ctx = stats["prompt_tokens"] + stats["output_tokens"]
            else:
                text, calls, stats = chat.send(inputs, first)
            first = False
            row["turns"] += 1
            for k in ("prompt_tokens", "output_tokens", "cached_tokens"):
                row[k] += stats.get(k, 0)
            turn = {"text": text, "calls": [], "hit_token_limit": stats.get("hit_token_limit", False)}
            row["transcript"].append(turn)

            inputs = []
            if not calls:
                text_turns += 1
                obs, _, _ = env.step("_no_tool", {})          # counts as a step
                if text_turns > MAX_TEXT_TURNS:
                    if not env.done:
                        env.done, env.reward = True, 0.0
                        env.final = {"status": "no_submit", "reason": "stopped calling tools"}
                    break
                inputs.append(chat.user(NUDGE + ("" if not env.done else " (out of tool calls)")))
                continue
            for i, call in enumerate(calls):
                if i == 0:
                    args, err = parse_args(call["arguments"])
                    out = f"Bad arguments for {call['name']}: {err}" if err else None
                    if err:
                        env.step("_no_tool", {})
                    else:
                        out, _, _ = env.step(call["name"], args)
                else:
                    out = "Not run: make one tool call per turn."
                turn["calls"].append({"name": call["name"], "arguments": call["arguments"], "output": out})
                inputs.append(chat.tool_result(call, out))
        final = env.final or {"status": "no_submit", "reason": "?"}
        row.update(status=final["status"], reason=final.get("reason", ""), reward=env.reward or 0.0,
                   steps=env.steps, final_code=env.current_design())
    except BudgetStop as e:
        row.update(status="error", reason=str(e), budget_stop=True)
    except Exception as e:
        row.update(status="error", reason=f"{type(e).__name__}: {e}")
    finally:
        if reservation is not None and budget is not None:     # reserved but never sent
            budget.settle(reservation, 0.0)
        env.close()
    row["seconds"] = round(time.time() - t0, 1)
    row["tools"] = dict(Counter(c["name"] for t in row["transcript"] for c in t["calls"]))
    row.update(habits(row))
    return row


def typical_episode(task_dir, cfg):
    """Guess at one episode's cost: 7 turns; context = prompt + the design file once read
    (4 chars/token, plus line numbers) + 2,000 tokens of tool output and edits; 1,500 output
    tokens per turn including reasoning."""
    n = (len(SYSTEM + RTLEnv(task_dir, max_steps=cfg["max_steps"]).intro()) + SCHEMA_CHARS
         + 1.2 * len((Path(task_dir) / "buggy.v").read_text())) / 4 + 2000
    return (7 * n * cfg["price_in"] + 7 * 1500 * cfg["price_out"]) / 1e6


class BudgetStop(Exception):
    pass


def habits(row):
    seq = [c["name"] for t in row["transcript"] for c in t["calls"]]
    if "submit" not in seq:
        return {"submitted": False}
    before = seq[:seq.index("submit")]
    last_edit = max([i for i, n in enumerate(before) if n in ("replace", "write_file")], default=-1)
    after = before[last_edit + 1:]
    return {"submitted": True, "compiled_after_last_edit": "compile" in after or "run_tests" in after,
            "tested_after_last_edit": "run_tests" in after, "edited": last_edit >= 0,
            "used_write_file": "write_file" in before}


# ----- summary -------------------------------------------------------------------
def summarize(run_dir, rows, config, spent=None):
    n = len(rows)
    passed = sum(r["status"] == "pass" for r in rows)
    lines = [f"model      {config['model']}  (agent, max {config['max_steps']} tool calls, effort {config['effort']}, "
             f"prompt {config['prompt_version']})",
             f"started    {config['started']}",
             f"pass@1     {passed}/{n} = {100 * passed / max(n, 1):.1f}%", "",
             "outcomes   " + ", ".join(f"{k} {v}" for k, v in Counter(r["status"] for r in rows).most_common())]
    if n:
        sub = [r for r in rows if r.get("submitted")]
        lines += [f"steps      {sum(r.get('steps', 0) for r in rows) / n:.1f} tool calls per episode, "
                  f"{sum(r['seconds'] for r in rows) / n:.0f}s per episode",
                  f"habits     submitted {len(sub)}/{n}; of those: compiled or tested after last edit "
                  f"{sum(r['compiled_after_last_edit'] for r in sub)}, ran tests after last edit "
                  f"{sum(r['tested_after_last_edit'] for r in sub)}, used write_file "
                  f"{sum(r['used_write_file'] for r in sub)}, made no edit {sum(not r['edited'] for r in sub)}",
                  "tools      " + ", ".join(f"{k} {v}" for k, v in
                                            sum((Counter(r.get("tools", {})) for r in rows), Counter()).most_common()),
                  f"tokens     {sum(r['prompt_tokens'] for r in rows) // n} in "
                  f"({sum(r['cached_tokens'] for r in rows) // n} cached), "
                  f"{sum(r['output_tokens'] for r in rows) // n} out per episode"]
        cut = sum(t["hit_token_limit"] for r in rows for t in r["transcript"])
        if cut:
            lines.append(f"WARNING    {cut} turns hit the output token limit")
    if spent is not None:
        lines.append(f"cost       ${spent:.4f} of ${config['budget']:.2f} budget "
                     f"(prices in/out per 1M: ${config['price_in']} / ${config['price_out']}; cached input "
                     f"counted at full price)")
    for key, title in (("task_type", "by task type"), ("seed", "by seed")):
        groups = defaultdict(list)
        for r in rows:
            groups[r.get(key, "?")].append(r["status"] == "pass")
        lines += ["", title] + [f"  {g:<14} {sum(v)}/{len(v)}" for g, v in sorted(groups.items())]
    text = "\n".join(lines) + "\n"
    (run_dir / "summary.txt").write_text(text)
    return text


# ----- main ----------------------------------------------------------------------
def pick_tasks(config):
    tasks = sorted(Path(config["tasks"]).glob("t_*"))
    if config.get("task_ids"):
        ids = [l.split()[0] for l in Path(config["task_ids"]).read_text().splitlines() if l.strip() and l[0] != "#"]
        by_name = {t.name: t for t in tasks}
        missing = [i for i in ids if i not in by_name]
        if missing:
            sys.exit(f"--task-ids: unknown tasks {missing[:5]}")
        tasks = [by_name[i] for i in ids]
    metas = {t: json.loads((t / "meta.json").read_text()) for t in tasks}
    if config.get("task_types"):
        tasks = [t for t in tasks if metas[t].get("task_type", "bugfix") in config["task_types"].split(",")]
    if config.get("seeds"):
        tasks = [t for t in tasks if metas[t]["seed"] in config["seeds"].split(",")]
    return tasks[:config["limit"]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="Ollama model (qwen2.5-coder:7b) or openai:<model>")
    ap.add_argument("--effort", choices=["minimal", "low", "medium", "high", "default"], default="medium")
    ap.add_argument("--price-in", type=float, help="OpenAI: USD per 1M input tokens")
    ap.add_argument("--price-out", type=float, help="OpenAI: USD per 1M output tokens")
    ap.add_argument("--budget", type=float, help="OpenAI: hard spending cap in USD for this run (all resumes)")
    ap.add_argument("--max-output", type=int, help="max output tokens per model turn (default 16000)")
    ap.add_argument("--max-steps", type=int, default=12, help="tool calls per episode (default 12)")
    ap.add_argument("--yes", action="store_true", help="OpenAI: actually send requests (otherwise estimate only)")
    ap.add_argument("--jobs", type=int, help="episodes at once (default: 8 for OpenAI, 1 for Ollama)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--task-ids", help="file with one task id per line")
    ap.add_argument("--task-types", help="comma-separated: bugfix,multibug,complete,module")
    ap.add_argument("--seeds", help="comma-separated seed names")
    ap.add_argument("--tasks", default=str(ROOT / "tasks"))
    ap.add_argument("--results-dir", default=str(ROOT / "results"))
    ap.add_argument("--resume", help="continue an interrupted run directory")
    args = ap.parse_args()

    if args.resume:
        run_dir = Path(args.resume)
        config = json.loads((run_dir / "config.json").read_text())
        if args.budget is not None:
            config["budget"] = args.budget
        if args.max_output is not None:
            config["max_output"] = args.max_output
    else:
        if not args.model:
            ap.error("--model is required")
        config = {"model": args.model, "effort": args.effort, "prompt_version": PROMPT_VERSION,
                  "started": datetime.now().isoformat(timespec="seconds"), "tasks": args.tasks,
                  "task_ids": args.task_ids, "limit": args.limit, "task_types": args.task_types, "seeds": args.seeds,
                  "max_output": args.max_output or 16000, "max_steps": args.max_steps,
                  "price_in": args.price_in, "price_out": args.price_out, "budget": args.budget}
        run_dir = None

    paid = is_paid(config["model"])
    if paid:
        missing = [f for f, k in (("--price-in", "price_in"), ("--price-out", "price_out"), ("--budget", "budget"))
                   if config.get(k) is None]
        if missing:
            ap.error(f"paid model needs {', '.join(missing)}")
        if config["budget"] <= 0 or config["price_in"] <= 0 or config["price_out"] <= 0:
            ap.error("--budget, --price-in and --price-out must be positive")
        if args.yes and not os.environ.get("OPENAI_API_KEY"):
            ap.error("OPENAI_API_KEY is not set (in Docker, add: --env-file .env)")

    tasks = pick_tasks(config)
    on_disk = []
    if run_dir and (run_dir / "episodes.jsonl").exists():
        on_disk = [json.loads(l) for l in (run_dir / "episodes.jsonl").read_text().splitlines()]
    done = {r["task_id"]: r for r in on_disk if r["status"] != "error"}
    spent = sum(r.get("cost", 0) for r in on_disk)
    todo = [t for t in tasks if t.name not in done]

    if paid:
        firsts = [first_turn_worst(RTLEnv(t, max_steps=config['max_steps']).intro(), config) for t in todo]
        turns = config["max_steps"] + MAX_TEXT_TURNS
        print(f"{config['model']} agent (effort {config['effort']}, {config['max_steps']} tool calls): "
              f"{len(todo)} episodes to run")
        typical = sum(typical_episode(t, config) for t in todo)
        print(f"  likely cost   ~${typical:.2f}  (guess: 7 turns, design file in context, 1,500 output tokens per turn)")
        print(f"  first turn    worst ${sum(firsts):.2f} total; later turns are reserved one at a time "
              f"(up to {turns} turns per episode)")
        print(f"  budget         ${config['budget']:.2f}, already spent ${spent:.4f}; the run stops before "
              f"any turn that could go over")
        if not args.yes:
            print("\nEstimate only: nothing was sent. Re-run with --yes to start.")
            return

    if run_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        base = Path(args.results_dir) / (f"agent_{re.sub(r'[^A-Za-z0-9.]+', '-', config['model'])}"
                                         f"_steps-{config['max_steps']}_{stamp}")
        run_dir, n = base, 2
        while run_dir.exists():
            run_dir, n = base.with_name(f"{base.name}-{n}"), n + 1
        run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")

    jobs = args.jobs or (8 if paid else 1)
    print(f"{config['model']}: {len(todo)} to run, {len(done)} already done, {jobs} at a time -> {run_dir}",
          flush=True)
    budget = Budget(config["budget"], spent) if paid else None
    finished = [0]

    def reserve(task_dir):
        if not paid:
            return True, None
        w = first_turn_worst(RTLEnv(task_dir, max_steps=config['max_steps']).intro(), config)
        return (True, w) if budget.reserve(w) else (False, budget.why_not(w))

    with (run_dir / "episodes.jsonl").open("a") as out:
        def on_done(task_dir, r, w):
            if r["status"] != "error":
                done[r["task_id"]] = r
            out.write(json.dumps(r) + "\n")
            out.flush()
            finished[0] += 1
            score = sum(x["status"] == "pass" for x in done.values())
            cost = f"  ${budget.spent:.4f} spent" if paid else ""
            print(f"  [{finished[0]}/{len(todo)}] {r['task_id']} {r['seed']:<14} {r['status']:<13} "
                  f"{r.get('steps', '-')} steps {r['seconds']}s | running total {score}/{len(done)}{cost}", flush=True)
            if r["status"] == "error":
                print(f"      {r['reason'][:200]}", flush=True)
            return r["status"] == "error" and not r.get("budget_stop")

        def work(task_dir, w=None):
            return run_episode(task_dir, config, budget, w)

        # run_pool hands us the reservation token via reserve(); pass it to the episode
        tokens = {}

        # Start gate: a new episode starts only if the money left covers twice the typical
        # episode for it and for every episode already running, so a tight budget is not
        # spread over many episodes that all get cut off. (The hard guard is per turn.)
        # Once an episode has been cut off by the budget, no new ones start.
        running, cut_off = {}, []

        def reserve_and_keep(task_dir):
            if cut_off:
                return False, f"budget: an episode was stopped by the budget ({cut_off[0][:120]})"
            if paid:
                mine = typical_episode(task_dir, config)
                need = 2 * sum(running.values()) + 2 * mine if running else mine
                if budget.spent + need > config["budget"]:
                    return False, f"budget: ${config['budget'] - budget.spent:.4f} left is not enough to " \
                                  f"start another episode safely"
            ok, tok = reserve(task_dir)
            if ok:
                tokens[task_dir] = tok
                running[task_dir] = typical_episode(task_dir, config) if paid else 0
            return ok, tok

        def done_and_release(task_dir, r, w):
            running.pop(task_dir, None)
            if r.get("budget_stop"):
                cut_off.append(r["reason"])
            return on_done(task_dir, r, w)

        stopped = run_pool(todo, lambda t: work(t, tokens.pop(t, None)), jobs, reserve_and_keep, done_and_release,
                           MAX_ERRORS_IN_A_ROW)
        if paid and budget.spent > config["budget"] + 1e-9:
            print(f"BUG: spent ${budget.spent:.4f} is over the budget", flush=True)

    if stopped:
        print(f"\nSTOPPED: {stopped}. Resume later with --resume {run_dir}")
    rows = [done[t.name] for t in tasks if t.name in done]
    left = len(tasks) - len(rows)
    if left:
        print(f"\n{left} episodes not finished (budget or errors). Resume with --resume {run_dir}")
    print("\n" + summarize(run_dir, rows, config, budget.spent if paid else None))


if __name__ == "__main__":
    main()
