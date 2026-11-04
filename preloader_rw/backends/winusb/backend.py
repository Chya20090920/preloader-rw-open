# -*- coding: utf-8 -*-
"""设备后端的 WinUSB 实现（端口类型见 preloader_rw.core.ports.device）。

枚举只读硬件 ID 与设备属性，不打开、不 claim 任何设备；
open/find 才产生设备句柄。
"""

from preloader_rw.core.ports.device import DeviceInfo

from . import bindings, enumerate as enumeration, profile
from .device import WinusbDevice


class WinusbBackend:
    """按 VID 过滤枚举，按 PID 集合查找。"""

    def __init__(self, vid=None, pids=None):
        self.vid = profile.DEFAULT_VID if vid is None else int(vid)
        self.pids = tuple(profile.DEFAULT_PIDS if pids is None else pids)
        self._closed = False

    def _check(self) -> None:
        if self._closed:
            raise IOError("后端已关闭")

    def list(self):
        """返回 VID 匹配的设备列表；无设备时返回空列表。"""
        self._check()
        return [info for info, _path in enumeration.list_interfaces(self.vid)]

    def open(self, info: DeviceInfo) -> WinusbDevice:
        """按 DeviceInfo 打开设备；找不到或打开失败抛 IOError。"""
        self._check()
        path = enumeration.find_path(info)
        if not path:
            raise IOError("未找到设备 %04x:%04x@addr%s" %
                          (info.vid, info.pid, info.addr))
        return self._open_path(info, path)

    def find(self, pids=None):
        """按 VID 与 PID 集合找设备，返回 (设备, 信息, 错误串)。"""
        self._check()
        want = tuple(int(p) for p in (pids if pids else self.pids))
        last_error = ""
        matched = False
        for info, path in enumeration.list_interfaces(self.vid):
            if not profile.matches(info, self.vid, want):
                continue
            matched = True
            try:
                return self._open_path(info, path), info, ""
            except IOError as exc:
                last_error = str(exc)
        if matched:
            return None, None, last_error
        return None, None, "未找到设备 %04x:%s" % (
            self.vid, "/".join("%04x" % p for p in want))

    def _open_path(self, info: DeviceInfo, path: str) -> WinusbDevice:
        """打开接口路径并初始化 WinUSB；任一步失败抛 IOError。"""
        api = bindings.WinUsbApi()
        handle = api.open_device(path)
        try:
            usb = api.winusb_initialize(handle)
        except IOError:
            api.close_handle(handle)
            raise
        return WinusbDevice(api, handle, usb, info)

    def close(self) -> None:
        self._closed = True
