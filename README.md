# rtl-env

Machine-generated, simulation-checked tasks for training and evaluating AI models on chip design verification.

Correct Verilog designs (seeds) are turned into graded tasks automatically: bugs are injected or logic is removed, the seed's tests are run on every candidate, and only candidates the tests catch are kept. A model sees the spec and the broken or incomplete file; the golden design and tests stay hidden and decide the reward. All tools are open source: Icarus Verilog, cocotb, Yosys.

## Pipeline

```
seed design + spec + cocotb tests
  -> inject bugs / remove logic          (scripts/inject.py, scripts/holes.py)
  -> simulate, keep what the tests catch (scripts/sim.py, scripts/build_tasks.py)
  -> task: spec.md + buggy.v, hidden golden.v + tests/
  -> grade: interface and cheat checks, then hidden tests (scripts/grade.py)
```

Task types:

| Type | What the model gets |
|---|---|
| bugfix | One injected bug |
| multibug | 2 to 5 bugs at once; each is caught by the tests on its own, so fixing only some still fails |
| complete | A chunk of logic replaced with `// TODO: missing logic` (an assign, a case branch, an always block, a whole submodule, or two submodules) |
| module | Only the module headers; write the whole design |

Bug types: comparison, logic, and arithmetic operator flips, dropped negations, constant changes, inverted conditions, deleted statements, same-width signal swaps, and state-name swaps. Generation is deterministic, and task ids are content hashes, so rebuilding gives identical tasks.

## Current task set

729 tasks from 11 seeds, all verified: every task passes on its golden design and fails on its broken one.

| Seed | Description |
|---|---|
| counter, fifo, alu, priority_encoder, shift_reg, edge_detector, pwm | Small single-module designs |
| traffic_light, rr_arbiter, uart_tx | Small state machines |
| apb_intc | APB3 interrupt controller: 296 lines, 5 modules, input synchronizer, level/edge triggers, priorities with threshold, claim/complete, error responses; tested at 4 parameter settings |

Every seed's tests were checked against hand-planted bugs before use. On apb_intc that process found and closed four test blind spots.

## Results

pass@1, one sample per problem. Qwen2.5-Coder 7B runs locally through Ollama at temperature 0; GPT-5.6 Luna runs through the OpenAI API at reasoning effort medium.

| Benchmark | Qwen2.5-Coder 7B | GPT-5.6 Luna |
|---|---|---|
| Our small-seed tasks | 162/322 = 50.3% (single-bug only) | 559/560 = 99.8% |
| apb_intc, first build (single and 2 to 3 bugs) | | 120/130 = 92.3% |
| apb_intc, hard tasks (3 to 5 bugs across modules, fill-in, full design) | | 62/79 = 78.5% |
| CVDP v1.1.0, open-tool subset (302 problems) | 39/302 = 12.9% | 153/302 = 50.7% |
| CVDP bug fixing (cid016) | 4/35 = 11.4% | 22/35 = 62.9% |

CVDP by category:

| Category | Qwen2.5-Coder 7B | GPT-5.6 Luna |
|---|---|---|
| cid002 code completion | 11/94 | 44/94 |
| cid003 spec to RTL | 13/78 | 42/78 |
| cid004 code modification | 8/55 | 33/55 |
| cid007 lint / QoR | 3/40 | 12/40 |
| cid016 debugging / bug fixing | 4/35 | 22/35 |

Full summaries and per-task results are in `results/`.

## Findings

1. **Small single-module tasks are solved by frontier models.** Luna gets 99.8% on them, while about half of CVDP stays unsolved for it.
2. **CVDP difficulty tracks size and design type.** Luna passes 71% of CVDP prompts under 3k characters and 16% of those over 12k, and 10 to 19% when two or more modules are given. Bus-protocol, FSM-controller, and pipeline designs sit around 27 to 31%. Lint / QoR ("make it smaller") is the hardest category. Details: `results/cvdp/unsolved_patterns.md`.
3. **Frontier models fix injected bugs well even in a multi-module design.** On the hard apb_intc tasks, 11 of Luna's 17 misses were one self-inflicted error: while rewriting the file, it connected a submodule clock to a nonexistent net. Fixing only that wire makes all 11 pass. Adding more bugs (3 to 5) barely changed the pass rate.
4. **The 7B model fails systematically, not randomly.** It repeatedly "fixes" correct code that looks suspicious. On uart_tx, 42 of its 48 failures remove a correct `+ 1` from a shift index. On the FIFO, 11 failures make the same wrong edit to the `full` flag. Behavior like this is what RL on graded tasks should change.
5. **Writing code is harder than editing it for the 7B model.** About a third of its CVDP answers do not compile, while almost all of its edits to our files compile.

Caveats: one sample per problem; the CVDP numbers cover the 302-problem open-tool subset (its testbench and assertion categories need Cadence Xcelium); some category counts are small.

## Usage

Build the toolchain image once:

```bash
docker build -t rtl-env .
```

Build all tasks (wipes and regenerates `tasks/`):

```bash
docker run --rm -v "$(pwd)":/work rtl-env python3 scripts/build_tasks.py
```

Grade a candidate file against a task:

```bash
docker run --rm -v "$(pwd)":/work rtl-env python3 scripts/grade.py tasks/<task_id> fix.v
```

Run a local model (Ollama on the host):

```bash
docker run --rm -v "$(pwd)":/work rtl-env python3 scripts/run_model.py --model qwen2.5-coder:7b
```

Run an OpenAI model. Prices (USD per 1M tokens) and a budget are required, and nothing is sent without `--yes`. Load the key in your shell with `read -s OPENAI_API_KEY && export OPENAI_API_KEY`, never in a file:

```bash
docker run --rm -e OPENAI_API_KEY -v "$(pwd)":/work rtl-env python3 scripts/run_model.py --model openai:gpt-5.6-luna --price-in 1 --price-out 6 --budget 5 --yes
```

Useful filters: `--limit N`, `--task-types bugfix,multibug,complete,module`, `--seeds apb_intc`, `--jobs N`, `--resume results/<run>`.

Spending safeguards (`scripts/llm.py`): a cost estimate before any run; a hard budget enforced by reserving each request's worst-case cost before sending it; a token cap on every answer; no automatic retries; failed requests with unknown usage charged at their worst case.

CVDP is run from a separate checkout of [NVlabs/cvdp_benchmark](https://github.com/NVlabs/cvdp_benchmark), outside this repo; `scripts/cvdp_infer.py` answers its exported prompts with the same models and safeguards. CVDP data is never used for seeds or training.

## Layout

```
Dockerfile          Icarus Verilog, Verilator, Yosys, cocotb
designs/<seed>/     <seed>.v, spec.md, test_<seed>.py, Makefile, meta.json, optional configs.json (parameter sweep)
scripts/            inject.py, holes.py, sim.py, build_tasks.py, grade.py, llm.py, run_model.py, cvdp_infer.py
results/            model runs on our tasks, and CVDP results and analysis
tasks/              generated, not committed (rebuild with build_tasks.py)
```

## Next

- Agent mode: the model can compile, run a visible subset of tests, and iterate before submitting, through a standard reset / step / reward interface.
- More large seeds built to the CVDP difficulty profile, with permissive licenses and no overlap with CVDP's source repositories.
- A first RL training run on an open model, measured on CVDP.
