# edge_detector: rising and falling edge pulses

Watches a 1-bit input `sig` and outputs a one-cycle pulse whenever it changes.

## Ports
| Port     | Dir | Width | Meaning |
|----------|-----|-------|---------|
| clk      | in  | 1     | Clock. All state changes on the rising edge. |
| rst      | in  | 1     | Synchronous reset, active high. |
| sig      | in  | 1     | Signal to watch. Sampled on each rising edge. |
| rise     | out | 1     | One-cycle pulse: `sig` went from 0 to 1. |
| fall     | out | 1     | One-cycle pulse: `sig` went from 1 to 0. |
| any_edge | out | 1     | High when `rise` or `fall` is high. |

## Behavior
Call the value of `sig` sampled at rising edge k `s[k]`.

1. **Registered outputs.** `rise` and `fall` change only on rising edges. After edge k:
   - `rise = 1` exactly when `s[k] = 1` and `s[k-1] = 0`.
   - `fall = 1` exactly when `s[k] = 0` and `s[k-1] = 1`.
   So a change in `sig` shows up as a pulse during the clock cycle *after* the edge that sampled it, lasting exactly one cycle.
2. **Reset.** On a rising edge with `rst` high: `rise = 0`, `fall = 0`, and the remembered previous sample becomes 0. So if `sig` is 1 at the first edge after reset ends, that counts as a rise.
3. `any_edge` is combinational: `rise | fall`.
