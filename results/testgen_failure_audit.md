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

## GPT-6 Astra on the two hardest designs (results/testgen_openai-gpt-6-astra_20261106-0708), 2026-11-05

5 suites each on ef_uart and oc_i2c (spec v2), $4.73 at list price. **UART 70.0% strict / 86.5% partial,
I2C 53.5% / 55.5%; overall 61.7% / 71.2%.** (Terra on the same designs: 12.1% and 40.0% strict.)
3 suites failed on golden (8 tests); all 8 pass to the end after patching, **0 spec gaps**:
- UART: lowered PR from 3 to 0 while the baud counter sat above 0. The spec: the counter "returns to 0
  after reaching PR, otherwise increments", so it counts up to its 16-bit wrap with no ticks (this is the
  quirk the UART spec already implies; confirmed by a probe of the failing loop iteration).
- I2C #0: read its own inputs in the same step it wrote them (cocotb applies writes after an await),
  first as Z, then as stale values in its Wishbone ACK model. Testbench mechanics, not the spec.
- I2C #4: BUSY checked 4 clocks after IF following a STOP (BUSY lags `done`); TIP checked in its one-clock
  overlap with IF; and expected the target to ACK after holding SCL low before a START. The spec's
  `slave_wait` sets only "when the master has just released SCL"; before a START SCL is already released,
  so the master drives SDA low while SCL is still held low, no START appears, and the target NACKs
  (shown by a target-state trace).

### Follow-up (same day): all remaining Luna tests run to the end, and a second, narrow spec gap

All 15 of Luna's failing tests now pass to the end after patching. One of them (#10) exposed a second gap,
and spec v2's own fix invited it: v2 said "transfers need PRER ≥ 2", Luna used exactly PRER = 2 with clock
stretching, and at PRER = 2 a repeated START issued within about 2 clocks of the previous byte, while the
target still holds SCL low, is generated with SDA falling while SCL is low. `slave_wait` sets in the same
clock `cnt` reaches 0, and the `cnt` = 0 rule wins. The target misses the START and shifts the address in
one bit late (shown by a master/target trace, then a sweep: PRER 2, gaps 0-2 fail; PRER 3-9, gaps 0-29
pass; no stretching passes everywhere). Fix (spec v3): with stretch_en = 1, use PRER ≥ 3.
**No reported score changes:** suite #10 also failed golden through its own verified mistakes (commands
sent without loading TXR, BUSY checked at IF), and no other suite in any I2C run hit this case.
The reported runs used spec v2; the tasks now carry v3.

## oc_aes (new design), 2026-11-06

Spec checked before any model run by an independent cycle model (scripts/spec_checks/test_oc_aes_spec.py),
which caught one draft error (decrypt restart rule), fixed before release.

Terra (results/testgen_openai-gpt-6.1-terra_20261106-0750): **84.6% strict**; 11 of 13 suites passed golden
and each caught all 40 hidden bugs. 2 suites failed golden (2 tests): both pass to the end after patching,
**0 spec gaps**. Both checked `done` after only one reset edge; the spec says done has no reset and is
computed from the counter value before each edge, so it is X until two edges have passed.

Luna (results/testgen_openai-gpt-6-luna_20261106-0753): **46.2% strict**; 6 passed, 5 failed golden, 2 were
refused by the grader (they used `getattr`, which the prompt bans). Of the 7 failing tests, 6 pass to the end
after patching and 1 fails on its own Python AES before touching the design (its implementation gives the
wrong FIPS-197 ciphertext). **0 spec gaps.** Mistakes: misremembered NIST SP 800-38A vectors (a garbled
plaintext, a shifted pair); decrypted a block with another vector's key loaded; read the text outputs as
integers while X (unspecified outside the done clock); expected done one clock early (twice in one suite);
checked done at the very first edge.

## oc_spi (new design), 2026-11-06

Spec checked before release by an independent cycle model (scripts/spec_checks/test_oc_spi_spec.py; 0
mismatches on 299,975 values). The model cannot catch ambiguous wording, and Terra found one:

- **Spec v1 runs** (Terra results/testgen_openai-gpt-6.1-terra_20261106-0759: 76.9% strict; Luna
  ...-luna_20261106-0802, not graded): Terra suite #6 failed golden only because the spec did not say that
  a FIFO write on an edge where SPE = 0 still stores its byte (Terra's model skipped the store). With only
  that line fixed in Terra's model, its whole test passes to the end. **Spec gap, fixed**; both runs redone.
- **Spec v2 runs (reported).** Terra (...-terra_20261106-0807): **100% strict**, 13 of 13 suites pass golden
  and each catches all 40 hidden bugs. Luna (...-luna_20261106-0809): **6.9% strict**; 11 suites failed
  golden (21 tests), 1 refused (private attributes `_rrp`, `_rwp`). Verified: 6 tests pass to the end after
  patching (5 only converted X to int: the FIFO storage is never cleared, so SPDR reads and MOSI in IDLE are X
  until written; 1 also wrote 0x54 for "CPOL = 1", which sets CPHA). **Not verified to the end:** the other
  15 tests; their first remaining failures are Luna's own bookkeeping (status values that contradict its
  comments, a FIFO it expects to stay full after an overwriting write, clock edges off by one). No spec gap
  was seen in them, but they were not run to the end.

## oc_fpu (new design), 2026-11-06: a spec gap found by Terra, runs superseded

Before release the spec passed its cycle-model check (681,494 values) and a handful of targeted probes.
The first runs (Terra results/testgen_openai-gpt-6.1-terra_20261106-1019: 0% strict, all 13 suites failed
golden; Luna ...-luna_20261106-1021: 13.5%) showed it was still wrong. Terra's suites aim straight at
boundary cases that random traffic rarely makes, and most of them failed on the underflow flag of
multiply/divide results near the smallest normal number. A structured boundary sweep (144,988 operations:
every pair from powers of two, their neighbors one unit up and down, extremes and subnormals, all ops and
modes) confirmed **the spec was wrong** there: for multiply and divide results that are zero, subnormal or
±2^-126, the design's rounding (exact results one unit too large, ties away from zero, the 2^-150 case
both ways) and its underflow flag (missing for some tiny inexact quotients, raised for a product slightly
above 2^-126) do not follow any simple rule. Terra was right on those checks (including one I had first
counted as its mistake: a product slightly above 2^-126 is not tiny, before or after rounding).
**Fix (spec v2):** for multiply/divide of finite nonzero operands with such results, `out`, `zero` and
`underflow` are not specified (deviation 2); the checker masks them there. Validated on 202,004 recorded
operations (0 violations) and by the cycle model (656,195 values, 0 mismatches). Tasks were rebuilt and
both models re-run on spec v2; those are the reported runs.

Reported runs on spec v2: Terra (results/testgen_openai-gpt-6.1-terra_20261106-1040) **86.5% strict**, 12 of
13 suites pass golden; the 13th crashes in its own Python reference model (`pack()` called with 9 arguments,
defined with 7 or 8): Terra's mistake. Luna (...-luna_20261106-1042) **0% strict**: 9 suites refused by the
grader (`getattr`, `.format`), 4 failed golden, each at its first failure on a check that contradicts an
explicit spec sentence: underflow expected from an exact-zero add (spec: add/subtract never underflow);
(+0) × (-0) expected +0 (spec: sign is the XOR, -0); qnan expected 0 for qNaN + qNaN (spec: qnan means the
result is a NaN); and a check of a tiny divide result the spec leaves unspecified. These 4 were not run to the end.
