# rr_arbiter: round-robin arbiter

`N` requesters share one resource. Each cycle at most one of them is granted it, and they take turns so that no requester waits forever.

## Parameters
| Name | Default | Meaning |
|------|---------|---------|
| N    | 4       | Number of requesters. Must be a power of 2, at least 2. |

## Ports
| Port     | Dir | Width    | Meaning |
|----------|-----|----------|---------|
| clk      | in  | 1        | Clock. All state changes on the rising edge. |
| rst      | in  | 1        | Synchronous reset, active high. |
| req      | in  | N        | Bit i high means requester i wants the resource. |
| grant    | out | N        | One-hot: bit i high means requester i holds the resource this cycle. All zeros means nobody does. |
| last_idx | out | clog2(N) | Index of the most recent winner. |

## Behavior
1. **Reset.** On a rising edge with `rst` high: `grant` becomes 0 and `last_idx` becomes N-1, so requester 0 has first priority after reset.
2. **Pick a winner.** On each rising edge (not in reset), look at `req` in this order: `last_idx + 1`, `last_idx + 2`, ..., wrapping around past N-1 to 0, and ending with `last_idx` itself (checked last). The first requester found with its `req` bit high wins.
3. **Grant.** If there is a winner w: `grant` becomes one-hot with bit w set, and `last_idx` becomes w. If nobody is requesting: `grant` becomes 0 and `last_idx` keeps its value.
4. `grant` is registered: it shows the decision made at the most recent rising edge, based on `req` sampled at that edge.

Example (N = 4): all four requesting every cycle gives grants 0, 1, 2, 3, 0, 1, ... after reset. If only requesters 1 and 3 ask, they alternate.
