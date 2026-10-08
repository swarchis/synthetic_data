# pwm: pulse-width modulation generator

Drives `out` high for `duty` cycles out of every `period` cycles, using an internal counter.

## Parameters
| Name  | Default | Meaning |
|-------|---------|---------|
| WIDTH | 8       | Bits in `period`, `duty`, and the counter. |

## Ports
| Port   | Dir | Width | Meaning |
|--------|-----|-------|---------|
| clk    | in  | 1     | Clock. All state changes on the rising edge. |
| rst    | in  | 1     | Synchronous reset, active high. |
| en     | in  | 1     | Enable. When low, the generator is stopped and cleared. |
| period | in  | WIDTH | Cycles per PWM period. |
| duty   | in  | WIDTH | Cycles per period that `out` is high. |
| cnt    | out | WIDTH | Current counter value. |
| out    | out | 1     | PWM output. |
| wrap   | out | 1     | High during the last cycle of each period. |

## Behavior
1. **Counter.** `cnt` counts 0, 1, ..., `period - 1`, then goes back to 0. A cycle where the counter is at the end of the period is called the *last* cycle:
   - *last* is true when `cnt >= period - 1`, or when `period = 0`.
   - The `>=` matters: if `period` is lowered while `cnt` is already past the new end, the next edge still returns `cnt` to 0.
   - With `period = 0` or `period = 1`, `cnt` stays at 0.
2. **Reset and enable.** On a rising edge with `rst` high or `en` low, `cnt` becomes 0. Otherwise, on a *last* cycle `cnt` becomes 0, and on any other cycle `cnt` goes up by 1.
3. **Output.** `out = en && (cnt < duty)`. So `duty = 0` keeps `out` low, and `duty >= period` keeps `out` high (while enabled).
4. **Wrap.** `wrap = en && last`.
5. `out` and `wrap` are combinational: they react to `en`, `period`, and `duty` immediately, without waiting for a clock edge.
