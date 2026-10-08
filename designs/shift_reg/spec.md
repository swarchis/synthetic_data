# shift_reg: loadable bidirectional shift register

Holds `WIDTH` bits. Each clock it can reset, load a whole word, or shift one place left or right with a new bit coming in.

## Parameters
| Name  | Default | Meaning |
|-------|---------|---------|
| WIDTH | 8       | Bits stored. At least 2. |

## Ports
| Port     | Dir | Width | Meaning |
|----------|-----|-------|---------|
| clk      | in  | 1     | Clock. All state changes on the rising edge. |
| rst      | in  | 1     | Synchronous reset, active high. |
| load     | in  | 1     | Load `din` into the register. |
| din      | in  | WIDTH | Word to load. |
| shift_en | in  | 1     | Shift by one place. |
| dir      | in  | 1     | Shift direction: 0 = left (toward the top bit), 1 = right (toward bit 0). |
| sin      | in  | 1     | Bit that enters the register on a shift. |
| q        | out | WIDTH | Current contents. |
| sout     | out | 1     | The bit that a shift in the current `dir` would push out. |

## Behavior
On each rising edge, the first matching rule applies:
1. **Reset.** `rst` high: `q` becomes 0.
2. **Load.** `load` high: `q` becomes `din`. Load wins over shift.
3. **Shift.** `shift_en` high:
   - `dir = 0` (left): every bit moves up one place, `sin` enters at bit 0, the old top bit is dropped.
   - `dir = 1` (right): every bit moves down one place, `sin` enters at the top bit, the old bit 0 is dropped.
4. **Hold.** Otherwise `q` keeps its value.

`sout` is combinational: it is `q[WIDTH-1]` when `dir = 0` and `q[0]` when `dir = 1`. It follows `dir` immediately, without waiting for a clock edge.
