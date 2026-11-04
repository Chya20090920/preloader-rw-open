# -*- coding: utf-8 -*-
"""READ-FLASH / READ-PARTITION-TABLE 的上行 Provider 片段。

两条读命令的 CommandSpec 定义在 commands.py（避免官方路线出现第二套命令
字节），本文件只做再导出并提供上行认领。

上行分工（与 write.py 同一原则：当轮收讫归当轮处理者）：
  · UPLOAD-FILE：先回一条通用收讫，再把落盘路径交给调度器——由调度器以
    内核原语 `recv_file(path)` 执行，本层不搬字节。
  · END：回一条通用收讫，命令随即完成。
"""

from ...engine.provider import RequestKind, Response
from ...proto.const import OK
from .commands import (
    read_flash_spec,
    read_partition_table_spec,
    read_specs,
)

__all__ = [
    "ReadProvider",
    "read_flash_spec",
    "read_partition_table_spec",
    "read_specs",
]


class ReadProvider(object):
    """读命令活动期内认领 UPLOAD_FILE 与该轮的 END。"""

    def __init__(self):
        self._active = None

    @property
    def active(self):
        return self._active

    def arm(self, instance):
        self._active = instance

    def release(self, instance):
        if self._active is instance:
            self._active = None

    def match(self, request):
        instance = self._active
        if instance is None or instance.done:
            return False
        if request.kind is RequestKind.END:
            return True
        if request.kind is not RequestKind.UPLOAD_FILE:
            return False
        return instance.stage.name == "upload"

    def serve(self, request):
        instance = self._active
        if request.kind is RequestKind.END:
            return Response(reply=OK)
        # UPLOAD-FILE：先回收讫，再交出落盘路径由调度器调内核 recv_file。
        return Response(reply=OK, recv_path=instance.recv_path)
