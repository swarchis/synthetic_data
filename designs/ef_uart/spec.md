# EF_UART_APB: UART with FIFOs and APB interface

A UART transmitter and receiver with 5 to 9 data bits, optional parity, 1 or 2 stop bits, 16-entry TX and RX FIFOs with level thresholds, an RX glitch filter, internal loopback, receive match / break / timeout detection, and 10 interrupt sources. Software controls it through an APB slave port.

The file contains: `EF_UART_APB` (top: APB registers, interrupts), `EF_UART` (UART core), `BAUDGEN` (baud tick generator), `UART_TX`, `UART_RX`, and utility modules `ef_util_sync`, `ef_util_ticker`, `ef_util_glitch_filter`, `ef_util_fifo`, `ef_util_gating_cell`. Tests use only the top-level ports. The design is based on Efabless's open-source EF_UART IP (Apache-2.0).

## Parameters (EF_UART_APB)

| Name | Default | Meaning |
|---|---|---|
| SC | 8 | Samples (baud ticks) per bit. |
| MDW | 9 | Maximum data width. |
| GFLEN | 8 | Glitch filter length (stages). |
| FAW | 4 | FIFO address width: each FIFO holds 2^FAW = 16 entries. |

## Ports

| Port | Dir | Width | Meaning |
|---|---|---|---|
| PCLK | in | 1 | Clock. |
| PRESETn | in | 1 | Reset, active low, asynchronous. |
| PSEL, PENABLE, PWRITE | in | 1 each | APB control. |
| PADDR | in | 32 | Address; only bits [15:0] are decoded. |
| PWDATA | in | 32 | Write data. |
| PRDATA | out | 32 | Read data (see "Reads"). |
| PREADY | out | 1 | Always 1. |
| IRQ | out | 1 | OR of the masked interrupt status bits. |
| rx | in | 1 | Serial input. |
| tx | out | 1 | Serial output (idle high). |

## APB writes and reads

- **Writes** happen on the rising edge of a cycle with PSEL = PENABLE = PWRITE = 1, to the register selected by PADDR[15:0], taking the low bits of PWDATA. Unmapped addresses and read-only registers ignore writes.
- **Reads:** PRDATA is combinational and depends only on PADDR[15:0] in every cycle. It shows the selected register zero-extended to 32 bits; unlisted addresses (including TXDATA and IC) return `0xDEADBEEF`.
- **RXDATA side effect:** a read transfer (PSEL = PENABLE = 1, PWRITE = 0) at RXDATA pops one entry from the RX FIFO at the end of that cycle.
- **TXDATA:** a write transfer at TXDATA pushes PWDATA[8:0] into the TX FIFO (ignored if the FIFO is full).

## Register map

| Offset | Name | Width | Access | Meaning |
|---|---|---|---|---|
| 0x0000 | RXDATA | 9 | R | The RX FIFO entry at the read pointer; reading pops it. When the FIFO is empty this is stale data (whatever that entry last held; undefined only if it was never written), and reading does not pop. |
| 0x0004 | TXDATA | 9 | W | Push to the TX FIFO. |
| 0x0008 | PR | 16 | RW | Baud prescaler: one baud tick every PR + 1 clock cycles. |
| 0x000C | CTRL | 5 | RW | Bit 0 EN (enable baud generator), 1 TXEN, 2 RXEN, 3 LPEN (loopback), 4 GFEN (glitch filter). |
| 0x0010 | CFG | 14 | RW | [3:0] WLEN data bits (5 to 9); [4] STP2 two stop bits; [7:5] PARITY: 000 none, 001 odd, 010 even, 100 sticky 0, 101 sticky 1; [13:8] TIMEOUT in bit times. **Reset value 0x3F08** (8 bits, no parity, 1 stop, timeout 63). |
| 0x001C | MATCH | 9 | RW | Match value for received data. |
| 0xFE00 | RX_LEVEL | 4 | R | RX FIFO level. |
| 0xFE04 | RX_THRESHOLD | 4 | RW | RX FIFO threshold. |
| 0xFE08 | RX_FLUSH | 1 | RW | Writing 1 sets the register for one cycle (it returns to 0 on the next cycle); the RX FIFO empties on the edge at the end of that cycle, not during the write itself. |
| 0xFE10 | TX_LEVEL | 4 | R | TX FIFO level. |
| 0xFE14 | TX_THRESHOLD | 4 | RW | TX FIFO threshold. |
| 0xFE18 | TX_FLUSH | 1 | RW | Same as RX_FLUSH, for the TX FIFO. |
| 0xFF00 | IM | 10 | RW | Interrupt mask. |
| 0xFF04 | MIS | 10 | R | RIS AND IM. |
| 0xFF08 | RIS | 10 | R | Raw interrupt status. |
| 0xFF0C | IC | 10 | W | Interrupt clear. |
| 0xFF10 | GCLK | 1 | RW | Clock-gate enable. In this build the gate is a plain wire, so GCLK is stored but has no effect. |

All registers reset to 0 except CFG.

## FIFOs (TX and RX, 16 entries each)

Each FIFO has a read pointer, write pointer, level, and full / empty flags (reset: empty, level 0). Per clock:
- write only (and not full): store, advance the write pointer, level + 1; becomes full when the next write position equals the read position;
- read only (and not empty): advance the read pointer, level - 1; becomes empty when the next read position equals the write position;
- write and read in the same cycle: both pointers advance and level / flags stay as they are (even if the FIFO was empty).

These cases are decided on the *accepted* write (write requested and the FIFO not full) and the *raw* read request (the read is not blocked when empty). So, deliberately (a quirk of the original IP): an accepted write together with a read on an **empty** FIFO advances both pointers and leaves the FIFO empty, so the written word is lost; a write together with a read on a **full** FIFO is a read only (the write is dropped).

A write is accepted only when the FIFO is not full. The level is 4 bits wide, so a **full FIFO reports level 0**. A flush resets pointers, level, and flags to empty on the next edge, overriding any read or write in that cycle. The read data is the entry at the read pointer.

## Baud ticks

A baud counter (reset 0) counts while EN = 1: it returns to 0 after reaching PR, otherwise increments. A **baud tick** is a cycle where the counter equals PR. The tick does **not** depend on EN: when EN = 0 the counter holds its value, and if it holds at PR (for example PR = 0 right after reset) every cycle is a baud tick. The transmitter advances only on baud ticks with TXEN = 1, the receiver only on baud ticks with RXEN = 1.

## Transmitter

Idle: `tx` = 1. When the TX FIFO is not empty, the transmitter takes the oldest entry and sends a frame. It leaves idle whenever the TX FIFO is not empty, **even if TXEN = 0**: only its baud ticks are gated by TXEN, so with TXEN = 0 it sits in the start bit (`tx` = 0) until ticks resume.

`tx` is a register that shows the current part of the frame one cycle late: the transmitter moves from idle to the start bit on the edge after it sees the FIFO non-empty, and `tx` goes to 0 on the edge after that. Each part below lasts the given number of baud ticks, counted by the transmitter's own tick counter:
1. **Start bit** (0), lasting SC + 1 baud ticks.
2. **Data bits**, least significant first, WLEN bits, SC baud ticks each.
3. **Parity bit** (if PARITY != 000), SC ticks: odd = NOT XOR of the data bits, even = XOR of the data bits, sticky 0 = 0, sticky 1 = 1 (computed on the low WLEN bits only). The unlisted PARITY codes (011, 110, 111) still send a parity bit, and it is 0; the receiver samples a parity bit for them and never reports a parity error. The parity is computed from the word at the head of the TX FIFO while the parity bit is sent. The head word is popped only when the frame ends, so this is the word being sent, unless the TX FIFO was flushed during the frame.
4. **Stop bit(s)** (1), SC ticks each; one, or two if STP2 = 1.

When the last stop bit ends, the entry is popped from the TX FIFO.

## Receiver

The receiver input is: `tx` itself when LPEN = 1; otherwise the glitch-filtered `rx` when GFEN = 1; otherwise `rx` after a 2-flop synchronizer (which has no reset).

On baud ticks: in idle, a 0 on the input starts a frame; the receiver waits SC/2 ticks (to the middle of the start bit), then samples each data bit every SC ticks, LSB first; then the parity bit if enabled; then the stop bit(s). When the last stop bit is sampled, the received word (aligned so bit 0 is the first data bit) is pushed into the RX FIFO.

- **Parity error:** the sampled parity bit does not match the expected value for the received data.
- **Frame error:** only a stop bit sampled *before* the last one counts (a quirk of the original IP). With STP2 = 1, the flag is set when the first stop bit is sampled as 0. A 0 in the last stop bit is not reported, so with STP2 = 0 the frame error flag is never set.
- **Match:** the received word equals MATCH.
- **Break:** the input has been 0 for 12 consecutive bit samples while receiving.

Parity error, frame error, and match are flags for the cycle in which a word completes.

## Glitch filter

The filter (used when GFEN = 1) shifts the synchronized input into an 8-stage register (reset 0) on internal ticks. Its output is a separate register (reset 0) updated on **every** clock edge, not only on ticks: it becomes 1 when all stages are currently 1, 0 when all are 0, and otherwise holds. So the output follows the stages one cycle late.

Internal ticks come from a divider that runs only while GFEN = 1. A counter (reset 0) on each clock with GFEN = 1 reloads to 1 when it is 0, otherwise counts down; with GFEN = 0 it holds. The tick is itself a register (reset 0): on each clock it takes (counter == 0) while GFEN = 1, and 0 while GFEN = 0. So with GFEN = 1 the filter shifts every second clock, one cycle after the counter reaches 0.

## Receive timeout

Two counters (both reset 0) run on **every** baud tick from the baud generator, regardless of RXEN and of whether a frame is being received: a tick counter (0 to SC - 1) and a bit counter. On a baud tick: if a word completes in that cycle, the bit counter resets to 0 and the tick counter holds (it is not reset). Otherwise, if the tick counter is SC - 1, it wraps to 0 and the bit counter increments (or resets to 0 if the timeout flag is high); otherwise the tick counter increments. The timeout flag is high whenever the bit counter equals TIMEOUT. It is a level, not a pulse, so RTO keeps setting while it holds.

## Interrupts

| RIS bit | Name | Set when |
|---|---|---|
| 0 | TXE | TX FIFO empty |
| 1 | RXF | RX FIFO full |
| 2 | TXB | TX level below TX_THRESHOLD (and TX FIFO not full) |
| 3 | RXA | RX level above RX_THRESHOLD, or RX FIFO full |
| 4 | BRK | break detected |
| 5 | MATCH | received word equals MATCH |
| 6 | FE | frame error |
| 7 | PRE | parity error |
| 8 | OR | overrun: a word completes while the RX FIFO is full |
| 9 | RTO | receive timeout |

Each RIS bit sets on any edge where its condition holds, and clears on the edge after a write of 1 to the same IC bit (IC holds the written value for one cycle; clearing wins over setting). MIS = RIS AND IM; IRQ = OR of MIS.
