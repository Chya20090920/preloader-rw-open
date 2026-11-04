# -*- coding: utf-8 -*-
"""设备端口。

内核只认逻辑端点；物理端点号由后端映射，内核不出现。
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol


class Endpoint(IntEnum):
    """逻辑端点。数值仅是枚举序号，不是 USB 端点号。"""

    OUT = 0
    IN = 1
    INT_IN = 2


@dataclass(frozen=True)
class DeviceInfo:
    """枚举结果。由后端填写，内核只用于展示与打开。"""

    vid: int
    pid: int
    addr: int
    product: str = ""


class Device(Protocol):
    """一个已打开的设备句柄。所有方法失败即抛 IOError，不返回哨兵值。"""

    def claim(self, interface: int) -> int:
        ...

    def release(self, interface: int) -> int:
        ...

    def read(self, ep: Endpoint, size: int, timeout_ms: int) -> bytes:
        ...

    def write(self, ep: Endpoint, data: bytes, timeout_ms: int) -> int:
        ...

    def write_zlp(self, ep: Endpoint, timeout_ms: int) -> int:
        ...

    def control(self, bm: int, req: int, wv: int, wi: int, wlen: int,
                timeout_ms: int) -> int:
        ...

    def clear_halt(self, ep: Endpoint) -> None:
        ...

    def close(self) -> None:
        ...
