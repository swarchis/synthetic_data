# Third-party source

`simple_spi_top.v` contains these files from the OpenCores simple_spi core by Richard Herveille, taken from
https://github.com/freecores/simple_spi at commit 61c39a5 ("Initial testbench release added"):

| Module | Source file |
|---|---|
| `fifo4` | `rtl/verilog/fifo4.v` (Copyright (C) 2002 Richard Herveille) |
| `simple_spi_top` | `rtl/verilog/simple_spi_top.v` (Copyright (C) 2002 Richard Herveille) |

Changes: the two files were concatenated into one file; the `timescale.v` includes were replaced by a
single `timescale; the `#1` simulation delays on register assignments were removed. No logic was
changed. Each file keeps its original copyright notice, license statement, and disclaimer:

> This source file may be used and distributed without restriction provided that this copyright
> statement is not removed from the file and that any derivative work contains the original
> copyright notice and the associated disclaimer.
>
> THIS SOFTWARE IS PROVIDED "AS IS" AND WITHOUT ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT
> NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.
> (full disclaimer in each source file header)

Contamination check (2026-11-05): neither the source repository nor its code appears in NVIDIA CVDP's
NOTICE or problem data. (CVDP has an unrelated problem named `simple_spi`: a 16-bit SPI state machine
with different ports and no Wishbone, FIFOs or SPCR/SPER registers.)
