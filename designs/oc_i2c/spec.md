# i2c_sys: I2C master with Wishbone interface, and a register target

A small I2C system: the OpenCores I2C master controller (by Richard Herveille) and an I2C target device on one open-drain bus with pull-ups. Software controls the master through a classic Wishbone slave port: it sets the bus speed, then issues byte-level commands (generate a START, write a byte, read a byte, generate a STOP), and polls a status register or takes an interrupt when each command completes. The target answers at address 0x50 with four 8-bit registers.

The file contains: `i2c_sys` (top: the bus and the two devices), `i2c_master_top` (Wishbone registers and interrupt), `i2c_master_byte_ctrl` (byte-level command sequencer), `i2c_master_bit_ctrl` (SCL/SDA bit timing, input filter, bus-state detection, arbitration), and `i2c_target` (the register target). Tests use only the top-level ports.

## Ports (i2c_sys)

| Port | Dir | Width | Meaning |
|---|---|---|---|
| clk | in | 1 | Clock for everything. |
| rst_n | in | 1 | Reset, active low, **asynchronous**, for the master and the target: every register with a reset clears immediately when rst_n falls. |
| wb_rst_i | in | 1 | Synchronous reset, active high, for the **master only**: on a rising edge with wb_rst_i = 1, every master register that rst_n resets takes the same reset value. The target is not affected. |
| wb_adr_i | in | 3 | Wishbone register address. |
| wb_dat_i | in | 8 | Wishbone write data. |
| wb_dat_o | out | 8 | Wishbone read data (see "Wishbone port"). |
| wb_we_i, wb_stb_i, wb_cyc_i | in | 1 each | Wishbone write enable, strobe, cycle. |
| wb_ack_o | out | 1 | Wishbone acknowledge. |
| wb_inta_o | out | 1 | Interrupt request. |
| stretch_en | in | 1 | Target clock stretching enable (see "The target"). |
| ext_scl_low, ext_sda_low | in | 1 each | Another device on the bus pulling SCL or SDA low (1 = pull low). |
| scl, sda | out | 1 each | The bus lines. |

## The bus

Both lines are open drain with pull-ups. A line is 1 unless some device pulls it low:

- `scl` = (master releases SCL) AND NOT (target pulls SCL low) AND NOT ext_scl_low
- `sda` = (master releases SDA) AND NOT (target pulls SDA low) AND NOT ext_sda_low

The master releases a line when its output enable for that line (`scl_oen` / `sda_oen` below, active low) is 1, and pulls it low when that enable is 0. The master and the target read the bus lines themselves. Everything is combinational from registers, so `scl` and `sda` change only just after rising clock edges (or when an input changes).

## Wishbone port

- `wb_ack_o` is a register, updated on every rising edge to `wb_cyc_i AND wb_stb_i AND NOT wb_ack_o`. So it rises one clock after CYC and STB are both high, stays high for exactly one clock, and, if CYC and STB stay high, alternates 1, 0, 1, ... It has no reset.
- **Writes** take effect on a rising edge where `wb_we_i` = 1 and `wb_ack_o` = 1 (the edge that ends the acknowledge cycle), using `wb_adr_i` and `wb_dat_i` at that edge. A master that keeps STB high for two clocks therefore writes once.
- **Reads:** `wb_dat_o` is a register, loaded on every rising edge (whatever CYC, STB and WE are) with the value of the register selected by `wb_adr_i`. So in the cycle where `wb_ack_o` = 1, `wb_dat_o` shows the selected register as it was at the previous rising edge. It has no reset.

## Registers (i2c_master_top)

| Address | Read | Write |
|---|---|---|
| 0 | PRERlo: prescale bits [7:0] | PRERlo |
| 1 | PRERhi: prescale bits [15:8] | PRERhi |
| 2 | CTR: control | CTR |
| 3 | RXR: received byte | TXR: byte to transmit |
| 4 | SR: status | CR: command |
| 5 | TXR | (no effect) |
| 6 | CR | (no effect) |
| 7 | 0x00 | (no effect) |

Reset values: PRER = 0xFFFF, CTR = 0x00, TXR = 0x00, CR = 0x00. All 8 bits of CTR are stored and read back.

**PRER** (16 bits) sets the bus speed: the bit controller advances one step every PRER + 1 clocks (see "Bit timing"), and each bit command takes 5 steps (one to leave idle, then A to D), so without clock stretching SCL runs at clk / (5 × (PRER + 1)).

**Minimum prescale: transfers need PRER ≥ 2.** With PRER = 0 or 1 the steps are faster than the master's input path (two capture flops, then the filter below). During a WRITE check step the filtered `sSDA` can still show an earlier low level after the master has released SDA, so `al_bit` rises and the command ends with AL = 1 (a false arbitration loss) instead of transferring the byte. In simulation every command tried at PRER = 0 or 1 ended this way, while PRER = 2 and up transfer normally.

**With target clock stretching (stretch_en = 1), use PRER ≥ 3.** At PRER = 2, a repeated START issued while the target is still holding SCL low can go wrong: `slave_wait` sets in the same clock that `cnt` reaches 0, and the `cnt` = 0 rule wins (see "Step clock"), so the master takes its next START step and pulls SDA low while SCL is still low. No START appears on the bus; the target stays in its current state and takes the following bits as data, one bit late. In simulation this happened only at PRER = 2, when the repeated START command followed the previous byte within about 2 clocks; PRER = 3 to 9 worked at every gap tried.

**CTR**: bit 7 EN (core enable), bit 6 IEN (interrupt enable). The other bits have no effect.

**CR** (command): bit 7 STA (generate a START or repeated START), bit 6 STO (generate a STOP), bit 5 RD (read a byte), bit 4 WR (write a byte), bit 3 ACK (the acknowledge to send after a read: 0 = ACK, 1 = NACK), bits 2:1 reserved, bit 0 IACK (clear the interrupt flag).
- A write to address 4 updates CR only if EN = 1 at that edge. With EN = 0 the write is lost.
- On every rising edge without a register write, CR updates as follows: bits 2:1 and bit 0 become 0, so IACK is a one-clock pulse; bits 7:4 become 0 if the byte controller signals "done" or the bit controller signals "arbitration lost" in that cycle, otherwise they hold. Bit 3 holds.

**SR** (status, read only):

| Bit | Name | Meaning |
|---|---|---|
| 7 | RxACK | A register loaded every clock with the byte controller's `ack_out`: the acknowledge bit received after the last write (0 = ACK, 1 = NACK). |
| 6 | BUSY | The bit controller's bus-busy flag (see "Bus state"). |
| 5 | AL | Arbitration lost. A register: each clock it becomes `al_bit OR (AL AND NOT STA)`, where `al_bit` is the bit controller's arbitration-lost signal. So it stays 1 until a command with STA = 1 is in CR. |
| 4:2 | | Always 0. |
| 1 | TIP | Transfer in progress. A register loaded every clock with `RD OR WR` (CR bits 5 and 4). |
| 0 | IF | Interrupt flag. A register: each clock it becomes `(done OR al_bit OR IF) AND NOT IACK`. |

RxACK, AL, TIP and IF reset to 0. `wb_inta_o` is a register (reset 0), loaded every clock with `IF AND IEN`.

## Byte controller (i2c_master_byte_ctrl)

It turns the CR command bits into a sequence of bit commands for the bit controller (START, STOP, WRITE one bit, READ one bit) and reports "done" when the whole command finishes.

State registers (all reset to the values in brackets by rst_n or wb_rst_i): a shift register `sreg` (0) that is also RXR, a bit counter `dcnt` (0), the bit command `core_cmd` (NOP), the bit to send `core_txd` (0), pulses `ld` (0) and `shift` (0), `done` (0), `ack_out` (0), and the state (IDLE).

- When the bit controller's arbitration-lost signal is 1, on that edge everything except `sreg` and `dcnt` returns to its reset value.
- `sreg`: on an edge with `ld` = 1 it loads TXR; otherwise, with `shift` = 1, it shifts left and takes the bit controller's received bit as its new bit 0. `dcnt`: loads 7 when `ld` = 1; decrements when `shift` = 1. `cnt_done` means `dcnt` = 0.
- Otherwise, on every edge: `core_txd` becomes `sreg[7]`, and `shift`, `ld` and `done` become 0, unless the state's rule below sets them. The state, `core_cmd` and `ack_out` hold unless a rule sets them. `go` = `(RD OR WR OR STO) AND NOT done`.

| State | On an edge |
|---|---|
| IDLE | If `go`: `ld` = 1, and go to START with bit command START if STA = 1, else to READ with READ if RD = 1, else to WRITE with WRITE if WR = 1, else to STOP with STOP. |
| START | When the bit controller acknowledges: `ld` = 1, and go to READ with READ if RD = 1, else to WRITE with WRITE. |
| WRITE | When the bit controller acknowledges: if `cnt_done`, go to ACK with bit command READ; otherwise stay in WRITE with WRITE and `shift` = 1. |
| READ | When the bit controller acknowledges: if `cnt_done`, go to ACK with bit command WRITE, otherwise stay in READ with READ; in both cases `shift` = 1 and `core_txd` = ACK (CR bit 3). |
| ACK | When the bit controller acknowledges: `ack_out` = the received bit, `core_txd` = 1, and if STO = 1 go to STOP with bit command STOP, otherwise go to IDLE with NOP and `done` = 1. Without the acknowledge: `core_txd` = ACK (CR bit 3). |
| STOP | When the bit controller acknowledges: go to IDLE with NOP, and `done` = 1. |

So a write transfers TXR MSB first and then reads the target's acknowledge bit into RxACK. A read shifts 8 received bits into RXR (MSB first) and then sends CR's ACK bit. `done` is a one-clock pulse at the end of the whole command, after the STOP if STO was set.

## Bit timing (i2c_master_bit_ctrl)

**Step clock.** A 16-bit counter `cnt` (reset 0) and a step pulse `clk_en` (reset 1). On each edge: if wb_rst_i = 1, or `cnt` = 0, or EN = 0, or `scl_sync` = 1, then `cnt` loads PRER and `clk_en` = 1. Otherwise, if `slave_wait` = 1, `cnt` holds and `clk_en` = 0. Otherwise `cnt` decrements and `clk_en` = 0.

**Input capture.** Each line is captured by two flip-flops (reset 0) every clock (`cSCL`, `cSDA`; the second stage is used below).

**Input filter.** A 14-bit counter `fcnt` (reset 0) is cleared by wb_rst_i or EN = 0. Otherwise, when it is 0 it loads PRER shifted right by 2 bits (truncated to 14 bits), else it decrements. On each edge where `fcnt` = 0, a 3-bit shift register per line (reset 111) shifts in the captured line value. Every clock, the filtered line `sSCL` / `sSDA` (reset 1) becomes the majority of its 3 shift-register bits, and the delayed copy `dSCL` / `dSDA` (reset 1) becomes the previous filtered value.

**Clock stretching.** `dscl_oen` (no reset) is the master's `scl_oen` one clock ago. `slave_wait` (reset 0) becomes `(scl_oen AND NOT dscl_oen AND NOT sSCL) OR (slave_wait AND NOT sSCL)`: it sets when the master has just released SCL but the filtered line is still low, and clears when the filtered line is high. `scl_sync` = `dSCL AND NOT sSCL AND scl_oen`: the filtered SCL fell while the master is releasing it (another device drove it low), which restarts the step counter.

**Received bit.** On each edge where `sSCL` = 1 and `dSCL` = 0 (filtered SCL rising), the received bit takes `sSDA`. It has no reset.

## Bus state and arbitration

- `sta_cond` (reset 0) becomes `NOT sSDA AND dSDA AND sSCL` each clock (SDA fell while SCL is high: a START by anyone), and `sto_cond` (reset 0) becomes `sSDA AND NOT dSDA AND sSCL` (a STOP by anyone).
- BUSY (reset 0) becomes `(sta_cond OR BUSY) AND NOT sto_cond` each clock.
- `cmd_stop` (reset 0) becomes "the current bit command is STOP" on each edge with `clk_en` = 1.
- `al_bit` (reset 0) becomes `(sda_chk AND NOT sSDA AND sda_oen) OR (state != idle AND sto_cond AND NOT cmd_stop)` each clock: the master released SDA to send a 1 while checking, but the line is low (another master won), or a STOP appeared on the bus that this master did not command.
- When `al_bit` = 1, on that edge the bit controller returns to idle with `scl_oen` = 1, `sda_oen` = 1, `sda_chk` = 0 and no acknowledge.

## Bit commands

The bit controller has a state machine (reset: idle, `scl_oen` = 1, `sda_oen` = 1, `sda_chk` = 0, acknowledge 0; wb_rst_i resets it the same way). Its acknowledge to the byte controller is 0 on every edge except as listed below. It moves only on edges where `clk_en` = 1. From idle it takes the current bit command (START, STOP, WRITE, READ; NOP stays idle and keeps both enables) and walks these states; each row gives `scl_oen`, `sda_oen` and `sda_chk` after that step:

| Command | Steps (`scl_oen`, `sda_oen`) | Last step |
|---|---|---|
| START | A (keep, 1), B (1, 1), C (1, 0), D (1, 0), E (0, 0) | E: acknowledge = 1, back to idle |
| STOP | A (0, 0), B (1, 0), C (1, 0), D (1, 1) | D: acknowledge = 1, back to idle |
| READ | A (0, 1), B (1, 1), C (1, 1), D (0, 1) | D: acknowledge = 1, back to idle |
| WRITE | A (0, d), B (1, d), C (1, d) with `sda_chk` = 1, D (0, d) | D: acknowledge = 1, back to idle |

Here d is the byte controller's `core_txd` at that step, and "keep" means unchanged. `sda_chk` is 1 only after WRITE step C and 0 after every other step. Because steps wait for `clk_en`, and `clk_en` stops while `slave_wait` = 1, a target holding SCL low delays the next step.

## The target (i2c_target, address 0x50)

The target samples the bus lines every clock into `scl_d` and `sda_d` (reset 1), and sees:
- START: `scl` and `scl_d` are 1, `sda_d` is 1 and `sda` is 0;
- STOP: `scl` and `scl_d` are 1, `sda_d` is 0 and `sda` is 1;
- SCL rising: `scl` = 1 and `scl_d` = 0; SCL falling: `scl` = 0 and `scl_d` = 1.

Registers (all reset by rst_n): state (IDLE), a bit counter (0), a shift register (0), a 2-bit pointer `ptr` (0), the direction bit, a stored NACK bit, four data registers `mem[0..3]` (all 0x00), and the two line drivers (both released).

Each clock, in this priority:
1. **START** (in any state): go to ADDRESS, bit counter = 0, release SDA.
2. **STOP**: go to IDLE, release SDA.
3. **IDLE**: nothing else happens until a START.
4. **Receiving** (ADDRESS, POINTER, DATA):
   - On SCL rising with fewer than 8 bits counted, shift in `sda` (MSB first) and count it.
   - On SCL falling with 8 bits counted, the count becomes 9 (the acknowledge slot), and:
     - ADDRESS: if the upper 7 bits match 0x50, pull SDA low (ACK) and store bit 0 as the direction; otherwise go to IDLE without acknowledging.
     - POINTER: `ptr` = the byte's low 2 bits; pull SDA low (ACK).
     - DATA: `mem[ptr]` = the byte; `ptr` increments (wrapping 3 to 0); pull SDA low (ACK).
   - On SCL falling in the acknowledge slot, the count returns to 0 and:
     - after a matched address with direction 1 (read), go to SENDING: load `mem[ptr]` and immediately drive its bit 7 (pull SDA low for a 0, release for a 1);
     - otherwise release SDA and go to POINTER (after an address) or DATA (after a pointer or data byte).
5. **Sending** (the byte's bits are driven MSB first):
   - On SCL rising with fewer than 8 bits counted, count the bit; with 8 counted, store `sda` as the master's acknowledge (1 = NACK) and the count becomes 9.
   - On SCL falling with 1 to 7 bits counted, drive the next bit (bit 7 minus the count).
   - On SCL falling with 8 bits counted, release SDA for the master's acknowledge.
   - On SCL falling in the acknowledge slot: after a NACK, go to IDLE and release SDA; after an ACK, `ptr` increments, load `mem[ptr]` (the new pointer), drive its bit 7, and the count returns to 0.

**Clock stretching.** On every SCL falling edge that ends an acknowledge slot (in receiving or sending), if `stretch_en` = 1 the target pulls SCL low and holds it for 24 clocks, then releases it. A START or STOP does not cancel a hold in progress.

The target writes only `mem`, never acknowledges any address other than 0x50, and does not check the R/W bit when deciding whether to acknowledge its address.
