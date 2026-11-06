# Test-writing failures on the correct design: audit

When a model's test suite fails on the correct (golden) design, it scores 0 (strict). Each such
failure is either the model's mistake or a spec gap (the spec doesn't pin the behavior down).
We check every one in simulation with `scripts/patch_rerun.py`:

1. Replace only the failing check(s) with the value the spec states.
2. Rerun that test alone on the golden design, to the end.
3. Count it as the model's mistake only if the test then passes and every patched value is
   stated by a spec sentence. Otherwise it's a spec gap, and the spec gets fixed.

Patches are saved next to each run as `golden_failure_patches.json`, so anyone can rerun them.

## ef_gpio8, Luna (results/testgen_openai-gpt-6-luna_20261106-0028), 2026-11-05

9 of 13 suites failed on golden (12 tests). After patching, all 12 pass to the end.
**0 spec gaps.**

| Mistake | Tests | Spec sentence it contradicts |
|---|---|---|
| Expected RIS = 0 after reset, or forgot that the other pins' level bits keep setting | #10, #1, #6, #7, #12 (6 tests) | "Because bits 0-15 are levels, once s[i] is defined one of bits i and 8+i sets on every rising edge." |
| Forgot RIS bits are sticky (expected high-level bits gone, or set for pins that never went high) | #1, #4 | "RIS bits are sticky otherwise: they stay 1 after the condition goes away." |
| Clock timing: its read helper waits extra clock edges, so it missed the one-cycle clear window or the 2-flop delay | #11, #4 | "IC holds the written value for one cycle ..." and the input-path latency paragraph |
| Lost track of its own state (mask still set, a bit it had cleared, a bit it had set) | #5, #0, #4, #10 | MIS = RIS AND IM |
| Read PRDATA without pointing PADDR at DATAI | #0, #7 | "PRDATA is combinational and depends only on PADDR[15:0]" |

Terra on the same seed: 13 of 13 suites passed on golden (nothing to audit).

## oc_i2c, first runs on spec v1 (2026-11-05): a spec gap found, runs superseded

Terra (results/testgen_openai-gpt-6.1-terra_20261106-0316): 33.3% strict, 8 of 13 suites failed on golden
(16 tests). All 16 pass to the end after patching (`golden_failure_patches.json`), **0 spec gaps**:

| Mistake | Tests | What the spec says |
|---|---|---|
| Asserted TIP = 0 the first time it saw IF = 1 | 13 (7 suites) | TIP is a register loaded with RD OR WR every clock, and CR's RD/WR clear on the same edge IF sets, so TIP stays 1 for one clock after IF rises |
| Asserted BUSY = 0 the first time it saw IF after a STOP | 1 | BUSY clears on the filtered STOP condition, a few clocks after `done` |
| Waited 60 clocks for BUSY with the filter counter loaded from a large PRER | 1 (plus a TIP check) | the filter counter reloads only when it reaches 0 |

Luna (results/testgen_openai-gpt-6-luna_20261106-0318): 0% strict, 12 of 13 suites failed on golden.
**One spec gap found:** 9 of the 12 suites ran transfers at PRER = 0 or 1, and at those speeds the
golden master reports a false arbitration loss on every command (filtered SDA lags the released line
during the WRITE check step; shown by a PRER 0-5 sweep and an `al_bit` probe). The spec never said
so. Fix: spec.md now states that transfers need PRER ≥ 2. Luna also made plain mistakes the spec
covers (read address 3 expecting TXR, which is at address 5; forgot the target pointer increments
after each data byte; never loaded the address into TXR), but its score on spec v1 is not meaningful.

Both models were re-asked on the fixed spec; those runs are the ones reported.

## oc_i2c on the fixed spec (spec v2), reported runs (2026-11-05)

Terra (results/testgen_openai-gpt-6.1-terra_20261106-0649): **40.0% strict, 69.6% partial**; 6 of 13 suites
passed golden and caught 34-35 of 40 hidden bugs each. 7 suites failed on golden (15 tests): all 15 pass to
the end after patching, **0 spec gaps**. Same mistakes as on spec v1: TIP checked in the one clock it overlaps
IF (12 tests, 6 suites), BUSY checked the moment IF rises after a STOP (1), and SCL pulled low in the same
clock the master's START appeared, so the target (which needs SCL high on two samples) never saw the START (1).
Across both Terra runs, 13 of 26 suites made the TIP/IF mistake: the spec implies the one-clock overlap
(TIP is a register copy of RD|WR; RD/WR clear on the edge IF sets) but never says it in words.

Luna (results/testgen_openai-gpt-6-luna_20261106-0652): **15.8% strict, 20.6% partial**; 3 suites passed
golden (26-29 of 40 caught). 10 suites failed on golden (15 tests). **0 spec gaps found.** 6 tests pass to the
end after patching. The other 9 were patched past 2-3 failures each in simulation (each one a mistake the spec
covers) but were not chased to the end; they stack many mistakes. Mistakes seen:
- addressed the target without loading TXR (address 0x00, NACK) (#3, #10)
- read address 3 expecting TXR (it is RXR; TXR is at 5), address 4 expecting CR (it is SR; CR is at 6) (#0, #11, #12)
- forgot the first byte after the address is the pointer, or that the pointer increments after each data byte (#6, #9, #12)
- expected RxACK = 0 after a read it ended with NACK (RxACK takes the master's own NACK) (#4, #5, #12)
- checked BUSY the moment IF rose after a STOP (#0, #4, #9, #10, #11)
- enabled the core with PRER = 0xFFFF, so the filter counter loaded 0x3FFF and stalled BUSY (#2)
- a Wishbone write helper that drops WE before the write edge (#9); CR with RD and WR both set (#11);
  IEN left 0 but expected the interrupt (#0); cocotb writes during ReadOnly (#11)
