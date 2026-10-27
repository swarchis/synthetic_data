"""Subtle single-bug variants: bugs that only show up in some situations.

    python3 scripts/inject_subtle.py <design.v>

inject.py flips one token (+ to -, & to |), which a model can often spot by eye.
These change behavior only in rarer cases, so finding them takes reasoning about
what the design does:

  drop_term        one condition dropped from an a && b && c chain
  priority_swap    two branches of an if / else if chain swap places (matters
                   only when both conditions hold)
  off_by_one       a compare against a signal limit gets a -1 or +1
  stage_swap       a register read one pipeline stage early or late (for a
                   register copy `t <= s;`, a read of s becomes t or back)
  width_trunc      a slice loses its top bit: x[15:0] -> x[14:0]
  reset_missing    one register left out of a reset branch
  blocking_assign  <= becomes = in a clocked block, for a register that is
                   read later in the same block

Same output as inject.py (dicts with op, line, before, after, source, edit), so
build code and combine() work on both. No randomness.
"""

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from inject import Design, _build_mutants, is_signal_name, module_spans

COMPARES = {"==", "!=", "<", "<=", ">", ">="}
RESET_NAME = re.compile(r"(^|_)(rst|reset|resetn|rst_n|presetn)$", re.I)
OPERAND_END = {")", ";", "&&", "||", "?", ":", "&", "|", ","}


def mutate_subtle(src):
    edits = []
    for s, e in module_spans(src) or [(0, len(src))]:
        edits += [(op, a + s, b + s, new) for op, a, b, new in _module_edits(src[s:e])]
    return _build_mutants(src, edits)


class Scan:
    def __init__(self, src):
        self.src = src
        self.d = d = Design(src)
        self.toks = d.toks
        self.edits = []

    def ok(self, i, j):
        return all(self.d.mutable[i:j + 1])

    def span(self, i, j):
        return self.toks[i].start, self.toks[j].end

    def block_end(self, k):
        """Last token of the statement starting at k: a begin..end block or a simple
        statement. None for if/case statements (not handled)."""
        toks = self.toks
        if toks[k].text == "begin":
            depth = 0
            for m in range(k, len(toks)):
                depth += (toks[m].text == "begin") - (toks[m].text == "end")
                if depth == 0:
                    return m
            return None
        if toks[k].text in ("if", "case", "casez", "casex", "for", "else"):
            return None
        return self.d._statement_end(k)

    # ----- bug kinds -------------------------------------------------------
    def drop_term(self):
        toks, d = self.toks, self.d
        spans = []
        for i, t in enumerate(toks):
            if t.text == "if" and i + 1 < len(toks) and toks[i + 1].text == "(":
                spans.append((i + 2, d.pairs[i + 1] - 1))
            elif t.text in ("=", "<=") and (d.lvalue_start(i) is not None or i in self._assign_eq()):
                spans.append((i + 1, d._statement_end(i) - 1))
        groups = set()
        for lo, hi in spans:
            groups.add((lo, hi))
            for k in range(lo, hi + 1):
                if toks[k].text == "(" and k in d.pairs:
                    groups.add((k + 1, d.pairs[k] - 1))
        for lo, hi in sorted(groups):
            if hi > lo and self.ok(lo, hi):
                self._drop_in_group(lo, hi)

    def _assign_eq(self):
        if not hasattr(self, "_eqs"):
            self._eqs = {i for i, t in enumerate(self.toks) if t.text == "=" and i >= 2
                         and self.toks[i - 2].text == "assign"}
        return self._eqs

    def _drop_in_group(self, lo, hi):
        toks, d = self.toks, self.d
        ops, k = [], lo
        while k <= hi:
            x = toks[k].text
            if x in "([{" and k in d.pairs:
                k = d.pairs[k] + 1
                continue
            if x in ("?", ":", ","):
                return                              # ternaries and lists: not a plain chain
            if x in ("&&", "||") or (x in ("&", "|") and d.is_binary(k)):
                ops.append(k)
            k += 1
        if not ops or len({toks[o].text for o in ops}) > 1:
            return                                  # no chain, or mixed operators
        bounds = [lo] + ops + [hi + 1]
        for n in range(len(ops) + 1):
            a, b = bounds[n] + (n > 0), bounds[n + 1] - 1   # term n is tokens a..b
            if n == 0:
                start, end = toks[a].start, toks[ops[0] + 1].start
            else:
                start, end = toks[ops[n - 1] - 1].end, toks[b].end
            self.edits.append(("drop_term", start, end, ""))

    def priority_swap(self):
        toks, d = self.toks, self.d
        for i, t in enumerate(toks):
            if t.text != "if" or toks[i + 1].text != "(":
                continue
            ca = (i + 2, d.pairs[i + 1] - 1)
            sa = d.pairs[i + 1] + 1
            ea = self.block_end(sa)
            if ea is None or ea + 3 >= len(toks) or toks[ea + 1].text != "else" or toks[ea + 2].text != "if":
                continue
            j = ea + 2
            cb = (j + 2, d.pairs[j + 1] - 1)
            sb = d.pairs[j + 1] + 1
            eb = self.block_end(sb)
            if eb is None or not self.ok(i, eb):
                continue
            A, B = self.src[slice(*self.span(*ca))], self.src[slice(*self.span(*cb))]
            S1, S2 = self.src[slice(*self.span(sa, ea))], self.src[slice(*self.span(sb, eb))]
            if A.split() == B.split() or any(is_signal_name(x) and RESET_NAME.search(x.text)
                                             for x in toks[ca[0]:ca[1] + 1] + toks[cb[0]:cb[1] + 1]):
                continue                            # demoting reset is a giveaway, not a subtle bug
            src, p = self.src, self.span
            new = (B + src[p(*ca)[1]:p(sa, ea)[0]] + S2 + src[p(sa, ea)[1]:p(*cb)[0]] + A
                   + src[p(*cb)[1]:p(sb, eb)[0]] + S1)
            self.edits.append(("priority_swap", p(*ca)[0], p(sb, eb)[1], new))

    def off_by_one(self):
        toks, d = self.toks, self.d
        for i, t in enumerate(toks[:-2]):
            if t.text not in COMPARES or not d.is_binary(i) or (t.text == "<=" and d.lvalue_start(i) is not None):
                continue
            r = toks[i + 1]
            if not (is_signal_name(r) and d.signals.get(r.text) and toks[i + 2].text in OPERAND_END
                    and self.ok(i, i + 1)):
                continue
            for sign in "-+":
                self.edits.append(("off_by_one", r.start, r.end, f"({r.text} {sign} 1)"))

    def stage_swap(self):
        toks, d = self.toks, self.d
        pairs = set()
        # A register written only by `t <= s;` (and constant resets) is a pure delay stage.
        writers = Counter(toks[k].text for k, kind in d.target_tok.items()
                          if kind == "procedural" and toks[k + 1].text == "<=" and toks[k + 2].kind != "number")
        for i, t in enumerate(toks):
            s = d.lvalue_start(i) if t.text == "<=" else None
            if s is None or s != i - 1 or toks[i + 2].text != ";":
                continue
            T, S = toks[i - 1].text, toks[i + 1].text
            if (is_signal_name(toks[i + 1]) and T != S and S not in d.clocks and writers[T] == 1
                    and d.signals.get(T) is not None and d.signals.get(T) == d.signals.get(S)):
                pairs.add((T, S, i))
        for T, S, at in sorted(pairs):
            for k, tk in enumerate(toks):
                if k in (at - 1, at + 1) or k in d.target_tok or not d.mutable[k]:
                    continue
                if toks[k - 1].text == "." or (k + 1 < len(toks) and toks[k + 1].text == "("):
                    continue
                other = {S: T, T: S}.get(tk.text)
                if other and other not in d.reads_with.get(k, set()) | d.writes.get(k, set()):   # `in & ~in`, `x = x`
                    self.edits.append(("stage_swap", tk.start, tk.end, other))

    def width_trunc(self):
        toks, d = self.toks, self.d
        for i, t in enumerate(toks[:-4]):
            if t.text != "[" or i not in d.pairs or d.pairs[i] != i + 4 or toks[i + 2].text != ":":
                continue
            hi, lo = toks[i + 1].text, toks[i + 3].text
            if hi.isdigit() and lo.isdigit() and int(hi) - int(lo) >= 2 and self.ok(i, i + 4):
                self.edits.append(("width_trunc", toks[i + 1].start, toks[i + 1].end, str(int(hi) - 1)))

    def reset_missing(self):
        toks, d = self.toks, self.d
        for i, t in enumerate(toks):
            if t.text != "if" or toks[i + 1].text != "(":
                continue
            cond = toks[i + 2:d.pairs[i + 1]]
            if not any(is_signal_name(c) and RESET_NAME.search(c.text) for c in cond):
                continue
            b = d.pairs[i + 1] + 1
            if toks[b].text != "begin":
                continue
            e = self.block_end(b)
            stmts = [k for k in range(b + 1, e) if toks[k].text in ("<=", "=")
                     and (s := d.lvalue_start(k)) is not None and toks[s - 1].text in (";", "begin")]
            if len(stmts) < 2:
                continue
            for k in stmts:
                s, end = d.lvalue_start(k), d._statement_end(k)
                ls = self.src.rfind("\n", 0, toks[s].start) + 1
                le = self.src.find("\n", toks[end].end)
                le = len(self.src) if le == -1 else le + 1
                if self.src[ls:toks[s].start].strip() == "" and self.src[toks[end].end:le].strip() == "" \
                        and self.ok(s, end):
                    self.edits.append(("reset_missing", ls, le, ""))

    def blocking_assign(self):
        toks, d = self.toks, self.d
        for i, t in enumerate(toks):
            if t.text != "always" or toks[i + 1].text != "@" or toks[i + 2].text != "(":
                continue
            if not any(x.text == "posedge" for x in toks[i + 3:d.pairs[i + 2]]):
                continue
            body = d.pairs[i + 2] + 1
            end = self.block_end(body) if toks[body].text == "begin" else self._always_end(body)
            if end is None:
                continue
            for k in range(body, end + 1):
                if toks[k].text != "<=" or (s := d.lvalue_start(k)) is None or not is_signal_name(toks[s]):
                    continue
                name = toks[s].text
                after = d._statement_end(k)
                if any(toks[m].text == name and m not in d.target_tok for m in range(after + 1, end + 1)):
                    self.edits.append(("blocking_assign", toks[k].start, toks[k].end, "="))

    def _always_end(self, k):
        """End of an always body that is a bare if/else chain (no begin)."""
        toks = self.toks
        nxt = next((m for m in range(k, len(toks)) if toks[m].text in ("always", "assign", "endmodule")
                    or (toks[m].text in ("reg", "wire") and toks[m - 1].text in (";", "end"))), len(toks))
        return nxt - 1


def _module_edits(src):
    s = Scan(src)
    for kind in ("drop_term", "priority_swap", "off_by_one", "stage_swap", "width_trunc",
                 "reset_missing", "blocking_assign"):
        getattr(s, kind)()
    return s.edits


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    mutants, dupes = mutate_subtle(open(path).read())
    print(f"{path}: {len(mutants)} mutants ({dupes} duplicates dropped)\n")
    for n, m in enumerate(mutants, 1):
        print(f"{n:4d}  {m['op']:<15} L{m['line']:<4} {m['before'][:90]}")
        print(f"{'':26}-> {m['after'][:90]}")
    print()
    for op, c in sorted(Counter(m["op"] for m in mutants).items()):
        print(f"  {op:<15} {c}")
