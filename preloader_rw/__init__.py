# -*- coding: utf-8 -*-
"""preloader_rw：MTK Preloader/DA 官方流程刷写与读取工具的主包。

实测范围：只在 vivo 的 MTK 平台设备上验证过；其他厂商或平台的架构可能不同，
未做验证。

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
