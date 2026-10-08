# traffic_light: traffic light controller with pedestrian button

Cycles green, yellow, red. A pedestrian button can end green early, but not before a minimum green time.

## Parameters
| Name        | Default | Meaning |
|-------------|---------|---------|
| GREEN_TIME  | 8       | Normal green length, in cycles. |
| MIN_GREEN   | 3       | Shortest green allowed when a pedestrian is waiting. 1 <= MIN_GREEN <= GREEN_TIME. |
| YELLOW_TIME | 3       | Yellow length, in cycles. |
| RED_TIME    | 6       | Red length, in cycles. |

All times are at least 1 and at most 16.

## Ports
| Port   | Dir | Width | Meaning |
|--------|-----|-------|---------|
| clk    | in  | 1     | Clock. All state changes on the rising edge. |
| rst    | in  | 1     | Synchronous reset, active high. |
| ped    | in  | 1     | Pedestrian button. Sampled on each rising edge. |
| state  | out | 2     | 0 = green, 1 = yellow, 2 = red. |
| green  | out | 1     | High when `state` is green. |
| yellow | out | 1     | High when `state` is yellow. |
| red    | out | 1     | High when `state` is red. |
| walk   | out | 1     | Pedestrians may cross. Same as `red`. |

## Behavior
A *phase* is a stretch of cycles in one state. Count the cycles of a phase from 1.

1. **Reset.** On a rising edge with `rst` high: state becomes green, starting a fresh phase, and any pending pedestrian request is cleared.
2. **Order.** Green, then yellow, then red, then green again.
3. **Lengths.**
   - Yellow lasts exactly YELLOW_TIME cycles.
   - Red lasts exactly RED_TIME cycles.
   - Green lasts GREEN_TIME cycles, unless a pedestrian request is pending (see 4). Then green ends after the first cycle that is both at least cycle MIN_GREEN of the phase and has the request already pending at the start of that cycle.
4. **Pedestrian request.** A request becomes pending at the rising edge where `ped` is high while the light is green or yellow. It takes effect from the next cycle on (it is registered). The request is cleared at every rising edge while the light is red, and `ped` is ignored during red.
5. Outputs `green`, `yellow`, `red`, and `walk` are decoded directly from `state`.
