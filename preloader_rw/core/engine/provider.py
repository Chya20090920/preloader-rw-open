# -*- coding: utf-8 -*-
"""上行请求与服务提供者协议。

Request 是设备事件的协议化形态；Response 是供数裁决，引擎只透传，不解释业务。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol

from .inbound import Event
from .failure import Failure


class RequestKind(str, Enum):
    DOWNLOAD_FILE = "download_file"
    FILE_SYS_OP = "file_sys_op"
    UPLOAD_FILE = "upload_file"
    START = "start"
    END = "end"
    OK = "ok"
    NOTICE = "notice"
    LOG = "log"
    OTHER = "other"


@dataclass(frozen=True)
class Request:
    """设备上行请求。字段与内核事件一一对应，不另造语义。"""

    kind: RequestKind
    raw: bytes = b""
    info: str = ""
    source_file: str = ""
    target_file: str = ""
    packet_length: int = 0
    key: str = ""
    file_path: str = ""
    result: str = ""
    text: str = ""

    @classmethod
    def from_event(cls, event):
        # type: (Event) -> Request
        try:
            kind = RequestKind(event.kind.value)
        except ValueError:
            kind = RequestKind.OTHER
        return cls(
            kind=kind,
            raw=event.raw,
            info=event.info,
            source_file=event.source_file,
            target_file=event.target_file,
            packet_length=event.packet_length,
            key=event.key,
            file_path=event.file_path,
            result=event.result,
            text=event.text,
        )


@dataclass(frozen=True)
class Response:
    """供数裁决。payload/decl_len/chunk/reply 的用法由上层决定。

    decl_reply 是紧随 reply 之后的第二应答帧（例如总长帧）；引擎只负责发送，
    不解释其内容。
    """

    payload: Optional[bytes] = None
    decl_len: Optional[int] = None
    chunk: Optional[int] = None
    reply: Optional[bytes] = None
    decl_reply: Optional[bytes] = None
    # 供数改为按块从磁盘读：source_path + total 给定时，引擎按块流式供数，
    # 不把整幅镜像读进内存（super 这类 GB 级分区必须走这条）。
    source_path: Optional[str] = None
    total: Optional[int] = None
    recv_path: Optional[str] = None
    failure: Optional[Failure] = None


class Provider(Protocol):
    """上行服务提供者。dispatcher 按注册顺序询问 match，第一个命中者 serve。"""

    def match(self, request):
        # type: (Request) -> bool
        ...

    def serve(self, request):
        # type: (Request) -> Response | None
        ...
