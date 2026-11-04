# -*- coding: utf-8 -*-
"""壳对 GUI 的意图与上行事件。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeHealth:
    pass


@dataclass(frozen=True)
class ReadInfo:
    pass


@dataclass(frozen=True)
class ReadPartition:
    name: str
    path: str


@dataclass(frozen=True)
class WritePartition:
    name: str
    image: str


@dataclass(frozen=True)
class ErasePartition:
    """按分区名擦除：设备按自身分区表解析起止。"""

    name: str


@dataclass(frozen=True)
class Cancel:
    pass


@dataclass(frozen=True)
class Fire:
    """触发一次完整生命周期（探测 → 引导 → 注册 → 分区表 → GATED）。

    da1/da2：DA 镜像路径；payload：载荷路径；table：命令表路径。
    空 = 用壳装配的既有值（表为空时用仓库自带默认表）。
    缺失任一时 fire 直接回 CONFIG 错误——可读、可修正，而不是启动即死。
    """
    da1: str = ""
    da2: str = ""
    payload: str = ""
    table: str = ""


@dataclass(frozen=True)
class HostInput:
    """壳向宿主索取运行时输入（如签名应答）：只带请求本身，不带任何设备原文。

    info 是回合类型标签（如 "SEC-POLICY"），不是挑战内容——挑战码属设备
    与服务器之间的事，主机侧不解析、不展示、不落盘。
    """

    kind: str
    info: str
    req_id: int
    timeout_s: float


@dataclass(frozen=True)
class ProvideInput:
    """宿主应答一次运行时索取：data 是签好名的应答原文。"""

    req_id: int
    data: bytes


@dataclass(frozen=True)
class CancelInput:
    """宿主取消一次运行时索取：流程必须干净中止。"""

    req_id: int


@dataclass(frozen=True)
class ProbeDevice:
    """只读读取设备信息（fastboot 在位判定 + getvar）。

    不切 EDL、不引导、不打开传输端点；GUI 一启动就能显示机型/序列号/槽位。
    """
    pass


@dataclass(frozen=True)
class Detect:
    """按 USB 描述符枚举一次 EDL 设备，回报真实在位/驱动状态。

    state 取值：none（未插入）/ no_driver（已插入但驱动未接管）/
    busy（已绑定但打不开：被占用或无权限）/ openable（可打开）。
    """

    pass


@dataclass(frozen=True)
class IntentResult:
    ok: bool
    code: str = ""
    detail: str = ""


@dataclass(frozen=True)
class HealthReport:
    source: str
    values: dict
    ts: float = 0.0


@dataclass(frozen=True)
class StateEvent:
    state: str
    reason: str = ""
    ts: float = 0.0


@dataclass(frozen=True)
class LogEvent:
    level: str
    text: str
    ts: float = 0.0
    source: str = "shell"


@dataclass(frozen=True)
class CommandEvent:
    """一条下行命令的终局；GUI 只认这一条宣告，不轮询壳内部。"""

    name: str
    ok: bool
    state: str
    failure: str = ""
    rounds: int = 0
    detail: str = ""
    ts: float = 0.0
