# -*- coding: utf-8 -*-
"""产品层：把用户意图编译为可执行的路线数据。

依赖方向（单向）：
    apps → product → engine → proto → ports

本包只允许 import preloader_rw.core.engine / preloader_rw.core.kernel / preloader_rw.core.proto / preloader_rw.core.ports
与标准库；不得 import apps、backends。
"""

from .registry import build_registry

__all__ = ["build_registry"]
