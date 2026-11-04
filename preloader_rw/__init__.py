# -*- coding: utf-8 -*-
"""preloader_rw：MTK Preloader/DA 官方流程刷写与读取工具的主包。

适用范围：仅限 vivo 厂商的 MTK 平台设备；其他品牌或平台不在支持范围。

子包：core（协议/引擎/内核/产品装配）、relay（常驻壳）、client（GUI）、
backends（USB 传输）、payloads（工作流载荷，按路径加载）。
"""

import os as _os
import sys as _sys

if getattr(_sys, "frozen", False):
    # 打包分发：资源（profiles/ payloads/ backends/ data/）随 exe 一起解到
    # PyInstaller 的 _MEIPASS，`preloader_rw/...` 这类相对路径以它为根。
    REPO_ROOT = getattr(_sys, "_MEIPASS",
                        _os.path.dirname(_os.path.abspath(_sys.executable)))
else:
    # 仓库根（data/ scripts/ 与本包所在层）。
    REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
