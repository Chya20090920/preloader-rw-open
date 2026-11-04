# -*- coding: utf-8 -*-
"""fastboot 设备轮询。"""

from __future__ import annotations

import time

from .cli import FastbootCli

DEFAULT_INTERVAL = 0.5


class FastbootWatcher:
    """按固定间隔轮询 devices()，判断目标设备是否出现。"""

    def __init__(
        self,
        cli: FastbootCli | None = None,
        serial: str | None = None,
        interval: float = DEFAULT_INTERVAL,
    ):
        self.cli = cli if cli is not None else FastbootCli()
        self.serial = serial
        self.interval = interval

    def poll(self) -> bool:
        """轮询一次；``serial`` 为空时任意设备即命中。

        ``devices`` 在部分 bootloader 上恒为空表，故空表时以单键 getvar 兜底
        （仅在未指定 serial 时可用）。
        """
        try:
            devices = self.cli.devices()
        except Exception:
            devices = []
        if devices:
            return True if self.serial is None else self.serial in devices
        if self.serial is not None:
            return False
        return self.cli.present(timeout=max(1.0, min(self.interval * 4, 3.0)))

    def wait(self, timeout_s: float) -> bool:
        """在时限内轮询直到命中；命中返回 True，超时返回 False。"""
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            if self.poll():
                return True
            remain = deadline - time.monotonic()
            if remain <= 0:
                return False
            time.sleep(min(self.interval, remain))
