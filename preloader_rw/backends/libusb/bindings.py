# -*- coding: utf-8 -*-
"""libusb 的 ctypes 绑定。

只声明工程实际用到的函数；返回值保持库原始错误码，由上层翻译成 IOError。
"""

import ctypes

from . import loader


class DeviceDescriptor(ctypes.Structure):
    """libusb_device_descriptor（18 字节）。"""

    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("bcdUSB", ctypes.c_uint16),
        ("bDeviceClass", ctypes.c_uint8),
        ("bDeviceSubClass", ctypes.c_uint8),
        ("bDeviceProtocol", ctypes.c_uint8),
        ("bMaxPacketSize0", ctypes.c_uint8),
        ("idVendor", ctypes.c_uint16),
        ("idProduct", ctypes.c_uint16),
        ("bcdDevice", ctypes.c_uint16),
        ("iManufacturer", ctypes.c_uint8),
        ("iProduct", ctypes.c_uint8),
        ("iSerialNumber", ctypes.c_uint8),
        ("bNumConfigurations", ctypes.c_uint8),
    ]


class LibUsb:
    """一个 libusb 上下文。init() 之后才能枚举设备。"""

    def __init__(self, dll_path=None):
        self.lib = loader.load(dll_path)
        self._declare()
        self.ctx = ctypes.c_void_p()
        self._initialized = False

    def _declare(self):
        sig = {
            "libusb_init": ([ctypes.POINTER(ctypes.c_void_p)], ctypes.c_int),
            "libusb_exit": ([ctypes.c_void_p], None),
            "libusb_get_device_list": (
                [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))],
                ctypes.c_ssize_t,
            ),
            "libusb_free_device_list": ([ctypes.POINTER(ctypes.c_void_p), ctypes.c_int], None),
            "libusb_get_device_descriptor": ([ctypes.c_void_p, ctypes.POINTER(DeviceDescriptor)], ctypes.c_int),
            "libusb_get_device_address": ([ctypes.c_void_p], ctypes.c_uint8),
            "libusb_open": ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_int),
            "libusb_close": ([ctypes.c_void_p], None),
            "libusb_claim_interface": ([ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
            "libusb_release_interface": ([ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
            "libusb_bulk_transfer": (
                [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_void_p, ctypes.c_int,
                 ctypes.POINTER(ctypes.c_int), ctypes.c_uint],
                ctypes.c_int,
            ),
            "libusb_control_transfer": (
                [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint16,
                 ctypes.c_uint16, ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint],
                ctypes.c_int,
            ),
            "libusb_clear_halt": ([ctypes.c_void_p, ctypes.c_uint8], ctypes.c_int),
            "libusb_error_name": ([ctypes.c_int], ctypes.c_char_p),
        }
        for name, (args, ret) in sig.items():
            fn = getattr(self.lib, name)
            fn.argtypes = args
            if ret is not None:
                fn.restype = ret

    def init(self) -> int:
        """初始化上下文；失败抛 IOError。"""
        rc = self.lib.libusb_init(ctypes.byref(self.ctx))
        if rc != 0:
            raise IOError("libusb_init: %s" % self.error_name(rc))
        self._initialized = True
        return 0

    def deinit(self) -> None:
        """释放上下文；可重复调用。"""
        if self._initialized:
            self.lib.libusb_exit(self.ctx)
            self._initialized = False
            self.ctx = ctypes.c_void_p()

    def error_name(self, code: int) -> str:
        raw = self.lib.libusb_error_name(int(code))
        if not raw:
            return "error %d" % int(code)
        return raw.decode("utf-8", "replace")

    def get_device_list(self):
        """返回 (list_ptr, [device_ptr...])；调用方负责 free_device_list。"""
        lst = ctypes.POINTER(ctypes.c_void_p)()
        n = self.lib.libusb_get_device_list(self.ctx, ctypes.byref(lst))
        if n < 0:
            raise IOError("libusb_get_device_list: %s" % self.error_name(n))
        return lst, [lst[i] for i in range(n)]

    def free_device_list(self, lst, unref: int = 1) -> None:
        if lst:
            self.lib.libusb_free_device_list(lst, int(unref))

    def get_device_descriptor(self, dev) -> DeviceDescriptor:
        desc = DeviceDescriptor()
        rc = self.lib.libusb_get_device_descriptor(dev, ctypes.byref(desc))
        if rc != 0:
            raise IOError("libusb_get_device_descriptor: %s" % self.error_name(rc))
        return desc

    def get_device_address(self, dev) -> int:
        return int(self.lib.libusb_get_device_address(dev))

    def open(self, dev):
        handle = ctypes.c_void_p()
        rc = self.lib.libusb_open(dev, ctypes.byref(handle))
        if rc != 0:
            raise IOError("libusb_open: %s" % self.error_name(rc))
        return handle

    def close(self, handle) -> None:
        if handle:
            self.lib.libusb_close(handle)

    def claim_interface(self, handle, interface: int) -> int:
        return int(self.lib.libusb_claim_interface(handle, int(interface)))

    def release_interface(self, handle, interface: int) -> int:
        return int(self.lib.libusb_release_interface(handle, int(interface)))

    def bulk_transfer(self, handle, ep_addr: int, buf, length: int, timeout_ms: int):
        """返回 (返回码, 实际传输字节数)。"""
        transferred = ctypes.c_int(0)
        rc = self.lib.libusb_bulk_transfer(
            handle, int(ep_addr), buf, int(length),
            ctypes.byref(transferred), int(timeout_ms),
        )
        return int(rc), int(transferred.value)

    def control_transfer(self, handle, bm: int, req: int, wv: int, wi: int,
                         buf, wlen: int, timeout_ms: int) -> int:
        return int(self.lib.libusb_control_transfer(
            handle, int(bm), int(req), int(wv), int(wi),
            buf, int(wlen), int(timeout_ms),
        ))

    def clear_halt(self, handle, ep_addr: int) -> int:
        return int(self.lib.libusb_clear_halt(handle, int(ep_addr)))
