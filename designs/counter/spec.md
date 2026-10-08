# counter: 4-bit up counter

Counts up by one every clock cycle and wraps from 15 back to 0.

## Ports
| Port  | Dir | Width | Meaning |
|-------|-----|-------|---------|
| clk   | in  | 1     | Clock. All state changes on the rising edge. |
| rst   | in  | 1     | Synchronous reset, active high. |
| count | out | 4     | Current count. |

## Behavior
1. **Reset.** On a rising edge with `rst` high, `count` becomes 0.
2. **Count.** On a rising edge with `rst` low, `count` becomes `count + 1`. After 15 it wraps to 0.
