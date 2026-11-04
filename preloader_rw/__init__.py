# -*- coding: utf-8 -*-
"""preloader_rw：MTK Preloader/DA 官方流程刷写与读取工具的主包。

子包：core（协议/引擎/内核/产品装配）、relay（常驻壳）、client（GUI）、
backends（USB 传输）、payloads（工作流载荷，按路径加载）。
"""

import os as _os

# 仓库根（data/ scripts/ 与本包所在层）。
REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
