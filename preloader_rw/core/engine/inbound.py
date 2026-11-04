# -*- coding: utf-8 -*-
"""上行事件的协议形态（引擎侧定义，内核与载荷共用）。

放在 engine 而不是 kernel：engine 是事件的第一消费者，若反过来 import
kernel 会形成 kernel → engine → kernel 的环。
"""

from dataclasses import dataclass
from enum import Enum


class EventKind(str, Enum):
    DOWNLOAD_FILE = "download_file"    # 设备请求文件（info / source_file / packet_length）
    FILE_SYS_OP = "file_sys_op"        # 设备查询（key=EXISTS / FILE-SIZE，file_path）
    UPLOAD_FILE = "upload_file"        # 设备请求上传（target_file）
    START = "start"                    # CMD:START
    END = "end"                        # CMD:END（result 已解析）
    OK = "ok"                          # 收讫帧（OK\0 / OK@0x… / OK!EOT）
    NOTICE = "notice"                  # 含 CMD: 的 XML 通知帧（如 CMD:PROGRESS-REPORT）
    LOG = "log"                        # 设备日志（flag≠1 或非命令正文）
    TIMEOUT = "timeout"                # 预算内无帧
    DEV_GONE = "dev_gone"              # 传输层报设备消失


@dataclass(frozen=True)
class Event:
    kind: EventKind
    raw: bytes = b""
    info: str = ""
    source_file: str = ""
    target_file: str = ""
    packet_length: int = 0
    key: str = ""
    file_path: str = ""
    result: str = ""
    text: str = ""
