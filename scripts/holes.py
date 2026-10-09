"""Make code-completion tasks: remove a chunk of logic and leave a TODO.

    python3 scripts/holes.py <design.v>

Kinds of hole, smallest to largest:
  assign        one `assign` statement
  assign_group  a run of consecutive `assign` statements
  case_item     the body of one `case` branch (the label stays)
  always        a whole `always` block
  module        everything between the module header and `endmodule`

Declarations and ports are never removed, except in `module` holes, where
only the header is kept. No randomness: the same input gives the same holes.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from inject import Design, lineno

MARK = "// TODO: missing logic"
MODULE_MARK = "// TODO: implement the module"
ITEM_KW = {"always", "assign", "initial", "localparam", "parameter", "reg", "wire", "logic",
           "integer", "genvar", "generate", "endmodule"}
OPENS = {"begin", "case", "casez", "casex", "fork"}
CLOSES = {"end", "endcase", "join"}


def indent_of(line):
    return line[:len(line) - len(line.lstrip())]


class Holes:
    def __init__(self, src):
        self.src = src
        self.lines = src.splitlines(keepends=True)
        self.d = Design(src)
        toks = self.d.toks
        self.mod = next(i for i, t in enumerate(toks) if t.text == "module")
        self.hdr_end = self.d._statement_end(self.mod)

    def line_of(self, i, end=False):
        t = self.d.toks[i]
        return lineno(self.src, t.end - 1 if end else t.start)

    def items(self):
        """Module-level items as (keyword, first token, last token)."""
        toks, depth, starts = self.d.toks, 0, []
        for i in range(self.hdr_end + 1, len(toks)):
            x = toks[i].text
            if depth == 0 and x in ITEM_KW and toks[i - 1].text in (";", "end", "endcase", "endgenerate"):
                starts.append(i)
            depth += (x in OPENS) - (x in CLOSES)
        return [(toks[a].text, a, b - 1) for a, b in zip(starts, starts[1:])]

    def own_lines(self, first, last):
        """Line range (1-based, inclusive) if tokens first..last sit alone on their lines."""
        l1, l2 = self.line_of(first), self.line_of(last, end=True)
        before = self.lines[l1 - 1][:self.d.toks[first].start - self.line_start(l1)]
        after = self.lines[l2 - 1][self.d.toks[last].end - self.line_start(l2):]
        if before.strip() or after.strip().split("//")[0].strip():
            return None
        return l1, l2

    def line_start(self, n):
        return sum(len(l) for l in self.lines[:n - 1])

    def replace_lines(self, l1, l2, new_lines):
        return "".join(self.lines[:l1 - 1] + new_lines + self.lines[l2:])

    def hole(self, kind, l1, l2, marker=MARK):
        ind = indent_of(self.lines[l1 - 1])
        return {"kind": kind, "line": l1, "end_line": l2,
                "removed": "".join(self.lines[l1 - 1:l2]),
                "source": self.replace_lines(l1, l2, [f"{ind}{marker}\n"])}

    def all(self):
        out = []
        items = self.items()
        run = []
        for kw, a, b in items + [("<end>", None, None)]:
            if kw == "assign":
                span = self.own_lines(a, b)
                if span:
                    out.append(self.hole("assign", *span))
                    run.append(span)
                    continue
            if len(run) >= 2:
                out.append(self.hole("assign_group", run[0][0], run[-1][1]))
            run = []
            if kw == "always":
                span = self.own_lines(a, b)
                if span:
                    out.append(self.hole("always", *span))
                    out += self.case_items(a, b)
        # Whole module: keep the header and endmodule, drop everything in between.
        end_mod = next(i for i in range(len(self.d.toks) - 1, -1, -1) if self.d.toks[i].text == "endmodule")
        l1, l2 = self.line_of(self.hdr_end, end=True) + 1, self.line_of(end_mod) - 1
        if l2 >= l1:
            h = self.hole("module", l1, l2, MODULE_MARK)
            h["source"] = self.replace_lines(l1, l2, [f"  {MODULE_MARK}\n"])
            out.append(h)
        seen, unique = {self.src}, []
        for h in out:
            if h["source"] not in seen:
                seen.add(h["source"])
                unique.append(h)
        return unique

    def case_items(self, a, b):
        """Empty out each branch of every `case` in tokens a..b, keeping its label."""
        toks, out = self.d.toks, []
        for c in range(a, b + 1):
            if toks[c].text not in ("case", "casez", "casex"):
                continue
            depth, e = 0, c
            for e in range(c, b + 1):
                depth += (toks[e].text in OPENS) - (toks[e].text in CLOSES)
                if depth == 0:
                    break
            l_case, l_end = self.line_of(c), self.line_of(e)
            body = [n for n in range(l_case + 1, l_end) if self.lines[n - 1].strip()]
            if not body:
                continue
            ind = indent_of(self.lines[body[0] - 1])
            starts = [n for n in body if indent_of(self.lines[n - 1]) == ind
                      and not self.lines[n - 1].strip().startswith(("end", "//"))]
            for s, nxt in zip(starts, starts[1:] + [l_end]):
                line = self.lines[s - 1]
                colon = label_colon(line)
                if colon is None:
                    continue
                label = line[:colon + 1]
                new = [f"{label} begin\n", f"{ind}  {MARK}\n", f"{ind}end\n"]
                h = {"kind": "case_item", "line": s, "end_line": nxt - 1,
                     "removed": "".join(self.lines[s - 1:nxt - 1]),
                     "source": self.replace_lines(s, nxt - 1, new)}
                out.append(h)
        return out


def label_colon(line):
    """Index of the colon ending a case label (not one inside [3:0])."""
    depth = 0
    for k, ch in enumerate(line):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ":" and depth == 0:
            return k
    return None


def holes(src):
    return Holes(src).all()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    hs = holes(open(sys.argv[1]).read())
    print(f"{sys.argv[1]}: {len(hs)} holes  ({', '.join(f'{k} {v}' for k, v in Counter(h['kind'] for h in hs).items())})")
    for h in hs:
        first = h["removed"].strip().splitlines()[0]
        print(f"  {h['kind']:<13} L{h['line']}-{h['end_line']:<4} {first[:70]}")
