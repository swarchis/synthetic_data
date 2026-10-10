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
from inject import bracket_pairs, is_signal_name, module_spans, tokenize
from sim import run_tests

# Could end the simulation early, read outside files, or touch the host.
BANNED = {"$finish", "$stop", "$fatal", "$readmemh", "$readmemb", "$fopen",
          "$fwrite", "$fdisplay", "$system", "`include"}
DIRECTIONS = {"input", "output", "inout"}
TYPE_WORDS = {"reg", "wire", "logic", "signed", "integer"}


def norm(s):
    return re.sub(r"\s+", "", s)


def interface(src):
    """Return {module name: ({port: (direction, width)}, {parameter: default})}."""
    out = {}
    for s, e in module_spans(src) or [(0, len(src))]:
        mods, ports, params = module_interface(src[s:e])
        if mods:
            out[mods[0]] = (ports, params)
    return out


def module_interface(src):
    """For one module: (module names, {port: (direction, width)}, {parameter: default})."""
    toks = tokenize(src)
    pairs = bracket_pairs(toks)
    text = lambda i, j: src[toks[i].start:toks[j].end]
    modules = [toks[i + 1].text for i, t in enumerate(toks) if t.text == "module" and i + 1 < len(toks)]
    ports, params = {}, {}
    # A module with a #(...) parameter list in its header exposes only those parameters;
    # a `parameter` in its body is then a local constant (IEEE 1364-2005, 4.10.1).
    m = next((k for k, t in enumerate(toks) if t.text == "module"), None)
    header_params = m is not None and m + 2 < len(toks) and toks[m + 2].text == "#"
    header_end = pairs.get(m + 3, m) if header_params else -1
    i = 0
    while i < len(toks):
        t = toks[i].text
        if t in ("function", "task"):          # their input/output arguments are not module ports
            end = "end" + t
            while i < len(toks) and toks[i].text != end:
                i += 1
            i += 1
            continue
        if t == "parameter" and header_params and i > header_end:
            i += 1                              # body parameter: local, not part of the interface
            continue
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


def eval_width(width, params):
    """'[MSB:LSB]' -> (msb, lsb) as integers, substituting parameter defaults, so
    '[1-1:0]', '[0:0]' and no range at all compare equal. None if it can't be evaluated."""
    if not width:
        return (0, 0)
    m = re.fullmatch(r"\[(.*):(.*)\]", width)
    if not m:
        return None
    env = {"__builtins__": {}, "clog2": lambda v: max(0, (int(v) - 1).bit_length())}

    def ev(expr, depth=0):
        expr = expr.replace("$clog2", "clog2")
        for name, val in params.items():           # substitute defaults (they may refer to each other)
            expr = re.sub(rf"\b{re.escape(name)}\b", f"({val})", expr)
        if not re.fullmatch(r"[\d\s()+\-*/%<>clog2]*", expr):
            return None
        try:
            return int(eval(expr.replace("/", "//"), env))
        except Exception:
            return None
    msb, lsb = ev(m.group(1)), ev(m.group(2))
    return None if msb is None or lsb is None else (msb, lsb)


def same_ports(cp, gp, cpar, gpar):
    """Port maps match: same names and directions, and widths equal numerically when both
    evaluate (with each side's parameter defaults), otherwise as text."""
    if set(cp) != set(gp):
        return False
    for name, (gdir, gw) in gp.items():
        cdir, cw = cp[name]
        if cdir != gdir:
            return False
        a, b = eval_width(cw, cpar), eval_width(gw, gpar)
        if (a, b) == (None, None) or a is None or b is None:
            if cw != gw:
                return False
        elif a != b:
            return False
    return True


def check(golden, candidate):
    """Return None if the candidate is allowed, else the reason it's rejected."""
    if not candidate.strip():
        return "empty file"
    try:
        toks = tokenize(candidate)
        cand = interface(candidate)
    except (ValueError, IndexError, KeyError) as e:
        return f"could not parse: {e}"
    gold = interface(golden)

    banned = sorted({t.text for t in toks if t.text in BANNED})
    if banned:
        return f"banned construct: {', '.join(banned)}"
    if sorted(cand) != sorted(gold):
        return f"modules {sorted(cand)}, expected {sorted(gold)}"
    for name in sorted(gold):
        (cp, cpar), (gp, gpar) = cand[name], gold[name]
        where = f" in {name}" if len(gold) > 1 else ""
        if cpar != gpar:
            return f"parameters changed{where}: {sorted(set(cpar.items()) ^ set(gpar.items()))}"
        if not same_ports(cp, gp, cpar, gpar):
            return f"ports changed{where}: {sorted(set(cp.items()) ^ set(gp.items()))}"
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
        r = run_tests(f.name, task_dir / "tests", timeout=30, golden=task_dir / "golden.v")
    reason = ""
    if r["status"] == "compile_error":
        # Keep the compiler's own messages, not make's noise, with the temp path hidden.
        lines = [re.sub(r"/tmp/sim_\w+/", "", l) for l in r["log"].splitlines()
                 if "design.v:" in l]
        reason = "\n".join(lines[:10]) or r["log"][-800:]
    elif r["status"] == "timeout":
        reason = "simulation did not finish in 30s (likely an infinite loop)"
    elif r["status"] == "fail" and any(t.startswith("diff:") for t in r["failed"]):
        from diffcheck import first_mismatch
        reason = first_mismatch(r["log"])
    elif r["status"] == "fail":
        # Hand-written tests: name them, plus the first assertion message.
        msg = next((l.strip() for l in r["log"].splitlines() if "AssertionError" in l and "raise " not in l), "")
        reason = f"failed tests: {', '.join(r['failed'][:6])}" + (f"; {msg[:300]}" if msg else "")
    return {"status": r["status"], "reason": reason, "passed": r["passed"], "failed": r["failed"]}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(json.dumps(grade(sys.argv[1], Path(sys.argv[2]).read_text()), indent=2))
