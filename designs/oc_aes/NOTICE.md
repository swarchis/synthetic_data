# Third-party source

`aes_unit.v` contains these files from the OpenCores AES core by Rudolf Usselmann, taken from
https://github.com/freecores/aes_core at commit 0d81d6e ("Add FuseSoC support and Github CI actions"):

| Module | Source file |
|---|---|
| `aes_sbox` | `rtl/verilog/aes_sbox.v` |
| `aes_inv_sbox` | `rtl/verilog/aes_inv_sbox.v` |
| `aes_rcon` | `rtl/verilog/aes_rcon.v` |
| `aes_key_expand_128` | `rtl/verilog/aes_key_expand_128.v` |
| `aes_cipher_top` | `rtl/verilog/aes_cipher_top.v` |
| `aes_inv_cipher_top` | `rtl/verilog/aes_inv_cipher_top.v` |

Changes: the files were concatenated into one file; the `timescale.v` includes were replaced by a
single `timescale; the `#1` simulation delays on register assignments were removed. No logic was
changed. Each file keeps its original copyright notice, license statement, and disclaimer:

> Copyright (C) 2000-2002 Rudolf Usselmann, www.asics.ws, rudi@asics.ws
>
> This source file may be used and distributed without restriction provided that this copyright
> statement is not removed from the file and that any derivative work contains the original
> copyright notice and the associated disclaimer.
>
> THIS SOFTWARE IS PROVIDED "AS IS" AND WITHOUT ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT
> NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.
> (full disclaimer in each source file header)

`aes_unit` (the top module that puts the two channels side by side) is original work by the rtl-env
founders.

Contamination check (2026-11-05): neither the source repository nor its code (module names, author)
appears in NVIDIA CVDP's NOTICE or problem data. CVDP does use other cipher cores by a different author
(secworks: blake2s, sha1, prince); none of those are used here.
