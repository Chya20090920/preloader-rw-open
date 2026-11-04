# -*- coding: utf-8 -*-
"""壳的命令行入口。

    python -m preloader_rw.relay [--route ...] [--credential kind=path] \
        [--da1 ...] [--da2 ...] [--attach] [--no-fastboot] [--json]

缺省输入取 `PANE_*` 环境变量（见 `relay\assembly.py`）；命令行参数优先。
不读任何配置文件，也不写回配置。
"""

import argparse
import os
import sys
import time

from .assembly import build_service
from .config import ConfigError, RelayConfig
from .preflight import PreflightFail, run as preflight_run


def _log(level, text):
    sys.stderr.write("[%s] %s\n" % (level, text))
    sys.stderr.flush()


def build_parser():
    ap = argparse.ArgumentParser(prog="relay", description="MTK 中继壳")
    ap.add_argument("--json", action="store_true", help="从 stdin 读意图，stdout 回结果")
    return ap


def main(argv=None):
    from .assembly import load_environment, resolve_path
    args = build_parser().parse_args(argv)
    try:
        asm = load_environment()
    except Exception as exc:
        _log("err", "环境变量装配失败: %r" % (exc,))
        return 2
    credentials = dict(asm["credentials"])
    require = list(asm["require"])
    images = {str(k): resolve_path(v) for k, v in (asm.get("images") or {}).items()}
    backend = asm["backend"]
    try:
        config = RelayConfig(
            route=asm["route"],
            table=asm.get("table", ""),
            credentials=credentials,
            required_kinds=tuple(require),
            da1=asm["da1"],
            da2=asm["da2"],
            attach=bool(asm["attach"]),
            fastboot_enabled=bool(asm["fastboot"]),
            fastboot_timeout_s=float(asm["fastboot_timeout"]),
            device_timeout_s=float(asm["device_timeout"]),
            images=images,
            backend=backend,
        )
    except (ConfigError, TypeError, ValueError) as exc:
        _log("err", "配置错误: %s" % (exc,))
        return 2
    _log("info", "装配已加载（仅环境变量）")

    result = preflight_run(config)
    if isinstance(result, PreflightFail):
        if not args.json:
            _log("err", "预检失败 %s: %s" % (result.code, result.detail))
            return 2
        # --json 由 GUI 驱动：凭证可以在 fire 时补齐，启动预检延后到首次 fire。
        _log("warn", "启动预检未过（%s: %s）：--json 模式延后到首次 fire 再检"
             % (result.code, result.detail))
        result = None
    else:
        _log("ok", "预检通过")
        _log("info", result.bundle.table.summary() if result.bundle.table
             else "命令表：内置默认")

    from preloader_rw.core.engine.runlog import RawLog, new_run_dir
    run_dir = new_run_dir()
    raw = RawLog(run_dir)

    bind = {}

    def kernel_log(level, text):
        now = time.time()
        stamp = (time.strftime("%H:%M:%S", time.localtime(now))
                 + ".%03d" % int((now % 1) * 1000))
        raw.write("[%s] [%s] %s" % (stamp, level, text))
        svc = bind.get("service")
        if svc is not None:
            svc.emit_kernel_log(level, text)

    _log("info", "运行目录: %s" % os.path.basename(str(run_dir)))
    service = build_service(config, backend=backend, log=_log,
                            kernel_log=kernel_log, run_dir=str(run_dir),
                            usb_vid=asm["usb_vid"], usb_pids=asm["usb_pids"])
    bind["service"] = service
    service.bundle = result.bundle if result is not None else None
    if args.json:
        # JSON 管道模式：生命周期由 GUI 的 fire 意图驱动，不自启。
        # 只读插拔轮询常开：先开工具、后插设备也能被识别。
        service.start_device_watch()
        from .api import serve_stdio
        try:
            serve_stdio(service)
        except KeyboardInterrupt:
            pass
        finally:
            service.stop()
            raw.close()
        return 0

    service.start()
    try:
        while service.machine.state.value not in ("failed", "dead"):
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        raw.close()
    return 0 if service.machine.state.value == "gated" else 1


if __name__ == "__main__":
    sys.exit(main())
