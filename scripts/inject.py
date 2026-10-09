"""Generate every single-bug variant of a Verilog design.

    python3 scripts/inject.py <design.v>

Each mutant changes one spot. The rest of the file (formatting, comments)
is untouched. No randomness: the same input always gives the same mutants
in the same order.

Only logic in the module body is mutated. The module header, declarations
(ports, regs, parameters) and sensitivity lists like @(posedge clk) are
left alone, since bugs there are mostly compile errors or change the spec.
"""

import re
import sys
from collections import Counter, namedtuple

Tok = namedtuple("Tok", "kind text start end")

TOKEN_RE = re.compile(r"""
    (?P<comment>//[^\n]*|/\*.*?\*/)
  | (?P<string>"(?:\\.|[^"\\])*")
  | (?P<space>\s+)
  | (?P<number>\d*\s*'[sS]?[bBoOdDhH]\s*[0-9a-fA-FxXzZ_?]+|'[01xXzZ]|\d[\d_]*)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_$]*|\$[A-Za-z_][A-Za-z0-9_$]*|`[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>===|!==|<<<|>>>|==|!=|<=|>=|&&|\|\||<<|>>|\*\*|~&|~\||~\^|\^~|[-+*/%<>=!~&|^?:;,.()\[\]{}@#])
""", re.S | re.X)

KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "reg", "wire", "logic",
    "integer", "parameter", "localparam", "assign", "always", "initial", "begin",
    "end", "if", "else", "case", "casez", "casex", "endcase", "default", "posedge",
    "negedge", "or", "for", "while", "genvar", "generate", "endgenerate",
    "function", "endfunction", "signed",
}
SIGNAL_KW = {"input", "output", "inout", "reg", "wire", "logic"}
DECL_KW = SIGNAL_KW | {"integer", "parameter", "localparam", "genvar"}
# A token right before an assignment's left-hand side. Tells `x <= y;`
# (assignment) apart from `a <= b` inside an expression (comparison).
STMT_START = {";", "begin", "end", "endcase", "else", ")", ":"}

COMPARE_SWAPS = {
    "==": ["!="], "!=": ["=="], "===": ["!=="], "!==": ["==="],
    "<": ["<=", ">"], "<=": ["<", ">="], ">": [">=", "<"], ">=": [">", "<="],
}
LOGIC_SWAPS = {"&&": ["||"], "||": ["&&"], "&": ["|"], "|": ["&"]}
ARITH_SWAPS = {"+": ["-"], "-": ["+"]}


def tokenize(src):
    toks, pos = [], 0
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m:
            raise ValueError(f"line {src.count(chr(10), 0, pos) + 1}: can't read {src[pos:pos + 20]!r}")
        if m.lastgroup not in ("space", "comment"):
            toks.append(Tok(m.lastgroup, m.group(), m.start(), m.end()))
        pos = m.end()
    return toks


def bracket_pairs(toks):
    """Map each (, [, { index to its closer and back."""
    pairs, stack = {}, []
    for i, t in enumerate(toks):
        if t.text in "([{":
            stack.append(i)
        elif t.text in ")]}":
            j = stack.pop()
            pairs[i], pairs[j] = j, i
    return pairs


def is_signal_name(t):
    return t.kind == "ident" and t.text not in KEYWORDS and t.text[0] not in "$`"


class Design:
    def __init__(self, src):
        self.src = src
        self.toks = tokenize(src)
        self.pairs = bracket_pairs(self.toks)
        self.signals = {}      # name -> width text, e.g. "[AW:0]" or "" for 1 bit
        self.inputs = set()
        self.clocks = set()    # anything in @(...): never swapped in or out
        self.states = {}       # localparam name -> bit size, for `localparam S_IDLE = 2'd0;`
        self.mutable = [True] * len(self.toks)
        self._scan()
        # Clocks passed down to submodules never appear in an @(...) here; catch them by name.
        self.clocks |= {n for n in self.signals if re.search(r"(^|_)(p?clk|clock)$", n, re.I)}
        self._find_assignments()

    def text(self, i, j):
        """Source text covering tokens i..j inclusive."""
        return self.src[self.toks[i].start:self.toks[j].end]

    def _freeze(self, i, j):
        for k in range(i, j + 1):
            self.mutable[k] = False

    def _statement_end(self, i):
        depth = 0
        for k in range(i, len(self.toks)):
            t = self.toks[k].text
            if t in "([{":
                depth += 1
            elif t in ")]}":
                depth -= 1
            elif t == ";" and depth == 0:
                return k
        return len(self.toks) - 1

    def _scan(self):
        toks, i = self.toks, 0
        while i < len(toks):
            t = toks[i].text
            if t == "module":
                end = self._statement_end(i)   # header ends at its ';'
                self._parse_decls(i, end)
                self._freeze(i, end)
                i = end + 1
            elif t in DECL_KW and (i == 0 or toks[i - 1].text in (";", "begin", "end")):
                end = self._statement_end(i)
                self._parse_decls(i, end)
                self._freeze(i, end)
                # `wire x = a && b;` -- the logic after '=' is fair game.
                if t in ("wire", "logic"):
                    eq = next((k for k in range(i, end) if toks[k].text == "="), None)
                    if eq is not None:
                        for k in range(eq + 1, end):
                            self.mutable[k] = True
                i = end + 1
            elif t == "@":
                if i + 1 < len(toks) and toks[i + 1].text == "(":
                    self.clocks |= {x.text for x in toks[i + 1:self.pairs[i + 1]] if is_signal_name(x)}
                    self._freeze(i, self.pairs[i + 1])
                    i = self.pairs[i + 1] + 1
                else:
                    self._freeze(i, i + 1)     # @*
                    i += 2
            elif t == "endmodule":
                self._freeze(i, i)
                i += 1
            elif t == "." and i + 1 < len(toks):
                self._freeze(i, i + 1)         # `.port(sig)`: the port name is not ours to change
                i += 2
            else:
                i += 1

    def _parse_decls(self, i, end):
        """Record signal names and widths from declarations in tokens i..end."""
        toks, k = self.toks, i
        while k <= end:
            t = toks[k].text
            if t in ("parameter", "localparam"):
                k += 1
                # One or more `NAME = value` pairs separated by commas.
                while k + 1 <= end and is_signal_name(toks[k]) and toks[k + 1].text == "=":
                    name, v = toks[k].text, k + 2
                    k = v
                    while k <= end and toks[k].text not in (",", ";", ")"):
                        k = self.pairs.get(k, k) + 1 if toks[k].text in "([{" else k + 1
                    if t == "localparam" and k - 1 == v and toks[v].kind == "number" and "'" in toks[v].text:
                        # A sized constant like 2'd1: likely a state encoding.
                        self.states[name] = toks[v].text.split("'")[0]
                    if k <= end and toks[k].text == "," and k + 2 <= end and toks[k + 2].text == "=":
                        k += 1
                        continue
                    break
                k += 1
                continue
            if t not in SIGNAL_KW:
                k = self.pairs.get(k, k) + 1 if t in "[{" else k + 1
                continue
            is_input = False
            while k <= end and toks[k].text in SIGNAL_KW | {"signed"}:
                is_input |= toks[k].text == "input"
                k += 1
            width = ""
            if k <= end and toks[k].text == "[":
                width = self.text(k, self.pairs[k]).replace(" ", "")
                k = self.pairs[k] + 1
            while k <= end and is_signal_name(toks[k]):
                name = toks[k].text
                k += 1
                if k <= end and toks[k].text == "[":       # memory array: never swapped
                    k = self.pairs[k] + 1
                    name = None
                if k <= end and toks[k].text == "=":       # skip initializer
                    k += 1
                    while k <= end and toks[k].text not in (",", ";", ")"):
                        k = self.pairs.get(k, k) + 1 if toks[k].text in "([{" else k + 1
                if name:
                    self.signals[name] = width
                    if is_input:
                        self.inputs.add(name)
                if k <= end and toks[k].text == "," and k + 1 <= end and is_signal_name(toks[k + 1]):
                    k += 1
                    continue
                break

    def _find_assignments(self):
        """For every assignment, note which token is the signal being written
        (the target) and which tokens are read to compute it."""
        self.target_tok = {}    # token index -> "procedural" | "continuous"
        self.writes = {}        # token index -> name of the signal its statement writes
        self.regs = set()       # signals written in always blocks
        self.reads_with = {}    # token index -> signals in the same expression
        for i, t in enumerate(self.toks):
            if t.text not in ("<=", "="):
                continue
            s = self.lvalue_start(i)
            if s is not None:
                kind = "procedural"
            elif i >= 2 and is_signal_name(self.toks[i - 1]) and self.toks[i - 2].text == "assign":
                s, kind = i - 1, "continuous"
            elif i >= 1 and is_signal_name(self.toks[i - 1]) and self.toks[i - 1].text in self.signals:
                s, kind = i - 1, "declaration"    # wire x = ...;
            else:
                continue
            if self.toks[s].text == "{":
                # `{carry, y} = ...` writes every signal inside the braces.
                targets = [k for k in range(s + 1, self.pairs[s]) if is_signal_name(self.toks[k])]
                body_start = self.pairs[s] + 1
            elif is_signal_name(self.toks[s]):
                targets, body_start = [s], s + 1
            else:
                continue
            names = {self.toks[k].text for k in targets}
            for k in targets:
                self.target_tok[k] = kind
            if kind == "procedural":
                self.regs |= names
            end = self._statement_end(i)
            for k in range(body_start, end + 1):
                self.writes[k] = names
            self._group(body_start, end)
        for i, t in enumerate(self.toks):
            if t.text == "if" and i + 1 < len(self.toks) and self.toks[i + 1].text == "(":
                self._group(i + 2, self.pairs[i + 1] - 1)

    def _group(self, i, j):
        """Remember which signals are read together in tokens i..j."""
        names = {self.toks[k].text for k in range(i, j + 1) if is_signal_name(self.toks[k])}
        for k in range(i, j + 1):
            self.reads_with[k] = names

    def swap_options(self, i):
        """Same-width signals that could plausibly be typed instead of token i."""
        name = self.toks[i].text
        if name in self.clocks:
            return []
        kind = self.target_tok.get(i)
        if kind in ("continuous", "declaration"):
            return []                               # rewiring an output: mostly compile errors
        if kind == "procedural":
            pool = self.regs
        else:
            # Skip names already in this expression: `full && !full` is a giveaway.
            pool = set(self.signals) - self.writes.get(i, set()) - self.reads_with.get(i, set())
        width = self.signals[name]
        return sorted(n for n in pool - self.clocks - {name} if self.signals.get(n) == width)

    def is_binary(self, i):
        """True if the operator at i has a left operand (a & b), not unary (&a)."""
        if i == 0:
            return False
        p = self.toks[i - 1]
        return (p.kind == "number" or is_signal_name(p) or p.text in (")", "]", "}")
                or (p.kind == "ident" and p.text[0] in "$`"))

    def lvalue_start(self, i):
        """If the '<=' or '=' at i is a procedural assignment, return the index
        where its left-hand side starts. Otherwise None."""
        j = i - 1
        while j >= 0 and self.toks[j].text == "]":
            j = self.pairs[j] - 1
        if j >= 0 and self.toks[j].text == "}":
            j = self.pairs[j] - 1
        elif j >= 0 and is_signal_name(self.toks[j]):
            j -= 1
        else:
            return None
        if j == i - 1 or j < 0 or self.toks[j].text not in STMT_START:
            return None
        return j + 1


def lineno(src, pos):
    return src.count("\n", 0, pos) + 1


def mutate_number(text):
    """Nearby values for a numeric literal, in the same format."""
    if text.startswith("'"):
        return []                       # SystemVerilog fill literal like '0: no nearby value
    clean = text.replace("_", "").replace(" ", "")
    m = re.fullmatch(r"(\d*)'([sS]?)([bBoOdDhH])(.+)", clean)
    if not m:
        v = int(clean)
        return [str(v + 1)] + ([str(v - 1)] if v > 0 else [])
    size, signed, base, digits = m.groups()
    if re.search(r"[xXzZ?]", digits):
        return []
    radix = {"b": 2, "o": 8, "d": 10, "h": 16}[base.lower()]
    v = int(digits, radix)
    width = int(size) if size else None
    if width == 1:
        cands = [1 - v]
    else:
        cands = [v + 1] if width is None or v + 1 < 2 ** width else []
        cands += [v - 1] if v > 0 else []
    fmt = {2: "b", 8: "o", 10: "d", 16: "x"}[radix]
    out = []
    for c in cands:
        s = format(c, fmt).zfill(len(digits) if radix != 10 else 1)
        out.append(f"{size}'{signed}{base}{s.upper() if digits.isupper() else s}")
    return out


def module_spans(src):
    """Character spans of each module..endmodule, each starting at the beginning of
    its `module` line, so every module can be analyzed on its own."""
    spans, start = [], None
    for t in tokenize(src):
        if t.text == "module" and start is None:
            start = src.rfind("\n", 0, t.start) + 1
        elif t.text == "endmodule" and start is not None:
            spans.append((start, t.end))
            start = None
    return spans


def mutate(src):
    """Return a list of mutants, each a dict with op, line, before, after, source.
    Each module is analyzed separately, so signal names never cross modules."""
    edits = []
    for s, e in module_spans(src) or [(0, len(src))]:
        edits += [(op, a + s, b + s, new) for op, a, b, new in _module_edits(src[s:e])]
    return _build_mutants(src, edits)


def _module_edits(src):
    """All single-bug edits for one module: (op, start, end, replacement)."""
    d = Design(src)
    toks = d.toks
    edits = []

    def swap(op, t, options):
        for new in options:
            edits.append((op, t.start, t.end, new))

    for i, t in enumerate(toks):
        if not d.mutable[i]:
            continue
        x = t.text

        if x in COMPARE_SWAPS and not (x == "<=" and d.lvalue_start(i) is not None):
            swap("compare_flip", t, COMPARE_SWAPS[x])
        elif x in LOGIC_SWAPS and (x in ("&&", "||") or d.is_binary(i)):
            swap("logic_flip", t, LOGIC_SWAPS[x])
        elif x in ARITH_SWAPS and d.is_binary(i):
            swap("arith_flip", t, ARITH_SWAPS[x])
        elif x in ("!", "~") and not d.is_binary(i):
            edits.append(("drop_not", t.start, t.end, ""))
        elif t.kind == "number":
            options = mutate_number(x)
            if i > 0 and toks[i - 1].text in ("+", "-"):
                options = [o for o in options if o != "0"]   # `x + 0` is a giveaway
            # `y <= 1;` on a 1-bit y: only 0 is a different value (2 truncates to 0).
            targets = d.writes.get(i, set())
            if x in ("0", "1") and toks[i - 1].text in ("<=", "=") and toks[i + 1].text == ";" \
                    and len(targets) == 1 and d.signals.get(next(iter(targets))) == "":
                options = ["1" if x == "0" else "0"]
            swap("const_change", t, options)
        elif x == "if" and i + 1 < len(toks) and toks[i + 1].text == "(":
            close = d.pairs[i + 1]
            inner = d.text(i + 2, close - 1)
            # `(!rst)` and `(!req[i])`, not `(!(rst))`; keep parens only around real expressions.
            simple = close == i + 3 or (is_signal_name(toks[i + 2]) and toks[i + 3].text == "["
                                        and d.pairs[i + 3] == close - 1)
            new = f"(!{inner})" if simple else f"(!({inner}))"
            edits.append(("invert_if", toks[i + 1].start, toks[close].end, new))
        elif x in d.signals:
            swap("signal_swap", t, d.swap_options(i))
        elif x in d.states:
            # Wrong state name: `state <= S_GREEN` where S_RED was meant, or a wrong case label.
            others = sorted(n for n, size in d.states.items() if size == d.states[x] and n != x)
            swap("state_swap", t, others)

        # Delete a whole assignment, but only when it sits alone on its own line
        # inside a sequence of statements, so the result still reads naturally.
        if x in ("<=", "=") and (s := d.lvalue_start(i)) is not None \
                and toks[s - 1].text in (";", "begin", "end", "endcase"):
            e = d._statement_end(i)
            line_start = src.rfind("\n", 0, toks[s].start) + 1
            line_end = src.find("\n", toks[e].end)
            line_end = len(src) if line_end == -1 else line_end + 1
            if src[line_start:toks[s].start].strip() == "" and src[toks[e].end:line_end].strip() == "":
                edits.append(("delete_stmt", line_start, line_end, ""))

    return edits


def _build_mutants(src, edits):
    mutants, seen = [], {src}
    dupes = 0
    for op, start, end, new in edits:
        out = src[:start] + new + src[end:]
        if out in seen:
            dupes += 1
            continue
        seen.add(out)
        line = lineno(src, start)
        before = src.splitlines()[line - 1].strip()
        after = "(line deleted)" if op == "delete_stmt" else out.splitlines()[line - 1].strip()
        if re.search(r"\?\s*([\w']+)\s*:\s*\1\s*[;)]", after):
            continue    # `c ? 0 : 0` gives the same answer both ways: a giveaway
        mutants.append({"op": op, "line": line, "before": before, "after": after, "source": out,
                        "edit": (start, end, new)})
    return mutants, dupes


def combine(src, mutants):
    """Apply several single-bug edits at once. Returns None if any two overlap."""
    edits = sorted((m["edit"] for m in mutants), key=lambda e: e[0])
    for (s1, e1, _), (s2, _, _) in zip(edits, edits[1:]):
        if s2 < e1 or s1 == s2:
            return None
    out = src
    for start, end, new in reversed(edits):     # back to front, so earlier offsets stay valid
        out = out[:start] + new + out[end:]
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    mutants, dupes = mutate(open(path).read())
    print(f"{path}: {len(mutants)} mutants ({dupes} duplicates dropped)\n")
    for n, m in enumerate(mutants, 1):
        print(f"{n:4d}  {m['op']:<13} L{m['line']:<3} {m['before']}")
        print(f"{'':24}-> {m['after']}")
    print()
    for op, c in sorted(Counter(m["op"] for m in mutants).items()):
        print(f"  {op:<13} {c}")
