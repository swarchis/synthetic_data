"""Grade a candidate fix for a task.

    python3 scripts/grade.py <task_dir> <candidate.v>

The candidate must keep the original module's interface (module name,
ports, parameters) and avoid constructs that could game the simulator.
Only then are the hidden tests run. This is the single source of truth
for reward: the baseline harness and the RL environment both call grade().
"""

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from inject import bracket_pairs, is_signal_name, tokenize
from sim import run_tests

# Could end the simulation early, read outside files, or touch the host.
BANNED = {"$finish", "$stop", "$fatal", "$readmemh", "$readmemb", "$fopen",
          "$fwrite", "$fdisplay", "$system", "`include"}
DIRECTIONS = {"input", "output", "inout"}
TYPE_WORDS = {"reg", "wire", "logic", "signed", "integer"}


def norm(s):
    return re.sub(r"\s+", "", s)


def interface(src):
    """Return (module names, {port: (direction, width)}, {parameter: default})."""
    toks = tokenize(src)
    pairs = bracket_pairs(toks)
    text = lambda i, j: src[toks[i].start:toks[j].end]
    modules = [toks[i + 1].text for i, t in enumerate(toks) if t.text == "module" and i + 1 < len(toks)]
    ports, params = {}, {}
    i = 0
    while i < len(toks):
        t = toks[i].text
        if t in DIRECTIONS:
            i += 1
            while i < len(toks) and toks[i].text in TYPE_WORDS:
                i += 1
            width = ""
            if i < len(toks) and toks[i].text == "[":
                width = norm(text(i, pairs[i]))
                i = pairs[i] + 1
            while i < len(toks) and is_signal_name(toks[i]):
                ports[toks[i].text] = (t, width)
                i += 1
                if i + 1 < len(toks) and toks[i].text == "," and is_signal_name(toks[i + 1]):
                    i += 1
                    continue
                break
        elif t == "parameter":
            i += 1
            while i < len(toks) and toks[i].text in TYPE_WORDS:
                i += 1
            if i < len(toks) and toks[i].text == "[":
                i = pairs[i] + 1
            while i + 1 < len(toks) and is_signal_name(toks[i]) and toks[i + 1].text == "=":
                name, start = toks[i].text, i + 2
                i = start
                while i < len(toks) and toks[i].text not in (",", ")", ";"):
                    i = pairs.get(i, i) + 1 if toks[i].text in "([{" else i + 1
                params[name] = norm(text(start, i - 1))
                if i + 2 < len(toks) and toks[i].text == "," and toks[i + 2].text == "=":
                    i += 1
                    continue
                break
        else:
            i += 1
    return modules, ports, params


def check(golden, candidate):
    """Return None if the candidate is allowed, else the reason it's rejected."""
    if not candidate.strip():
        return "empty file"
    try:
        toks = tokenize(candidate)
        cand_mods, cand_ports, cand_params = interface(candidate)
    except (ValueError, IndexError, KeyError) as e:
        return f"could not parse: {e}"
    gold_mods, gold_ports, gold_params = interface(golden)

    banned = sorted({t.text for t in toks if t.text in BANNED})
    if banned:
        return f"banned construct: {', '.join(banned)}"
    if cand_mods != gold_mods:
        return f"modules {cand_mods}, expected {gold_mods}"
    if cand_ports != gold_ports:
        diff = sorted(set(cand_ports.items()) ^ set(gold_ports.items()))
        return f"ports changed: {diff}"
    if cand_params != gold_params:
        diff = sorted(set(cand_params.items()) ^ set(gold_params.items()))
        return f"parameters changed: {diff}"
    return None


def grade(task_dir, candidate):
    """candidate is Verilog source text. Returns {"status", "reason", "passed", "failed"}.

    status: pass | fail | compile_error | timeout | rejected
    """
    task_dir = Path(task_dir)
    reason = check((task_dir / "golden.v").read_text(), candidate)
    if reason:
        return {"status": "rejected", "reason": reason, "passed": [], "failed": []}
    with tempfile.NamedTemporaryFile("w", suffix=".v") as f:
        f.write(candidate)
        f.flush()
        r = run_tests(f.name, task_dir / "tests", timeout=30)
    reason = ""
    if r["status"] == "compile_error":
        # Keep the compiler's own messages, not make's noise, with the temp path hidden.
        lines = [re.sub(r"/tmp/sim_\w+/", "", l) for l in r["log"].splitlines()
                 if "design.v:" in l]
        reason = "\n".join(lines[:10]) or r["log"][-800:]
    elif r["status"] == "timeout":
        reason = "simulation did not finish in 30s (likely an infinite loop)"
    return {"status": r["status"], "reason": reason, "passed": r["passed"], "failed": r["failed"]}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(json.dumps(grade(sys.argv[1], Path(sys.argv[2]).read_text()), indent=2))
