# Third-party source

`EF_TMR32_APB.v` combines, without logic changes, these Apache-2.0 files by Efabless Corp.:

| Module(s) | Source | Commit |
|---|---|---|
| `ef_util_ped`, `ef_util_gating_cell` | https://github.com/efabless/EF_IP_UTIL, `hdl/ef_util_lib.v` | a2b3fb6 (2025-01-13) |
| `EF_TMR32` | https://github.com/efabless/EF_TMR32, `hdl/rtl/EF_TMR32.v` | 767f7b0 (2025-02-06) |
| `EF_TMR32_APB` | https://github.com/efabless/EF_TMR32, `hdl/rtl/bus_wrappers/EF_TMR32_APB.v` | 767f7b0 (2025-02-06) |

Changes: the files were concatenated into one file and only the needed utility modules were kept. Original copyright and license headers are preserved. See `LICENSE` (Apache License 2.0).
