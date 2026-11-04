# -*- coding: utf-8 -*-
"""挑一个能 import PySide6 的解释器，把它的路径打到 stdout；挑不到退出码 1。

仅供启动器调用，不参与工程运行时。
"""

import os
import subprocess
import sys

CANDIDATES = []


def _add(path):
    if path and path not in CANDIDATES and os.path.isfile(path):
        CANDIDATES.append(path)


_add(sys.executable)
_add(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python",
                  "Python313", "python.exe"))
for root in ("C:\\", os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
    if not root:
        continue
    for rel in ("Python313", "Python312", "Python311", "Python310",
                r"Programs\Python\Python313", r"Programs\Python\Python312"):
        _add(os.path.join(root, rel, "python.exe"))
# PATH 上的 python
for name in ("python.exe", "python3.exe"):
    for part in os.environ.get("PATH", "").split(os.pathsep):
        _add(os.path.join(part, name))


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def has_pyside(path):
    try:
        rc = subprocess.call([path, "-c", "import PySide6"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=NO_WINDOW, timeout=30)
    except Exception:
        return False
    return rc == 0


for candidate in CANDIDATES:
    if has_pyside(candidate):
        sys.stdout.write(candidate)
        sys.exit(0)
sys.exit(1)
