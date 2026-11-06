# EF_PSRAM_CTRL_AHBL: serial PSRAM / SRAM controller with AHB-Lite interface

A memory controller that maps a serial pseudo-static RAM (PSRAM) or serial SRAM into an AHB-Lite address space. Each AHB data access becomes one serial transaction on the memory pins: a command byte, a 24-bit address, optional wait (dummy) cycles for reads, and 1, 2 or 4 data bytes. The serial link runs in SPI mode (1 bit per clock), QSPI mode (command on 1 line, address and data on 4 lines) or QPI mode (everything on 4 lines). Read/write command codes, wait cycles, mode and two special commands are programmable through a configuration address range.

The file contains: `EF_PSRAM_CTRL_AHBL` (top: AHB-Lite slave, configuration registers) and `EF_PSRAM_CTRL` (the serial engine). Tests use only the top-level ports. The design is Efabless's open-source EF_PSRAM_CTRL IP (Apache-2.0).

## Ports

| Port | Dir | Width | Meaning |
|---|---|---|---|
| HCLK | in | 1 | Clock. Everything happens on its rising edge. |
| HRESETn | in | 1 | Reset, active low, **asynchronous**: registers with a reset value clear immediately when HRESETn falls. |
| HSEL, HADDR, HTRANS, HWRITE, HSIZE | in | 1, 32, 2, 1, 3 | AHB-Lite address-phase signals. |
| HWDATA | in | 32 | AHB-Lite write data (data phase). |
| HREADY | in | 1 | AHB-Lite ready (in a one-slave system, this slave's HREADYOUT). |
| HREADYOUT | out | 1 | This slave's ready (a register). |
| HRDATA | out | 32 | Read data (combinational, see "Read data"). |
| sck | out | 1 | Memory serial clock (a register). |
| ce_n | out | 1 | Memory chip enable, active low (a register). |
| dout | out | 4 | Data to the memory, one value per serial step (combinational, see "Pin tables"). |
| douten | out | 4 | Output enables for the four data lines (1 = drive; combinational). |
| din | in | 4 | Data from the memory. In SPI mode only din[1] is used. |

## AHB-Lite slave

An address phase is **accepted** in a cycle with HTRANS[1] = 1 (NONSEQ or SEQ), HSEL = 1 and HREADY = 1 (call this `addr_phase`). The address space is split by HADDR[23]: **data accesses** have HADDR[23] = 0 and go to the memory; **configuration accesses** have HADDR[23] = 1 and reach the registers below. HADDR[31:24] are ignored.

These registers capture the address phase (all reset to 0 and loaded on every edge where HREADY = 1, whether or not anything is selected): `last_HSEL`, `last_HADDR`, `last_HWRITE`, `last_HTRANS`. Separately, `last_HSIZE` (reset 0) loads HSIZE on every edge where HTRANS[1] = 1 and **HREADYOUT** = 1 (the slave's own ready, not HREADY, and without checking HSEL).

Other wrapper registers (reset values in brackets), each updated on every edge:
- `last_addr_phase` [0] becomes `addr_phase`.
- `data_cfg` [0]: on an edge with `addr_phase` = 1 it becomes NOT HADDR[23]; otherwise it holds.
- `data_i` (no reset): on an edge with `last_addr_phase` = 1 it loads HWDATA.
- `rd_wr` [0]: on an edge with `last_HTRANS[1]` = 1 it becomes NOT `last_HWRITE` (1 = read); otherwise it holds.
- `state` [IDLE]: IDLE goes to WAIT on an edge where `start` = 1; WAIT goes back to IDLE on an edge where `done` = 1 and `last_HADDR[23]` = 0. Here `start` = `last_addr_phase` AND `state` = IDLE AND `data_cfg`, and `done` comes from the serial engine (below).
- `HREADYOUT` [1]: in IDLE it becomes 0 if `last_addr_phase` OR `addr_phase`, else 1; in WAIT it becomes 1 if `done` = 1 and `last_HADDR[23]` = 0, else 0.

So a data access holds HREADYOUT low from the edge after its address phase until its serial transaction completes, and a configuration access holds it low for two cycles. All conditions use values before the edge.

## Configuration registers

| Register | Width | Reset | Selected by `last_HADDR` bit | Use |
|---|---|---|---|---|
| RD_CMD | 8 | 0x03 | 8 | Command byte for reads |
| WR_CMD | 8 | 0x02 | 9 | Command byte for writes |
| EQPI_CMD | 8 | 0x35 | 10 | "Enter QPI" command byte |
| XQPI_CMD | 8 | 0xF5 | 11 | "Exit QPI" command byte |
| WAIT_STATES | 4 | 0 | 12 | Wait (dummy) serial steps for reads |
| MODE | 2 | 0 | 13 | Bit 0 QSPI, bit 1 QPI |
| ENTER_QPI | 1 | 0 | 14 | Send EQPI_CMD instead of the next accesses' commands |
| EXIT_QPI | 1 | 0 | 15 | Send XQPI_CMD instead of the next accesses' commands |

On every edge where `last_HADDR[23]` = 1, `last_HWRITE` = 1, `last_HSEL` = 1 and `last_HTRANS[1]` = 1, each register whose selecting bit of `last_HADDR` is 1 takes the low bits of the current HWDATA (several registers can be written at once). Because the `last_*` registers hold while HREADY = 0, this repeats on every edge of a stalled data phase (with a well-behaved master HWDATA is stable, so the result is the same). Configuration reads change nothing. The registers cannot be read back (see "Read data").

## Serial engine

Inputs from the wrapper: the transaction address `{0, last_HADDR[22:0]}` (24 bits); the write data `data_i_sized` (see "Byte lanes"); `size` = 1, 2, 4, 4 bytes for `last_HSIZE` = 0, 1, 2, and anything else; `start`; WAIT_STATES; `rd_wr`; QSPI = MODE[0]; QPI = MODE[1]; `short_cmd` = ENTER_QPI OR EXIT_QPI; and the command byte `cmd` = ENTER_QPI ? EQPI_CMD : EXIT_QPI ? XQPI_CMD : `rd_wr` ? RD_CMD : WR_CMD.

Derived counts (8-bit values), with quad = QPI OR QSPI:
- `wait_start` = (QPI ? 2 : 8) + (quad ? 6 : 24)
- `data_start` = `wait_start` + (`rd_wr` ? WAIT_STATES : 0)
- `final_count` = `short_cmd` ? 8 : `data_start` + (quad ? 2 : 8) × `size`
- `done` = (`counter` = `final_count`), combinational.

Registers (reset values in brackets), updated on every edge:
- `estate` [IDLE]: IDLE goes to BUSY when `start` = 1; BUSY goes to IDLE when `done` = 1.
- `sck` [0]: becomes 0 if `done`; else toggles if `ce_n` = 0; else holds.
- `ce_n` [1]: becomes 1 if `done`; else 0 if `estate` = BUSY; else 1.
- `counter` [0]: increments if `sck` = 1 and NOT `done`; else becomes 0 if `estate` = IDLE; else holds.

So after `start`, `ce_n` falls one edge after the engine enters BUSY, `sck` then toggles every clock (one serial step = one `sck` period of 2 clocks, counted on the falling edge of `sck`), and when `counter` reaches `final_count` the transaction ends.

## Pin tables

`dout` depends on the mode and the current `counter` value. Unlisted counts give 0.

- **SPI** (QPI = 0, QSPI = 0): `dout[0]` carries one bit per count and `dout[3:1]` are 0. Count 0 to 7: `cmd[7 - count]`. Count 8 to 31: `addr[31 - count]` (address bits 23 down to 0). Count 32 and up: `data_i_sized[k]` with k = 39 - count for counts 32 to 39, 55 - count for 40 to 47, 71 - count for 48 to 55, 87 - count for 56 to 63, and bit 0 for counts 64 and up. (So the data goes out byte 0 first, each byte MSB first.)
- **QSPI** (QPI = 0, QSPI = 1): count 0 to 7: `dout[0]` = `cmd[7 - count]`, other bits 0. Counts 8 to 13: the address nibbles `addr[23:20]`, `[19:16]`, `[15:12]`, `[11:8]`, `[7:4]`, `[3:0]`. Counts 14 to 21: the data nibbles `data_i_sized[7:4]`, `[3:0]`, `[15:12]`, `[11:8]`, `[23:20]`, `[19:16]`, `[31:28]`, `[27:24]`.
- **QPI** (QPI = 1, either QSPI): counts 0 and 1: `cmd[7:4]`, `cmd[3:0]`. Counts 2 to 7: the six address nibbles in the order above. Counts 8 to 15: the eight data nibbles in the order above.

`douten`, with `has_wait` = (WAIT_STATES ≠ 0) AND `rd_wr`:
- SPI: always 0001.
- QSPI: count < 8: 0001; count < 14: 1111; else if count < `data_start` and `has_wait`: 0000; else if `rd_wr`: 0000; else 1111.
- QPI: count < 8: 1111; else if count < `data_start` and `has_wait`: 0000; else if `rd_wr`: 0000; else 1111.

## Read capture

Four byte registers `data[0..3]` (no reset). On every edge where `data_start` ≤ `counter` ≤ `final_count` and `sck` = 1, the byte `data[i]` with i = (`counter` - `data_start`) >> (quad ? 1 : 3) shifts in the memory data: quad modes `data[i]` becomes `{data[i][3:0], din}`; SPI `data[i]` becomes `{data[i][6:0], din[1]}`. An index above 3 changes nothing. `data_o` = `{data[3], data[2], data[1], data[0]}`.

## Byte lanes

`data_i_sized` and HRDATA follow `last_HSIZE` and `last_HADDR[1:0]`:

| last_HSIZE | last_HADDR[1:0] | data_i_sized | HRDATA |
|---|---|---|---|
| 2 (word) | any | `data_i` | `data_o` |
| 1 (half) | bit 1 = 0 | `{16'b0, data_i[15:0]}` | `{16'b0, data_o[15:0]}` |
| 1 (half) | bit 1 = 1 | `{16'b0, data_i[31:16]}` | `{data_o[15:0], 16'b0}` |
| 0 (byte) | 00 | `{24'b0, data_i[7:0]}` | `{24'b0, data_o[7:0]}` |
| 0 (byte) | 01 | `{24'b0, data_i[15:8]}` | `{16'b0, data_o[7:0], 8'b0}` |
| 0 (byte) | 10 | `{24'b0, data_i[23:16]}` | `{8'b0, data_o[7:0], 16'b0}` |
| 0 (byte) | 11 | `{24'b0, data_i[31:24]}` | `{data_o[7:0], 24'b0}` |
| 3 to 7 | any | `{24'b0, data_i[31:24]}` | `{data_o[7:0], 24'b0}` |

HRDATA is combinational and is shown in every cycle, whatever the access is.
