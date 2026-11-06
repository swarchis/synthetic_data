# Third-party source

`EF_GPIO8_APB.v` combines, without logic changes, these Apache-2.0 files by Efabless Corp.:

| Module(s) | Source | Commit |
|---|---|---|
| `ef_util_sync`, `ef_util_ped`, `ef_util_ned`, `ef_util_gating_cell` | https://github.com/efabless/EF_IP_UTIL, `hdl/ef_util_lib.v` | a2b3fb6 (2025-01-13) |
| `EF_GPIO8` | https://github.com/efabless/EF_GPIO8, `hdl/rtl/EF_GPIO8.v` | 3896b56 (2025-02-06) |
| `EF_GPIO8_APB` | https://github.com/efabless/EF_GPIO8, `hdl/rtl/bus_wrappers/EF_GPIO8_APB.v` | 3896b56 (2025-02-06) |

Changes: the files were concatenated into one file and only the needed utility modules were kept. Original copyright and license headers are preserved. See `LICENSE` (Apache License 2.0).
