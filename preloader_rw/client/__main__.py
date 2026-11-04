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
    return app_main(argv)


if __name__ == "__main__":
    _redirect_when_headless()
    sys.exit(main())
