# -*- coding: utf-8 -*-
"""内核包：协议原语 + 注册器 + 命令循环。

依赖方向（单向）：
    apps → product → engine → proto → ports
    backends → ports（只实现，不反向 import）
"""

__all__ = ["ports", "proto", "engine", "kernel"]
