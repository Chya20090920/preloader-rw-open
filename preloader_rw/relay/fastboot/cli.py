# -*- coding: utf-8 -*-
"""固定路径的 fastboot 调用封装。

只依赖标准库；运行时不在 PATH 中查找工具，只认 ``FASTBOOT_EXE``。
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

PKG = Path(__file__).resolve().parents[2]
FASTBOOT_EXE = PKG / "backends" / "fastboot" / "fastboot.exe"

DEFAULT_TIMEOUT = 3.0

# 隐藏子进程控制台窗口；非 Windows 平台不需要该标志。
CREATE_NO_WINDOW = (
    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0
)


class FastbootError(RuntimeError):
    """fastboot 以非零退出码结束。"""

    def __init__(self, rc: int, out: str):
        self.rc = rc
        self.out = out
        super().__init__("fastboot 退出码 %s：%s" % (rc, out.strip()))


class FastbootTimeout(RuntimeError):
    """fastboot 超过时限未结束，进程已被杀死。"""


def _decode(raw: bytes) -> str:
    """优先按 UTF-8 解码，失败回退 GBK。"""
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="replace")


def parse_getvar(text: str) -> dict[str, str]:
    """解析 getvar 输出，同时接受 ``(bootloader) key: value`` 与 ``key: value``。"""
    values: dict[str, str] = {}
    for line in text.splitlines():
        item = line.strip()
        if not item:
            continue
        if item.lower().startswith("finished."):
            continue
        if item.startswith("(bootloader)"):
            item = item[len("(bootloader)"):].strip()
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        values[key] = value if value else "unknown"
    return values


class FastbootCli:
    """以工程内固定路径调用 fastboot。"""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self.timeout = timeout

    def _run(self, args: Sequence[str], timeout: float | None = None) -> tuple[int, str]:
        """执行一条 fastboot 命令。

        显式指定工作目录（DLL 与可执行文件同目录）、超时、编码与隐藏窗口。
        """
        exe = FASTBOOT_EXE
        if not exe.exists():
            # 工具缺失属于调用失败，不能当成“设备不在”。
            raise FastbootError(-1, "fastboot 不存在：%s" % exe)
        limit = self.timeout if timeout is None else timeout
        proc = subprocess.Popen(
            [str(exe), *args],
            cwd=str(exe.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
        try:
            raw, _ = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise FastbootTimeout(
                "fastboot 超时 %.1fs：%s" % (limit, " ".join(args))
            ) from None
        out = _decode(raw or b"")
        if proc.returncode != 0:
            raise FastbootError(proc.returncode, out)
        return proc.returncode, out

    def present(self, timeout: float = 3.0) -> bool:
        """轻量在位判定：单键 ``getvar`` 成功即算在位。

        ``devices`` 在部分 bootloader 上返回空表（本机实测），不能作判据；
        单键读取才是可靠的在位信号。
        """
        for key in ("serialno", "product"):
            try:
                self._run(["getvar", key], timeout=timeout)
            except Exception:
                continue
            return True
        return False

    def devices(self) -> list[str]:
        """返回在线设备的序列号列表。"""
        _, out = self._run(["devices"])
        serials: list[str] = []
        for line in out.splitlines():
            item = line.strip()
            if not item or item.startswith("***"):
                continue
            serials.append(item.split()[0])
        return serials

    def getvar(self, key: str = "all") -> dict[str, str]:
        """读取变量；值为空时标 ``unknown``。"""
        _, out = self._run(["getvar", key])
        return parse_getvar(out)

    def oem(self, command: str) -> tuple[int, str]:
        """执行 oem 子命令，返回 ``(退出码, 输出)``。"""
        return self._run(["oem", command])
