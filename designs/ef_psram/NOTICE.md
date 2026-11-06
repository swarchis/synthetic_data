# Third-party source

`EF_PSRAM_CTRL_AHBL.v` combines these Apache-2.0 files from Efabless (full license text in `LICENSE`),
taken from https://github.com/efabless/EF_PSRAM_CTRL at commit 87dbffb:

| Module | Source file |
|---|---|
| `EF_PSRAM_CTRL` | `hdl/rtl/EF_PSRAM_CTRL.v` |
| `EF_PSRAM_CTRL_AHBL` | `hdl/rtl/bus_wrapper/EF_PSRAM_CTRL_AHBL.v` |

Copyright Efabless Corp. Changes for rtl-env: the two files were concatenated into one file, and the
`timescale and `default_nettype lines were replaced by a single `timescale. No logic was changed.

Contamination check (2026-11-06): neither the repository nor its code appears in NVIDIA CVDP's NOTICE
or problem data.
