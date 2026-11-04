# -*- coding: utf-8 -*-
"""Windows 原生 USB 绑定：setupapi.dll / winusb.dll / kernel32.dll。

只声明工程实际用到的函数；返回值保持系统原始错误码，由上层翻译成 IOError。
本文件不做任何设备打开动作，只提供能力。
"""

import ctypes
from ctypes import wintypes

# ---------------------------------------------------------------- 常量

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

SPDRP_DEVICEDESC = 0
SPDRP_HARDWAREID = 1
SPDRP_FRIENDLYNAME = 0x0C
SPDRP_ADDRESS = 0x1C

REG_SZ = 1
REG_EXPAND_SZ = 2
REG_MULTI_SZ = 7

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80

ERROR_INVALID_HANDLE = 6
ERROR_GEN_FAILURE = 31
ERROR_NOT_SUPPORTED = 50
ERROR_SEM_TIMEOUT = 121
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NO_MORE_ITEMS = 259
ERROR_NO_SUCH_DEVICE = 433
ERROR_DEVICE_NOT_CONNECTED = 1167

PIPE_TRANSFER_TIMEOUT = 0x03

FORMAT_MESSAGE_FROM_SYSTEM = 0x00001000
FORMAT_MESSAGE_IGNORE_INSERTS = 0x00000200

# 设备消失类错误：句柄已失效或设备已从总线移除
_GONE_ERRORS = frozenset((
    ERROR_INVALID_HANDLE,
    ERROR_GEN_FAILURE,
    ERROR_NO_SUCH_DEVICE,
    ERROR_DEVICE_NOT_CONNECTED,
))
_TIMEOUT_ERRORS = frozenset((ERROR_SEM_TIMEOUT,))


class GUID(ctypes.Structure):
    """Windows GUID。"""

    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# GUID_DEVINTERFACE_USB_DEVICE = {A5DCBF10-6530-11D2-901F-00C04FB951ED}
GUID_DEVINTERFACE_USB_DEVICE = GUID(
    0xA5DCBF10, 0x6530, 0x11D2,
    (ctypes.c_ubyte * 8)(0x90, 0x1F, 0x00, 0xC0, 0x4F, 0xB9, 0x51, 0xED),
)


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    """SetupAPI 设备接口数据。"""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8
         else ctypes.c_ulong),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    """SetupAPI 设备节点数据。"""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8
         else ctypes.c_ulong),
    ]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    """设备接口详细数据（变长结构，DevicePath 后接路径正文）。"""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("DevicePath", ctypes.c_wchar * 1),
    ]


# 该结构的 cbSize 必须按平台取固定值，不能直接用 ctypes 的结构体大小
_DETAIL_CB_SIZE = ctypes.sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W)
if ctypes.sizeof(ctypes.c_void_p) == 4:
    _DETAIL_CB_SIZE = 6


class WINUSB_SETUP_PACKET(ctypes.Structure):
    """WinUSB 控制传输的建立包。"""

    _pack_ = 1
    _fields_ = [
        ("RequestType", ctypes.c_ubyte),
        ("Request", ctypes.c_ubyte),
        ("Value", ctypes.c_ushort),
        ("Index", ctypes.c_ushort),
        ("Length", ctypes.c_ushort),
    ]


class USB_INTERFACE_DESCRIPTOR(ctypes.Structure):
    """USB 接口描述符（9 字节）。"""

    _pack_ = 1
    _fields_ = [
        ("bLength", ctypes.c_ubyte),
        ("bDescriptorType", ctypes.c_ubyte),
        ("bInterfaceNumber", ctypes.c_ubyte),
        ("bAlternateSetting", ctypes.c_ubyte),
        ("bNumEndpoints", ctypes.c_ubyte),
        ("bInterfaceClass", ctypes.c_ubyte),
        ("bInterfaceSubClass", ctypes.c_ubyte),
        ("bInterfaceProtocol", ctypes.c_ubyte),
        ("iInterface", ctypes.c_ubyte),
    ]


class WINUSB_PIPE_INFORMATION(ctypes.Structure):
    """WinUSB 管道信息。"""

    _fields_ = [
        ("PipeType", ctypes.c_int),
        ("PipeId", ctypes.c_ubyte),
        ("MaximumPacketSize", ctypes.c_ushort),
        ("Interval", ctypes.c_ubyte),
    ]


# ---------------------------------------------------------------- 错误码工具

_KERNEL32 = None


def _kernel32():
    global _KERNEL32
    if _KERNEL32 is None:
        lib = ctypes.WinDLL("kernel32", use_last_error=True)
        lib.FormatMessageW.argtypes = [
            wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p,
        ]
        lib.FormatMessageW.restype = wintypes.DWORD
        _KERNEL32 = lib
    return _KERNEL32


def error_message(code: int) -> str:
    """系统错误码 → 可读文本。"""
    code = int(code) & 0xFFFFFFFF
    if code == 0:
        return "成功"
    buf = ctypes.create_unicode_buffer(1024)
    n = _kernel32().FormatMessageW(
        FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        None, code, 0, buf, len(buf), None,
    )
    if n:
        return buf.value.strip()
    return "未知错误"


def is_device_gone(code: int) -> bool:
    """错误码是否表示设备已消失。"""
    return int(code) in _GONE_ERRORS


def is_timeout(code: int) -> bool:
    """错误码是否表示传输超时。"""
    return int(code) in _TIMEOUT_ERRORS


# ---------------------------------------------------------------- SetupAPI


class SetupApi:
    """setupapi.dll 的只读枚举能力。"""

    def __init__(self):
        self.lib = ctypes.WinDLL("setupapi", use_last_error=True)
        self._declare()

    def _declare(self) -> None:
        lib = self.lib
        lib.SetupDiGetClassDevsW.argtypes = [
            ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD,
        ]
        lib.SetupDiGetClassDevsW.restype = wintypes.HANDLE

        lib.SetupDiEnumDeviceInterfaces.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA),
            ctypes.POINTER(GUID), wintypes.DWORD,
            ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ]
        lib.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL

        lib.SetupDiGetDeviceInterfaceDetailW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
            ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA_W), wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(SP_DEVINFO_DATA),
        ]
        lib.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL

        lib.SetupDiGetDeviceRegistryPropertyW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        lib.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL

        lib.SetupDiGetDeviceInstanceIdW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.LPWSTR,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        lib.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL

        lib.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
        lib.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

    def create_device_info_list(self, guid: GUID):
        """按接口 GUID 取设备信息集；失败抛 IOError。"""
        handle = self.lib.SetupDiGetClassDevsW(
            ctypes.byref(guid), None, None,
            DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
        )
        if not handle or handle == INVALID_HANDLE_VALUE:
            raise IOError("SetupDiGetClassDevsW: %s" %
                          error_message(ctypes.get_last_error()))
        return handle

    def enum_interface(self, hdev, index: int, guid: GUID):
        """枚举第 index 个接口。返回 (成功, 接口数据, 设备节点, 错误码)。"""
        iface = SP_DEVICE_INTERFACE_DATA()
        iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
        devinfo = SP_DEVINFO_DATA()
        devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
        ok = self.lib.SetupDiEnumDeviceInterfaces(
            hdev, ctypes.byref(devinfo), ctypes.byref(guid), int(index),
            ctypes.byref(iface),
        )
        err = 0 if ok else ctypes.get_last_error()
        return bool(ok), iface, devinfo, err

    def interface_detail(self, hdev, iface, devinfo):
        """取接口路径；失败返回 None。"""
        required = wintypes.DWORD(0)
        self.lib.SetupDiGetDeviceInterfaceDetailW(
            hdev, ctypes.byref(iface), None, 0,
            ctypes.byref(required), ctypes.byref(devinfo),
        )
        if not required.value:
            return None
        raw = ctypes.create_string_buffer(required.value)
        detail = ctypes.cast(
            raw, ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA_W))
        detail.contents.cbSize = _DETAIL_CB_SIZE
        ok = self.lib.SetupDiGetDeviceInterfaceDetailW(
            hdev, ctypes.byref(iface), detail, required.value,
            ctypes.byref(required), ctypes.byref(devinfo),
        )
        if not ok:
            return None
        return detail.contents.DevicePath

    def get_property(self, hdev, devinfo, prop: int):
        """读设备属性。返回 (数据类型, 原始字节)；失败返回 None。"""
        data_type = wintypes.DWORD(0)
        required = wintypes.DWORD(0)
        self.lib.SetupDiGetDeviceRegistryPropertyW(
            hdev, ctypes.byref(devinfo), int(prop), ctypes.byref(data_type),
            None, 0, ctypes.byref(required),
        )
        if not required.value:
            return None
        buf = ctypes.create_string_buffer(required.value)
        ok = self.lib.SetupDiGetDeviceRegistryPropertyW(
            hdev, ctypes.byref(devinfo), int(prop), ctypes.byref(data_type),
            buf, required.value, ctypes.byref(required),
        )
        if not ok:
            return None
        return int(data_type.value), buf.raw[:required.value]

    def get_device_instance_id(self, hdev, devinfo):
        """取设备实例 ID；失败返回空串。"""
        size = wintypes.DWORD(0)
        self.lib.SetupDiGetDeviceInstanceIdW(
            hdev, ctypes.byref(devinfo), None, 0, ctypes.byref(size))
        if not size.value:
            return ""
        buf = ctypes.create_unicode_buffer(size.value)
        ok = self.lib.SetupDiGetDeviceInstanceIdW(
            hdev, ctypes.byref(devinfo), buf, size.value, ctypes.byref(size))
        return buf.value if ok else ""

    def destroy(self, hdev) -> None:
        if hdev and hdev != INVALID_HANDLE_VALUE:
            self.lib.SetupDiDestroyDeviceInfoList(hdev)


# ---------------------------------------------------------------- WinUSB


class WinUsbApi:
    """winusb.dll 与 kernel32.dll 的设备打开 / 传输能力。"""

    def __init__(self):
        self.lib = ctypes.WinDLL("winusb", use_last_error=True)
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._declare()

    def _declare(self) -> None:
        lib = self.lib
        k32 = self.k32

        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.DeviceIoControl.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        k32.DeviceIoControl.restype = wintypes.BOOL

        lib.WinUsb_Initialize.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE),
        ]
        lib.WinUsb_Initialize.restype = wintypes.BOOL
        lib.WinUsb_Free.argtypes = [wintypes.HANDLE]
        lib.WinUsb_Free.restype = wintypes.BOOL
        lib.WinUsb_QueryInterfaceSettings.argtypes = [
            wintypes.HANDLE, ctypes.c_ubyte,
            ctypes.POINTER(USB_INTERFACE_DESCRIPTOR),
        ]
        lib.WinUsb_QueryInterfaceSettings.restype = wintypes.BOOL
        lib.WinUsb_QueryPipe.argtypes = [
            wintypes.HANDLE, ctypes.c_ubyte, ctypes.c_ubyte,
            ctypes.POINTER(WINUSB_PIPE_INFORMATION),
        ]
        lib.WinUsb_QueryPipe.restype = wintypes.BOOL
        lib.WinUsb_ReadPipe.argtypes = [
            wintypes.HANDLE, ctypes.c_ubyte, ctypes.c_void_p, wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p,
        ]
        lib.WinUsb_ReadPipe.restype = wintypes.BOOL
        lib.WinUsb_WritePipe.argtypes = [
            wintypes.HANDLE, ctypes.c_ubyte, ctypes.c_void_p, wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p,
        ]
        lib.WinUsb_WritePipe.restype = wintypes.BOOL
        lib.WinUsb_ControlTransfer.argtypes = [
            wintypes.HANDLE, WINUSB_SETUP_PACKET, ctypes.c_void_p,
            wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p,
        ]
        lib.WinUsb_ControlTransfer.restype = wintypes.BOOL
        lib.WinUsb_ResetPipe.argtypes = [wintypes.HANDLE, ctypes.c_ubyte]
        lib.WinUsb_ResetPipe.restype = wintypes.BOOL
        lib.WinUsb_AbortPipe.argtypes = [wintypes.HANDLE, ctypes.c_ubyte]
        lib.WinUsb_AbortPipe.restype = wintypes.BOOL
        lib.WinUsb_FlushPipe.argtypes = [wintypes.HANDLE, ctypes.c_ubyte]
        lib.WinUsb_FlushPipe.restype = wintypes.BOOL
        lib.WinUsb_SetPipePolicy.argtypes = [
            wintypes.HANDLE, ctypes.c_ubyte, wintypes.ULONG, wintypes.ULONG,
            ctypes.c_void_p,
        ]
        lib.WinUsb_SetPipePolicy.restype = wintypes.BOOL

    # -------------------------------------------------------- 句柄

    def open_device(self, path: str):
        """按接口路径打开设备句柄；失败抛 IOError。"""
        handle = self.k32.CreateFileW(
            path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
        )
        if not handle or handle == INVALID_HANDLE_VALUE:
            raise IOError("CreateFileW(%s): %s" %
                          (path, error_message(ctypes.get_last_error())))
        return handle

    def close_handle(self, handle) -> None:
        if handle and handle != INVALID_HANDLE_VALUE:
            self.k32.CloseHandle(handle)

    def winusb_initialize(self, handle):
        """WinUsb_Initialize；失败抛 IOError。"""
        usb = wintypes.HANDLE()
        if not self.lib.WinUsb_Initialize(handle, ctypes.byref(usb)):
            raise IOError("WinUsb_Initialize: %s" %
                          error_message(ctypes.get_last_error()))
        return usb

    def winusb_free(self, usb_handle) -> None:
        if usb_handle:
            self.lib.WinUsb_Free(usb_handle)

    # -------------------------------------------------------- 查询

    def query_interface_settings(self, usb_handle, alternate: int = 0):
        desc = USB_INTERFACE_DESCRIPTOR()
        ok = self.lib.WinUsb_QueryInterfaceSettings(
            usb_handle, int(alternate), ctypes.byref(desc))
        if not ok:
            raise IOError("WinUsb_QueryInterfaceSettings: %s" %
                          error_message(ctypes.get_last_error()))
        return desc

    def query_pipe(self, usb_handle, alternate: int, index: int):
        info = WINUSB_PIPE_INFORMATION()
        ok = self.lib.WinUsb_QueryPipe(
            usb_handle, int(alternate), int(index), ctypes.byref(info))
        if not ok:
            raise IOError("WinUsb_QueryPipe: %s" %
                          error_message(ctypes.get_last_error()))
        return info

    # -------------------------------------------------------- 传输

    def _set_timeout(self, usb_handle, pipe_id: int, timeout_ms: int) -> int:
        """设置管道传输超时；返回 0 表示成功。"""
        if timeout_ms is None or int(timeout_ms) < 0:
            return 0
        value = wintypes.ULONG(int(timeout_ms))
        ok = self.lib.WinUsb_SetPipePolicy(
            usb_handle, int(pipe_id), PIPE_TRANSFER_TIMEOUT,
            ctypes.sizeof(value), ctypes.byref(value),
        )
        return 0 if ok else ctypes.get_last_error()

    def read_pipe(self, usb_handle, pipe_id: int, size: int, timeout_ms: int):
        """读管道。返回 (成功, 实收字节, 错误码, 数据)。"""
        err = self._set_timeout(usb_handle, pipe_id, timeout_ms)
        if err:
            return False, 0, err, b""
        size = int(size)
        buf = ctypes.create_string_buffer(max(size, 1))
        transferred = wintypes.ULONG(0)
        ok = self.lib.WinUsb_ReadPipe(
            usb_handle, int(pipe_id), buf, size,
            ctypes.byref(transferred), None,
        )
        err = 0 if ok else ctypes.get_last_error()
        n = int(transferred.value)
        return bool(ok), n, err, buf.raw[:n]

    def write_pipe(self, usb_handle, pipe_id: int, data: bytes,
                   timeout_ms: int):
        """写管道。返回 (成功, 实发字节, 错误码)。"""
        err = self._set_timeout(usb_handle, pipe_id, timeout_ms)
        if err:
            return False, 0, err
        raw = bytes(data)
        buf = ctypes.create_string_buffer(raw, max(len(raw), 1))
        transferred = wintypes.ULONG(0)
        ok = self.lib.WinUsb_WritePipe(
            usb_handle, int(pipe_id), buf, len(raw),
            ctypes.byref(transferred), None,
        )
        err = 0 if ok else ctypes.get_last_error()
        return bool(ok), int(transferred.value), err

    def control_transfer(self, usb_handle, bm: int, req: int, wv: int,
                         wi: int, wlen: int, timeout_ms: int):
        """控制传输。返回 (成功, 实收字节, 错误码, 输出数据)。"""
        packet = WINUSB_SETUP_PACKET(
            int(bm) & 0xFF, int(req) & 0xFF, int(wv) & 0xFFFF,
            int(wi) & 0xFFFF, int(wlen) & 0xFFFF,
        )
        wlen = int(wlen)
        buf = ctypes.create_string_buffer(max(wlen, 1))
        transferred = wintypes.ULONG(0)
        ok = self.lib.WinUsb_ControlTransfer(
            usb_handle, packet, buf, wlen,
            ctypes.byref(transferred), None,
        )
        err = 0 if ok else ctypes.get_last_error()
        n = int(transferred.value)
        return bool(ok), n, err, buf.raw[:n]

    def reset_pipe(self, usb_handle, pipe_id: int) -> int:
        ok = self.lib.WinUsb_ResetPipe(usb_handle, int(pipe_id))
        return 0 if ok else ctypes.get_last_error()

    def abort_pipe(self, usb_handle, pipe_id: int) -> int:
        ok = self.lib.WinUsb_AbortPipe(usb_handle, int(pipe_id))
        return 0 if ok else ctypes.get_last_error()

    def flush_pipe(self, usb_handle, pipe_id: int) -> int:
        ok = self.lib.WinUsb_FlushPipe(usb_handle, int(pipe_id))
        return 0 if ok else ctypes.get_last_error()

    # -------------------------------------------------------- 通用 IOCTL

    def device_io_control(self, handle, code: int, in_bytes: bytes = b"",
                          out_size: int = 0):
        """DeviceIoControl。返回 (成功, 输出字节, 错误码)。"""
        raw = bytes(in_bytes)
        inbuf = ctypes.create_string_buffer(raw, max(len(raw), 1))
        outbuf = ctypes.create_string_buffer(max(int(out_size), 1))
        returned = wintypes.DWORD(0)
        ok = self.k32.DeviceIoControl(
            handle, int(code), inbuf, len(raw), outbuf, int(out_size),
            ctypes.byref(returned), None,
        )
        err = 0 if ok else ctypes.get_last_error()
        return bool(ok), outbuf.raw[:returned.value], err

    def error_message(self, code: int) -> str:
        return error_message(code)
