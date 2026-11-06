# simple_spi_top: SPI master with Wishbone interface and FIFOs

An SPI master modeled on the Motorola MC68HC11E SPI, with an 8-bit classic Wishbone slave port, a 4-entry write FIFO (bytes to send) and a 4-entry read FIFO (bytes received), a programmable serial-clock divider, all four SPI clock modes, and an interrupt after a programmable number of transfers. It drives SCK and MOSI and samples MISO; there is no slave select output (software drives selects elsewhere).

The file contains two modules: `simple_spi_top` (top) and `fifo4` (the 4-entry FIFO, used twice). Tests use only the top-level ports. The design is the OpenCores simple_spi core by Richard Herveille.

## Ports

| Port | Dir | Width | Meaning |
|---|---|---|---|
| clk_i | in | 1 | Clock. Everything below happens on its rising edge. |
| rst_i | in | 1 | Reset, active low (see "Reset"). |
| cyc_i, stb_i, we_i | in | 1 each | Wishbone cycle, strobe, write enable. |
| adr_i | in | 2 | Register address. |
| dat_i | in | 8 | Write data. |
| dat_o | out | 8 | Read data (a register, see "Wishbone"). |
| ack_o | out | 1 | Wishbone acknowledge (a register). |
| inta_o | out | 1 | Interrupt (a register). |
| sck_o | out | 1 | SPI serial clock (a register). |
| mosi_o | out | 1 | SPI data out: always bit 7 of the shift register `treg`. |
| miso_i | in | 1 | SPI data in. |

## Reset

rst_i = 0 resets these immediately (asynchronously): SPCR to 0x10, SPER to 0x00, ack_o to 0, and both FIFOs' read and write pointers to 0. Each FIFO's guard bit (see "FIFOs") is reset synchronously: it clears on a rising edge where rst_i = 0. Nothing else has a reset; the rest of the logic is cleared through SPE = 0 (SPCR reset value has SPE = 0), as described below, starting at the first rising edge. `dat_o` and the FIFO storage are never cleared.

## Wishbone

Let "access" mean cyc_i = 1 and stb_i = 1.
- `ack_o` is a register: on every rising edge it becomes `access AND NOT ack_o`. So it rises one clock after an access starts, stays high one clock, and alternates 1, 0, 1, ... if the access is held.
- `dat_o` is a register loaded on every rising edge (whatever cyc_i, stb_i and we_i are) with the register selected by adr_i at that edge (table below; address 2 gives the read FIFO's oldest entry).
- **Register writes** (SPCR, SPSR, SPER) take effect on **every** rising edge with access = 1 and we_i = 1, whether or not ack_o is high, using adr_i and dat_i at that edge.
- **FIFO accesses** at address 2 take effect only on a rising edge where access = 1 **and ack_o = 1**: with we_i = 1 the edge writes dat_i into the write FIFO, with we_i = 0 it removes the oldest entry of the read FIFO (it advances the read pointer).

So a master that holds an access for two clocks (until the acknowledge) writes a control register twice with the same value, pushes or pops a FIFO once, and sees the read data in the cycle where ack_o = 1.

## Registers

| adr_i | Name | Read | Write |
|---|---|---|---|
| 0 | SPCR | SPCR | SPCR = dat_i OR 0x10 |
| 1 | SPSR | SPSR (status) | clears flags (below) |
| 2 | SPDR | read FIFO oldest entry | push into the write FIFO |
| 3 | SPER | SPER | SPER = dat_i |

**SPCR** (control, reset 0x10): bit 7 SPIE (interrupt enable), bit 6 SPE (core enable), bit 5 DWOM (stored, no effect), bit 4 MSTR (always 1: every write ORs in 0x10), bit 3 CPOL (clock idle level), bit 2 CPHA (clock phase), bits 1:0 SPR (clock rate, low bits).

**SPER** (extension, reset 0x00): bits 7:6 ICNT (interrupt after ICNT + 1 transfers), bits 1:0 SPRE (clock rate, high bits). Bits 5:2 are stored and read back but have no effect.

**SPSR** (status): bit 7 SPIF, bit 6 WCOL, bits 5:4 always 0, bit 3 write FIFO full, bit 2 write FIFO empty, bit 1 read FIFO full, bit 0 read FIFO empty. A register write to address 1 with dat_i bit 7 = 1 clears SPIF, and with dat_i bit 6 = 1 clears WCOL (rules below).

## SPE = 0

On every rising edge where SPE = 0 (the SPCR value before that edge): both FIFOs are cleared (pointers and guard bit to 0; the stored bytes stay, and a write on that same edge still stores its byte at entry `wp`, using the pointer value before the edge, as described under "FIFOs"), SPIF and WCOL become 0, the transfer state machine returns to IDLE with `bcnt` = 0, `treg` = 0x00, `sck_o` = 0 and its two internal pulses (`wfre`, `rfwe`) = 0, and the transfer counter `tcnt` loads ICNT. Note that sck_o = 0 here regardless of CPOL.

## FIFOs (both identical)

Each FIFO has 4 byte entries, a 2-bit write pointer `wp`, a 2-bit read pointer `rp` (both wrap 3 to 0), and a guard bit `gb`.
- empty = (wp == rp) AND NOT gb; full = (wp == rp) AND gb. The oldest entry (the output) is always entry `rp`, whatever empty says.
- A write stores the byte at entry `wp` and increments `wp`. **Writes are never refused**: writing a full FIFO overwrites entry `wp` (the oldest entry) and still increments `wp`. The store itself does not depend on SPE or on clearing: a write on an edge where the FIFO is being cleared (SPE = 0) still stores its byte at entry `wp` (the value before the edge), and the pointers then become 0. So bytes written to SPDR while SPE = 0 are kept in the storage (the FIFO still reads as empty), and the transfer engine loads `treg` from entry `rp` in IDLE whether or not the FIFO is empty.
- A read increments `rp`. **Reads are never refused** either: reading an empty FIFO still increments `rp`.
- On an edge with a write and a read together, both pointers increment.
- Guard bit, each edge in this priority: cleared (synchronous reset or SPE = 0); else set to 1 if this edge writes and `wp + 1` equals `rp` (before the edge); else cleared if this edge reads; else unchanged.

The write FIFO is written by Wishbone (address 2) and read by the transfer engine; the read FIFO is written by the transfer engine and read by Wishbone (address 2).

## Serial clock divider

ESPR = {SPRE, SPR} (4 bits) selects a reload value N:

| ESPR | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12-15 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| N | 0 | 1 | 3 | 15 | 31 | 7 | 63 | 127 | 255 | 511 | 1023 | 2047 | (none) |

A 12-bit counter `clkcnt` (no reset) updates on each edge: if SPE = 1 and `clkcnt` ≠ 0 and the state machine is not in IDLE, it decrements; otherwise it loads N (for ESPR 12 to 15 there is no N and `clkcnt` keeps its value). The step enable `ena` is 1 in cycles where `clkcnt` = 0. During a transfer `clkcnt` therefore counts N, N-1, ..., 0, reloads, and `ena` is 1 once every N + 1 clocks; each SCK half-period is N + 1 clocks, so SCK = clk / (2 (N + 1)).

## Transfer state machine (SPE = 1)

States IDLE, PHASE2 and PHASE1; registers `bcnt` (3 bits), `treg` (8 bits), `sck_o`, and the one-clock pulses `wfre` (pop the write FIFO) and `rfwe` (push into the read FIFO), which are 0 on every edge unless a rule below sets them. mosi_o = treg[7] at all times.

- **IDLE**, on each edge: `bcnt` = 7, `treg` = the write FIFO's oldest entry, `sck_o` = CPOL. If the write FIFO is not empty: `wfre` = 1, go to PHASE2, and if CPHA = 1, `sck_o` instead becomes the inverse of its current value.
- **PHASE2**, on an edge with `ena` = 1: invert `sck_o`, go to PHASE1. (Without `ena`, nothing changes.)
- **PHASE1**, on an edge with `ena` = 1: `treg` shifts left taking miso_i as its new bit 0, `bcnt` decrements. If `bcnt` was 0 (the 8th bit): go to IDLE, `sck_o` = CPOL, `rfwe` = 1. Otherwise: go to PHASE2 and invert `sck_o`.

`wfre` = 1 pops the write FIFO on the next edge; `rfwe` = 1 pushes the current `treg` (the received byte) into the read FIFO on the next edge (overwriting if full, as above). So a byte is sent MSB first: bit 7 of the byte is on MOSI from the IDLE edge that loads it, and each PHASE1 step samples MISO and shifts the next bit out.

## Transfer count, SPIF, WCOL and the interrupt

- `tcnt` (2 bits): while SPE = 0 it loads ICNT each edge. With SPE = 1, on an edge where `rfwe` = 1: if `tcnt` ≠ 0 it decrements, else it reloads ICNT. `tirq` = (`tcnt` = 0) AND `rfwe`. So SPIF requests come after every (ICNT + 1) completed transfers.
- SPIF: with SPE = 1, each edge it becomes `(tirq OR SPIF) AND NOT clear7`, where clear7 means this edge is a register write to address 1 with dat_i bit 7 = 1. Clearing wins over setting.
- WCOL: with SPE = 1, each edge it becomes `(wfov OR WCOL) AND NOT clear6` (dat_i bit 6 in the same kind of write), where `wfov` = this edge is a write-FIFO push (address 2, ack edge, as above) while the write FIFO is full. The push still happens.
- inta_o: a register (no reset) that becomes SPIF AND SPIE on each edge (using the values before the edge).
