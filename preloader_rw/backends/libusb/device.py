# -*- coding: utf-8 -*-
"""preloader_rw.core.ports.device.Device 的 libusb 实现。

逻辑端点经 profile.endpoint_address() 映射为物理端点号；
除传输超时按「无数据」处理外，所有失败一律抛 IOError。
"""

import ctypes
import time

from preloader_rw.core.ports.device import Endpoint

from . import profile

LIBUSB_ERROR_IO = -1
LIBUSB_ERROR_TIMEOUT = -7
LIBUSB_ERROR_PIPE = -9

# 与既有传输层一致：PIPE / IO 错误重试两次并清 halt
RETRY_CODES = (LIBUSB_ERROR_PIPE, LIBUSB_ERROR_IO)
RETRIES = 2
RETRY_DELAY_S = 0.05


class LibusbDevice:
    """一个已打开的 libusb 设备句柄。"""

    def __init__(self, api, handle, info=None):
        self._api = api
        self._handle = handle
        self.info = info
        self._closed = False

    def _check(self) -> None:
        if self._closed or not self._handle:
            raise IOError("设备已关闭")

    def _addr(self, ep: Endpoint) -> int:
        return profile.endpoint_address(ep)

    def _fail(self, action: str, rc: int):
        raise IOError("%s: %s" % (action, self._api.error_name(rc)))

    def claim(self, interface: int) -> int:
        self._check()
        rc = self._api.claim_interface(self._handle, interface)
        if rc < 0:
            self._fail("claim_interface(%d)" % interface, rc)
        return rc

    def release(self, interface: int) -> int:
        self._check()
        rc = self._api.release_interface(self._handle, interface)
        if rc < 0:
            self._fail("release_interface(%d)" % interface, rc)
        return rc

    def read(self, ep: Endpoint, size: int, timeout_ms: int) -> bytes:
        self._check()
        if size <= 0:
            return b""
        addr = self._addr(ep)
        buf = (ctypes.c_uint8 * size)()
        for attempt in range(RETRIES + 1):
            rc, transferred = self._api.bulk_transfer(self._handle, addr, buf, size, timeout_ms)
            if rc == 0:
                return bytes(bytearray(buf))[:transferred]
            # 超时按「本轮无数据」返回，与既有传输层语义一致
            if rc == LIBUSB_ERROR_TIMEOUT:
                return b""
            if rc in RETRY_CODES and attempt < RETRIES:
                self._halt_recover(addr)
                continue
            self._fail("bulk read 0x%02X" % addr, rc)
        self._fail("bulk read 0x%02X: 重试耗尽" % addr, LIBUSB_ERROR_IO)

    def write(self, ep: Endpoint, data: bytes, timeout_ms: int) -> int:
        """单笔 bulk OUT：只发一次，失败不重发。

        重发会把已经落地的字节再送一遍（迟到的 URB 仍会到达设备），
        命令流/数据流会因此重复一帧而错位。OUT 的重试必须由协议层决定，
        不能藏在传输层。
        """
        self._check()
        data = bytes(data)
        addr = self._addr(ep)
        buf = (ctypes.c_uint8 * max(len(data), 1)).from_buffer_copy(data or b"\x00")
        rc, transferred = self._api.bulk_transfer(self._handle, addr, buf, len(data),
                                                  timeout_ms)
        if rc == 0:
            if transferred != len(data):
                raise IOError("bulk write 0x%02X: 短写 %d/%d B" %
                              (addr, transferred, len(data)))
            return transferred
        self._fail("bulk write 0x%02X" % addr, rc)

    def write_zlp(self, ep: Endpoint, timeout_ms: int) -> int:
        """发送零长包。失败按端口约定抛 IOError。"""
        self._check()
        addr = self._addr(ep)
        buf = (ctypes.c_uint8 * 1)()
        rc, _ = self._api.bulk_transfer(self._handle, addr, buf, 0, timeout_ms)
        if rc < 0:
            self._fail("bulk zlp 0x%02X" % addr, rc)
        return 0

    def control(self, bm: int, req: int, wv: int, wi: int, wlen: int,
                timeout_ms: int) -> int:
        self._check()
        buf = (ctypes.c_uint8 * max(wlen, 1))()
        rc = self._api.control_transfer(self._handle, bm, req, wv, wi, buf, wlen, timeout_ms)
        if rc < 0:
            self._fail("control 0x%02X/0x%02X" % (bm, req), rc)
        return rc

    def clear_halt(self, ep: Endpoint) -> None:
        self._check()
        rc = self._api.clear_halt(self._handle, self._addr(ep))
        if rc < 0:
            self._fail("clear_halt", rc)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._handle:
            self._api.close(self._handle)
            self._handle = None

    def _halt_recover(self, addr: int) -> None:
        self._api.clear_halt(self._handle, addr)
        time.sleep(RETRY_DELAY_S)
