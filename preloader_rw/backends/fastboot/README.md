# backends/fastboot

Google Android platform-tools 的 fastboot 客户端（`fastboot.exe`）及其 Windows
运行依赖（`AdbWinApi.dll` / `AdbWinUsbApi.dll`）。

**谁在用**：`preloader_rw/relay/fastboot/cli.py` 以固定路径调用它执行
`fastboot oem reboot-edl`（把设备从 fastboot 切进 EDL/下载态）；
`preloader_rw/relay/preflight.py` 启动预检逐件核对这三个文件，缺件或空文件
回 `VENDOR_INCOMPLETE`。

**来源与版本**：Google Android SDK platform-tools。所附二进制不含版本资源
字段，具体版本未知；如需确定版本，请自行用官方同版本文件替换整组三件
（三件必须配套，混版本会起不来）。

**许可证**：Apache License 2.0（全文见本目录 `LICENSE-APACHE-2.0.txt`）。
仓库根的 `LICENSE`（MIT）只覆盖本工程自身代码，与本目录互不干扰。
