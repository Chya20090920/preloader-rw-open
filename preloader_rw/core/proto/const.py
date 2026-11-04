# -*- coding: utf-8 -*-
"""协议层常量。

帧头固定 12 字节：同步字(4) + flag(4，小端) + 载荷长度(4，小端)。
DA1 上传使用的传输头是大端，属另一套形态，见 da.py。
"""

FRAME_SYNC = b"\xEF\xEE\xEE\xFE"

FLAG_CMD = 1
FLAG_LOG = 2

OK = b"OK\x00"
OK0 = b"OK@0x0\x00"
EXISTS_NOT = b"OK@NOT-EXISTS\x00"

# 单帧载荷上限。超过即视为帧流失步，不做分配、不产出帧。
MAX_PAYLOAD = 64 * 1024 * 1024

__all__ = [
    "FRAME_SYNC",
    "FLAG_CMD",
    "FLAG_LOG",
    "OK",
    "OK0",
    "EXISTS_NOT",
    "MAX_PAYLOAD",
]
