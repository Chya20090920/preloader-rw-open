# -*- coding: utf-8 -*-
"""fastboot → EDL 切换。"""

from __future__ import annotations

from .cli import FastbootError


class EdlSwitchFailed(RuntimeError):
    """oem reboot-edl 未被执行或未返回 OKAY。"""

    def __init__(self, rc: int, out: str):
        self.rc = rc
        self.out = out
        super().__init__("oem reboot-edl 失败（rc=%s）：%s" % (rc, out.strip()))


def enter_edl(cli) -> tuple[int, str]:
    """执行 ``oem reboot-edl``；退出码非 0 或输出无 OKAY 即抛错。"""
    try:
        rc, out = cli.oem("reboot-edl")
    except FastbootError as exc:
        raise EdlSwitchFailed(exc.rc, exc.out) from exc
    if rc != 0 or "OKAY" not in out.upper():
        raise EdlSwitchFailed(rc, out)
    return rc, out
