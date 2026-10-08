# fifo: synchronous first-in, first-out queue

Stores up to `DEPTH` words of `WIDTH` bits. Words come out in the order they went in.

## Parameters
| Name  | Default | Meaning |
|-------|---------|---------|
| WIDTH | 8       | Bits per word |
| DEPTH | 4       | Max words stored. Must be a power of 2, at least 2. |

## Ports
| Port  | Dir | Width              | Meaning |
|-------|-----|--------------------|---------|
| clk   | in  | 1                  | Clock. All state changes on the rising edge. |
| rst   | in  | 1                  | Synchronous reset, active high. |
| push  | in  | 1                  | Write `din` into the queue this cycle. |
| din   | in  | WIDTH              | Data to write. |
| pop   | in  | 1                  | Remove the oldest word this cycle. |
| dout  | out | WIDTH              | The oldest word in the queue. |
| full  | out | 1                  | High when the queue holds DEPTH words. |
| empty | out | 1                  | High when the queue holds 0 words. |
| count | out | clog2(DEPTH)+1     | Number of words currently stored (0 to DEPTH). |

## Behavior
1. **Reset.** On a rising edge with `rst` high, the queue becomes empty: `empty = 1`, `full = 0`, `count = 0`. Reset wins over `push` and `pop`.
2. **Push.** On a rising edge with `push` high and `full` low, `din` is added to the back of the queue. If `full` is high, the push is ignored and the stored data is unchanged.
3. **Pop.** On a rising edge with `pop` high and `empty` low, the oldest word is removed. If `empty` is high, the pop is ignored.
4. **Push and pop together.** Each is checked against `full` and `empty` as they were *before* the edge. When the queue is neither full nor empty, both happen and `count` stays the same. When full, only the pop happens. When empty, only the push happens.
5. **Read is show-ahead.** `dout` always shows the oldest stored word, with no delay. You do not need to pop to see it. Popping moves `dout` to the next word. `dout` is undefined when `empty` is high.
6. `full`, `empty`, `count`, and `dout` depend only on stored state, not on this cycle's inputs.
