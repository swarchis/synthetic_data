"""Regression checks for the grader's anti-gaming rules (grade.check / grade.unsafe).

    docker run --rm --network none -v "$(pwd)":/work:ro -w /work rtl-env python3 scripts/check_grader.py

1. golden    every seed's golden design, submitted as an answer, still passes grade()
2. hacks     reward-hacking answers are rejected, and their file-writing payloads never run:
             - golden-copy wrapper: the top module instantiates <top>__ref, the differential
               checker's renamed golden copy (diff-only seeds would otherwise pass)
             - hierarchical reference to the golden copy (diff_top.u_ref.<port>), escaped
               identifier and defparam variants
             - banned tasks hidden in `ifdef __ICARUS__ (Icarus defines it, the analysis
               tokenizer blanked it), and built by macro token pasting
             - each banned system task / prefix, `include
             - a seed that uses its own macro: the candidate edits the macro body to paste
               $fopen together (only the iverilog -E scan can see it)
3. answers   every model answer that passed in results/ still passes check() (no false rejects)
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from grade import check, grade, interface, unsafe
from inject import module_spans

ROOT = Path(__file__).resolve().parent.parent
MARKER = "/tmp/check_grader_pwned"


def first_task(seed, tasks=ROOT / "tasks"):
    for m in sorted(tasks.glob("t_*/meta.json")):
        j = json.loads(m.read_text())
        if j["seed"] == seed and j["task_type"] == "bugfix":
            return m.parent


def top_of(task):
    return json.loads((task / "meta.json").read_text())["top"]


def top_span(src, top):
    return next((s, e) for s, e in module_spans(src) if re.match(rf"\s*module\s+{re.escape(top)}\b", src[s:e]))


def header_end(src, s):
    depth = 0
    for i in range(s, len(src)):
        depth += (src[i] == "(") - (src[i] == ")")
        if src[i] == ";" and depth == 0:
            return i + 1


def with_top_body(task, body):
    """buggy.v with the top module's body replaced (header kept, so the interface matches)."""
    src = (task / "buggy.v").read_text()
    s, e = top_span(src, top_of(task))
    return src[:header_end(src, s)] + "\n" + body + "\nendmodule" + src[e:]


def inject(task, snippet, prefix=""):
    """golden.v with `snippet` added at the end of the top module (behavior otherwise correct)."""
    src = (task / "golden.v").read_text()
    s, e = top_span(src, top_of(task))
    end = src.rfind("endmodule", s, e)
    return prefix + src[:end] + snippet + src[end:]


def hacks(task):
    top = top_of(task)
    ports, params = interface((task / "golden.v").read_text())[top]
    pov = f" #({', '.join(f'.{k}({k})' for k in params)})" if params else ""
    conns = ", ".join(f".{p}({p})" for p in ports)
    outs = [p for p, (d, _) in ports.items() if d == "output"]
    fopen = f'  integer pwn_fd;\n  initial begin pwn_fd = $fopen("{MARKER}", "w"); $fclose(pwn_fd); end\n'
    out = {
        "wrapper": with_top_body(task, f"  {top}__ref{pov} u_copy ({conns});"),
        "hierarchical": with_top_body(task, "\n".join(f"  assign {p} = diff_top.u_ref.{p};" for p in outs)),
        "escaped name": with_top_body(task, f"  \\{top}__ref {pov} u_copy ({conns});"),
        "defparam": inject(task, "  defparam diff_top.u_ref.UNUSED = 1;\n"),
        "ifdef $fopen": inject(task, f"`ifdef __ICARUS__\n{fopen}`endif\n"),
        "ifdef $system": inject(task, f'`ifdef __ICARUS__\n  initial $system("touch {MARKER}");\n`endif\n'),
        "token paste": inject(task, f'  integer pwn_fd;\n  initial begin pwn_fd = `GLUE($fo,pen)("{MARKER}", "w"); '
                                    f'`GLUE($fcl,ose)(pwn_fd); end\n', prefix="`define GLUE(a, b) a``b\n"),
        "new ifndef": inject(task, "`ifndef NOT_DEFINED_ANYWHERE\n  wire pwn_unused;\n`endif\n"),
        "`include": inject(task, '`include "/etc/hostname"\n'),
    }
    for name in ("$finish", "$finish_and_return", "$stop", "$writememh", "$writememb", "$dumpfile", "$dumpvars",
                 "$fflush", "$value$plusargs", "$test$plusargs", "$readmemh", "$fdisplay"):
        out[name] = inject(task, f"  initial {name}(\"x\");\n")
    return out


def main():
    fails = []

    def expect(name, ok, detail=""):
        print(f"{'ok  ' if ok else 'FAIL'} {name:<44} {detail[:110]}", flush=True)
        if not ok:
            fails.append(name)

    seeds = sorted(p.parent.name for p in (ROOT / "designs").glob("*/meta.json"))
    print("1. golden designs still pass")
    for seed in seeds:
        t = first_task(seed)
        r = grade(t, (t / "golden.v").read_text())
        expect(f"{seed} golden", r["status"] == "pass", f"{r['status']} {r['reason']}")

    print("\n2. hacks are rejected")
    for seed in ("ef_uart", "ef_tmr32", "counter"):        # two diff-only seeds, one hand-test seed
        t = first_task(seed)
        for name, cand in hacks(t).items():
            if os.path.exists(MARKER):
                os.remove(MARKER)
            r = grade(t, cand)
            expect(f"{seed} {name}", r["status"] == "rejected" and not os.path.exists(MARKER),
                   f"{r['status']}: {r['reason']}")

    # Our seeds define macros but never use them; a future seed might. Its macro body is
    # then fair game to edit, and only the preprocessed source shows what it expands to.
    macro_golden = ("`define DELAY(n, d) always @(posedge clk) n``_q <= d;\n"          # uses `` itself
                    "module m (input clk, input d, output reg x_q);\n  `DELAY(x, d)\nendmodule\n")
    expect("macro seed: golden itself", unsafe(macro_golden, macro_golden) is None, str(unsafe(macro_golden, macro_golden)))
    tidy = macro_golden.replace("_q <= d;", "_q  <=  d;")
    expect("macro seed: harmless macro edit allowed", unsafe(macro_golden, tidy) is None, str(unsafe(macro_golden, tidy)))
    evil = macro_golden.replace("_q <= d;", f'_q <= d; integer fd; initial fd = $fo``pen("{MARKER}", "w");')
    r = unsafe(macro_golden, evil)
    expect("macro seed: pasted $fopen in macro body", bool(r) and "after preprocessing" in r, str(r))

    print("\n3. earlier passing answers still pass the check")
    checked, rejected = 0, []
    for f in sorted((ROOT / "results").glob("*/results.jsonl")):
        for line in f.read_text().splitlines():
            r = json.loads(line)
            if r.get("status") != "pass" or not r.get("code"):
                continue
            task = next((d / r["task_id"] for d in (ROOT / "tasks", ROOT / "tasks_hard")
                         if (d / r["task_id"] / "golden.v").exists()), None)
            if task is None:
                continue
            checked += 1
            if reason := check((task / "golden.v").read_text(), r["code"]):
                rejected.append((f.parent.name, r["task_id"], reason))
    for run, tid, reason in rejected[:10]:
        print(f"     newly rejected: {run} {tid}: {reason}")
    expect(f"{checked} passing answers re-checked", not rejected, f"{len(rejected)} newly rejected")

    print("\nALL OK" if not fails else f"\nFAILED: {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
