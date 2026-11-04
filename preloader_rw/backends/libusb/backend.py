# -*- coding: utf-8 -*-
"""设备后端的 libusb 实现（端口类型见 preloader_rw.core.ports.device）。

枚举只读描述符，不打开、不 claim 任何设备；open/find 才产生设备句柄。
"""

from preloader_rw.core.ports.device import DeviceInfo

from . import bindings, profile
from .device import LibusbDevice


class LibusbBackend:
    """按 VID 过滤枚举，按 PID 集合查找。"""

    def __init__(self, dll_path=None, vid=None, pids=None):
        self.vid = profile.DEFAULT_VID if vid is None else int(vid)
        self.pids = tuple(profile.DEFAULT_PIDS if pids is None else pids)
        self._api = bindings.LibUsb(dll_path)
        self._api.init()
        self._closed = False

    def _check(self) -> None:
        if self._closed:
            raise IOError("后端已关闭")

    def detect(self):
        """枚举当前连接状态，供界面如实显示。只读描述符 + 试开一次。

        返回 {"state", "vid", "pid", "detail"}，state 取值：
          none      没有 VID 匹配的设备（未插入）
          no_driver 有设备但打不开：驱动未接管（LIBUSB_ERROR_NOT_SUPPORTED）
          busy      有设备但打不开：被占用或无权限（其余错误）
          openable  可以打开
        """
        self._check()
        devices = self.list()
        if not devices:
            return {"state": "none", "vid": self.vid, "pid": 0,
                    "edl": False, "detail": ""}
        info = devices[0]
        edl = info.pid in self.pids
        try:
            device = self.open(info)
        except IOError as exc:
            text = str(exc)
            state = ("no_driver" if "NOT_SUPPORTED" in text.upper()
                     else "busy")
            return {"state": state, "vid": info.vid, "pid": info.pid,
                    "edl": edl, "detail": text}
        try:
            device.close()
        except Exception:
            pass
        return {"state": "openable", "vid": info.vid, "pid": info.pid,
                "edl": edl, "detail": ""}

    def list(self):
        """返回 VID 匹配的设备列表；无设备时返回空列表。"""
        self._check()
        lst, devices = self._api.get_device_list()
        out = []
        try:
            for dev in devices:
                try:
                    desc = self._api.get_device_descriptor(dev)
                except IOError:
                    continue
                if desc.idVendor != self.vid:
                    continue
                out.append(DeviceInfo(
                    vid=desc.idVendor,
                    pid=desc.idProduct,
                    addr=self._api.get_device_address(dev),
                    product="",
                ))
        finally:
            self._api.free_device_list(lst, 1)
        return out

    def open(self, info: DeviceInfo) -> LibusbDevice:
        """按 DeviceInfo 打开设备；找不到或打开失败抛 IOError。"""
        self._check()
        wanted = None if not info.addr else info.addr
        handle, opened, error = self._open_matching((info.pid,), wanted, info.vid)
        if handle is None:
            if error is not None:
                raise error
            raise IOError("未找到设备 %04x:%04x@addr%s" %
                          (info.vid, info.pid, info.addr))
        return LibusbDevice(self._api, handle, opened)

    def find(self, pids=None):
        """按 VID 与 PID 集合找设备，返回 (设备, 信息, 错误串)。

        open 失败按“未就绪”处理：只把错误串带回去，由调用方继续轮询。
        设备从 fastboot 切 EDL 的瞬间可能匹配到 VID/PID 但驱动尚未就绪，
        一次 open 失败不能当成设备不存在，也不能终止等待。
        """
        self._check()
        want = tuple(pids) if pids else self.pids
        handle, info, error = self._open_matching(want, None)
        if handle is not None:
            return LibusbDevice(self._api, handle, info), info, ""
        if error is not None:
            return None, None, str(error)
        return None, None, "未找到设备 %04x:%s" % (
            self.vid, "/".join("%04x" % p for p in want))

    def _open_matching(self, pids, addr=None, vid=None):
        """枚举并打开首个匹配设备；未匹配返回 (None, None, None)。

        匹配到但 open 失败时返回 (None, None, 异常)，不向上抛。
        """
        want_vid = self.vid if vid is None else int(vid)
        lst, devices = self._api.get_device_list()
        matched = False
        last_error = None
        try:
            for dev in devices:
                try:
                    desc = self._api.get_device_descriptor(dev)
                except IOError:
                    continue
                if desc.idVendor != want_vid or desc.idProduct not in pids:
                    continue
                dev_addr = self._api.get_device_address(dev)
                if addr is not None and dev_addr != addr:
                    continue
                matched = True
                info = DeviceInfo(
                    vid=desc.idVendor,
                    pid=desc.idProduct,
                    addr=dev_addr,
                    product="",
                )
                try:
                    handle = self._api.open(dev)
                except IOError as exc:
                    last_error = exc
                    continue
                return handle, info, None
        finally:
            self._api.free_device_list(lst, 1)
        if matched and last_error is not None:
            return None, None, last_error
        return None, None, None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._api.deinit()
