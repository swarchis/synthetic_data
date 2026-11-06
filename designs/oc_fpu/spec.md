# fpu: pipelined single-precision floating-point unit

A floating-point unit for IEEE 754 binary32 (single precision) numbers: add, subtract, multiply and divide, in four rounding modes, with exception flags. It is fully pipelined: it accepts a new operation on every clock and returns each result a fixed number of clocks later. Results follow IEEE 754 except for the cases listed under "Deviations from IEEE 754", which are part of this design's behavior.

The file contains: `fpu` (top), `except` (operand classification), `pre_norm` (add/subtract alignment), `pre_norm_fmul` (multiply/divide exponent and sign), `post_norm` (normalization and rounding), and arithmetic primitives (`add_sub27`, `mul_r2`, `div_r2`). Tests use only the top-level ports. The design is the OpenCores FPU by Rudolf Usselmann.

## Ports

| Port | Dir | Width | Meaning |
|---|---|---|---|
| clk | in | 1 | Clock. |
| fpu_op | in | 3 | Operation: 0 add (opa + opb), 1 subtract (opa - opb), 2 multiply (opa × opb), 3 divide (opa / opb). 4 to 7 are reserved (see below). |
| rmode | in | 2 | Rounding mode: 0 to nearest, ties to even; 1 toward zero; 2 toward +infinity; 3 toward -infinity. |
| opa, opb | in | 32 | Operands, binary32 bit patterns (bit 31 sign, bits 30:23 biased exponent, bits 22:0 fraction). |
| out | out | 32 | Result, binary32. |
| inf | out | 1 | The result is an infinity. |
| snan | out | 1 | An operand is a signaling NaN. |
| qnan | out | 1 | The result is a NaN. |
| ine | out | 1 | Inexact. |
| overflow | out | 1 | Overflow. |
| underflow | out | 1 | Underflow. |
| zero | out | 1 | The result is a zero. |
| div_by_zero | out | 1 | Division of a finite nonzero number by zero. |

There is no reset and no handshake.

## Timing

fpu_op, rmode, opa and opb are sampled together at every rising edge. For the operation sampled at edge E, `out` and all eight flags show its result after edge E+3, until edge E+4 replaces them with the result of the operation sampled at E+1. So one result comes out per clock, three clocks after its inputs. All outputs are registers. They are undefined (X) until the first operations have passed through the pipeline (the first three edges).

## Numbers

All binary32 classes are supported as inputs and outputs: zeros of both signs, subnormal numbers (exponent field 0, nonzero fraction; they are not flushed to zero), normal numbers, infinities, quiet NaNs (exponent 255, fraction bit 22 = 1) and signaling NaNs (exponent 255, fraction bit 22 = 0, fraction nonzero).

## Results

Except for the deviations below, `out` is the IEEE 754 result of the operation, correctly rounded in the selected mode:
- Rounding uses the exact result. A result too large for the format becomes ±infinity in round-to-nearest, and in round toward ±infinity when it rounds away from zero; otherwise (round toward zero, or rounding toward zero's side) it becomes the largest finite number of that sign (0x7F7FFFFF or 0xFF7FFFFF).
- Results in the subnormal range are subnormal numbers (or zero), rounded at the subnormal precision (2^-149). (For multiply and divide, see deviation 2.)
- Zero signs follow IEEE 754: an exact zero sum or difference of operands with opposite signs (including x - x) is +0, except in round toward -infinity where it is -0; (-0) + (-0) is -0; products and quotients of zeros have the XOR of the operand signs.
- Infinities: inf ± finite = inf; finite × inf (nonzero) = inf; inf / finite = inf; finite / inf = 0; x / 0 for finite nonzero x = inf (signs as XOR for × and /).
- Invalid operations give a NaN: 0 × inf, inf × 0, 0 / 0.
- Any NaN operand gives a NaN result.

## NaN results

Every NaN result has magnitude 0x7FC00001 (a quiet NaN with fraction 0x400001), for every operation and whatever the operand NaNs were. If exactly one operand is a NaN, the result's sign bit is that operand's sign bit. If both operands are NaNs, or the NaN comes from an invalid operation, the sign bit is not specified.

## Deviations from IEEE 754

1. **Both operands infinite.** For any operation where opa and opb are both infinities (either signs), `out` and all eight flags are not specified. (In this design the result depends on neighboring operations in the pipeline.) Operations around it are not affected.
2. **Tiny multiply and divide results.** For a multiply or divide of two finite nonzero operands whose IEEE 754 result is a zero, a subnormal number, or ±2^-126 (magnitude 0x00800000, the smallest normal number), `out`, `zero` and `underflow` are not specified. (The design's rounding and underflow detection in this range do not follow IEEE 754: for example, some exact results come out one unit larger, some ties round away from zero, and underflow is sometimes missing or extra.) The other flags still follow their definitions below, including ine.
3. **Reserved operations.** For fpu_op = 4 to 7, `out` and the flags for that operation are not specified.

## Flags

Each flag describes the same operation as `out` in the same clock.

- **inf**: `out` is +infinity or -infinity.
- **qnan**: `out` is a NaN.
- **snan**: opa or opb is a signaling NaN (for any operation).
- **zero**: `out` is +0 or -0.
- **div_by_zero**: the operation is a division, opb is ±0, and opa is a finite nonzero number (normal or subnormal).
- **overflow**: the exact result, rounded to the format's precision with an unbounded exponent, exceeds the largest finite magnitude (IEEE 754 overflow). It is 0 for results that are infinite because an operand is infinite, and for division by zero.
- **ine** (inexact): the rounded result differs from the exact result; it is 1 whenever overflow is 1. It is 0 for exact results, NaN results and results that are exactly infinite or zero by the rules above. For a division whose opa is subnormal, ine is not specified.
- **underflow**: call a result tiny if the exact result is nonzero and its magnitude is below 2^-126 (the smallest normal number), judged before rounding. underflow is 1 when the result is tiny and inexact, and 0 otherwise, except where deviation 2 says it is not specified and for a division whose opa is subnormal (also not specified). (Add and subtract results that are tiny are always exact, so for add and subtract underflow is always 0.)
