# -*- coding: utf-8 -*-
"""协议层：帧、通道、命令 XML、供数、GPT 运算、DA 上传、preloader 引导。

依赖方向：proto → ports。本层不 import engine / product / apps / backends。
"""

from . import bootstrap, const, da, frame, supply, tables, xml
from .channel import Channel

__all__ = [
    "Channel",
    "bootstrap",
    "const",
    "da",
    "frame",
    "supply",
    "tables",
    "xml",
]
