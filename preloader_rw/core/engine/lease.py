# -*- coding: utf-8 -*-
"""会话租约状态机。

会话状态只由本状态机推进；上层不得自行改写状态。

    ABSENT --mark_present--> PRESENT --acquire--> CLAIMED
    CLAIMED --enter_bootstrapping--> BOOTSTRAPPING --enter_gated--> GATED
    GATED --begin_command--> BUSY --end_command--> GATED
    BUSY  --enter_stale--> STALE --confirm_alive--> GATED
    GATED --enter_stale--> STALE
    任意存活态 --mark_dead(DEV_GONE)--> DEAD

约束：
- BOOTSTRAPPING 只做一次：本租约生命周期内不得再次进入。
- GATED 可反复发命令；一条命令结束只回 BUSY -> GATED，不退出 GATED。
- BUSY 期间拒绝第二条命令，理由写在 LeaseError.reason == "busy"。
- STALE 是「设备状态未知」，不是就绪态：
  · 拒绝一切新命令，理由 `LeaseError.reason == "device_unknown"`；
  · 只允许一条指定探针命令进入（`begin_command(probe=True)`）；
  · 只有探针回合尾真的收到 `CMD:END result=OK`，外壳才可调
    `confirm_alive()` 解除——探针失败一律留在 STALE。
- DEAD 只能由 DEV_GONE 进入，且为终态。
- 非法转移一律抛 LeaseError。
"""

from .events import SessionState

DEV_GONE_CAUSE = "dev_gone"
# STALE 的缺省原因：设备状态未知（命令回合没有明确结果）。
DEVICE_UNKNOWN = "device_unknown"


class LeaseError(Exception):
    """租约非法转移。reason 供调用方分类，不需要解析消息文本。"""

    def __init__(self, message, reason="illegal_transition", state=None, target=None):
        super().__init__(message)
        self.reason = reason
        self.state = state
        self.target = target


class SessionLease(object):
    """单次会话的租约。

    设备重插后应新建实例，不复用 DEAD 实例。
    """

    _LIVE = frozenset((
        SessionState.PRESENT,
        SessionState.CLAIMED,
        SessionState.BOOTSTRAPPING,
        SessionState.GATED,
        SessionState.BUSY,
        SessionState.STALE,
    ))

    def __init__(self):
        self._state = SessionState.ABSENT
        self._bootstrapped = False
        self._active = ""
        self._dead_cause = ""
        self._stale_cause = ""
        self._active_probe = False

    # ---- 只读视图 ----

    @property
    def state(self):
        return self._state

    @property
    def bootstrapped(self):
        return self._bootstrapped

    @property
    def active_command(self):
        return self._active

    @property
    def dead_cause(self):
        return self._dead_cause

    @property
    def stale_cause(self):
        """STALE 的原因串；非 STALE 时为空。"""
        return self._stale_cause if self._state is SessionState.STALE else ""

    @property
    def ready(self):
        """「可以发普通命令」的唯一判据：只有 GATED。STALE / BUSY 都不算。"""
        return self._state is SessionState.GATED

    # ---- 观测 ----

    def mark_present(self):
        """设备被枚举到但尚未 claim。重复观测保持 PRESENT。"""
        if self._state is SessionState.PRESENT:
            return
        self._require({SessionState.ABSENT}, SessionState.PRESENT, "mark_present")

    def mark_absent(self):
        """claim 之前设备消失：回到 ABSENT。claim 之后消失走 DEV_GONE。"""
        if self._state is SessionState.ABSENT:
            return
        if self._state is not SessionState.PRESENT:
            raise LeaseError(
                "非法转移 mark_absent: %s -> ABSENT；claim 之后的消失只能由 DEV_GONE 进入 DEAD"
                % (self._state.value,),
                reason="dev_gone_required",
                state=self._state,
                target=SessionState.ABSENT,
            )
        self._state = SessionState.ABSENT

    # ---- 必需转移 ----

    def acquire(self):
        """枚举并 claim 设备。允许在 ABSENT 直接完成，也接受已观测到 PRESENT。"""
        self._require(
            {SessionState.ABSENT, SessionState.PRESENT},
            SessionState.CLAIMED,
            "acquire",
        )

    def enter_bootstrapping(self):
        """进入引导。整个租约生命周期只能进入一次。"""
        if self._bootstrapped:
            raise LeaseError(
                "BOOTSTRAPPING 只做一次：本会话已完成引导，当前 %s" % (self._state.value,),
                reason="bootstrapping_once",
                state=self._state,
                target=SessionState.BOOTSTRAPPING,
            )
        self._require({SessionState.CLAIMED}, SessionState.BOOTSTRAPPING, "enter_bootstrapping")

    def enter_gated(self):
        """引导完成，命令循环就绪。此后可反复发命令。

        注意：这是**引导阶段**的转移，判据由 RegisterPhase 用探针裁决后给出，
        不接受载荷自述。STALE 不得走这里恢复（见 confirm_alive）。
        """
        self._require({SessionState.BOOTSTRAPPING}, SessionState.GATED, "enter_gated")
        self._bootstrapped = True
        self._active = ""
        self._stale_cause = ""

    def enter_stale(self, cause=DEVICE_UNKNOWN):
        """设备状态未知：命令回合没有明确结果（缺 CMD:END / 空 result）。

        进入后：租约拒绝一切新命令（reason="device_unknown"），只放行显式探针。
        这是**不是**就绪态：GATED 与 STALE 是两个不同枚举值，任何
        `state is GATED` 判断都不会命中。
        """
        if self._state is SessionState.STALE:
            if cause:
                self._stale_cause = cause
            return self._state
        self._require(
            {SessionState.BUSY, SessionState.GATED, SessionState.BOOTSTRAPPING},
            SessionState.STALE,
            "enter_stale",
        )
        self._stale_cause = cause or DEVICE_UNKNOWN
        self._active = ""
        self._active_probe = False
        return self._state

    def confirm_alive(self):
        """STALE -> GATED：只有外壳显式发起一次指定探针并收到 OK 才允许调用。

        调用方必须先拿到探针回合的 `CMD:END result=OK`（见 KernelSession 与
        Kernel.probe_alive）；这条转移本身不验证，验证责任在调用点。
        """
        self._require({SessionState.STALE}, SessionState.GATED, "confirm_alive")
        self._stale_cause = ""
        self._active_probe = False
        return self._state

    def begin_command(self, name="", probe=False):
        """占用会话执行一条命令。

        BUSY 时拒绝第二条，状态保持不变；拒绝理由见 LeaseError.reason == "busy"。
        STALE 时拒绝一切非探针命令，理由 LeaseError.reason == "device_unknown"；
        probe=True 是唯一例外（外壳显式发起的那一次指定探针）。
        """
        if self._state is SessionState.BUSY:
            raise LeaseError(
                "BUSY: 正在执行 %r，拒绝第二条命令 %r；状态保持 BUSY"
                % (self._active or "?", name or "?"),
                reason="busy",
                state=self._state,
                target=SessionState.BUSY,
            )
        if self._state is SessionState.STALE and not probe:
            raise LeaseError(
                "STALE: 设备状态未知（%s），拒绝命令 %r；须先发一次指定探针"
                "（GET-DA-INFO / GET-HW-INFO）并收到 CMD:END result=OK"
                % (self._stale_cause or DEVICE_UNKNOWN, name or "?"),
                reason=DEVICE_UNKNOWN,
                state=self._state,
                target=SessionState.BUSY,
            )
        sources = ({SessionState.GATED, SessionState.STALE} if probe
                   else {SessionState.GATED})
        self._require(sources, SessionState.BUSY,
                      "begin_probe" if probe else "begin_command")
        self._active = name
        self._active_probe = bool(probe)

    def end_command(self, target=None):
        """命令结束：BUSY -> target（缺省 GATED）。锁与票据必须一起收尾。

        target=STALE 用于「回合没有明确结果」与「探针没拿到 OK」两种情形：
        设备已不可信，租约不得回到就绪态。
        """
        if self._state is not SessionState.BUSY:
            raise LeaseError(
                "非法转移 end_command: %s -> %s"
                % (self._state.value,
                   (target or SessionState.GATED).value),
                reason="illegal_transition",
                state=self._state,
                target=target or SessionState.GATED,
            )
        target = SessionState.GATED if target is None else target
        if target not in (SessionState.GATED, SessionState.STALE):
            raise LeaseError(
                "end_command 只接受 GATED / STALE，收到 %s" % (target.value,),
                reason="illegal_transition",
                state=self._state,
                target=target,
            )
        self._active = ""
        self._state = target
        if target is SessionState.GATED:
            self._stale_cause = ""
        else:
            self._stale_cause = self._stale_cause or DEVICE_UNKNOWN
        self._active_probe = False
        return self._state

    def mark_dead(self, cause=DEV_GONE_CAUSE):
        """设备从总线消失。DEAD 只能由本方法进入，且只接受 DEV_GONE。"""
        if cause != DEV_GONE_CAUSE:
            raise LeaseError(
                "DEAD 只能由 DEV_GONE 进入，收到 %r" % (cause,),
                reason="dead_requires_dev_gone",
                state=self._state,
                target=SessionState.DEAD,
            )
        if self._state is SessionState.DEAD:
            return
        if self._state not in self._LIVE:
            raise LeaseError(
                "非法转移 mark_dead(DEV_GONE): %s -> DEAD；%s 不是存活态"
                % (self._state.value, self._state.value),
                reason="illegal_transition",
                state=self._state,
                target=SessionState.DEAD,
            )
        self._state = SessionState.DEAD
        self._active = ""
        self._active_probe = False
        self._stale_cause = ""
        self._dead_cause = cause

    # ---- 内部 ----

    def _require(self, sources, target, method):
        if self._state not in sources:
            raise LeaseError(
                "非法转移 %s: %s -> %s" % (method, self._state.value, target.value),
                reason="illegal_transition",
                state=self._state,
                target=target,
            )
        self._state = target
