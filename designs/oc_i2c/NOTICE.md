# Third-party source

`i2c_sys.v` contains these files from the OpenCores I2C controller by Richard Herveille, taken from
https://github.com/freecores/i2c at commit dba90b2 ("Sync rtl/verilog to OpenCores SVN rev 76"),
which is the original author's code before later third-party additions:

| Module | Source file |
|---|---|
| `i2c_master_bit_ctrl` | `rtl/verilog/i2c_master_bit_ctrl.v` |
| `i2c_master_byte_ctrl` | `rtl/verilog/i2c_master_byte_ctrl.v` |
| `i2c_master_top` | `rtl/verilog/i2c_master_top.v` |
| (defines) | `rtl/verilog/i2c_master_defines.v` |

Changes: the files were concatenated into one file; `include lines were replaced by the inlined
contents of `i2c_master_defines.v` and a single `timescale; the `#1` simulation delays on register
assignments were removed. No logic was changed. Each file keeps its original copyright notice,
license statement, and disclaimer:

> Copyright (C) 2001 Richard Herveille, richard@asics.ws
>
> This source file may be used and distributed without restriction provided that this copyright
> statement is not removed from the file and that any derivative work contains the original
> copyright notice and the associated disclaimer.
>
> THIS SOFTWARE IS PROVIDED "AS IS" AND WITHOUT ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT
> NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.
> (full disclaimer in each source file header)

`i2c_target` and `i2c_sys` are original work by the rtl-env founders.
