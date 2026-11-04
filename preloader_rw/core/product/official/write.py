# -*- coding: utf-8 -*-
"""WRITE-PARTITION 的上行 Provider 与命令实例。

行为对齐 `src\\writer.py` 的四段对话：
    EXISTS ×2      → 主机回 `OK\\0` + `OK@NOT-EXISTS\\0`（两帧）
    DOWNLOAD-FILE  → 主机回 `OK\\0` + `OK@0x<总长>\\0`，随后供数
    END            → `<result>OK</result>` 才算成功

Response 的用法（本文件与调度器之间的约定）：
  · reply      ：请求到达后立即发送的第一帧（本路径恒为 `OK\\0`）。
  · decl_len   ：DOWNLOAD-FILE 的总长；调度器据此构造 `OK@0x<总长>\\0`。
  · chunk      ：供数片长；调度器交给内核 supply。
  · payload    ：decl_len 非空时为供数数据；decl_len 为空时为第二应答帧
                 （EXISTS 的 `OK@NOT-EXISTS\\0`）。
  · failure    ：裁决失败分类，由调度器决定终止或重试。
"""

from dataclasses import dataclass
import os

from ...engine.failure import Failure
from ...engine.provider import RequestKind, Response
from ...engine.inbound import EventKind
from ...proto import xml
from ...proto.const import EXISTS_NOT, OK
from .partitions import PartitionTableHolder

__all__ = ["DEFAULT_CHUNK", "WriteRequest", "WriteProvider", "WriteCommandInstance"]

DEFAULT_CHUNK = 0x200000

_STAGE_EXISTS = "exists"
_STAGE_DOWNLOAD = "download"
_STAGE_SUPPLY = "supply"
_STAGE_END = "end"


@dataclass(frozen=True)
class WriteRequest:
    """一条 WRITE-PARTITION 的输入。

    table 必须是设备上传后解析出的运行期分区表；为 None 时 Provider
    拒绝服务——不允许拿静态表或猜测值写设备。
    """

    partition: str
    image: str
    total: int = 0
    packet_length: int = DEFAULT_CHUNK
    table: object = None
    # >0 表示原始偏移写（WRITE-FLASH）：设备先问 FILE-SIZE，再 DOWNLOAD-FILE；
    # 0 表示分区写（WRITE-PARTITION）：设备先问 EXISTS。
    offset: int = 0

    def __post_init__(self):
        if not self.partition:
            raise ValueError("WriteRequest.partition 不得为空")
        if not self.image:
            raise ValueError("WriteRequest.image 不得为空")
        if self.total < 0:
            raise ValueError("WriteRequest.total 不得为负: %r" % (self.total,))
        if self.packet_length < 0:
            raise ValueError("WriteRequest.packet_length 不得为负: %r" % (self.packet_length,))
        if self.offset < 0:
            raise ValueError("WriteRequest.offset 不得为负: %r" % (self.offset,))

    @property
    def flash_mode(self):
        """原始偏移写：以 FILE-SIZE 开场，不经分区表校验。"""
        return self.offset > 0


class WriteProvider(object):
    """一条写命令在活动期间独占的上行服务者。

    Provider 通过 arm/release 与命令实例绑定；命令结束后不再命中任何请求。

    holder 可以是 PartitionTable（离线装配时已知表）或 PartitionTableHolder
    （运行期：表由 READ-FLASH 直读 GPT 后写入）。
    """

    def __init__(self, holder=None):
        self._holder = (holder if isinstance(holder, PartitionTableHolder)
                        else PartitionTableHolder(holder))
        self._active = None
        self._capacity = 0
        self.exists_asked = 0
        self.filesize_asked = 0
        self.download_served = 0
        self.bytes_planned = 0
        self.result_text = ""
        self.ended = False
        self.error = ""

    # ---- 绑定 -------------------------------------------------------

    @property
    def holder(self):
        return self._holder

    @property
    def table(self):
        return self._holder.get()

    @property
    def active(self):
        return self._active

    def arm(self, instance):
        """命令实例开始执行时调用；分区写没有运行期分区表即拒绝。

        原始偏移写（WRITE-FLASH）不经分区表：设备按 partition+offset 自解，
        故只记 0 容量（不参与容量校验）。
        """
        if instance.request.flash_mode:
            self._capacity = 0
        else:
            table = instance.request.table or self._holder.get()
            if table is None:
                raise ValueError(
                    "写入 %r 前未加载设备分区表；拒绝用静态表或猜测值写设备"
                    % (instance.request.partition,))
            base, size = table.at(instance.request.partition)
            if base is None:
                raise ValueError("分区 %r 不在设备上传的分区表中"
                                 % (instance.request.partition,))
            self._capacity = size
        self._active = instance
        self.exists_asked = 0
        self.filesize_asked = 0
        self.download_served = 0
        self.bytes_planned = 0
        self.result_text = ""
        self.ended = False
        self.error = ""

    def release(self, instance):
        """命令结束后解绑；只解除自己绑定的实例。"""
        if self._active is instance:
            self._active = None

    # ---- 上行匹配 ---------------------------------------------------

    def match(self, request):
        instance = self._active
        if instance is None:
            return False

        if request.kind is RequestKind.FILE_SYS_OP:
            key = (request.key or "").upper()
            # 分区写问 EXISTS；原始偏移写问 FILE-SIZE。两者都由本 Provider 吃掉。
            if instance.request.flash_mode:
                return key == "FILE-SIZE"
            return instance.stage.name == _STAGE_EXISTS and key == "EXISTS"

        if request.kind is RequestKind.DOWNLOAD_FILE:
            if instance.stage.name not in (_STAGE_EXISTS, _STAGE_DOWNLOAD):
                return False
            return not request.info or request.info == instance.request.partition

        if request.kind is RequestKind.END:
            return not instance.done

        return False

    # ---- 上行服务 ---------------------------------------------------

    def serve(self, request):
        if request.kind is RequestKind.FILE_SYS_OP:
            if self._active is not None and self._active.request.flash_mode:
                return self._serve_filesize(request)
            return self._serve_exists(request)
        if request.kind is RequestKind.DOWNLOAD_FILE:
            return self._serve_download(request)
        if request.kind is RequestKind.END:
            return self._serve_end(request)
        return None

    def _serve_filesize(self, request):
        """FILE-SIZE：回 `OK\\0` + `OK@0x<总长>\\0` 两帧（官方 LIST2 第 18 条）。"""
        self.filesize_asked += 1
        path = self._active.request.image
        try:
            total = os.path.getsize(path)
        except OSError as exc:
            self.error = "读取镜像失败: %r" % (exc,)
            return Response(failure=Failure.ROUTE_ERROR)
        self.bytes_planned = total
        return Response(reply=OK, payload=xml.size_reply(total))

    def _serve_exists(self, request):
        # 实机判据（LEDGER F287）：EXISTS 必须两帧，只发后者会令设备静默。
        self.exists_asked += 1
        return Response(reply=OK, payload=EXISTS_NOT)

    def _serve_download(self, request):
        path = self._active.request.image
        try:
            total = os.path.getsize(path)
            handle = open(path, "rb")
            handle.close()
        except OSError as exc:
            self.error = "读取镜像失败: %r" % (exc,)
            return Response(failure=Failure.ROUTE_ERROR)

        packet = request.packet_length or self._active.request.packet_length or DEFAULT_CHUNK
        packet = max(1, min(int(packet), 0x1000000))
        if self._capacity and total > self._capacity:
            self.error = ("镜像 %d B 超过分区 %r 容量 0x%X ⇒ 拒绝"
                          % (total, self._active.request.partition, self._capacity))
            return Response(failure=Failure.ROUTE_ERROR)
        self.download_served += 1
        self.bytes_planned = total
        # 先 OK\0 → 再发总长帧 OK@0x<总长>\0 → 供数。
        # 供数回合（含「长度帧之后等设备 3B OK」「每块令牌之后等设备 3B OK」）
        # 由 proto\supply.download 唯一实现：
        # 每条命令设备 3B OK 数 = 数据笔数 + 1，令牌数与 ZLP 数 = 数据笔数。
        return Response(source_path=path, total=total, chunk=packet, reply=OK,
                        decl_reply=xml.size_reply(total))

    def _serve_end(self, request):
        self.result_text = request.result
        self.ended = True
        if request.result == "OK":
            return Response(reply=OK)
        self.error = "分区 %s 回 result=%r" % (
            self._active.request.partition, request.result)
        return Response(reply=OK, failure=Failure.DEV_ERROR)


class WriteCommandInstance(object):
    """一条 WRITE-PARTITION 的阶段推进器。

    阶段：exists → download → supply → end。
    Provider 先消化请求，本实例随后按事件推进；END 的 result=OK 才算成功。
    """

    def __init__(self, spec, request, provider):
        if not isinstance(request, WriteRequest):
            raise TypeError("WriteCommandInstance 需要 WriteRequest，收到 %r"
                            % (type(request).__name__,))
        self._spec = spec
        self.request = request
        self._provider = provider
        self._index = 0
        self._done = False
        self._failure = None
        self._exists_seen = 0
        provider.arm(self)

    # ---- 只读视图 ---------------------------------------------------

    @property
    def spec(self):
        return self._spec

    @property
    def stage(self):
        return self._spec.stages[min(self._index, len(self._spec.stages) - 1)]

    @property
    def done(self):
        return self._done

    @property
    def failure(self):
        return self._failure

    @property
    def payload(self):
        """首帧命令字节：分区写给分区名+镜像；原始偏移写给分区名+offset+镜像。"""
        request = self.request
        if request.flash_mode:
            return xml.write_flash(request.partition, request.offset, request.image)
        return xml.write_partition(request.partition, request.image)

    def next_wait(self):
        return self._spec.wait_for(self._index)

    # ---- 推进 -------------------------------------------------------

    def advance(self, event):
        if self._done:
            return

        kind = event.kind

        if kind is EventKind.DEV_GONE:
            self._fail(Failure.DEV_GONE)
            return

        if kind is EventKind.TIMEOUT:
            self._fail(Failure.HOST_TIMEOUT)
            return

        if kind is EventKind.FILE_SYS_OP:
            if self.stage.name == _STAGE_EXISTS:
                self._exists_seen += 1
                # 分区写问 EXISTS 两次；原始偏移写问 FILE-SIZE 一次。
                need = 1 if self.request.flash_mode else 2
                if self._exists_seen >= need:
                    self._index = max(self._index, 1)
            return

        if kind is EventKind.DOWNLOAD_FILE:
            if self._provider.error and self._provider.download_served == 0:
                self._fail(Failure.ROUTE_ERROR)
                return
            self._index = max(self._index, 2)
            return

        if kind is EventKind.END:
            self._index = max(self._index, 3)
            if event.result == "OK":
                self._finish()
            else:
                self._fail(Failure.DEV_ERROR)

    # ---- 内部 -------------------------------------------------------

    def _finish(self):
        self._done = True
        self._provider.release(self)

    def _fail(self, failure):
        self._failure = failure
        self._done = True
        if not self._provider.error:
            self._provider.error = str(failure.value)
        self._provider.release(self)
