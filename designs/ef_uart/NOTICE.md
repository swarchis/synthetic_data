# Third-party source

`EF_UART_APB.v` combines, without logic changes, these Apache-2.0 files by Efabless Corp.:

| Module(s) | Source | Commit |
|---|---|---|
| `ef_util_sync`, `ef_util_ticker`, `ef_util_glitch_filter`, `ef_util_fifo`, `ef_util_gating_cell` | https://github.com/efabless/EF_IP_UTIL, `hdl/ef_util_lib.v` | a2b3fb6 (2025-01-13) |
| `EF_UART`, `BAUDGEN`, `UART_RX`, `UART_TX` | https://github.com/efabless/EF_UART, `hdl/rtl/EF_UART.v` | 793ec5a (2025-02-13) |
| `EF_UART_APB` | https://github.com/efabless/EF_UART, `hdl/rtl/bus_wrappers/EF_UART_APB.v` | 793ec5a (2025-02-13) |

Changes: the files were concatenated into one file and only the needed utility modules were kept. Original copyright and license headers are preserved. See `LICENSE` (Apache License 2.0).
