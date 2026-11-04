# -*- coding: utf-8 -*-
"""libusb 后端的物理常量。

全工程只有本文件允许出现物理端点号。内核只用 Endpoint 的逻辑枚举值，
后端在此完成「逻辑端点 → 物理端点号」的映射。
"""

from preloader_rw.core.ports.device import Endpoint

# 物理端点号（USB 端点地址：bit7 为方向，0=OUT / 1=IN）
EP_OUT = 0x01
EP_IN = 0x81
EP_INT_IN = 0x83

# 默认过滤参数：联发科 preloader / EDL 与 fastboot
VID = 0x0E8D
PID = 0x2000
PID_FASTBOOT = 0x201C
DEFAULT_VID = VID
DEFAULT_PIDS = (PID,)

# 单笔 bulk 传输的默认超时（毫秒）。上限 3 s：等待即判失败，不等尸体。
BULK_TIMEOUT_MS = 3000

_ENDPOINTS = {
    Endpoint.OUT: EP_OUT,
    Endpoint.IN: EP_IN,
    Endpoint.INT_IN: EP_INT_IN,
}


def endpoint_address(ep: Endpoint) -> int:
    """逻辑端点 → 物理端点号。未知端点按失败处理。"""
    try:
        return _ENDPOINTS[Endpoint(ep)]
    except (KeyError, ValueError):
        raise IOError("未知逻辑端点: %r" % (ep,))
