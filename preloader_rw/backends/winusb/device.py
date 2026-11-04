# -*- coding: utf-8 -*-
"""preloader_rw.core.ports.device.Device 的 WinUSB 实现。

逻辑端点经 profile.endpoint_address() 映射为物理端点号；
除传输超时按「本轮无数据」处理外，所有失败一律抛 IOError。
设备从总线移除时，IOError 消息含 "device gone"。
"""

from preloader_rw.core.ports.device import Endpoint

from . import bindings, profile


class WinusbDevice:
    """一个已打开的 WinUSB 设备句柄。"""

    def __init__(self, api, handle, usb_handle, info=None):
        self._api = api
        self._handle = handle
        self._usb = usb_handle
        self.info = info
        self._closed = False

    # ------------------------------------------------------------ 内部

    def _check(self) -> None:
        if self._closed or not self._handle or not self._usb:
            raise IOError("设备已关闭")

    def _addr(self, ep: Endpoint) -> int:
        return profile.endpoint_address(ep)

    def _fail(self, action: str, code: int):
        if bindings.is_device_gone(code):
            raise IOError("%s: device gone (Windows 错误 %d)" % (action, code))
        raise IOError("%s: %s (Windows 错误 %d)" %
                      (action, self._api.error_message(code), code))

    # ------------------------------------------------------------ 端口协议

    def claim(self, interface: int) -> int:
        """WinUSB 由驱动独占接口，无需再次 claim。"""
        self._check()
        return 0

    def release(self, interface: int) -> int:
        """与 claim 对称的空操作。"""
        self._check()
        return 0

    def read(self, ep: Endpoint, size: int, timeout_ms: int) -> bytes:
        self._check()
        if size <= 0:
            return b""
        addr = self._addr(ep)
        ok, _n, err, data = self._api.read_pipe(
            self._usb, addr, size, timeout_ms)
        if ok:
            return data
        # 超时按「本轮无数据」返回，与既有传输层语义一致
        if bindings.is_timeout(err):
            return b""
        self._fail("bulk read 0x%02X" % addr, err)

    def write(self, ep: Endpoint, data: bytes, timeout_ms: int) -> int:
        self._check()
        raw = bytes(data)
        addr = self._addr(ep)
        ok, sent, err = self._api.write_pipe(
            self._usb, addr, raw, timeout_ms)
        if ok:
            if sent != len(raw):
                raise IOError("bulk write 0x%02X: 短写 %d/%d B" %
                              (addr, sent, len(raw)))
            return sent
        self._fail("bulk write 0x%02X" % addr, err)

    def write_zlp(self, ep: Endpoint, timeout_ms: int) -> int:
        """发送零长包。失败按端口约定抛 IOError。"""
        self._check()
        addr = self._addr(ep)
        ok, _sent, err = self._api.write_pipe(
            self._usb, addr, b"", timeout_ms)
        if not ok:
            self._fail("bulk zlp 0x%02X" % addr, err)
        return 0

    def control(self, bm: int, req: int, wv: int, wi: int, wlen: int,
                timeout_ms: int) -> int:
        self._check()
        ok, got, err, _data = self._api.control_transfer(
            self._usb, bm, req, wv, wi, wlen, timeout_ms)
        if not ok:
            self._fail("control 0x%02X/0x%02X" % (bm, req), err)
        return got

    def clear_halt(self, ep: Endpoint) -> None:
        self._check()
        addr = self._addr(ep)
        err = self._api.reset_pipe(self._usb, addr)
        if err:
            self._fail("clear_halt 0x%02X" % addr, err)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._usb:
            self._api.winusb_free(self._usb)
            self._usb = None
        if self._handle:
            self._api.close_handle(self._handle)
            self._handle = None
