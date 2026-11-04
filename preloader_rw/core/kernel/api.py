# -*- coding: utf-8 -*-
"""内核暴露给路线载荷的唯一接口。

载荷（动态库 / Python 扩展）只依赖本文件；不得 import 内核其他模块。
所有等待都经内核实现，等待期间持续服务 IN（设备心跳不断）。
"""

from dataclasses import dataclass
from typing import Mapping, Optional, Protocol

from ..engine.inbound import Event, EventKind

__all__ = ["CommandResult", "Event", "EventKind", "KernelAPI",
           "HostInputTimeout", "HostInputCancelled", "HostInputUnavailable"]


class HostInputTimeout(IOError):
    """宿主在时限内没有给出运行时输入——与设备拒绝、传输错误分开。"""


class HostInputCancelled(IOError):
    """宿主主动取消了运行时输入——干净中止，不算设备拒绝。"""


class HostInputUnavailable(IOError):
    """当前没有可用的宿主输入通道（壳未接 GUI/JSON 宿主）。"""


@dataclass(frozen=True)
class CommandResult:
    """一次命令回合的结果。

    name    命令名（与命令表里登记的名字一致）
    result  设备回合尾 `CMD:END` 的 result（"OK" / "ERR" / ""）
    moved   该回合搬运的字节数：upload = 收下并落盘的字节；download = 供出的字节
    path    upload 的落盘路径；其余为空
    """

    name: str
    result: str = ""
    moved: int = 0
    path: str = ""


class KernelAPI(Protocol):
    """原语面。内核不知道路线语义，只提供这些机械动作。"""

    @property
    def attached(self) -> bool:
        """附加模式：设备已运行 DA2，没有 DA1 上传与 2nd-DA 下载。"""
        ...

    def send(self, payload: bytes, flag: int = 1) -> None:
        """发一帧。帧头与载荷分两次传输由实现负责。"""
        ...

    def wait_event(self, budget_ms: int) -> Event:
        """等到一个设备事件；期间持续服务 IN。超时返回 EventKind.TIMEOUT。"""
        ...

    def reply(self, payload: bytes) -> None:
        """应答当前设备请求。"""
        ...

    def serve_da2(self) -> None:
        """发 BOOT-TO 并完成 2nd-DA 下载；返回时设备已进入 DA2。

        由载荷决定调用时机（DA1 前置命令之后）。附加模式下不可用。
        """
        ...

    def command(self, name: str, args: "Mapping | None" = None, *,
                args_xml: "bytes | None" = None,
                shape: "str | None" = None,
                source: "bytes | str | None" = None,
                sources: "Mapping[str, bytes | str] | None" = None,
                declare: "int | None" = None,
                save_path: str = "",
                save_paths: "Mapping[str, str] | None" = None,
                filesys: "Mapping[str, bytes] | None" = None) -> CommandResult:
        """执行一条完整命令回合，返回结果。

        本原语覆盖命令表里登记的全部命令；载荷只给参数与数据，
        帧构造与回合推进（END / START / 上传 / 下载 / 文件查询）由内核完成。
        设备在回合里发什么，内核就按该请求自己的 `<info>` / `<key>` 查绑定表，
        不靠命令名分支。

        shape       形态覆盖；一般省略，条目已由反汇编定形
        source      单个下载项的数据：bytes 或文件路径；仅单下载项命令可用
        sources     多下载项命令：`<info>` → bytes / 文件路径
        declare     下载申报长度覆盖；仅单下载项命令可用
        save_path   单个上传项的落盘路径；仅单上传项命令可用
        save_paths  多上传项命令：`<info>` → 落盘路径
        filesys     FILE-SYS-OPERATION 应答表：`<key>` → 第二应答帧，
                    值可以是 bytes，也可以是 `f(event) -> bytes`。
                    EXISTS 缺省回 `OK@NOT-EXISTS`；FILE-SIZE 缺省按设备回显的
                    `<file_path>` 取长度（限定在与已绑定载荷同目录），取不到
                    才回落到 LocalFile 长度；MKDIR / REMOVE / REMOVE-ALL
                    必须显式给（设备侧只有这 5 个 key）
        """
        ...

    def supply(self, data: bytes, decl_len: "int | None" = None,
               chunk: "int | None" = None) -> int:
        """供数：令牌 + 数据分块 + 逐块收讫。返回实际供出字节数。

        chunk 为单块上限；为 None 时取内核默认。
        """
        ...

    def recv_file(self, path: str) -> int:
        """接收设备上传并落盘，返回字节数。

        序列（从 OK@<decl> 态起）：
        OK@<decl> → OK → [块标记/数据] → END(64B OK)。
        前置 UPLOAD-FILE 帧由调用方派发并应答。
        """
        ...

    def request_input(self, kind: str, info: str) -> bytes:
        """运行途中向宿主索取输入（如签名应答）：阻塞到回应。

        info 只带回合类型标签（如 "SEC-POLICY"），不带任何设备原文。
        超时抛 HostInputTimeout，取消抛 HostInputCancelled，无宿主通道抛
        HostInputUnavailable——三者都不是设备拒绝，载荷不得混为一谈。
        """
        ...

    def require(self, kind: str) -> bytes:
        """注册点索取二层文件。等待期间照收心跳；取不到即抛 IOError。"""
        ...

    def enter_loop(self) -> None:
        """载荷**声明**命令面已打通（声明级，不是判据）。

        内核不再以此为放行条件：RegisterPhase 在载荷返回后用固定探针
        （GET-DA-INFO / GET-HW-INFO 至少一条回 `CMD:END result=OK`）裁决。
        本调用只把 `loop_entered` 置真并记一行日志。
        """
        ...

    def sleep(self, ms: int) -> None:
        """节拍等待，期间服务 IN。"""
        ...

    def log(self, level: str, text: str) -> None:
        """结构化日志：level ∈ info / ok / warn / err。"""
        ...
