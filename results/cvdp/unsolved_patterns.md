# What makes CVDP hard: patterns in the 142 problems neither model solved

Source: CVDP v1.1.0 non-agentic no-commercial subset (302 problems), pass@1, graded 2026-10-09.
Models: GPT-5.6 Luna (153/302) and Qwen2.5-Coder 7B (39/302). Groups: both solved 32, Luna only 121,
Qwen only 7, neither 142. Patterns only; no CVDP problem content is copied here.

## Strongest predictors of failure (Luna pass rate)

| Feature | Luna pass rate |
|---|---|
| Prompt under 3k chars | 71% |
| Prompt 3k to 6k chars | 58% |
| Prompt 6k to 12k chars | 40% |
| Prompt over 12k chars | **16%** |
| Given RTL context of 1 to 49 lines | 77% |
| Given RTL context of 150 to 399 lines | **19%** |
| Context with 1 module | 59% |
| Context with 2+ modules | **10 to 19%** |

## By design type (keyword match, noisy)

| Topic | Luna pass rate |
|---|---|
| Bus protocols (AXI, APB, AHB, Wishbone) | 27% |
| FSM / protocol controllers | 31% |
| Pipelines | 31% |
| Memory / FIFO / cache | 44% |
| Serial (UART, SPI, I2C) | 44% |
| Arithmetic / DSP | 53% |

## By category: share unsolved by both models

| Category | Easy | Medium |
|---|---|---|
| cid002 code completion | 42% | 63% |
| cid003 spec to RTL | 29% | 59% |
| cid004 code modification | 23% | 52% |
| cid007 lint / QoR (make it smaller) | 55% | **83%** |
| cid016 bug fixing | 19% | 57% |

## How Luna fails on the 142

- 80: compiled, all tests fail (wrong logic)
- 18: compiled, some tests pass, some fail
- 16: did not compile; 15: did not build; 7: not smaller (QoR); 6: timeout
- Of 27 unsolved problems with several test configurations, Luna passed **some** configurations in 22.
  It often gets the default parameters right and breaks at other widths/depths: generality across
  parameters is a distinct weakness.

The 142 come from 101 distinct designs (e.g. elevator control, interrupt controller, APB GPIO,
AXI tap, matrix multiplier, load/store unit, digital filters), so this is broad, not a few hard designs.

## Implications for our next tasks

Our current seeds are all under 100 lines, 1 module, short specs: exactly the "easy" end above.
Harder tasks should be:
1. 150 to 400+ lines of RTL, 2+ modules (hierarchy).
2. Long, detailed specs (6k to 12k+ chars), with protocol rules and timing.
3. Bus/protocol controllers, FSM-heavy controllers, pipelines, memory controllers.
4. Parameterized, and tested across several parameter sets.
5. Include QoR tasks (reduce area, graded by tests + Yosys cell count): the hardest category, gradable with free tools.
