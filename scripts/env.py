"""Agent-mode environment: a task as a sandboxed workspace plus tools.

    env = RTLEnv(task_dir)
    obs = env.reset()                          # instructions, spec, file list
    obs, reward, done = env.step("read_file", {"path": "fifo.v"})
    ...
    obs, reward, done = env.step("submit", {}) # hidden checks run once: reward 1.0 or 0.0

The workspace holds only what the model may see: spec.md and the design file
(the task's buggy or incomplete version, named <top>.v). The golden design and
hidden tests stay in the task folder and never enter the workspace. File tools
refuse paths outside the workspace, absolute paths, and symlinks.

Visible checks (run_tests) are a hint: for seeds with hand-written tests, a few of
those tests at the first parameter setting; for differential seeds, one short run
with a random seed the hidden check never uses. The reward comes only from submit,
which runs grade.py (all hand tests, every parameter setting, full differential).
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from grade import grade
from sim import result, run_hand_tests

MAX_READ_LINES = 2000
MAX_FILE_BYTES = 400_000
MAX_OUTPUT_CHARS = 6000
VISIBLE_TESTS_DEFAULT = 2

TASK_TEXT = {
    "bugfix": "The design does not match its specification: it contains exactly one bug. Find and fix it.",
    "multibug": "The design does not match its specification: it contains one or more bugs. Find and fix all of them.",
    "complete": "Some logic in the design was removed and replaced with a `// TODO: missing logic` comment. "
                "Write the missing logic so the design matches its specification.",
    "module": "The design has only its module interfaces. Write the module bodies so the design matches its specification.",
}

TOOLS = {
    "read_file": {"description": "Read a file in the workspace, with line numbers. Optional start/end lines (1-based, inclusive).",
                  "params": {"path": "string", "start": "integer?", "end": "integer?"}},
    "search": {"description": "Find lines containing a text (literal, case-sensitive) in workspace files. Returns file:line: text.",
               "params": {"text": "string", "path": "string?"}},
    "replace": {"description": "Replace an exact piece of text in a file. `old` must occur exactly once; include enough "
                               "surrounding text to make it unique. Prefer this over write_file for small changes.",
                "params": {"path": "string", "old": "string", "new": "string"}},
    "write_file": {"description": "Overwrite a whole file with new content.", "params": {"path": "string", "content": "string"}},
    "compile": {"description": "Compile the design with Icarus Verilog; returns errors and warnings (implicit wires, port and range issues).",
                "params": {}},
    "run_tests": {"description": "Run the visible checks against the current design and show what fails. The final grade uses "
                                 "more and longer checks than these.", "params": {}},
    "submit": {"description": "Submit the current design for final grading. Ends the task.", "params": {}},
}


class RTLEnv:
    def __init__(self, task_dir, max_steps=12, tool_timeout=60):
        self.task_dir = Path(task_dir).resolve()
        self.meta = json.loads((self.task_dir / "meta.json").read_text())
        self.max_steps, self.tool_timeout = max_steps, tool_timeout
        self.design_name = f"{self.meta['top']}.v"
        self.ws = None
        self.steps, self.done, self.reward, self.final = 0, False, None, None
        self.log = []

    # ----- lifecycle -------------------------------------------------------
    def reset(self):
        if self.ws:
            shutil.rmtree(self.ws, ignore_errors=True)
        self.ws = Path(tempfile.mkdtemp(prefix="ws_"))
        shutil.copy(self.task_dir / "spec.md", self.ws / "spec.md")
        shutil.copy(self.task_dir / "buggy.v", self.ws / self.design_name)
        self.steps, self.done, self.reward, self.final, self.log = 0, False, None, None, []
        return self.intro()

    def intro(self):
        """The first observation: task, rules, files, and the spec. Built from the task
        folder, so it can be computed without a workspace (for cost estimates)."""
        spec = (self.task_dir / "spec.md").read_text()
        design = (self.task_dir / "buggy.v").read_text()
        files = (f"  {self.design_name}  ({len(design.splitlines())} lines)\n"
                 f"  spec.md  ({len(spec.splitlines())} lines)")
        task = TASK_TEXT[self.meta.get("task_type", "bugfix")]
        return (f"{task}\n\nRules: keep every module name, port, and parameter exactly the same. "
                f"You have {self.max_steps} tool calls; finish with submit. Use compile and run_tests to check your work.\n\n"
                f"Workspace files:\n{files}\n\nThe design is in {self.design_name}. The specification (also in spec.md):\n\n"
                f"{spec.strip()}")

    def close(self):
        if self.ws:
            shutil.rmtree(self.ws, ignore_errors=True)
            self.ws = None

    def step(self, tool, args):
        """Run one tool call. Returns (observation text, reward or None, done)."""
        if self.done:
            return "The task is already finished.", self.reward, True
        self.steps += 1
        fn = getattr(self, f"_t_{tool}", None)
        if fn is None or tool not in TOOLS:
            obs = f"Unknown tool {tool!r}. Tools: {', '.join(TOOLS)}."
        else:
            try:
                obs = fn(**(args or {}))
            except (TypeError, ValueError) as e:
                obs = f"Bad arguments for {tool}: {e}"
            except ToolError as e:
                obs = f"Error: {e}"
        left = self.max_steps - self.steps
        if not self.done and 0 < left <= 3:
            obs += f"\n\n({left} tool call{'s' if left > 1 else ''} left. Call submit before you run out.)"
        if not self.done and self.steps >= self.max_steps:
            self.done, self.reward = True, 0.0
            self.final = {"status": "no_submit", "reason": f"used all {self.max_steps} tool calls without submitting"}
            obs += f"\n\nOut of tool calls ({self.max_steps}); the task ended without a submission."
        obs = obs if len(obs) <= MAX_OUTPUT_CHARS else obs[:MAX_OUTPUT_CHARS] + "\n... (output truncated)"
        self.log.append({"step": self.steps, "tool": tool, "args": _short(args), "output": obs[:2000]})
        return obs, self.reward, self.done

    def current_design(self):
        return (self.ws / self.design_name).read_text()

    # ----- sandbox ---------------------------------------------------------
    def _path(self, path, must_exist=True):
        if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
            raise ToolError("paths must be relative to the workspace, without '..'")
        p = self.ws / path
        if p.is_symlink() or not p.resolve().is_relative_to(self.ws.resolve()):
            raise ToolError("that path is outside the workspace")
        if must_exist and not p.is_file():
            raise ToolError(f"no such file: {path} (files: {', '.join(sorted(x.name for x in self.ws.iterdir()))})")
        return p

    # ----- tools -----------------------------------------------------------
    def _t_read_file(self, path, start=None, end=None):
        lines = self._path(path).read_text().splitlines()
        a = max(1, int(start or 1))
        b = min(len(lines), int(end or len(lines)), a + MAX_READ_LINES - 1)
        # A tab after the number, so the separator is never mistaken for indentation.
        body = "\n".join(f"{n:6}\t{lines[n - 1]}" for n in range(a, b + 1))
        more = (f"\n... ({len(lines) - b} more lines; read again with start={b + 1})"
                if b < len(lines) and (end is None or int(end) > b) else "")
        return f"{path}, lines {a}-{b} of {len(lines)}:\n{body}{more}"

    def _t_search(self, text, path=None):
        if not isinstance(text, str) or not text:
            raise ToolError("text must be a non-empty string")
        files = [self._path(path)] if path else sorted(self.ws.iterdir())
        hits = [f"{f.name}:{n}: {line.strip()}" for f in files
                for n, line in enumerate(f.read_text().splitlines(), 1) if text in line]
        if not hits:
            return f"No lines contain {text!r}."
        return "\n".join(hits[:60]) + (f"\n... ({len(hits) - 60} more)" if len(hits) > 60 else "")

    def _t_replace(self, path, old, new):
        p = self._path(path)
        if not isinstance(old, str) or not isinstance(new, str):
            raise ToolError("old and new must be strings")
        src = p.read_text()
        n = src.count(old) if old else 0
        note = ""
        if n == 1:
            out = src.replace(old, new)
            line = src[:src.index(old)].count("\n") + 1
        else:
            fuzzy = _replace_ignoring_indent(src, old, new) if n == 0 else None
            if fuzzy is None:
                return (f"Not replaced: the text occurs {n} times in {path}; it must occur exactly once."
                        + (_closest(src, old) if n == 0 else ""))
            out, line = fuzzy
            note = " (matched ignoring indentation)"
        if len(out.encode()) > MAX_FILE_BYTES:
            raise ToolError("the file would be too large")
        p.write_text(out)
        return f"Replaced 1 occurrence in {path} at line {line}{note}."

    def _t_write_file(self, path, content):
        p = self._path(path, must_exist=False)
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        if p.name != self.design_name or p.parent != self.ws:
            raise ToolError(f"only {self.design_name} can be written")
        if len(content.encode()) > MAX_FILE_BYTES:
            raise ToolError("content too large")
        p.write_text(content)
        return f"Wrote {path} ({len(content.splitlines())} lines)."

    def _t_compile(self):
        cmd = ["iverilog", "-g2012", "-Wimplicit", "-Wportbind", "-Wselect-range", "-s", self.meta["top"],
               "-o", str(self.ws / ".compile.vvp"), str(self.ws / self.design_name)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.tool_timeout)
        except subprocess.TimeoutExpired:
            return "Compile timed out."
        (self.ws / ".compile.vvp").unlink(missing_ok=True)
        msgs = [l.replace(str(self.ws) + "/", "") for l in (r.stdout + r.stderr).splitlines() if l.strip()]
        if r.returncode != 0:
            return "Compile FAILED:\n" + "\n".join(msgs[:40])
        return "Compiled OK." + ("\nWarnings:\n" + "\n".join(msgs[:40]) if msgs else " No warnings.")

    def _t_run_tests(self):
        r = visible_check(self.task_dir, self.current_design(), self.tool_timeout)
        if r["status"] == "pass":
            return f"Visible checks passed ({len(r['passed'])} runs). The final grade uses more and longer checks."
        head = {"fail": "Visible checks FAILED", "compile_error": "Did not compile", "timeout": "Timed out (likely an infinite loop)"}
        lines = [f"{head.get(r['status'], r['status'])}. Failed: {', '.join(r['failed'][:8]) or '-'}"]
        lines += _failure_lines(r["log"])
        return "\n".join(lines)

    def _t_submit(self):
        g = grade(self.task_dir, self.current_design())
        self.done, self.reward = True, 1.0 if g["status"] == "pass" else 0.0
        self.final = g
        return f"Submitted. Result: {g['status']}."


class ToolError(Exception):
    pass


def visible_tests(task_dir):
    """Names of the hand-written tests the agent may run: meta 'visible_tests', else
    the first two tests in the test file."""
    meta = json.loads((Path(task_dir) / "meta.json").read_text())
    if meta.get("visible_tests"):
        return meta["visible_tests"]
    names = []
    for f in sorted((Path(task_dir) / "tests").glob("test_*.py")):
        names += re.findall(r"@cocotb\.test\(\)\s*\nasync def (\w+)", f.read_text())
    return names[:VISIBLE_TESTS_DEFAULT]


def visible_check(task_dir, design_src, timeout=60):
    """Agent-visible checks: a subset of the hand tests (first parameter setting) and a
    short differential run with its own seed. Never the hidden reward."""
    task_dir = Path(task_dir)
    tests = task_dir / "tests"
    with tempfile.NamedTemporaryFile("w", suffix=".v", delete=False) as f:
        f.write(design_src)
    try:
        if (tests / "Makefile").exists():
            r = run_hand_tests(Path(f.name), tests, timeout, only=visible_tests(task_dir), first_config_only=True)
            if r["status"] != "pass" or not (tests / "diff.json").exists():
                return r
        else:
            r = result("pass", [], [], "")
        if (tests / "diff.json").exists():
            from diffcheck import diff_check
            d = diff_check(design_src, (task_dir / "golden.v").read_text(), tests, timeout, visible=True)
            return result(d["status"], r["passed"] + [f"diff:{t}" for t in d["passed"]],
                          [f"diff:{t}" for t in d["failed"]], (r["log"] + "\n" + d["log"]).strip())
        return r
    finally:
        Path(f.name).unlink(missing_ok=True)


def _indent(line):
    return len(line) - len(line.lstrip())


def _replace_ignoring_indent(src, old, new):
    """Find `old` as whole lines, comparing each line without leading/trailing
    whitespace. If exactly one block matches, replace it with `new`, shifted to the
    file's indentation. Returns (new source, line number) or None."""
    lines = src.split("\n")
    olds = old.strip("\n").split("\n")
    key = [l.strip() for l in olds]
    if not any(key):
        return None
    hits = [i for i in range(len(lines) - len(key) + 1)
            if [l.strip() for l in lines[i:i + len(key)]] == key]
    if len(hits) != 1:
        return None
    i = hits[0]
    j = next(k for k, l in enumerate(key) if l)
    shift = _indent(lines[i + j]) - _indent(olds[j])
    news = []
    for l in new.strip("\n").split("\n"):
        if shift >= 0:
            news.append(" " * shift + l if l.strip() else l)
        else:
            news.append(l[min(-shift, _indent(l)):])
    return "\n".join(lines[:i] + news + lines[i + len(key):]), i + 1


def _closest(src, old):
    import difflib
    first = next((l.strip() for l in old.split("\n") if l.strip()), "")
    lines = src.split("\n")
    best = difflib.get_close_matches(first, [l.strip() for l in lines], n=1, cutoff=0.6)
    if not best:
        return ""
    n = next(k for k, l in enumerate(lines) if l.strip() == best[0])
    return f"\nClosest line {n + 1}, exactly: {lines[n]!r}"


def _failure_lines(log):
    """The useful lines of a failing run: assertion and mismatch messages, compiler errors."""
    keep = []
    for l in log.splitlines():
        s = l.strip()
        if re.search(r"AssertionError:|Mismatch: |design\.v:\d+:|error:", s) and "raise " not in s:
            s = re.sub(r"/tmp/\w+/", "", s)
            if s not in keep:
                keep.append(s)
    return keep[:12] or ["(no details)"]


def _short(args):
    return {k: (v if not isinstance(v, str) or len(v) < 300 else v[:300] + f"... ({len(v)} chars)")
            for k, v in (args or {}).items()}
