# apb_intc: APB interrupt controller

Collects up to `NUM_SRC` interrupt requests, picks the most urgent one, and signals a processor through `irq` / `irq_id`. Software configures it and handles interrupts over an APB3 slave port, using a claim/complete handshake: reading CLAIM takes ownership of the best interrupt; writing its ID back to CLAIM releases it.

The design is one file with these modules: `apb_intc` (top), `intc_apb_if` (APB protocol), `intc_sync` (input synchronizer), `intc_pending` (pending-bit logic), `intc_prio_select` (priority selection), and `intc_regs` (registers and address decode). Their behavior together is specified below; tests only use the top-level ports.

## Parameters

| Name      | Default | Meaning |
|-----------|---------|---------|
| NUM_SRC   | 8       | Number of interrupt sources, 2 to 32. Sources are numbered 0 to NUM_SRC-1; their IDs are 1 to NUM_SRC. |
| PRIO_BITS | 3       | Bits of priority per source, 1 to 4. Priorities range from 0 to 2^PRIO_BITS - 1. |

`IDW = clog2(NUM_SRC + 1)` is the width of an interrupt ID.

## Ports

| Port    | Dir | Width   | Meaning |
|---------|-----|---------|---------|
| PCLK    | in  | 1       | Clock. All state changes on the rising edge. |
| PRESETn | in  | 1       | Synchronous reset, **active low**. |
| PSEL    | in  | 1       | APB select. |
| PENABLE | in  | 1       | APB enable (second cycle of a transfer). |
| PWRITE  | in  | 1       | 1 = write, 0 = read. |
| PADDR   | in  | 8       | Byte address. |
| PWDATA  | in  | 32      | Write data. |
| PRDATA  | out | 32      | Read data. |
| PREADY  | out | 1       | Always 1: every transfer completes without wait states. |
| PSLVERR | out | 1       | Error response, see "Errors". |
| irq_src | in  | NUM_SRC | Interrupt request lines, may be asynchronous to PCLK. |
| irq     | out | 1       | Interrupt request to the processor. |
| irq_id  | out | IDW     | ID (1 to NUM_SRC) of the interrupt `irq` is reporting; 0 when `irq` is 0. |

## APB transfers

A transfer has two cycles:
1. **Setup:** PSEL = 1, PENABLE = 0.
2. **Access:** PSEL = 1, PENABLE = 1. Because PREADY is always 1, every access cycle completes the transfer.

During the access cycle of a read, PRDATA shows the register value and PSLVERR shows the error status, combinationally. In every other cycle (idle, setup, or any write), PRDATA is 0 and PSLVERR is 0. A write, and any side effect of a read, takes effect on the rising edge that ends the access cycle. PADDR, PWRITE, and PWDATA are only looked at during the access cycle.

## Errors

During an access cycle, PSLVERR is 1 if any of these is true:
- PADDR is not a multiple of 4.
- PADDR is not a register in the map below. PRIO words beyond the last one that exists for this NUM_SRC are unmapped.
- The transfer is a write to a read-only register (INSERVICE, STATUS, ID).

A transfer with an error has no effect at all: a write changes nothing, a read returns PRDATA = 0 and has no side effect (in particular, an erroring transfer never claims).

## Register map

All registers are 32 bits. Bits not listed read as 0 and ignore writes. "Bit i" refers to source i. Reset values are 0 unless stated.

| Offset | Name      | Access | Contents |
|--------|-----------|--------|----------|
| 0x00 | CTRL      | RW  | Bit 0: GEN, global enable. When 0, `irq` is 0 and CLAIM reads return 0. |
| 0x04 | ENABLE    | RW  | Bits [NUM_SRC-1:0]: per-source enable. |
| 0x08 | TRIGGER   | RW  | Bits [NUM_SRC-1:0]: 1 = rising-edge triggered, 0 = level triggered. |
| 0x0C | PENDING   | R / W1C | Bits [NUM_SRC-1:0]: pending flags. Writing 1 to a bit clears it; writing 0 leaves it. |
| 0x10 | FORCE     | W   | Writing 1 to bit i makes source i pending (a software interrupt). Reads return 0 (not an error). |
| 0x14 | THRESHOLD | RW  | Bits [PRIO_BITS-1:0]: only interrupts with priority strictly greater than this are reported. |
| 0x18 | CLAIM     | R: claim / W: complete | See "Claim and complete". |
| 0x1C | INSERVICE | RO  | Bits [NUM_SRC-1:0]: sources claimed and not yet completed. |
| 0x20 | STATUS    | RO  | Bit 0: current `irq`. Bits [15:8]: current `irq_id`. |
| 0x24 | ID        | RO  | Constant 0x1C0D0000 + (PRIO_BITS << 8) + NUM_SRC. |
| 0x40 + 4k | PRIO_k | RW | Priorities of sources 8k to 8k+7, 4 bits per source: source 8k+j uses bits [4j+PRIO_BITS-1 : 4j]. The other bits of each 4-bit field read as 0. Words exist for k = 0 to ceil(NUM_SRC/8) - 1; fields for sources >= NUM_SRC read as 0 and ignore writes. |

## Input synchronization

Each `irq_src` bit passes through two flip-flops before use (both reset to 0). So a change on `irq_src` sampled at rising edge t is seen by the pending logic at rising edge t+2. Below, `s` means the synchronized value, and `s_prev` means the synchronized value one cycle earlier (also reset to 0).

## Pending flags

On every rising edge (outside reset), each source's pending flag becomes:

    pending_next = (pending AND NOT clear) OR set

where
- `set` is 1 if any of these holds:
  - the source is level triggered (TRIGGER bit 0) and `s` = 1;
  - the source is edge triggered (TRIGGER bit 1) and `s` = 1 and `s_prev` = 0 (a rising edge);
  - this cycle completes a FORCE write with that bit set.
- `clear` is 1 if this cycle completes a PENDING write with that bit set (W1C), or this cycle completes a CLAIM read that claims this source.

The TRIGGER value used is the one before any write in the same cycle. Set wins over clear: a level source whose line is still high stays pending after being cleared or claimed. Pending flags are set regardless of ENABLE, priority, GEN, or in-service state.

## Selecting the interrupt

A source is **eligible** when it is pending, enabled (ENABLE bit 1), and not in service (INSERVICE bit 0).

The **best** source is the eligible source with the highest priority. On a tie, the lowest-numbered source wins. A source with priority 0 is never selected.

`irq` = GEN AND (a best source exists) AND (its priority > THRESHOLD).
`irq_id` = (best source number + 1) when `irq` is 1, else 0.

`irq` and `irq_id` depend only on state (registers, pending, in-service), not on the current APB inputs.

## Claim and complete

- **Claim (read CLAIM):** returns `irq_id` (0 if `irq` is 0). If `irq` is 1, the read also, at the end of the access cycle, sets that source's INSERVICE bit and clears its pending flag. If `irq` is 0, the read has no side effect.
- **Complete (write CLAIM):** if PWDATA is between 1 and NUM_SRC, the INSERVICE bit of source PWDATA-1 is cleared. Any other value is ignored (not an error).

While a source is in service it is not eligible, but it can still become pending. After it is completed, it is eligible again if it is pending.

## Reset

On a rising edge with PRESETn = 0, every register, pending flag, in-service bit, and synchronizer flip-flop becomes 0 (GEN = 0, all priorities 0). APB inputs are ignored during reset.
