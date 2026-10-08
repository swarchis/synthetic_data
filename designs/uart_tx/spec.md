# uart_tx: UART transmitter (8N1)

Sends one byte at a time over a single wire `tx`: a start bit, 8 data bits (least significant first), and a stop bit. No parity. Each bit is held for `CLKS_PER_BIT` clock cycles.

## Parameters
| Name         | Default | Meaning |
|--------------|---------|---------|
| CLKS_PER_BIT | 4       | Clock cycles per bit on `tx`. At least 2. |

## Ports
| Port  | Dir | Width | Meaning |
|-------|-----|-------|---------|
| clk   | in  | 1     | Clock. All state changes on the rising edge. |
| rst   | in  | 1     | Synchronous reset, active high. |
| start | in  | 1     | Request to send `data`. Only acted on when idle. |
| data  | in  | 8     | Byte to send. Captured when the transfer starts. |
| tx    | out | 1     | Serial output. High when idle. |
| busy  | out | 1     | High while a byte is being sent. |
| done  | out | 1     | One-cycle pulse when a byte has finished. |

## Behavior
1. **Idle.** `tx` is 1 and `busy` is 0.
2. **Start.** On a rising edge while idle with `start` high, the transmitter captures `data` and begins sending. Changing `data` later has no effect on the byte being sent. `start` is ignored while busy.
3. **Frame.** Starting right after that edge, `tx` outputs, each bit held for exactly CLKS_PER_BIT cycles:
   - the start bit (0),
   - data bit 0, bit 1, ..., bit 7,
   - the stop bit (1).
   That is 10 bits, so 10 x CLKS_PER_BIT cycles in total.
4. **Busy.** `busy` is 1 from the cycle after the start edge through the last cycle of the stop bit.
5. **Done.** On the edge that ends the stop bit, the transmitter returns to idle and `done` is 1 for exactly that one following cycle (while `busy` is already 0). A new `start` seen on that same cycle's next edge begins the next byte right away.
6. **Reset.** On a rising edge with `rst` high: idle, `tx = 1`, `done = 0`. A transfer in progress is abandoned.
