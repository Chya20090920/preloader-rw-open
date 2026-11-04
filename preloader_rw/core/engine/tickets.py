# -*- coding: utf-8 -*-
"""命令票据：一次下行命令的完成回执与状态返回。

`submit` 返回票据；循环在命令结束（成功/失败）或会话死掉时结算票据。
调用方由此拿到三样东西，不必轮询内部状态：

  · 裁决   —— ok / 失败分类（Failure）
  · 状态   —— 命令结束时的租约状态（正常回 GATED；会话死掉回 DEAD）
  · 轮数   —— 该命令实际经历的循环轮数

不设时间预算：会话死掉也必须结算票据，否则调用方会永远等下去。
"""

import threading
from dataclasses import dataclass

from .events import SessionState
from .failure import Failure


@dataclass(frozen=True)
class CommandResult:
    """一条命令的终局。"""

    name: str
    ok: bool
    state: SessionState
    failure: "Failure | None" = None
    rounds: int = 0
    detail: str = ""


class CommandTicket(object):
    """票据 = 完成事件 + 终局。线程安全：循环线程结算，壳线程等待。"""

    def __init__(self, name):
        self.name = name
        self._done = threading.Event()
        self._result = None

    @property
    def done(self):
        return self._done.is_set()

    @property
    def result(self):
        return self._result

    def wait(self):
        """阻塞到本条命令结算；返回 CommandResult。"""
        self._done.wait()
        return self._result

    def resolve(self, result):
        """结算；重复结算忽略，保证只有一次终局。"""
        if self._done.is_set():
            return False
        self._result = result
        self._done.set()
        return True


__all__ = ["CommandResult", "CommandTicket"]
