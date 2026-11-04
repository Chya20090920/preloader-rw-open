@echo off
rem ===========================================================================
rem Example user config for Preloader_Rw. Copy this file to "config.bat"
rem AT THE REPO ROOT (one level up from scripts\) and edit the paths.
rem config.bat is git-ignored.
rem Keep this file pure ASCII if your paths allow it; cmd parses .bat by bytes.
rem Scope: vivo MTK platform devices only. Other brands/platforms are unsupported.
rem ===========================================================================

rem --- Required: DA1 / DA2 images -------------------------------------------
rem These are proprietary vendor binaries (Download Agent). Supply your own
rem dumps for your device; the repo does not ship them.
set "PANE_DA1=C:\path\to\DA1.bin"
set "PANE_DA2=C:\path\to\DA2.bin"

rem --- Authentication ---------------------------------------------------------
rem The only auth method: the device asks for a signed response at the
rem SECURITY-SET-FLASH-POLICY round; the GUI (or a --json host) is asked at
rem that moment. Nothing to pre-configure here -- there is NO preset path.
rem Wait timeout for the host input (seconds, default 300):
rem set "PANE_INPUT_TIMEOUT_S=300"

rem --- Optional --------------------------------------------------------------
rem Payload (workflow script): default preloader_rw/payloads/route.py; point
rem to your own to replace the whole DA1->DA2->auth workflow.
rem Contract: preloader_rw/core/kernel/api.py
rem set "PANE_PAYLOAD=preloader_rw\payloads\route.py"

rem Command table (JSON): default preloader_rw/profiles/mtk_official.json.
rem The kernel knows no command names of its own -- it reads this table.
rem Swap it together with the payload when you bring your own DA firmware.
rem set "PANE_TABLE=preloader_rw\profiles\mtk_official.json"

rem Transport backend: libusb (default) or winusb
rem set "PANE_BACKEND=libusb"

rem Target USB IDs (decimal or 0x hex). Defaults: VID 0x0E8D, PID 0x2000
rem (MediaTek preloader). If your device enumerates with other IDs, set
rem them here AND add the matching HardwareID to
rem preloader_rw\backends\winusb\driver\mtk_edl.inf
rem before installing the driver. Multiple PIDs: separate with ';'
rem set "PANE_USB_VID=0x0E8D"
rem set "PANE_USB_PIDS=0x2000"

rem Partition images for flashing: partition=path pairs separated by ';'
rem set "PANE_IMAGES=boot=C:\path\to\boot.img;system=C:\path\to\system.img"

rem Device wait timeout seconds (default 15)
rem set "PANE_DEVICE_TIMEOUT=15"

rem fastboot health probing: 1 (default) or 0
rem set "PANE_FASTBOOT=1"
