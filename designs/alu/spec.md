# alu: 8-bit arithmetic logic unit

Combinational. Outputs change as soon as inputs change; there is no clock.

## Parameters
| Name  | Default | Meaning |
|-------|---------|---------|
| WIDTH | 8       | Bits in `a`, `b`, and `y`. |

## Ports
| Port  | Dir | Width | Meaning |
|-------|-----|-------|---------|
| a     | in  | WIDTH | First operand (unsigned). |
| b     | in  | WIDTH | Second operand (unsigned). |
| op    | in  | 3     | Operation select, see table. |
| y     | out | WIDTH | Result. |
| carry | out | 1     | Extra result bit, see table. |
| zero  | out | 1     | High when `y` is 0. |

## Operations
| op | Name | y | carry |
|----|------|---|-------|
| 0 | ADD | (a + b), low WIDTH bits | 1 if a + b does not fit in WIDTH bits |
| 1 | SUB | (a - b), low WIDTH bits (wraps around) | 1 if a < b (a borrow happened) |
| 2 | AND | a & b | 0 |
| 3 | OR  | a \| b | 0 |
| 4 | XOR | a ^ b | 0 |
| 5 | SHL | a shifted left by 1, a 0 enters at bit 0 | the bit shifted out (a's top bit) |
| 6 | SHR | a shifted right by 1, a 0 enters at the top | the bit shifted out (a's bit 0) |
| 7 | SLT | 1 if a < b, else 0 | 0 |

`zero` is 1 exactly when `y` is 0, for every operation.
