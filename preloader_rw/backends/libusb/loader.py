# -*- coding: utf-8 -*-
"""libusb DLL 路径解析与显式注入。

默认取与本文件同目录的 libusb-1.0.dll；构造后端时可传入其他路径覆盖。
显式路径注入避免依赖系统 PATH 中的同名 DLL。
"""

import ctypes
import os

DEFAULT_DLL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "libusb-1.0.dll")


def default_path() -> str:
    """工程默认 DLL 路径。"""
    return DEFAULT_DLL_PATH


def resolve_path(path=None) -> str:
    """返回实际使用的 DLL 绝对路径；path 为空时用默认值。"""
    return os.path.abspath(path or DEFAULT_DLL_PATH)


def load(path=None):
    """按路径加载 libusb DLL。加载失败抛 IOError。"""
    p = resolve_path(path)
    if not os.path.isfile(p):
        raise IOError("libusb DLL 不存在: %s" % p)
    try:
        return ctypes.CDLL(p)
    except OSError as exc:
        raise IOError("libusb DLL 加载失败: %s (%s)" % (p, exc))
