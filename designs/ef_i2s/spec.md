# EF_I2S_APB: I2S microphone receiver with FIFO and voice-activity detection, APB interface

An I2S master receiver for digital microphones. It generates the serial clock `sck` and the word-select `ws`, shifts in serial data from `sdi`, turns each received word into a sample of programmable size (optionally sign-extended), keeps left, right or both channels in a 16-entry FIFO, and runs two simple voice-activity detectors on the sample stream: an average-magnitude detector and a zero-crossing-rate detector. Software controls it through an APB slave port.

The file contains: `EF_I2S_APB` (top: APB registers, interrupts, input synchronizer), `EF_I2S` (clock generation, sample processing, detectors), `i2s_rx` (serial receiver), and utility modules `ef_util_fifo` and `ef_util_gating_cell`. Tests use only the top-level ports. The design is Efabless's open-source EF_I2S IP (Apache-2.0).

## Ports

| Port | Dir | Width | Meaning |
|---|---|---|---|
| PCLK | in | 1 | Clock. Everything happens on its rising edge. |
| PRESETn | in | 1 | Reset, active low, **asynchronous**: every register that has a reset value clears immediately when PRESETn falls. |
| PSEL, PENABLE, PWRITE | in | 1 each | APB control. |
| PADDR | in | 32 | Address. Only bits [15:0] are decoded. |
| PWDATA | in | 32 | Write data. |
| PRDATA | out | 32 | Read data (combinational, see "APB"). |
| PREADY | out | 1 | Always 1. |
| IRQ | out | 1 | Interrupt: OR of the masked interrupt status bits. |
| sck | out | 1 | I2S serial clock (a register). |
| ws | out | 1 | I2S word select (a register). |
| sdi | in | 1 | I2S serial data from the microphone. |

## APB

- A **write** happens on the rising edge of a cycle with PSEL = PENABLE = PWRITE = 1: the register selected by PADDR[15:0] takes the low bits of PWDATA (as wide as the register). Writes to read-only or unmapped addresses do nothing.
- A **read cycle** is a cycle with PSEL = PENABLE = 1 and PWRITE = 0.
- **PRDATA** is combinational and depends only on PADDR[15:0], in every cycle: it shows the selected register, zero-extended. Any address not listed below (including IC) reads 0xDEADBEEF.
- Reading **RXDATA**: every read cycle at address 0x0000 is a FIFO read request at the end of that cycle (see "FIFO").

## Registers

| Offset | Name | Width | Access | Reset | Meaning |
|---|---|---|---|---|---|
| 0x0000 | RXDATA | 32 | R | | The FIFO entry at the read pointer; reading it requests a FIFO read. |
| 0x0004 | PR | 8 | RW | 0 | Serial clock prescaler. |
| 0x0008 | AVGT | 32 | RW | 0 | Average detector threshold. |
| 0x000C | ZCRT | 32 | RW | 0 | Zero-crossing detector threshold. |
| 0x0010 | CTRL | 4 | RW | 0 | Bit 0 EN (run the clock generator), bit 1 FIFO_EN (store samples), bit 2 AVG_EN, bit 3 ZCR_EN. |
| 0x0014 | CFG | 12 | RW | 0x201 | Bits 1:0 CHANNELS (01 right, 10 left, 11 both, 00 none), bit 2 SIGN_EXTEND, bit 3 LEFT_JUSTIFIED, bits 9:4 SAMPLE_SIZE (0 to 63), bit 10 AVG_SEL, bit 11 ZCR_SEL. The reset value means: right channel only, SAMPLE_SIZE 32, everything else 0. |
| 0xFE00 | RX_LEVEL | 4 | R | | FIFO level. |
| 0xFE04 | RX_THRESHOLD | 4 | RW | 0 | FIFO level threshold. |
| 0xFE08 | RX_FLUSH | 1 | RW | 0 | Writing bit 0 = 1 sets this register for one cycle; it returns to 0 on the next edge. While it is 1, the FIFO empties at the next edge. |
| 0xFF00 | IM | 6 | RW | 0 | Interrupt mask. |
| 0xFF04 | MIS | 6 | R | | RIS AND IM. |
| 0xFF08 | RIS | 6 | R | 0 | Raw interrupt status. |
| 0xFF0C | IC | 6 | W | 0 | Interrupt clear (reads as 0xDEADBEEF). |
| 0xFF10 | GCLK | 1 | RW | 0 | Clock-gate enable. In this build the clock gate is a plain wire, so GCLK is stored and read back but has no effect: the logic is always clocked. |

## Input synchronizer

`sdi` passes through two flip-flops (both reset to 0) clocked by PCLK. Call the second flip-flop's output `sd`: the value on `sdi` before rising edge t is seen as `sd` after edge t+1.

## Serial clock and word select

Three registers, updated only while EN = 1 (with EN = 0 they all hold):

- `prescaler` (8 bits, reset 0): on each edge, if it is 0 it loads PR, otherwise it decrements.
- `sck` (reset 0): on each edge where `prescaler` = 0, it toggles. So each half-period of `sck` is PR + 1 clocks.
- `bit_ctr` (5 bits, reset 0): on each edge where `prescaler` = 0 and `sck` = 1 (a falling `sck` toggle), it increments (wrapping 31 to 0).
- `ws` (reset 1): on each edge where `prescaler` = 0, `sck` = 1 and `bit_ctr` = 0, it toggles. So `ws` changes together with a falling `sck` toggle once every 32 `sck` periods, and each `ws` half-period is 32 `sck` periods.

All conditions use the register values before the edge.

## Receiver

The receiver works on `sd`, `ws` and `sck` and keeps these registers (reset values in brackets): `last_ws` [1], `last_sck` [0], a 32-bit shift register `sr` [0], `ws_dly0` [0], `ws_dly` [0], `last_ws_dly` [0], the pulse registers `ws_ppulse`, `ws_npulse`, `ws_dly_ppulse`, `ws_dly_npulse` [0], `first` [0], `rdy` [0] and the 32-bit `sample` [0]. On each edge (all right-hand values from before the edge):

- `last_sck` becomes `sck`. Define `sck_rise` = `sck` AND NOT `last_sck`, and `sck_fall` = NOT `sck` AND `last_sck` (both combinational).
- `last_ws` becomes `ws`; `ws_ppulse` becomes `ws` AND NOT `last_ws`; `ws_npulse` becomes NOT `ws` AND `last_ws`. Define `ws_pulse` = `ws_ppulse` OR `ws_npulse`.
- If `sck_fall`: `ws_dly0` becomes `ws` and `ws_dly` becomes `ws_dly0` (so `ws_dly` is `ws` delayed by two falling `sck` steps).
- `last_ws_dly` becomes `ws_dly`; `ws_dly_ppulse` becomes `ws_dly` AND NOT `last_ws_dly`; `ws_dly_npulse` becomes NOT `ws_dly` AND `last_ws_dly`. Define `ws_dly_pulse` = `ws_dly_ppulse` OR `ws_dly_npulse`.
- If `sck_rise`: `sr` becomes `{sr[30:0], sd}`.
- `sample`: if LEFT_JUSTIFIED = 1 and `ws_pulse`, it becomes `sr`; else if LEFT_JUSTIFIED = 0 and `ws_dly_pulse`, it becomes `sr`; otherwise it holds.
- `first` becomes 1 if `ws_pulse` OR `ws_dly_pulse` (and then stays 1).
- `rdy` becomes (LEFT_JUSTIFIED ? `ws_pulse` : `ws_dly_pulse`) AND `first`. So the first such pulse after reset produces no `rdy`.

The receiver runs whether or not EN = 1; it only sees `sck` and `ws` change while EN = 1.

## Sample processing

`rdy` = 1 marks a new `sample` (from the edge before). In that cycle:

- `current_channel` is `10` (left) if LEFT_JUSTIFIED equals NOT `ws`, and `01` (right) otherwise, using `ws` in that cycle. A sample is **selected** when `current_channel` AND CHANNELS is nonzero.
- The processed value is `value = (sample >> (32 - SAMPLE_SIZE)) | sign`, where `sign` = SIGN_EXTEND ? ({32{sample[31]}} << SAMPLE_SIZE) : 0. Both shifts are on 32-bit values with the shift amount taken as an unsigned 32-bit number, so a shift by 32 or more gives 0: SAMPLE_SIZE = 0 gives `value` = `sign` = 0, and SAMPLE_SIZE above 32 gives a `sample` part of 0 and a `sign` part of 0.
- If FIFO_EN = 1 and the sample is selected, `value` is written to the FIFO at the end of the cycle.

## FIFO (16 entries of 32 bits)

The FIFO has a read pointer, write pointer, a 4-bit level and full / empty flags (reset: empty, level 0). Per clock, with write = the FIFO write above, read = a RXDATA read cycle:
- write only (and not full): store, advance the write pointer, level + 1; becomes full when the next write position equals the read position;
- read only (and not empty): advance the read pointer, level - 1; becomes empty when the next read position equals the write position;
- write and read in the same cycle: both pointers advance and level / flags stay as they are (even if the FIFO was empty).

These cases are decided on the *accepted* write (write requested and the FIFO not full) and the *raw* read request (the read is not blocked when empty). So an accepted write together with a read on an **empty** FIFO advances both pointers and leaves the FIFO empty (the written word is lost); a write together with a read on a **full** FIFO is a read only (the write is dropped). A write is accepted only when the FIFO is not full. The level is 4 bits wide, so a **full FIFO reports level 0**. A flush (RX_FLUSH = 1) resets pointers, level and flags to empty on the next edge, overriding any read or write in that cycle. RXDATA is always the entry at the read pointer (undefined for an entry never written).

`level_above` = (level > RX_THRESHOLD) OR full.

## Average detector

Registers `sum_ctr` (6 bits, reset 0) and `sum` (32 bits, reset 0). On every edge where `rdy` = 1 and the sample is selected (whether or not FIFO_EN = 1):
- `sum_ctr` increments (wrapping 63 to 0).
- If `sum_ctr` (before the edge) is "zero" (AVG_SEL = 1: all 6 bits are 0; AVG_SEL = 0: the low 5 bits are 0), `sum` becomes `abs`; otherwise, if AVG_EN = 1, `sum` becomes `sum + abs` (32-bit, wrapping); otherwise it holds.

Here `abs` = `value[31]` ? NOT `value` : `value` (bitwise inversion, not two's-complement negation). The flag `avg_flag` = AVG_EN AND (AVG_SEL ? `sum[31:6]` : `sum[31:5]`) > AVGT (unsigned, combinational).

## Zero-crossing detector

Registers `zc_ctr` (6 bits, reset 0), `prev_sign` (reset 0) and `zcr` (32 bits, reset 0). On every edge where `rdy` = 1 and the sample is selected:
- `zc_ctr` increments; `prev_sign` becomes `value[31]`.
- If `zc_ctr` (before the edge) is "zero" (ZCR_SEL = 1: all 6 bits; ZCR_SEL = 0: low 5 bits), `zcr` becomes 0; otherwise, if ZCR_EN = 1, `zcr` becomes `zcr` + (`prev_sign` XOR `value[31]`), using `prev_sign` from before the edge; otherwise it holds.

`zcr_flag` = ZCR_EN AND (`zcr` > ZCRT) (unsigned, combinational). `vad_flag` = `avg_flag` AND `zcr_flag`.

## Interrupts

RIS has 6 bits, each set by a level condition:

| Bit | Name | Condition |
|---|---|---|
| 0 | FIFOE | FIFO empty |
| 1 | FIFOA | `level_above` |
| 2 | FIFOF | FIFO full |
| 3 | AVGF | `avg_flag` |
| 4 | ZCRF | `zcr_flag` |
| 5 | VADF | `vad_flag` |

Each RIS bit sets on any edge where its condition is 1 (the condition's value before the edge), and clears on the edge after a write of 1 to that IC bit: IC holds the written value for one cycle, then returns to 0, and while IC's bit is 1, clearing wins over setting. A bit whose condition is still true sets again on the next edge after IC returns to 0. Otherwise RIS bits stay 1. MIS = RIS AND IM; IRQ = OR of the MIS bits (combinational).
