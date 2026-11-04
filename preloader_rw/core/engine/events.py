# -*- coding: utf-8 -*-
"""上行事件：引擎 → 壳。所有锁定状态都是这些事件的派生值。"""

from dataclasses import dataclass
from enum import Enum
from typing import Union


class SessionState(str, Enum):
    """会话状态。GATED 是唯一的「可发命令」态；STALE 明确不是就绪态。

    STALE = 设备状态未知：
      · 命令回合已收到申报长度的全部数据但缺 `CMD:END`；
      · 或回合在没有明确结果（空 result / 无 END）的情况下结束。
    语义与约束：
      · 租约拒绝一切新命令，理由 `LeaseError.reason == "device_unknown"`；
      · 不得被任何 `state is GATED` 一类判断当成就绪（GATED 与 STALE 是
        两个不同的枚举值，任何等值判断都不会命中 STALE）；
      · 只有外壳显式发起一次指定探针（GET-DA-INFO / GET-HW-INFO）并收到
        `CMD:END result=OK`，才允许经 `SessionLease.confirm_alive()` 解除。
    """

    ABSENT = "absent"
    PRESENT = "present"
    CLAIMED = "claimed"
    BOOTSTRAPPING = "bootstrapping"
    GATED = "gated"          # 命令循环就绪：认证一次，可反复发命令
    BUSY = "busy"            # 正在执行一条命令
    STALE = "stale"          # 设备状态未知：数据完整但缺 CMD:END / 回合无明确结果
    DEAD = "dead"


@dataclass(frozen=True)
class DeviceChanged:
    present: bool
    vid: int = 0
    pid: int = 0
    addr: int = 0


@dataclass(frozen=True)
class SessionStateChanged:
    state: SessionState
    reason: str = ""


@dataclass(frozen=True)
class Phase:
    stage: str
    index: int
    total: int


@dataclass(frozen=True)
class Progress:
    done: int
    total: int
    bytes_per_s: float = 0.0


@dataclass(frozen=True)
class Log:
    level: str
    source: str
    text: str
    raw: bytes = b""


@dataclass(frozen=True)
class Verdict:
    ok: bool
    run_dir: str
    message: str


Event = Union[DeviceChanged, SessionStateChanged, Phase, Progress, Log, Verdict]
