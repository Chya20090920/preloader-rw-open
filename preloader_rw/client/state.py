# -*- coding: utf-8 -*-
"""把壳的事件流折成 GUI 状态；唯一真相在壳，本件只是镜像。

受众: 代理(GUI 端)
视角: 对读者称「你」；本件不出现「我」
禁入: 元指令 · 过程注解 · 自注括号 · 对第三方的话

不变量：
    · state 事件覆盖式合并；log / command 追加式转发。
    · 本件不缓存命令细节，只转发；页面自行决定怎么显示。
"""

import time

from PySide6.QtCore import QObject, Signal

from . import protocol as P


def _stamp(ts):
    if not ts:
        return time.strftime("%H:%M:%S")
    return time.strftime("%H:%M:%S", time.localtime(float(ts)))


class RelayState(QObject):
    state_changed = Signal(str, str)            # state, reason
    health_changed = Signal(object)             # values dict
    snapshot_changed = Signal(object)           # info 快照 dict
    log_appended = Signal(str, str, str, str)   # level, source, text, HH:MM:SS
    command_finished = Signal(object)           # command data dict

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "boot"
        self.reason = ""
        self.health = {}
        self.snapshot = {}

    def apply_event(self, evt):
        if not isinstance(evt, dict) or evt.get("kind") != P.KIND_EVT:
            return
        name = evt.get("event")
        data = evt.get("data") or {}
        if name == P.EV_STATE:
            self.state = str(data.get("state", ""))
            self.reason = str(data.get("reason", ""))
            self.state_changed.emit(self.state, self.reason)
        elif name == P.EV_HEALTH:
            self.health = dict(data.get("values") or {})
            self.health_changed.emit(self.health)
        elif name == P.EV_LOG:
            self.log_appended.emit(str(data.get("level", "info")),
                                   str(data.get("source", "shell")),
                                   str(data.get("text", "")),
                                   _stamp(data.get("ts")))
        elif name == P.EV_COMMAND:
            self.command_finished.emit(data)

    def apply_response(self, _rid, res):
        """info 快照折进状态；其余响应由发起者自己的回调处理。"""
        if not isinstance(res, dict) or not res.get("ok"):
            return
        detail = res.get("detail")
        if isinstance(detail, dict) and "state" in detail:
            self.snapshot = detail
            if detail.get("state"):
                self.state = str(detail["state"])
            self.snapshot_changed.emit(self.snapshot)
