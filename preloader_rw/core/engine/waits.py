# -*- coding: utf-8 -*-
"""等待原语。

所有等待都必须经 Waiter；引擎内不得出现裸 sleep。
Waiter 只调用内核等待原语（wait_event / sleep），等待期间由内核持续服务 IN。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from . import timing
from .inbound import Event, EventKind


class KernelWait(Protocol):
    """引擎只需要内核的这两个等待原语；不 import kernel 包。"""

    def wait_event(self, budget_ms: int) -> Event:
        ...

    def sleep(self, ms: int) -> None:
        ...


class WaitKind(str, Enum):
    """等待类型。只表达预算语义，不表达业务。"""

    FRAME = "frame"          # 等一帧（有响应预算）
    SILENCE = "silence"      # 等一个静默窗口（无帧预算）
    TRANSFER = "transfer"    # 单笔传输预算


@dataclass(frozen=True)
class Wait:
    """一次等待的完整描述。

    serve_in=True  ：等待设备事件，期间服务 IN。
    serve_in=False ：只消耗预算（内核 sleep 同样服务 IN），不取事件。
    """

    kind: WaitKind
    budget_ms: int
    serve_in: bool = True

    def __post_init__(self):
        if not isinstance(self.kind, WaitKind):
            raise ValueError("Wait.kind 必须是 WaitKind，收到 %r" % (self.kind,))
        if self.budget_ms < 0:
            raise ValueError("Wait.budget_ms 不得为负：%r" % (self.budget_ms,))

    @classmethod
    def frame(cls, budget_ms=None):
        return cls(WaitKind.FRAME, timing.DIALOG_MS if budget_ms is None else budget_ms)

    @classmethod
    def silence(cls, budget_ms=None):
        return cls(WaitKind.SILENCE, timing.SILENCE_MS if budget_ms is None else budget_ms)

    @classmethod
    def transfer(cls, budget_ms=None):
        return cls(WaitKind.TRANSFER, timing.TRANSFER_MS if budget_ms is None else budget_ms)


class Waiter(object):
    """唯一等待入口。"""

    def __init__(self, kernel):
        # type: (KernelAPI) -> None
        self._kernel = kernel

    @property
    def kernel(self):
        return self._kernel

    def wait(self, wait):
        """执行一次等待，返回一个设备事件；只消耗预算时返回 TIMEOUT。"""
        if wait.serve_in:
            return self._kernel.wait_event(wait.budget_ms)
        self._kernel.sleep(wait.budget_ms)
        return Event(EventKind.TIMEOUT)

    def wait_frame(self, budget_ms=None):
        return self.wait(Wait.frame(budget_ms))

    def wait_silence(self, budget_ms=None):
        return self.wait(Wait.silence(budget_ms))

    def sleep(self, budget_ms):
        """节拍等待；统一走内核，保持 IN 被服务。"""
        if budget_ms < 0:
            raise ValueError("sleep 预算不得为负：%r" % (budget_ms,))
        self._kernel.sleep(budget_ms)

    def tick(self):
        """一个命令循环节拍。"""
        self._kernel.sleep(timing.TICK_MS)

    @staticmethod
    def budget(wait):
        """取出等待预算，供内核的单笔传输调用使用。"""
        return wait.budget_ms
