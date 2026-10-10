"""Spending checks for run_agent.py against a fake OpenAI server (no real requests).

    docker run --rm -v "$(pwd)":/work rtl-env python3 scripts/check_agent_spend.py

The fake server plays an agent (read, edit, compile, run_tests, submit) and bills
every response it sends. Its own tally is the ground truth: the script's reported
spending must match it, and neither may ever exceed the budget.
"""

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRICE_IN, PRICE_OUT = 1.0, 6.0


class Fake:
    def __init__(self):
        self.lock = threading.Lock()
        self.mode = "agent"
        self.billed = 0.0
        self.requests = 0
        self.by_task = {}
        self.ctx = {}           # response id -> (context tokens, task, turn)
        self.n = 0

    def respond(self, body):
        """Bills like a real API: a turn's input = the previous turn's input + its output +
        the new items. 'greedy' counts 2 characters per token (the script's own worst case)
        and always uses the full output allowance; normal mode counts 4 and uses less."""
        k = 2 if self.mode == "greedy" else 4
        with self.lock:
            self.requests += 1
            self.n += 1
            rid = f"resp_{self.n}"
            prev = body.get("previous_response_id")
            new = len(json.dumps(body["input"])) // k
            if prev:
                inp, task, turn = self.ctx[prev]
                inp += new
            else:
                inp, task, turn = len(body.get("instructions", "")) // k + new, None, 0
                m = re.search(r"The design is in (\S+)\.v\b", json.dumps(body["input"]))
                task = m.group(1) if m else "?"
            self.by_task[task] = self.by_task.get(task, 0) + 1
        if self.mode == "error":
            return 500, {"error": "boom"}
        mo = body["max_output_tokens"]
        out = mo if self.mode == "greedy" else random.randint(50, mo // 4)
        design = f"{task}.v"
        if self.mode == "chatty":
            output = [{"type": "message", "content": [{"type": "output_text", "text": "thinking out loud"}]}]
        else:
            plan = [("read_file", {"path": design}), ("replace", {"path": design, "old": "zzz_not_there", "new": "x"}),
                    ("compile", {}), ("run_tests", {}), ("submit", {})]
            name, args = plan[min(turn, len(plan) - 1)]
            output = [{"type": "reasoning"}, {"type": "function_call", "call_id": f"c{rid}", "name": name,
                                              "arguments": json.dumps(args)}]
            if self.mode == "parallel":
                output.append({"type": "function_call", "call_id": f"d{rid}", "name": "compile", "arguments": "{}"})
        with self.lock:
            self.ctx[rid] = (inp + out, task, turn + 1)
            self.billed += (inp * PRICE_IN + out * PRICE_OUT) / 1e6
        return 200, {"id": rid, "status": "completed", "output": output,
                     "usage": {"input_tokens": inp, "output_tokens": out}}


FAKE = Fake()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        code, out = FAKE.respond(body)
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def run(results, *extra, resume=None):
    env = {**os.environ, "OPENAI_API_KEY": "fake", "OPENAI_BASE_URL": f"http://127.0.0.1:{PORT}/v1"}
    args = ["--resume", resume] if resume else ["--model", "openai:fake", "--price-in", str(PRICE_IN),
                                                 "--price-out", str(PRICE_OUT), "--results-dir", results,
                                                 "--seeds", "counter,edge_detector,priority_encoder", *extra]
    if resume:
        args += list(extra)
    p = subprocess.run([sys.executable, str(ROOT / "scripts/run_agent.py"), *args, "--yes"],
                       capture_output=True, text=True, env=env, cwd=ROOT, timeout=1800)
    return p.stdout + p.stderr


def run_dir(results):
    return sorted(Path(results).iterdir())[-1]


def episodes(d):
    return [json.loads(l) for l in (d / "episodes.jsonl").read_text().splitlines()]


def main():
    global PORT
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    random.seed(0)
    fails = []

    def check(name, cond, detail=""):
        print(f"{'ok  ' if cond else 'FAIL'} {name} {detail}", flush=True)
        if not cond:
            fails.append(name)

    tmp = Path(tempfile.mkdtemp())
    try:
        # 1. normal run: costs add up, budget not hit
        FAKE.billed, FAKE.requests = 0.0, 0
        out = run(str(tmp / "a"), "--budget", "5", "--limit", "6", "--jobs", "4")
        d = run_dir(tmp / "a")
        eps = episodes(d)
        spent = sum(e["cost"] for e in eps)
        check("normal: all episodes finish", len(eps) == 6 and all(e["status"] != "error" for e in eps),
              f"{[e['status'] for e in eps]}")
        check("normal: 5 turns each", all(e["turns"] == 5 for e in eps), f"{[e['turns'] for e in eps]}")
        check("normal: script cost == server bill", abs(spent - FAKE.billed) < 1e-9, f"{spent:.6f} vs {FAKE.billed:.6f}")
        check("normal: transcript has tool outputs", "Compiled OK" in json.dumps(eps[0]["transcript"]))

        # 2. greedy billing + tight budget: stops early, never over budget; resume never re-runs finished ones
        FAKE.billed, FAKE.requests, FAKE.mode, FAKE.by_task = 0.0, 0, "greedy", {}
        out = run(str(tmp / "b"), "--budget", "0.8", "--limit", "12", "--jobs", "8")
        d = run_dir(tmp / "b")
        eps = episodes(d)
        spent = sum(e["cost"] for e in eps)
        check("tight: server bill <= budget", FAKE.billed <= 0.8 + 1e-9, f"${FAKE.billed:.4f}")
        check("tight: script cost == server bill", abs(spent - FAKE.billed) < 1e-9, f"{spent:.6f} vs {FAKE.billed:.6f}")
        finished = {e["task_id"] for e in eps if e["status"] != "error"}
        check("tight: stopped early", len(finished) < 12 and ("STOPPED" in out or "not finished" in out),
              f"{len(finished)} finished")

        check("tight: no BUG line", "BUG" not in out)
        before = FAKE.billed
        out = run(str(tmp / "b"), resume=str(d))
        check("tight resume, same budget: bill still <= budget", FAKE.billed <= 0.8 + 1e-9, f"${FAKE.billed:.4f}")
        FAKE.by_task = {}
        out = run(str(tmp / "b"), "--budget", "20", resume=str(d))
        eps = episodes(d)
        done = [e for e in eps if e["status"] != "error"]
        check("tight resume, raised budget: all 12 done", len({e["task_id"] for e in done}) == 12)
        check("tight resume: finished episodes not re-run", len(done) == 12, f"{len(done)} non-error rows")
        check("tight resume: total cost == server bill", abs(sum(e["cost"] for e in eps) - FAKE.billed) < 1e-9)

        # 2b. realistic billing + tight budget: the start gate keeps cut-off episodes to at most one
        FAKE.billed, FAKE.mode = 0.0, "agent"
        out = run(str(tmp / "g"), "--budget", "0.35", "--limit", "12", "--jobs", "8")
        eps = episodes(run_dir(tmp / "g"))
        cut = [e for e in eps if e.get("budget_stop")]
        fin = [e for e in eps if e["status"] != "error"]
        check("gate: bill <= budget", FAKE.billed <= 0.35 + 1e-9, f"${FAKE.billed:.4f}")
        check("gate: at most one episode cut off", len(cut) <= 1 and len(fin) >= 2,
              f"{len(cut)} cut, {len(fin)} finished, ${FAKE.billed:.4f}")

        # 3. server errors: stops after 3 failed episodes, at most one wave, charged at worst case
        FAKE.billed, FAKE.requests, FAKE.mode = 0.0, 0, "error"
        out = run(str(tmp / "c"), "--budget", "5", "--limit", "20", "--jobs", "8")
        eps = episodes(run_dir(tmp / "c"))
        check("errors: run stops", "errors in a row" in out, out[-300:])
        check("errors: few requests", FAKE.requests <= 8 + 3, f"{FAKE.requests} requests")
        check("errors: failed requests charged", all(e["cost"] > 0 for e in eps))

        # 4. model that never calls tools: ends after a few turns
        FAKE.mode = "chatty"
        out = run(str(tmp / "e"), "--budget", "5", "--limit", "2")
        eps = episodes(run_dir(tmp / "e"))
        check("chatty: no_submit after 3 turns", all(e["status"] == "no_submit" and e["turns"] == 3 for e in eps),
              f"{[(e['status'], e['turns']) for e in eps]}")

        # 5. parallel calls: only the first runs
        FAKE.mode = "parallel"
        out = run(str(tmp / "f"), "--budget", "5", "--limit", "1")
        eps = episodes(run_dir(tmp / "f"))
        check("parallel: extra call refused", "Not run: make one tool call per turn." in json.dumps(eps[0]["transcript"])
              and eps[0]["steps"] == 5, f"steps {eps[0].get('steps')}")

        # 6. dry run sends nothing
        FAKE.requests, FAKE.mode = 0, "agent"
        p = subprocess.run([sys.executable, str(ROOT / "scripts/run_agent.py"), "--model", "openai:fake",
                            "--price-in", "1", "--price-out", "6", "--budget", "5", "--limit", "3"],
                           capture_output=True, text=True, cwd=ROOT,
                           env={k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"} | {"OPENAI_BASE_URL": f"http://127.0.0.1:{PORT}/v1"})
        check("dry run: nothing sent", FAKE.requests == 0 and "Estimate only" in p.stdout, p.stdout[-300:] + p.stderr[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        srv.shutdown()
    print("\nALL OK" if not fails else f"\nFAILED: {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
