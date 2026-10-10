"""Shared model backends, spending guard, and parallel runner.

Used by run_model.py (our tasks) and cvdp_infer.py (CVDP), so both judge
cost the same way and share one set of safeguards.

Spending guard for paid models: before each request, its worst case (a
generous prompt estimate plus a full max_output answer) is *reserved*. A
request is only sent if spent + all reservations + its own worst case fits
in the budget, so the budget holds even with many requests in flight. When
it finishes, the reservation is replaced by the real cost (or kept at the
worst case if the usage is unknown).
"""

import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

OPENAI_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")


class RequestFailed(Exception):
    pass


def is_paid(model):
    return model.startswith("openai:")


def post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def ask_ollama(url, model, system, user, think="off", num_ctx=8192, num_predict=4096):
    """Local model through Ollama. Returns (answer, thinking, stats)."""
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    body = {"model": model, "messages": messages, "stream": False,
            "options": {"temperature": 0, "seed": 0, "num_ctx": num_ctx, "num_predict": num_predict}}
    if think != "default":
        body["think"] = think == "on"
    try:
        r = post(f"{url}/api/chat", body, {}, 1800)
    except urllib.error.HTTPError as e:
        # Models without a thinking mode reject the `think` field; retry without it.
        if "think" in body and "think" in e.read().decode(errors="replace").lower():
            body.pop("think")
            r = post(f"{url}/api/chat", body, {}, 1800)
        else:
            raise
    msg = r.get("message", {})
    answer, thinking = msg.get("content", ""), msg.get("thinking", "")
    # Some models put their reasoning inline instead of in a separate field.
    inline = re.findall(r"<think>(.*?)</think>", answer, re.S)
    if inline:
        thinking += "\n".join(inline)
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S)
    stats = {"prompt_tokens": r.get("prompt_eval_count", 0), "output_tokens": r.get("eval_count", 0),
             "hit_token_limit": r.get("done_reason") == "length"}
    return answer, thinking, stats


def ask_openai(model, system, user, max_output, effort="medium"):
    """OpenAI Responses API. Returns (answer, "", stats). Raises RequestFailed."""
    key = os.environ.get("OPENAI_API_KEY", "")
    body = {"model": model, "instructions": system, "input": user, "max_output_tokens": max_output}
    if effort != "default":
        body["reasoning"] = {"effort": effort}
    try:
        r = post(f"{OPENAI_URL}/responses", body, {"Authorization": f"Bearer {key}"}, 600)
    except urllib.error.HTTPError as e:
        raise RequestFailed(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    except Exception as e:
        raise RequestFailed(f"{type(e).__name__}: {e}")
    answer = "".join(c.get("text", "") for item in r.get("output", []) if item.get("type") == "message"
                     for c in item.get("content", []) if c.get("type") == "output_text")
    usage = r.get("usage")
    if not usage:
        raise RequestFailed("response had no usage information")
    stats = {"prompt_tokens": usage.get("input_tokens", 0),
             "output_tokens": usage.get("output_tokens", 0),
             "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
             "hit_token_limit": r.get("status") == "incomplete"
             and (r.get("incomplete_details") or {}).get("reason") == "max_output_tokens"}
    return answer, "", stats


def worst_case_cost(system, user, price_in, price_out, max_output):
    """Most one request could cost: prompt at 2 characters per token (real
    text is ~4, so this overestimates) plus an answer using every allowed token."""
    est_in = math.ceil(len((system or "") + user) / 2) + 100
    return (est_in * price_in + max_output * price_out) / 1e6


def actual_cost(stats, price_in, price_out):
    return (stats["prompt_tokens"] * price_in + stats["output_tokens"] * price_out) / 1e6


class Budget:
    """Spent money plus money reserved for requests in flight. Thread-safe, so
    agent episodes can reserve each turn from worker threads."""

    def __init__(self, limit, spent=0.0):
        self.limit, self.spent, self.reserved = limit, spent, 0.0
        self.cond = threading.Condition()

    def reserve(self, worst):
        with self.cond:
            if self.spent + self.reserved + worst > self.limit:
                return False
            self.reserved += worst
            return True

    def reserve_wait(self, worst, timeout=900):
        """Like reserve, but if other requests are in flight, wait for their money to
        settle and try again. Gives up when nothing is in flight or after `timeout`."""
        deadline = time.time() + timeout
        with self.cond:
            while self.spent + self.reserved + worst > self.limit:
                left = deadline - time.time()
                if self.reserved <= 1e-12 or left <= 0:
                    return False
                self.cond.wait(left)
            self.reserved += worst
            return True

    def settle(self, worst, actual):
        with self.cond:
            self.reserved -= worst
            self.spent += actual
            self.cond.notify_all()

    def why_not(self, worst):
        return (f"budget: spent ${self.spent:.4f} + ${self.reserved:.4f} in flight, next request could cost "
                f"up to ${worst:.4f}, budget ${self.limit:.2f}")


def run_pool(todo, work, jobs, reserve, on_done, max_errors=3):
    """Run work(item) for every item, up to `jobs` at a time.

    reserve(item) -> (ok, token): called before sending. If ok is False while
        other requests are in flight, the item waits for their money to settle
        and is tried again; if nothing is in flight, the run stops (token is
        then the reason). Runs in this thread only.
    on_done(item, result, token) -> True if the result is an error. Runs in
        this thread, in completion order, so it can write files safely.
    After an error, only one request is sent at a time until one succeeds,
    so a failing server gets at most one wave of requests. Stops sending
    after `max_errors` errors in a row; requests already in flight still
    finish. Returns the reason it stopped early, or None.
    """
    items, inflight, stop, errors, pending = iter(todo), {}, None, 0, None
    with ThreadPoolExecutor(max(1, jobs)) as pool:
        while True:
            target = jobs if errors == 0 else 1
            while stop is None and len(inflight) < target:
                item, pending = (pending if pending is not None else next(items, None)), None
                if item is None:
                    break
                ok, token = reserve(item)
                if not ok:
                    if inflight:            # money is tied up in flight; wait for it, then retry
                        pending = item
                        break
                    stop = token
                    break
                inflight[pool.submit(work, item)] = (item, token)
            if not inflight:
                return stop
            done, _ = wait(inflight, return_when=FIRST_COMPLETED)
            for f in done:
                item, token = inflight.pop(f)
                errors = errors + 1 if on_done(item, f.result(), token) else 0
                if errors >= max_errors and stop is None:
                    stop = f"{max_errors} errors in a row (see above); not retrying"
