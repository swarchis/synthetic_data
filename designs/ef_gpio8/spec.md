# EF_GPIO8_APB: 8-bit GPIO with interrupts, APB interface

An 8-pin general-purpose I/O block. Each pin has an output value, an output-enable (direction) bit, and an input path through a two-flop synchronizer. The synchronized inputs drive 32 interrupt sources per block: pin high, pin low, rising edge, and falling edge, for each of the 8 pins. Software controls it through an APB slave port.

The file contains these modules: `EF_GPIO8_APB` (top: APB registers and interrupts), `EF_GPIO8` (synchronizers and edge detectors), `ef_util_sync` (two-flop synchronizer), `ef_util_ped` (rising-edge detector), `ef_util_ned` (falling-edge detector), and `ef_util_gating_cell` (clock gate). Tests use only the top-level ports. The design is based on Efabless's open-source EF_GPIO8 IP (Apache-2.0).

## Ports (EF_GPIO8_APB)

| Port | Dir | Width | Meaning |
|---|---|---|---|
| PCLK | in | 1 | Clock. |
| PRESETn | in | 1 | Reset, active low, **asynchronous**: every register with a reset clears immediately when PRESETn falls. |
| PSEL, PENABLE, PWRITE | in | 1 each | APB control. |
| PADDR | in | 32 | Address. Only bits [15:0] are decoded; upper bits are ignored. |
| PWDATA | in | 32 | Write data. |
| PRDATA | out | 32 | Read data (see "APB writes and reads"). |
| PREADY | out | 1 | Always 1. |
| IRQ | out | 1 | Interrupt: OR of the masked interrupt status bits. |
| io_in | in | 8 | Pin inputs. Bit i is pin i. |
| io_out | out | 8 | Pin output values: always equal to the DATAO register. |
| io_oe | out | 8 | Pin output enables: always equal to the DIR register (1 = the pin drives io_out). |

`io_out` and `io_oe` are driven combinationally from their registers. They do not depend on `io_in`, and `io_in` is read the same way whatever DIR says: the block never loops its outputs back to its inputs.

## APB writes and reads

- **Writes:** a write happens on the rising edge of a cycle with PSEL = 1, PENABLE = 1, PWRITE = 1. The register selected by PADDR[15:0] takes the low bits of PWDATA (as wide as the register). Writes to unmapped addresses and to read-only registers do nothing. There is no error response.
- **Reads:** PRDATA is combinational and depends only on PADDR[15:0], in every cycle (not only during a read transfer). It shows the selected register's value, zero-extended to 32 bits. Any address not listed as readable below, including IC, returns `0xDEADBEEF`.

## Register map

| Offset | Name | Width | Access | Meaning |
|---|---|---|---|---|
| 0x0000 | DATAI | 8 | R | The synchronized pin inputs (see "Input path"). |
| 0x0004 | DATAO | 8 | RW | Output values; drives `io_out`. |
| 0x0008 | DIR | 8 | RW | Output enables; drives `io_oe`. |
| 0xFF00 | IM | 32 | RW | Interrupt mask. |
| 0xFF04 | MIS | 32 | R | Masked interrupt status: RIS AND IM. |
| 0xFF08 | RIS | 32 | R | Raw interrupt status (see "Interrupts"). |
| 0xFF0C | IC | 32 | W | Interrupt clear (not readable: reads return 0xDEADBEEF). |
| 0xFF10 | GCLK | 1 | RW | Clock-gate enable. In this build the clock gate is a plain wire (no gating macro is defined), so GCLK is stored and read back but has no effect: the logic is always clocked. |

All registers reset to 0.

## Input path

Each pin input passes through a two-flop synchronizer clocked by PCLK, so a value present on `io_in[i]` at rising edge t appears at the synchronizer output on rising edge t+1 (that is, it is sampled at edge t and seen after edge t+1). Call the synchronizer output `s[i]`. DATAI shows `s`.

For each pin, an edge detector keeps the previous value of `s[i]` (updated on every rising edge):
- **rising edge** `pe[i]` = `s[i]` AND NOT previous `s[i]`;
- **falling edge** `ne[i]` = NOT `s[i]` AND previous `s[i]`.

So `pe[i]` (or `ne[i]`) is high for exactly one cycle after `s[i]` changes.

The synchronizer and edge-detector flops have **no reset**. Their values are undefined until the inputs have been clocked through them (two rising edges for `s`, three for the edge detectors); reset does not change them.

## Interrupts

RIS has 32 bits. For pin i (0 to 7):

| RIS bit | Condition |
|---|---|
| i | `s[i]` = 1 (pin high, a level) |
| 8 + i | `s[i]` = 0 (pin low, a level) |
| 16 + i | `pe[i]` = 1 (rising edge) |
| 24 + i | `ne[i]` = 1 (falling edge) |

Because bits 0-15 are levels, once `s[i]` is defined one of bits i and 8+i sets on every rising edge.

Each RIS bit sets on any rising edge where its condition is 1, and clears on the edge after a write of 1 to that IC bit: IC holds the written value for one cycle, then returns to 0, and while IC's bit is 1, clearing wins over setting. A bit whose condition is still true sets again on the next edge after IC returns to 0. RIS bits are sticky otherwise: they stay 1 after the condition goes away. MIS = RIS AND IM; IRQ = OR of the 32 MIS bits.
