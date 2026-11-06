# Third-party source

`EF_I2S_APB.v` combines these Apache-2.0 files from Efabless (full license text in `LICENSE`):

| Module(s) | Source |
|---|---|
| `i2s_rx`, `EF_I2S` | https://github.com/efabless/EF_I2S @ d335705, `hdl/rtl/EF_I2S.v` |
| `EF_I2S_APB` | same repository, `hdl/rtl/bus_wrappers/EF_I2S_APB.v` |
| `ef_util_fifo`, `ef_util_gating_cell` | https://github.com/efabless/EF_IP_UTIL @ a2b3fb6, `hdl/ef_util_lib.v` |

Copyright 2023 Efabless Corp. Changes for rtl-env: the files were concatenated into one file, and the
`timescale and `default_nettype lines were replaced by a single `timescale. No logic was changed.

Contamination check (2026-11-06): neither repository nor its code (module names `i2s_rx`, `EF_I2S`)
appears in NVIDIA CVDP's NOTICE or problem data.
