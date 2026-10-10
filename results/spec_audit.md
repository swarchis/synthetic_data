# Spec audit from model failures (2026-10-10)

Method (`scripts/spec_audit.py`): for every failed Luna answer on a seed (one-shot and agent runs),
put each of the answer's modules into the golden design on its own and run the seed's differential
check. A module that breaks golden, in a task that injected no bug there, means the answer changed
correct behavior. Many independent answers breaking the same module points at a spec gap; each was
then checked by hand against the golden code.

The 10 small seeds were not audited this way: Luna rebuilt every one of them from spec alone
(the module task) and passed, so their specs are already sufficient.

## Results by seed

| Seed | Failed answers | Answers that broke correct code | Verdict |
|---|---|---|---|
| ef_tmr32 | 18 | 1 | spec clean; failures are unfixed bugs |
| apb_intc | 17 | 5 | spec clean; all 5 are Luna's `.clk(clk)` miswire |
| ef_uart | 51 | most | 10 spec gaps, fixed below |

## ef_uart: what answers changed, and why

| Module (answers) | What answers did | Verdict | Spec fix |
|---|---|---|---|
| BAUDGEN (35) | gated the tick by EN | gap | tick does not depend on EN |
| EF_UART core (29) | timeout counter: reset or gated it differently (all 23 RIS mismatches are RTO) | gap | exact counter rules: runs on every baud tick, tick counter holds on word completion, flag is a level |
| ef_util_fifo (18) | blocked a read when empty (16), or let a write in when full (2) | gap (rule stated, intent not) | rule stated on accepted write + raw read, both quirks spelled out |
| ef_util_glitch_filter (16) | updated the output only on ticks | gap | output updates every clock |
| ef_util_ticker (6) | made the tick combinational | gap | "internal ticks" defined exactly |
| UART_TX (14) | tx one cycle early; parity from a latched word | gap | registered timing; starts even with TXEN = 0; parity source; unlisted parity codes send 0 |
| UART_RX (13) | frame error from the last stop bit; RX word not shifted by WLEN | gap (frame error); Luna error (shift) | frame error quirk |
| EF_UART_APB (4) | flush in the write cycle (1); setting wins over clearing (3) | gap (flush); Luna error (spec says clearing wins) | flush timing |
| any (several) | RXDATA read while the RX FIFO is empty | **grader/spec mismatch**: spec said "undefined" but the checker compares it | spec now defines it (stale entry at the read pointer) |

Takeaway: when a strong model "fixes" correct behavior, the cause is usually a sentence that does not
pin the design down. Model failures are a cheap audit for spec-vs-golden gaps.
