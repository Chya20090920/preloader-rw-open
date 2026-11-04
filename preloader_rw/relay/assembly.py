# -*- coding: utf-8 -*-
"""壳的装配根：唯一知道具体 backend 与内核构造方式的地方。

运行期装配 = `PANE_*` 环境变量；缺项回落到本件内置的 `_BUILTIN`。
不读任何配置文件，也不写回配置。命令行显式参数优先于环境变量。

环境变量（全部可选，缺项用 `_BUILTIN`）：

    PANE_PAYLOAD     载荷路径（工作流脚本，默认 preloader_rw/payloads/route.py；可换自己的载荷）
    PANE_TABLE       命令表路径（JSON，默认 preloader_rw/profiles/mtk_official.json；
                     换自己的 DA 固件时与载荷一起换）
    PANE_DA1         DA1 镜像路径
    PANE_DA2         DA2 镜像路径
    PANE_ATTACH      `1/true/yes/on` 表示附加模式
    PANE_FASTBOOT    `1/true/yes/on` 表示启用 fastboot 健康轮
    PANE_FASTBOOT_TIMEOUT  fastboot 在位等待秒数，默认 2.0
    PANE_DEVICE_TIMEOUT    等设备出现秒数，默认 3.0
    PANE_BACKEND     `winusb` / `libusb`
    PANE_IMAGES      分区镜像，`partition=path` 以 `;` 分隔
    PANE_INPUT_TIMEOUT_S  运行时索取输入的等待秒数（默认 300）
    PANE_USB_VID     目标设备 VID（十进制或 0x 十六进制，默认 0x0E8D）
    PANE_USB_PIDS    目标设备 PID 集合，`;` 分隔（默认 0x2000）
"""

import os

from preloader_rw import REPO_ROOT
from preloader_rw.core.kernel.kernel import Kernel, KernelConfig

from .config import RelayConfig

ROOT = REPO_ROOT

# 内置回落值：仅在对应 PANE_* 未设置时生效。
# DA1/DA2 镜像不设默认值：它们是设备厂商的专有二进制，必须由用户经
# PANE_DA1 / PANE_DA2 显式提供（见 config.example.bat），预检会对缺项报错。
# 认证只有一条路：SECURITY-SET-FLASH-POLICY 提交签名应答；应答在设备
# 开口要的那一刻由宿主实时提供（host_input），不设任何预置路径。
_BUILTIN = {
    "route": "preloader_rw/payloads/route.py",
    "table": "",
    "require": (),
    "credentials": {},
    "da1": "",
    "da2": "",
    "attach": False,
    "fastboot": True,
    "fastboot_timeout": 2.0,
    "device_timeout": 15.0,
    "backend": "libusb",
    "images": {},
    "usb_vid": None,
    "usb_pids": None,
}


class AssemblyError(RuntimeError):
    pass


def _pairs(text, label):
    """`k=v;k=v` → dict。格式错误立即抛，不做静默跳过。"""
    out = {}
    for item in (text or "").replace(",", ";").split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise AssemblyError("%s 需要 k=v，收到 %r" % (label, item))
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise AssemblyError("%s 含空键或空值: %r" % (label, item))
        out[key] = value
    return out


def _flag(value, default):
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _number(value, default, label):
    if value is None or not str(value).strip():
        return default
    try:
        return float(value)
    except ValueError:
        raise AssemblyError("%s 需要数字，收到 %r" % (label, value))


def _usb_id(text, label):
    """单个 VID/PID：十进制或 0x 十六进制。格式错误立即抛。"""
    try:
        value = int(str(text).strip(), 0)
    except ValueError:
        raise AssemblyError("%s 需要 USB ID（十进制或 0x 十六进制），收到 %r"
                            % (label, text))
    if not 0 <= value <= 0xFFFF:
        raise AssemblyError("%s 超出 16 位范围: %r" % (label, text))
    return value


def _usb_pids(text):
    out = tuple(_usb_id(item, "PANE_USB_PIDS")
                for item in str(text).replace(",", ";").split(";")
                if item.strip())
    if not out:
        raise AssemblyError("PANE_USB_PIDS 为空")
    return out


def load_environment(environ=None):
    """从环境变量读装配；缺项用内置值补齐。不触碰文件系统。"""
    env = os.environ if environ is None else environ
    merged = dict(_BUILTIN)

    if env.get("PANE_PAYLOAD"):
        merged["route"] = env["PANE_PAYLOAD"].strip()
    if env.get("PANE_TABLE"):
        merged["table"] = env["PANE_TABLE"].strip()
    if env.get("PANE_DA1"):
        merged["da1"] = env["PANE_DA1"]
    if env.get("PANE_DA2"):
        merged["da2"] = env["PANE_DA2"]
    if env.get("PANE_IMAGES"):
        merged["images"] = _pairs(env["PANE_IMAGES"], "PANE_IMAGES")
    merged["attach"] = _flag(env.get("PANE_ATTACH"), merged["attach"])
    merged["fastboot"] = _flag(env.get("PANE_FASTBOOT"), merged["fastboot"])
    merged["fastboot_timeout"] = _number(
        env.get("PANE_FASTBOOT_TIMEOUT"), merged["fastboot_timeout"],
        "PANE_FASTBOOT_TIMEOUT")
    merged["device_timeout"] = _number(
        env.get("PANE_DEVICE_TIMEOUT"), merged["device_timeout"],
        "PANE_DEVICE_TIMEOUT")
    if env.get("PANE_BACKEND"):
        merged["backend"] = env["PANE_BACKEND"]
    if env.get("PANE_USB_VID"):
        merged["usb_vid"] = _usb_id(env["PANE_USB_VID"], "PANE_USB_VID")
    if env.get("PANE_USB_PIDS"):
        merged["usb_pids"] = _usb_pids(env["PANE_USB_PIDS"])
    return merged


def resolve_path(value):
    if not value:
        return ""
    text = os.fspath(value)
    return text if os.path.isabs(text) else os.path.join(ROOT, text)


class AssemblyError(RuntimeError):
    pass


def build_backend_factory(backend="winusb", vid=None, pids=None):
    """vid/pids 为 None 时由后端用自己的 profile 默认值。"""
    if backend == "winusb":
        from preloader_rw.backends.winusb.backend import WinusbBackend
        return lambda: WinusbBackend(vid=vid, pids=pids)
    if backend == "libusb":
        from preloader_rw.backends.libusb.backend import LibusbBackend
        return lambda: LibusbBackend(vid=vid, pids=pids)
    raise AssemblyError("未知 EDL 后端: %r" % (backend,))


def build_kernel_factory(config, log=None):
    def factory(bundle, backend, runtime_input):
        kernel_config = KernelConfig(
            da1=bundle.da1,
            da2=bundle.da2,
            attach=config.attach,
            beat=config.beat,
            # 表由预检装好并验过；这里只搬过去，不重读文件。
            table=getattr(bundle, "table", None),
        )
        return Kernel(backend, runtime_input, kernel_config, log=log)
    return factory


def _event_text(event):
    """把壳事件压成一行日志；时间戳由宿主统一加。"""
    if hasattr(event, "name") and hasattr(event, "ok"):
        verdict = "OK" if event.ok else (event.failure or "FAIL")
        return "命令 %s 结束: %s（终态 %s）" % (event.name, verdict, event.state)
    if hasattr(event, "state"):
        return "状态 -> %s（%s）" % (event.state, getattr(event, "reason", ""))
    if hasattr(event, "text"):
        return event.text
    if hasattr(event, "source"):
        return "健康报告(%s): %s" % (event.source, event.values)
    return repr(event)


def build_service(config, backend="winusb", log=None, kernel_log=None,
                  usb_vid=None, usb_pids=None, **kwargs):
    from .service import RelayService
    return RelayService(
        config,
        kernel_factory=build_kernel_factory(config, log=kernel_log),
        backend_factory=build_backend_factory(backend, vid=usb_vid,
                                                pids=usb_pids),
        emit=(lambda event: log("info", _event_text(event))) if log else None,
        **kwargs
    )

