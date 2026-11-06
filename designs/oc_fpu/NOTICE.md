# Third-party source

`fpu.v` contains these files from the OpenCores FPU by Rudolf Usselmann, taken from
https://github.com/freecores/fpu at commit 99ed52c (directory `verilog/`):

| Module(s) | Source file |
|---|---|
| `add_sub27`, `mul_r2`, `div_r2` (primitives) | `verilog/primitives.v` |
| `except` | `verilog/except.v` |
| `pre_norm` | `verilog/pre_norm.v` |
| `pre_norm_fmul` | `verilog/pre_norm_fmul.v` |
| `post_norm` | `verilog/post_norm.v` |
| `fpu` | `verilog/fpu.v` |

Changes: the files were concatenated into one file with a single `timescale; the `#1` simulation
delays on register assignments were removed; and three simulation-only debug blocks (between
`synopsys translate_off` and `translate_on`, which referenced the original testbench hierarchy
`test.*` and are ignored by synthesis) were removed. No synthesizable logic was changed. Each file keeps
its original copyright notice, license statement, and disclaimer:

> Copyright (C) 2000 Rudolf Usselmann, rudi@asics.ws
>
> This source file may be used and distributed without restriction provided that this copyright
> statement is not removed from the file and that any derivative work contains the original
> copyright notice and the associated disclaimer.
>
> THIS SOFTWARE IS PROVIDED "AS IS" AND WITHOUT ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT
> NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.
> (full disclaimer in each source file header)

Contamination check (2026-11-06): neither the source repository nor its code (module names such as
`pre_norm_fmul`, `post_norm`, signal names such as `fpu_op`, `rmode`) appears in NVIDIA CVDP's NOTICE or
problem data.
