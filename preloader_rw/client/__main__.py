# -*- coding: utf-8 -*-
"""GUI 入口：`python -m preloader_rw.client`。

pythonw（无控制台）运行时，把 stdout/stderr 与未处理异常落到工程根 `client.log`，
否则崩了看不见任何东西。
"""

import os
import sys
import time
import traceback

from preloader_rw import REPO_ROOT

ROOT = REPO_ROOT
LOG = os.path.join(ROOT, "client.log")


def _hide_console_window():
    """打包版是控制台子系统（壳子进程要有真 stdio），GUI 起来后把这个窗口收掉。

    子进程用 CREATE_NO_WINDOW 启动，不受影响；源码运行没有控制台可收，直接跳过。
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def _redirect_when_headless():
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        handle = open(LOG, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stdout = handle
    sys.stderr = handle
    handle.write("\n==== client start %s ====\n"
                 % time.strftime("%Y-%m-%d %H:%M:%S"))
    sys.excepthook = lambda kind, value, tb: traceback.print_exception(
        kind, value, tb, file=handle)


def main(argv=None):
    from .app import main as app_main
    _hide_console_window()
    return app_main(argv)


if __name__ == "__main__":
    _redirect_when_headless()
    sys.exit(main())
