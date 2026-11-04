# -*- coding: utf-8 -*-
"""官方整机路线：设备身份、分区表、命令清单、命令注册与写/读编排。

本子包不含任何 USB 端点、帧头或原生绑定；协议字节全部经 preloader_rw.core.proto。
"""

__all__ = [
    "partitions",
    "commands",
    "write",
    "read",
]
