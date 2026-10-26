# EF_TMR32_APB: 32-bit timer with PWM, APB interface

A 32-bit timer/counter with a prescaler, up / down / up-down counting, one-shot and periodic modes, two PWM outputs with optional dead time and inversion, a fault input, and three interrupt sources. Software controls it through an APB slave port.

The file contains these modules: `EF_TMR32_APB` (top: APB registers and interrupts), `EF_TMR32` (timer and PWM core), `ef_util_ped` (rising-edge detector), and `ef_util_gating_cell` (clock gate). Tests use only the top-level ports. The design is based on Efabless's open-source EF_TMR32 IP (Apache-2.0).

## Parameters

| Name | Default | Meaning |
|---|---|---|
| PRW | 16 | Width of the prescaler register. |

`EF_TMR32` also has `PWM_FAULT_CLR_C0 = 16'hA539` and `PWM_FAULT_CLR_C1 = 16'hA953`, the two keys used to clear a fault (see "Fault handling").

## Ports (EF_TMR32_APB)

| Port | Dir | Width | Meaning |
|---|---|---|---|
| PCLK | in | 1 | Clock. |
| PRESETn | in | 1 | Reset, active low, **asynchronous**: every register with a reset clears immediately when PRESETn falls. |
| PSEL, PENABLE, PWRITE | in | 1 each | APB control. |
| PADDR | in | 32 | Address. Only bits [15:0] are decoded; upper bits are ignored. |
| PWDATA | in | 32 | Write data. |
| PRDATA | out | 32 | Read data (see "Reads"). |
| PREADY | out | 1 | Always 1. |
| IRQ | out | 1 | Interrupt: OR of the masked interrupt status bits. |
| pwm0, pwm1 | out | 1 each | PWM outputs. |
| pwm_fault | in | 1 | Fault input. It reaches the PWM outputs only through the fault latch (see "Fault handling"), so they go low one clock edge after `pwm_fault` rises, never combinationally. |

## APB writes and reads

- **Writes:** a write happens on the rising edge of a cycle with PSEL = 1, PENABLE = 1, PWRITE = 1. The register selected by PADDR[15:0] takes the low bits of PWDATA (as wide as the register). Writes to unmapped addresses and to read-only registers do nothing. There is no error response.
- **Reads:** PRDATA is combinational and depends only on PADDR[15:0], in every cycle (not only during a read transfer). It shows the selected register's value, zero-extended to 32 bits. Any address not listed as readable below returns `0xDEADBEEF`.

## Register map

| Offset | Name | Width | Access | Meaning |
|---|---|---|---|---|
| 0x0000 | TMR | 32 | R | Current timer value. |
| 0x0004 | RELOAD | 32 | RW | Terminal count when counting up; start value when counting down. |
| 0x0008 | PR | PRW | RW | Prescaler: the timer advances once every PR + 1 clock cycles. |
| 0x000C | CMPX | 32 | RW | Compare value X. |
| 0x0010 | CMPY | 32 | RW | Compare value Y. |
| 0x0014 | CTRL | 7 | RW | Bit 0 TE timer enable; 1 TS restart; 2 P0E PWM0 enable; 3 P1E PWM1 enable; 4 DTE dead-time enable; 5 PI0 invert PWM0; 6 PI1 invert PWM1. |
| 0x0018 | CFG | 3 | RW | Bits [1:0] DIR: 10 up, 01 down, 11 up/down, 00 hold. Bit 2 P: 1 periodic, 0 one-shot. |
| 0x001C | PWM0CFG | 12 | RW | PWM0 actions, see "PWM". |
| 0x0020 | PWM1CFG | 16 | RW | PWM1 actions; all 16 bits are stored and read back, only bits [11:0] are used. |
| 0x0024 | PWMDT | 8 | RW | Dead time, in timer ticks. |
| 0x0028 | PWMFC | 16 | RW | Fault-clear register. |
| 0xFF00 | IM | 3 | RW | Interrupt mask. |
| 0xFF04 | MIS | 3 | R | Masked interrupt status: RIS AND IM. |
| 0xFF08 | RIS | 3 | R | Raw interrupt status. |
| 0xFF0C | IC | 3 | W | Interrupt clear (not readable: reads return 0xDEADBEEF). |
| 0xFF10 | GCLK | 1 | RW | Clock-gate enable. In this build the clock gate is a plain wire (no gating macro is defined), so GCLK is stored and read back but has no effect. |

All registers reset to 0.

## Prescaler and ticks

The core keeps a prescale counter `pr` (reset value **1**). On each rising edge: if TE = 0, `pr` loads PR; if TE = 1, `pr` loads PR when `pr` is 0 and otherwise decrements. A **tick** is any cycle in which `pr` = 0. With TE = 1 there is one tick every PR + 1 cycles. Note that with TE = 0 and PR = 0, every cycle is a tick.

## Starting the timer

A rising edge of TE (TE was 0 in the previous cycle and is 1 now) is a **start**. On the edge that ends a start cycle:
- TMR loads RELOAD if DIR = 01 (down), otherwise 0;
- the direction becomes down if DIR = 01, otherwise up;
- the timer becomes **running**.

The edge detector that finds the rising edge of TE has no reset.

## Counting

TMR changes only while TE = 1, only on ticks, and only while the timer is running (it holds otherwise). On a tick, in order of priority:

1. TS = 1 and DIR = 01: TMR loads RELOAD.
2. TS = 1 and DIR = 10 or 11: TMR becomes 0.
3. DIR = 11 (up/down): TMR increments when the direction is up, decrements when it is down.
4. DIR = 01 (down): if TMR = 0, TMR loads RELOAD when periodic and holds when one-shot; otherwise TMR decrements.
5. DIR = 10 (up): if TMR = RELOAD, TMR becomes 0 when periodic and holds when one-shot; otherwise TMR increments.
6. DIR = 00: TMR holds.

**Direction** (reset value up) is updated on every tick, whether or not TE = 1: DIR = 11 turns down when TMR = RELOAD - 1 while going up, and turns up when TMR = 1 while going down. DIR = 10 and 00 set it up; DIR = 01 sets it down.

**One-shot stop:** in one-shot mode, on a tick, if DIR[0] = 1 (down or up/down), TMR = 1 and the direction is down, the timer stops running. (Up-counting one-shot mode does not stop the timer; it holds at RELOAD per rule 5.)

## Match flags

- MX = (TMR == CMPX), MY = (TMR == CMPY), combinational.
- TO (timeout) = (TMR == RELOAD) when the direction is up, (TMR == 0) when it is down.

## PWM

Each PWM channel has an internal state bit (reset 0). On a tick with that channel enabled (P0E / P1E), the state becomes 0 if `pwm_fault` = 1, otherwise it applies the action selected by exactly one of these events (evaluated on the current TMR and direction):

| Event | Condition | Config bits |
|---|---|---|
| E0 zero | TMR = 0, TMR != CMPX, TMR != CMPY, TMR != RELOAD (either direction) | [1:0] |
| E1 CMPX going up | direction up, TMR = CMPX, TMR != 0, != CMPY, != RELOAD | [3:2] |
| E2 CMPY going up | direction up, TMR = CMPY, TMR != 0, != CMPX, != RELOAD | [5:4] |
| E3 reload | TMR = RELOAD, TMR != 0, != CMPX, != CMPY (either direction) | [7:6] |
| E4 CMPY going down | direction down, TMR = CMPY, TMR != 0, != CMPX, != RELOAD | [9:8] |
| E5 CMPX going down | direction down, TMR = CMPX, TMR != 0, != CMPY, != RELOAD | [11:10] |

Action codes: 00 keep, 01 set low, 10 set high, 11 invert. If no event matches exactly (none, or several values coincide), the state is kept.

**Dead time** (shared by both channels, driven by PWM0's state): a dead-time counter (reset 0) loads PWMDT on a tick when it is 0 and otherwise decrements on ticks. A delayed copy of PWM0's state (reset 0) samples PWM0's state on ticks when the counter is 0.

**Output stage** (registered every cycle, reset 0):
- with DTE = 1: out0 = delayed AND state0; out1 = NOT delayed AND NOT state0 (complementary outputs with a gap);
- with DTE = 0: out0 = state0; out1 = state1.

Then `pwmN = (outN XOR PIN) AND NOT fault_latch AND PNE`, combinationally, where `fault_latch` is the registered latch from "Fault handling" (not the `pwm_fault` input itself).

## Fault handling

A fault latch (reset 0) sets whenever `pwm_fault` = 1. To clear it, write PWMFC = 0xA539, then PWMFC = 0xA953: an internal "armed" bit sets while PWMFC = 0xA539 and clears while PWMFC = 0xA953; the latch clears on an edge where it is armed and PWMFC = 0xA953 (and `pwm_fault` = 0). While the latch is set, both PWM outputs are 0.

## Interrupts

RIS bit 0 = TO, bit 1 = MX, bit 2 = MY. Each RIS bit sets on any edge where its flag is 1 and clears on the edge after a write of 1 to that IC bit (IC holds the written value for one cycle, then returns to 0; while IC's bit is 1, clearing wins over setting). MIS = RIS AND IM; IRQ = OR of MIS.
