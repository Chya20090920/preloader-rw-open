# -*- coding: utf-8 -*-
"""命令协议：规格、阶段、实例。

本文件只定义结构与约束，不含任何具体命令实现。
命令名、分区名、地址只能由上层（product）在注册时以数据形式给出。
"""

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .inbound import Event
from . import timing
from .events import SessionState
from .failure import Failure
from .waits import Wait, WaitKind


@dataclass(frozen=True)
class Stage:
    """命令的一个阶段。

    timeout_profile 为空时沿用 CommandSpec.timeout_profile。
    """

    name: str
    kind: WaitKind = WaitKind.FRAME
    timeout_profile: str = ""

    def __post_init__(self):
        if not self.name:
            raise ValueError("Stage.name 不得为空")
        if not isinstance(self.kind, WaitKind):
            raise ValueError("Stage.kind 必须是 WaitKind，收到 %r" % (self.kind,))
        if self.timeout_profile and self.timeout_profile not in timing.TIMEOUT_PROFILES:
            raise ValueError("未知超时档: %r" % (self.timeout_profile,))


@dataclass(frozen=True)
class CommandSpec:
    """一个命令的静态规格。

    build(args) 由上层提供，返回 CommandInstance；本层不认识命令语义。
    requires 是允许执行该命令的会话状态集合。
    """

    name: str
    build: Callable[[Any], "CommandInstance"]
    stages: "tuple[Stage, ...]"
    requires: "frozenset[SessionState]" = frozenset((SessionState.GATED,))
    idempotent: bool = False
    timeout_profile: str = timing.DEFAULT_TIMEOUT_PROFILE

    def __post_init__(self):
        if not self.name:
            raise ValueError("CommandSpec.name 不得为空")
        if not callable(self.build):
            raise ValueError("CommandSpec.build 必须可调用")
        stages = tuple(self.stages)
        if not stages:
            raise ValueError("CommandSpec.stages 不得为空")
        for stage in stages:
            if not isinstance(stage, Stage):
                raise ValueError("stages 只能包含 Stage，收到 %r" % (stage,))
        requires = frozenset(self.requires)
        if not requires:
            raise ValueError("CommandSpec.requires 不得为空")
        for state in requires:
            if not isinstance(state, SessionState):
                raise ValueError("requires 只能包含 SessionState，收到 %r" % (state,))
        if self.timeout_profile not in timing.TIMEOUT_PROFILES:
            raise ValueError("未知超时档: %r" % (self.timeout_profile,))
        object.__setattr__(self, "stages", stages)
        object.__setattr__(self, "requires", requires)

    def wait_for(self, index):
        """按阶段下标生成等待描述；预算来自阶段档或规格档。"""
        stage = self.stages[index]
        profile = stage.timeout_profile or self.timeout_profile
        return Wait(stage.kind, timing.profile_ms(profile), serve_in=True)


class CommandInstance(Protocol):
    """命令的一次执行实例。循环只认这五个成员。"""

    @property
    def spec(self):
        # type: () -> CommandSpec
        ...

    @property
    def stage(self):
        # type: () -> Stage
        ...

    @property
    def done(self):
        # type: () -> bool
        ...

    @property
    def failure(self):
        # type: () -> Failure | None
        ...

    def next_wait(self):
        # type: () -> Wait | None
        """下一次等待；返回 None 表示用循环默认预算。"""
        ...

    def advance(self, event):
        # type: (Event) -> None
        """消化一个设备事件，推进阶段。"""
        ...
