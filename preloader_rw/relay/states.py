# -*- coding: utf-8 -*-
"""中继壳状态与转移。非法转移立即抛错。"""

from enum import Enum


class RelayState(str, Enum):
    BOOT = "boot"
    LISTENING = "listening"
    FB_PROBING = "fb_probing"
    FB_READY = "fb_ready"
    KERNEL_WAIT = "kernel_wait"
    BOOTSTRAPPING = "bootstrapping"
    GATED = "gated"
    BUSY = "busy"
    # 设备状态未知（内核 SessionState.STALE 的壳侧镜像）：
    # 命令回合没有明确结果（数据完整但缺 CMD:END / END 无 result）。
    # **不是就绪态**：一切读写意图都被 submit() 拒掉，只有显式探针
    # （RelayService.recover）确认 OK 才回到 GATED。
    UNKNOWN = "unknown"
    DEAD = "dead"
    FAILED = "failed"


ALLOWED = {
    RelayState.BOOT: {RelayState.LISTENING, RelayState.FAILED},
    RelayState.LISTENING: {RelayState.FB_PROBING, RelayState.KERNEL_WAIT, RelayState.FAILED},
    RelayState.FB_PROBING: {RelayState.FB_READY, RelayState.KERNEL_WAIT, RelayState.FAILED},
    RelayState.FB_READY: {RelayState.KERNEL_WAIT, RelayState.FAILED},
    RelayState.KERNEL_WAIT: {RelayState.BOOTSTRAPPING, RelayState.FAILED, RelayState.DEAD},
    RelayState.BOOTSTRAPPING: {RelayState.GATED, RelayState.UNKNOWN,
                               RelayState.FAILED, RelayState.DEAD},
    RelayState.GATED: {RelayState.BUSY, RelayState.UNKNOWN, RelayState.DEAD},
    RelayState.BUSY: {RelayState.GATED, RelayState.UNKNOWN, RelayState.DEAD},
    # 状态未知只允许三条出路：探针确认（GATED）、重开会话（LISTENING，
    # 旧内核会被关掉重建）、或判死/失败。
    RelayState.UNKNOWN: {RelayState.GATED, RelayState.LISTENING,
                         RelayState.DEAD, RelayState.FAILED},
    RelayState.DEAD: {RelayState.LISTENING},
    # 失败后允许重试：每次 fire 都新建内核与后端，失败的旧内核被替换。
    RelayState.FAILED: {RelayState.LISTENING},
}


class TransitionError(RuntimeError):
    pass


class RelayMachine(object):
    def __init__(self, emit=None):
        self._state = RelayState.BOOT
        self._emit = emit or (lambda event: None)

    @property
    def state(self):
        return self._state

    def enter(self, target, reason=""):
        if target not in ALLOWED[self._state]:
            raise TransitionError("非法转移: %s -> %s（%s）"
                                  % (self._state.value, target.value, reason))
        self._state = target
        self._emit(("state", target.value, reason))
        return self._state
