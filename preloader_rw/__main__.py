# -*- coding: utf-8 -*-
"""统一入口：源码运行与打包分发共用。

    python -m preloader_rw              启动 GUI
    python -m preloader_rw --relay ...  JSON 壳（GUI 在冻结包里用这条把自己拉起）

源码运行时 GUI 用 `python -m preloader_rw.relay --json` 起子进程；冻结成 exe 后
没有 `-m`，改由本入口按 `--relay` 分派，两条路走的是同一份代码。
"""

import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--relay":
        from preloader_rw.relay.__main__ import main as relay_main
        return relay_main(argv[1:])
    from preloader_rw.client.__main__ import main as gui_main
    return gui_main(argv)


if __name__ == "__main__":
    sys.exit(main())
