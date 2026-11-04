# -*- coding: utf-8 -*-
"""载荷加载器。

两种载体：
  · Python 扩展（.py / .pyc）—— importlib 加载，取 route_main(api)
  · 动态库（.dll / .pyd / .so）—— ctypes.CDLL，取 route_main(kernel_api_t*)

C ABI 只传原始帧，XML 字段由载荷自行解析；内核不替载荷解释协议。
"""

import ctypes
import importlib.util
import os
from pathlib import Path

from .api import EventKind


class RouteLoadError(IOError):
    pass


# C ABI 的事件编号：与 api.EventKind 一一对应，载荷据此分支。
_KIND_CODE = {
    EventKind.DOWNLOAD_FILE: 0,
    EventKind.FILE_SYS_OP: 1,
    EventKind.UPLOAD_FILE: 2,
    EventKind.START: 3,
    EventKind.END: 4,
    EventKind.OK: 5,
    EventKind.LOG: 6,
    EventKind.TIMEOUT: 7,
    EventKind.DEV_GONE: 8,
}


class PythonRoute(object):
    def __init__(self, path):
        self.path = path
        self._fn = None

    def load(self):
        path = Path(self.path)
        name = "mtkpr_route_%s" % (path.stem.replace("-", "_").replace(".", "_"))
        spec = importlib.util.spec_from_file_location(name, str(path))
        if spec is None or spec.loader is None:
            raise RouteLoadError("无法加载载荷: %s" % (self.path,))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = getattr(module, "route_main", None)
        if not callable(fn):
            raise RouteLoadError("载荷缺少 route_main(api): %s" % (self.path,))
        self._fn = fn
        return self

    def run(self, api):
        if self._fn is None:
            self.load()
        return self._fn(api)


# ---- C ABI ---------------------------------------------------------
#
# 完整声明见 payloads/kernel_api_t.h（第三方写原生载荷的唯一依据）。
# 关键约定：
#   · 结构体只追加字段，旧载荷的旧布局照常可用；
#   · request_input 阻塞到宿主应答；返回 >0 = 写入 out 的字节数，
#     0 = 无宿主通道，-1 = 超时，-2 = 宿主取消，-3 = out 缓冲不足。
#     三种失败与「设备拒绝」无关，载荷必须按码分支，不得笼统处理。

_SEND = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p,
                         ctypes.c_uint32, ctypes.c_uint32)
_WAIT = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
                         ctypes.POINTER(ctypes.c_char_p),
                         ctypes.POINTER(ctypes.c_uint32))
_REPLY = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32)
_SUPPLY = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_char_p,
                           ctypes.c_uint32, ctypes.c_int32)
_RECV = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_char_p)
# 注意：出参缓冲必须是 c_void_p。若声明为 c_char_p，ctypes 会把指针提前
# 转换成 Python bytes（遇 NUL 截断），memmove 会写进 bytes 对象的内部缓冲
# 而不是调用方的缓冲——那是堆破坏，不是返回数据。
_REQUIRE = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_char_p,
                            ctypes.c_void_p, ctypes.c_uint32)
_ENTER = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
_SLEEP = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32)
_LOG = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p)
_ATTACHED = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
_SERVE_DA2 = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
# request_input：>0 写入字节数 / 0 无宿主通道 / -1 超时 / -2 取消 / -3 缓冲不足
_REQUEST_INPUT = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p,
                                  ctypes.c_char_p, ctypes.c_char_p,
                                  ctypes.c_void_p, ctypes.c_uint32)


class _KernelApiT(ctypes.Structure):
    _fields_ = [
        ("ctx", ctypes.c_void_p),
        ("send", _SEND),
        ("wait_event", _WAIT),
        ("reply", _REPLY),
        ("supply", _SUPPLY),
        ("recv_file", _RECV),
        ("require", _REQUIRE),
        ("enter_loop", _ENTER),
        ("sleep_ms", _SLEEP),
        ("log", _LOG),
        ("attached", _ATTACHED),
        ("serve_da2", _SERVE_DA2),
        ("request_input", _REQUEST_INPUT),
    ]


class CAbiRoute(object):
    """动态库载体。回调闭包持有内核 API；帧缓冲在回调内存活到下次调用。"""

    def __init__(self, path):
        self.path = path
        self._lib = None
        self._table = None
        self._keep = []
        self._raw = ctypes.c_char_p()
        self._raw_len = ctypes.c_uint32(0)

    def load(self):
        self._lib = ctypes.CDLL(os.fspath(self.path))
        fn = getattr(self._lib, "route_main", None)
        if fn is None:
            raise RouteLoadError("动态库缺少 route_main: %s" % (self.path,))
        fn.argtypes = [ctypes.POINTER(_KernelApiT)]
        fn.restype = ctypes.c_int
        self._fn = fn
        return self

    def run(self, api):
        if self._lib is None:
            self.load()

        def _as_bytes(data, length):
            return ctypes.string_at(data, length) if data else b""

        def cb_send(ctx, data, length, flag):
            api.send(_as_bytes(data, length), flag=flag)

        def cb_wait(ctx, budget_ms, raw_ptr, length_ptr):
            event = api.wait_event(budget_ms)
            self._raw.value = event.raw or b""
            self._raw_len.value = len(event.raw or b"")
            if raw_ptr:
                raw_ptr[0] = ctypes.cast(self._raw, ctypes.c_char_p)
            if length_ptr:
                length_ptr[0] = self._raw_len.value
            return _KIND_CODE[event.kind]

        def cb_reply(ctx, data, length):
            api.reply(_as_bytes(data, length))

        def cb_supply(ctx, data, length, decl_len):
            return api.supply(_as_bytes(data, length),
                              None if decl_len < 0 else decl_len)

        def cb_recv(ctx, path):
            return api.recv_file(path.decode("utf-8") if path else "")

        def cb_require(ctx, kind, out, cap):
            data = api.require(kind.decode("utf-8") if kind else "")
            take = min(len(data), int(cap))
            if out and take:
                ctypes.memmove(out, data, take)
            return take

        def cb_enter(ctx):
            api.enter_loop()

        def cb_sleep(ctx, ms):
            api.sleep(int(ms))

        def cb_log(ctx, level, text):
            api.log(level.decode("ascii", "ignore") if level else "info",
                    text.decode("utf-8", "ignore") if text else "")

        def cb_attached(ctx):
            return 1 if api.attached else 0

        def cb_serve_da2(ctx):
            api.serve_da2()

        def cb_request_input(ctx, kind, info, out, cap):
            from .api import (HostInputCancelled, HostInputTimeout,
                              HostInputUnavailable)
            try:
                data = api.request_input(
                    kind.decode("utf-8") if kind else "",
                    info.decode("utf-8") if info else "")
            except HostInputUnavailable:
                return 0
            except HostInputTimeout:
                return -1
            except HostInputCancelled:
                return -2
            if len(data) > int(cap):
                return -3
            if out and data:
                ctypes.memmove(out, data, len(data))
            return len(data)

        # 回调对象必须在调用期间存活
        self._keep = [_SEND(cb_send), _WAIT(cb_wait), _REPLY(cb_reply),
                      _SUPPLY(cb_supply), _RECV(cb_recv), _REQUIRE(cb_require),
                      _ENTER(cb_enter), _SLEEP(cb_sleep), _LOG(cb_log),
                      _ATTACHED(cb_attached), _SERVE_DA2(cb_serve_da2),
                      _REQUEST_INPUT(cb_request_input)]
        self._table = _KernelApiT(None, *self._keep)
        return self._fn(ctypes.byref(self._table))


def load(path):
    suffix = Path(path).suffix.lower()
    if suffix in (".dll", ".pyd", ".so", ".dylib"):
        return CAbiRoute(path)
    return PythonRoute(path)


def route_of(path):
    return load(path)
