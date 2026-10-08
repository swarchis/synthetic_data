# priority_encoder: highest-request-wins encoder

Combinational. Given `N` request lines, reports which is the highest-numbered one that is asking. Outputs change as soon as `req` changes; there is no clock.

## Parameters
| Name | Default | Meaning |
|------|---------|---------|
| N    | 8       | Number of request lines. Must be a power of 2, at least 2. |

## Ports
| Port   | Dir | Width      | Meaning |
|--------|-----|------------|---------|
| req    | in  | N          | Request lines. Bit i high means requester i is asking. |
| idx    | out | clog2(N)   | Index of the highest set bit of `req`. |
| valid  | out | 1          | High when any bit of `req` is set. |
| onehot | out | N          | `req` with only its highest set bit kept. |

## Behavior
1. **Priority.** Bit N-1 has the highest priority, bit 0 the lowest. `idx` is the index of the highest-numbered set bit.
   Example (N = 8): `req = 8'b0010_0110` gives `idx = 5`.
2. **One-hot.** `onehot` has exactly one bit set, at position `idx`. Same example: `onehot = 8'b0010_0000`.
3. **No requests.** When `req` is 0: `valid = 0`, `idx = 0`, `onehot = 0`.
