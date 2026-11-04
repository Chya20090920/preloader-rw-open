# -*- coding: utf-8 -*-
"""KernelAPI 的实现：把原语落到 Channel 与端口上。

本文件是载荷与内核之间唯一的桥。载荷只见 api.py 的协议，不见 Channel。
"""

from ..engine import timing
from ..engine import acks
from ..engine.gate import StartGate
from ..proto import da
from ..proto import redact
from ..proto import supply as supply_mod
from ..proto import xml
from ..proto.const import FLAG_CMD, OK
from . import commands as command_book
from .api import CommandResult, Event, EventKind

import os


def _file_size(path):
    return os.path.getsize(os.fspath(path))


def _size_of(payload):
    """下载项的实际长度：bytes 取长度，其余按路径取文件大小。"""
    if isinstance(payload, (bytes, bytearray, memoryview)):
        return len(payload)
    return _file_size(payload)


class KernelError(IOError):
    pass


# 回合尾应答。**必须是 3 字节 `OK\0`，不能用 64 字节。**
# 设备对回合尾应答的长度有上限（< 64），超长会返回错误码并结束命令循环，
# 表现是「命令无响应、心跳还在」。
END_REPLY = OK


def _is_device_log(payload):
    """设备日志与命令帧同 flag；判定规则只在 proto.redact 维护。"""
    return redact.is_device_log(payload)


def to_event(flag, payload, table=None):
    """一帧 → 一个内核事件。字段解析只在 proto/xml.py 做。

    帧属于哪一类由**表**决定（`table.classify`）：换一份 DA 只要表里的
    传输符号跟着换，本函数不认识任何具体命令名。
    """
    if flag != FLAG_CMD:
        return Event(EventKind.LOG, raw=payload,
                     text=payload.decode("ascii", "ignore")[:200])
    if payload[:5] == b"<?xml" and not _is_device_log(payload):
        table = table if table is not None else command_book.default_table()
        kind = table.classify(payload)
        if kind == "download":
            return Event(EventKind.DOWNLOAD_FILE, raw=payload,
                         info=xml.info(payload),
                         source_file=xml.source_file(payload),
                         packet_length=xml.packet_length(payload))
        if kind == "filesys":
            return Event(EventKind.FILE_SYS_OP, raw=payload,
                         key=xml.key(payload),
                         file_path=xml.file_path(payload))
        if kind == "upload":
            return Event(EventKind.UPLOAD_FILE, raw=payload,
                         info=xml.info(payload),
                         target_file=xml.target_file(payload))
        if kind == "start":
            return Event(EventKind.START, raw=payload)
        if kind == "end":
            return Event(EventKind.END, raw=payload, result=xml.result(payload))
        if kind == "notice":
            # 其余含 CMD: 的 XML（如 CMD:PROGRESS-REPORT）：旧 L437 一律回 OK，
            # 不是日志，必须单独立类，否则会被当 LOG 丢掉、漏应答。
            return Event(EventKind.NOTICE, raw=payload,
                         text=payload.decode("ascii", "ignore")[:200])
        return Event(EventKind.LOG, raw=payload,
                     text=payload.decode("ascii", "ignore")[:200])
    if payload.startswith(b"OK"):
        return Event(EventKind.OK, raw=payload,
                     text=payload.decode("ascii", "ignore")[:64])
    return Event(EventKind.LOG, raw=payload,
                 text=payload.decode("ascii", "ignore")[:200])


class KernelSession(object):
    """原语的实现。所有等待都经 Channel.poll，期间持续服务 IN。"""

    def __init__(self, channel, clock, input_provider, log=None,
                 supply_chunk=0x200000,
                 poll_slice_s=0.05, read_ms=20,
                 upload_timeout_s=30.0, end_reply=None,
                 attached=False, da2=b"", boot_at=0, boot_jmp=0,
                 da2_mem_addr=0, table=None):
        self.channel = channel
        self.clock = clock
        self.input = input_provider
        self.log = log or (lambda level, text: None)
        # 命令表：内核认识的全部命令都从这里查。None = 仓库自带的那张。
        self.table = table if table is not None else command_book.default_table()
        self.supply_chunk = supply_chunk
        self.poll_slice_s = poll_slice_s
        self.read_ms = read_ms
        self.upload_timeout_s = upload_timeout_s
        self.end_reply = end_reply if end_reply is not None else OK
        self.loop_entered = False      # 载荷自述：已进入命令循环（声明级）
        self.loop_verified = False     # 设备侧确认：命令回合真的收到过 END
        # 上一回合为什么「没有明确结果」（空串 = 有结果）。
        # 引擎据此把会话落到 STALE，并在原因串里写清是哪一种：
        #   "数据收满但无 CMD:END" / "回合尾无 CMD:END"。
        self.last_round_gap = ""
        # 附加模式：设备已在 DA2 命令态，没有 DA1 上传与 2nd-DA 下载。
        self.attached = attached
        self.da2 = da2
        self.boot_at = boot_at
        self.boot_jmp = boot_jmp
        self.da2_mem_addr = da2_mem_addr
        # 命令许可闸：设备每条命令结束后发 CMD:START 并等 3 B OK。
        # 挂在会话上，命令循环按批次新建也不会丢许可。
        self.start_gate = StartGate()

    # ---- 发送 -------------------------------------------------------

    def send(self, payload, flag=FLAG_CMD):
        self.channel.send(payload, flag=flag, role=redact.CTL)

    def reply(self, payload):
        self.channel.send(payload, role=redact.CTL)

    # ---- 接收 -------------------------------------------------------

    def _next_frame(self, seconds):
        """在预算内取一帧。

        只用一个队列（Channel 自己的）。此处不缓存多余帧，否则供数模块
        直接读 Channel 时会与缓存错位、互相吞帧。
        """
        end = self.clock.now() + max(0.0, seconds)
        while self.clock.now() < end:
            slice_s = min(self.poll_slice_s, max(0.0, end - self.clock.now()))
            try:
                frames = self.channel.poll(slice_s, self.read_ms, limit=1)
            except IOError as exc:
                return None, exc
            if frames:
                return frames[0], None
        return None, None

    def wait_event(self, budget_ms):
        frame, exc = self._next_frame(budget_ms / 1000.0)
        if exc is not None:
            self.log("err", "传输异常: %r" % (exc,))
            return Event(EventKind.DEV_GONE, text=str(exc))
        if frame is None:
            return Event(EventKind.TIMEOUT)
        return to_event(frame[0], frame[1], self.table)

    def sleep(self, ms):
        end = self.clock.now() + max(0.0, ms / 1000.0)
        while self.clock.now() < end:
            slice_s = min(self.poll_slice_s, max(0.0, end - self.clock.now()))
            try:
                frames = self.channel.poll(slice_s, self.read_ms, limit=1)
            except IOError:
                return
            if frames:
                self.channel.unread(frames)

    # ---- 第一段 DA 交接 ---------------------------------------------

    def serve_da2(self):
        """发 BOOT-TO 并完成 2nd-DA 下载；返回时设备已进入 DA2。

        按官方下载流程完成 BOOT-TO 与 2nd-DA 供数；供数完成后拉起常驻读
        线程（此前 DA1/DA2 直接读 IN，两条读会抢回显）。
        """
        if self.attached:
            raise KernelError("附加模式没有 2nd-DA 下载")
        total = len(self.da2)
        if not total:
            raise KernelError("DA2 为空")
        self.log("info", "发 BOOT-TO 触发 2nd-DA 请求")
        self.send(xml.boot_to(self.boot_at, self.boot_jmp,
                              xml.mem_source(self.da2_mem_addr, total)))
        while True:
            event = self.wait_event(timing.DEV_INIT_MS)
            if event.kind is EventKind.DOWNLOAD_FILE:
                info = (event.info or "").strip()
                if info and info not in self.table.da2_tags:
                    raise KernelError("设备请求未知文件: %r（表里的 2nd-DA 别名: %r）"
                                      % (info, list(self.table.da2_tags)))
                # DOWNLOAD-FILE ⇒ 通用 OK ⇒ 长度应答 OK@0x57014。缺前一条
                # 通用 OK 会少一帧，设备停在等 OK 上。
                self.reply(OK)
                self.reply(xml.size_reply(total))
                self.channel.poll(1.0)
                self.log("info", redact.asset_identity("da2", self.da2))
                da.upload_da2(self.channel, self.clock, self.da2,
                              log=self.log_line)
                self.channel.start_reader()
                return
            if event.kind is EventKind.END:
                self.reply(OK)
                continue
            if event.kind is EventKind.START:
                self.reply(OK)
                continue
            if event.kind is EventKind.NOTICE:
                self.reply(OK)
                continue
            if event.kind is EventKind.OK:
                # 纯 3B OK 是接收循环的握手，不是收讫请求；只答带内容的帧。
                if event.raw != OK:
                    self.reply(OK)
                continue
            if event.kind is EventKind.LOG:
                continue
            if event.kind in (EventKind.TIMEOUT, EventKind.DEV_GONE):
                raise KernelError("等 DA2 下载请求失败: %s" % event.kind.value)

    def log_line(self, text):
        """给 da.upload_da2 用的单行日志出口。"""
        self.log("info", text)

    # ---- 命令回合 ---------------------------------------------------

    def command(self, name, args=None, *, args_xml=None, shape=None,
                source=None, sources=None, declare=None,
                save_path="", save_paths=None, filesys=None):
        """执行一条完整命令回合；帧构造与回合推进都留在内核。

        设备决定回合里出什么：它发 DOWNLOAD-FILE 就要文件，发 UPLOAD-FILE
        就要落盘，发 FILE-SYS-OPERATION 就要一次查询应答。内核按请求自身的
        `<info>` / `<key>` 查绑定表，不按命令名分支；命令表只用来校验该命令
        会不会出现这类请求（命令表的 pulls / pushes / fs_keys）。

        source     单个文件：仅当该命令只索要一种 `<info>` 时可用
        sources    `<info>` → bytes / 路径；多文件命令必须用这个
        declare    申报长度覆盖；仅单文件命令可用
        save_path  单个落盘：仅当该命令只上传一种 `<info>` 时可用
        save_paths `<info>` → 落盘路径；分区名类上传用分区名作键
        filesys    `<key>` → 第二应答帧；EXISTS / FILE-SIZE 有缺省，
                   其余 key（MKDIR / REMOVE / REMOVE-ALL）必须显式给
        """
        spec = self.table.get(name)
        self.table.shape(name, shape)
        frame = self.table.frame(name, args, args_xml)
        pulls = self._bind_pulls(spec, source, sources, declare)
        saves = self._bind_saves(spec, args, save_path, save_paths)
        answers = dict(filesys or {})
        self._settle_start()
        self.last_round_gap = ""       # 新回合：清掉上一回合的「无结果」记录
        self.log("info", "命令 %s（%s）" % (spec.name, spec.shape))
        self.send(frame)
        return self._run_round(spec, pulls, saves, answers, declare)

    # ---- 绑定表 -----------------------------------------------------

    def _settle_start(self):
        """发下一条命令帧之前，把设备重发的 `CMD:START` 认掉。

        设备每轮结束都会发 `CMD:START` 并等主机回 3 字节 `OK`。
        **若它没认出这次应答，会回顶再发一次 START，并把落在原地的那个
        主机帧当成垃圾丢掉**，此后双方互等：设备等 OK，主机等命令结果。

        因此这里不能无条件清空队列：见到 `CMD:START` 必须回 `OK`，且要
        一直认到不再重发为止。其余迟到帧仍按旧语义丢弃。
        """
        deadline = self.clock.now() + 0.2
        while self.clock.now() < deadline:
            frame, exc = self._next_frame(0.02)
            if exc is not None:
                return
            if frame is None:
                continue
            flag, payload = frame
            if flag != FLAG_CMD or _is_device_log(payload):
                continue
            if self.table.transport.start in payload:
                self.log("info", "补认设备重发的 CMD:START ⇒ 回 OK")
                self.reply(OK)
                deadline = self.clock.now() + 0.2   # 可能再发，继续认
                continue
            # 其余帧是上一轮的迟到帧，按旧语义丢弃。

    @staticmethod
    def _bind_pulls(spec, source, sources, declare):
        pulls = dict(sources or {})
        if source is not None:
            if len(spec.pulls) != 1:
                raise ValueError(
                    "命令 %s 会索要 %r，单一 source 无法定位；改用 sources"
                    % (spec.name, list(spec.pulls)))
            tag = spec.pulls[0]
            if tag in pulls and pulls[tag] is not source:
                raise ValueError(
                    "命令 %s 的 %r 同时给了 source 与 sources，二者不一致"
                    % (spec.name, tag))
            pulls[tag] = source
        if declare is not None and len(pulls) > 1:
            raise ValueError("命令 %s 有多个下载项，declare 无法区分" % (spec.name,))
        return pulls

    @staticmethod
    def _bind_saves(spec, args, save_path, save_paths):
        saves = dict(save_paths or {})
        if save_path:
            if len(spec.pushes) != 1:
                raise ValueError(
                    "命令 %s 会上传 %r，单一 save_path 无法定位；改用 save_paths"
                    % (spec.name, list(spec.pushes)))
            tag = spec.pushes[0]
            if tag in saves and saves[tag] != save_path:
                raise ValueError(
                    "命令 %s 的 %r 同时给了 save_path 与 save_paths，二者不一致"
                    % (spec.name, tag))
            saves[tag] = save_path
        if self.table.partition_tag in saves and args:
            saves.setdefault(str(args.get("partition", "")),
                             saves[self.table.partition_tag])
        return saves

    def _upload_path(self, spec, saves, tag):
        key = tag.strip()
        if key in saves:
            return saves[key]
        if self.table.partition_tag in saves:
            return saves[self.table.partition_tag]
        if len(spec.pushes) == 1 and "" in saves:
            return saves[""]
        raise KernelError("%s: 设备上传 info=%r，未绑定落盘路径（可绑定键=%r）"
                          % (spec.name, key, sorted(saves)))

    def _fs_answer(self, spec, answers, pulls, key, event):
        fs = self.table.filesys
        if key in answers:
            value = answers[key]
            return value(event) if callable(value) else value
        if key == fs.exists.upper():
            return fs.not_exists_reply
        if key == fs.file_size.upper():
            size = self._queried_size(event.file_path, pulls)
            if size is None:
                payload = pulls.get("LocalFile")
                if payload is None:
                    raise KernelError(
                        "%s: 设备查询 FILE-SIZE（path=%r），既无可读文件也未绑定 "
                        "LocalFile" % (spec.name, event.file_path))
                size = _size_of(payload)
            return xml.size_reply(size)
        raise KernelError(
            "%s: 设备查询 %r，应答表未给（可给键=%r；EXISTS/FILE-SIZE 有缺省）"
            % (spec.name, key, sorted(answers)))

    @staticmethod
    def _queried_size(file_path, pulls):
        """按设备回显的路径取文件长度；只认与已绑定载荷同目录的文件。

        官方 WRITE-FLASH 按 128 MiB 切片，每个 chunk 单独问一次 FILE-SIZE，
        `<file_path>` 是该 chunk 的真实路径（r3 流 81735/83187/84663/86139）。
        限定同目录：设备给的路径不可能借此探测任意文件。
        """
        base = pulls.get("LocalFile")
        if not file_path or not isinstance(base, str):
            return None
        root = os.path.dirname(os.path.abspath(os.fspath(base)))
        target = os.path.abspath(os.fspath(file_path))
        if os.path.dirname(target) != root or not os.path.isfile(target):
            return None
        return os.path.getsize(target)

    def _run_round(self, spec, pulls, saves, answers, declare):
        moved = 0
        result = ""
        upload_path = ""
        # 活性账本：任何一帧（含心跳 devlog）到达就刷新 last_frame。
        # 它与「回合有没有结果」是两件事——心跳只证明设备还活着，不产生结果。
        last_frame = self.clock.now()
        while True:
            event = self.wait_event(timing.DIALOG_MS)
            kind = event.kind
            if kind is EventKind.TIMEOUT:
                silent_ms = (self.clock.now() - last_frame) * 1000.0
                if silent_ms < timing.LIVENESS_MS:
                    # 只是这一拍没等到帧，设备仍在发心跳：继续等，不算结果。
                    continue
                raise KernelError(
                    "设备静默 %.0f ms（超过 %d 个心跳周期，会话已死）"
                    % (silent_ms, timing.LIVENESS_MS // timing.HEARTBEAT_MS))
            last_frame = self.clock.now()
            if kind is EventKind.DEV_GONE:
                raise KernelError("设备消失: %s" % (event.text or "",))
            if kind is EventKind.LOG:
                continue
            if kind in (EventKind.OK, EventKind.NOTICE):
                reply = acks.generic_reply(kind.value, event.raw, OK)
                if reply:
                    self.reply(reply)
                continue
            if kind is EventKind.END:
                result = event.result or ""
                self.reply(self.end_reply)
                self.last_round_gap = ("" if result
                                       else "回合尾 CMD:END 缺 result")
                continue
            if kind is EventKind.START:
                reply = acks.generic_reply(kind.value, event.raw, OK)
                if reply:
                    self.reply(reply)
                # 判据只认 result == "OK"。此前写成 `if result`（只判非空），
                # 会把 ERR / Security deny / ERR!UNSUPPORTED 也置成"已验证"——
                # 那正是设备明确说"不行"的三种情形，不能算通过。
                if (result or "").strip().upper() == "OK" and not self.loop_verified:
                    self.loop_verified = True
                    self.log("ok", "注册已验证：命令回合收到 END result=OK（%s）"
                             % (spec.name,))
                if not result:
                    # 回合走到 START 却没有 END：设备状态未知。
                    self.last_round_gap = "回合尾无 CMD:END"
                return CommandResult(
                    name=spec.name, result=result, moved=moved,
                    path=upload_path)
            if kind is EventKind.UPLOAD_FILE:
                if not spec.pushes:
                    raise KernelError("%s: 设备上传，但命令表未登记上传项"
                                      % (spec.name,))
                path = self._upload_path(spec, saves, event.info or "")
                self.reply(OK)
                moved += self.recv_file(path)
                upload_path = path
                continue
            if kind is EventKind.DOWNLOAD_FILE:
                tag = (event.info or "").strip()
                if tag not in pulls:
                    raise KernelError(
                        "%s: 设备索要 info=%r，未绑定数据（可绑定键=%r）"
                        % (spec.name, tag, list(spec.pulls)))
                payload = pulls[tag]
                total = declare if declare is not None else _size_of(payload)
                self.reply(OK)
                self.reply(xml.size_reply(total))
                if isinstance(payload, (bytes, bytearray, memoryview)):
                    moved += self.supply(bytes(payload), total)
                else:
                    moved += self.supply_file(payload, total)
                continue
            if kind is EventKind.FILE_SYS_OP:
                key = (event.key or "").strip().upper()
                if not spec.fs_keys:
                    raise KernelError("%s: 设备查询 %r，但命令表未登记文件查询"
                                      % (spec.name, key))
                answer = self._fs_answer(spec, answers, pulls, key, event)
                self.reply(OK)
                self.reply(answer)
                continue
            raise KernelError("%s: 未预期事件 %r" % (spec.name, kind))

    # ---- 供数 -------------------------------------------------------

    def drop_pending(self):
        """命令边界：丢掉上一条命令遗留的纯收讫帧（3B OK / 7B OK@0x0）。

        不丢会出的事：下一条命令的供数等待把旧收讫当成本命令的长度帧回执，
        在设备尚未确认新命令时就发令牌与数据。
        """
        self.channel.drop_stale_acks()

    def supply_file(self, path, total, chunk=None):
        """按同一状态机从文件流式供数：每块现读，不整幅入内存。"""
        use = chunk or self.supply_chunk
        self.log("info", "供数: 总长 %d B，块长 %d B（%d 块）"
                 % (total, use, (total + use - 1) // use if use else 0))
        with open(path, "rb") as stream:
            sent = supply_mod.download_stream(
                self.channel, stream, total, use,
                budget_ms=timing.MAX_WAIT_MS)
        if sent < total:
            raise IOError("供数不完整: %d/%d B（设备未收讫）" % (sent, total))
        return sent

    def supply(self, data, decl_len=None, chunk=None):
        """按 proto.supply 的唯一帧序供数；decl_len 只用于一致性核对。"""
        if decl_len is not None and int(decl_len) != len(data):
            self.log("err", "供数申报 %d B 与实际 %d B 不一致"
                     % (int(decl_len), len(data)))
        use = chunk or self.supply_chunk
        self.log("info", "供数: 总长 %d B，块长 %d B（%d 块）"
                 % (len(data), use, (len(data) + use - 1) // use if use else 0))
        sent = supply_mod.download(self.channel, data, use,
                                   budget_ms=timing.MAX_WAIT_MS)
        if sent < len(data):
            raise IOError("供数不完整: %d/%d B（设备未收讫）" % (sent, len(data)))
        return sent

    # ---- 上传 -------------------------------------------------------

    def recv_file(self, path):
        """设备上传落盘。序列以 golden 002034（i=186–195）为权威：

        UPLOAD-FILE(调用方应答) → OK@<decl> ⇒ 一条 OK → 设备 3B OK ⇒ 一条 OK
        → 数据 ⇒ 收满一条 OK → END ⇒ 64B。
        golden 申报帧后那两条 H2D OK：第二条是应答设备的 3B OK 帧，不是对
        申报帧连回两条。多回一条会打乱回合边界，令下一轮命令被设备拒收。
        进入收数后重复 UPLOAD-FILE = 协议违例；申报长度前的未知帧按设备噪声跳过，
        并以「连续无帧超过 upload_timeout_s」兜底，不因一条日志帧把会话打成 DEAD。
        """
        buf = bytearray()
        declared = 0
        armed = False
        seq = 0
        last = self.clock.now()
        while True:
            frame, exc = self._next_frame(1.0)
            if exc is not None:
                raise IOError("上传传输异常: %r" % (exc,))
            if frame is None:
                if self.clock.now() - last > self.upload_timeout_s:
                    raise IOError("上传超时：%.1fs 无设备帧"
                                  % self.upload_timeout_s)
                continue
            last = self.clock.now()
            flag, payload = frame
            if flag != FLAG_CMD or _is_device_log(payload):
                continue
            seq += 1
            self.log("info", "上传收帧 " + redact.frame_line(
                "D2H", seq, payload, redact.classify_inbound(payload)))
            if self.table.transport.end in payload:
                self.log("info", "上传收到 END（累计 %d B）⇒ 回 64B" % len(buf))
                self.reply(END_REPLY)
                break
            if self.table.transport.upload in payload:
                raise IOError("上传收到重复 UPLOAD-FILE（协议违例）")
            if not armed:
                if not payload.startswith(b"OK@"):
                    # 申报长度前只有 OK@ 是合法控制帧；其余按设备噪声跳过，
                    # 不能因一条未识别的日志帧把整个会话打成 DEAD。
                    self.log("info", "上传忽略非协议帧 len=%d" % len(payload))
                    continue
                try:
                    declared = int(payload[3:].split(b"\x00")[0], 16)
                except ValueError:
                    declared = 0
                self.log("info", "设备申报长度 = %s"
                         % (("0x%x" % declared) if declared else "?"))
                self.reply(OK)
                armed = True
                continue
            if payload == OK:
                # 设备申报后先发一条 3B OK 握手（golden i=191）⇒ 只回一条。
                self.reply(OK)
                continue
            if payload.startswith(b"OK@"):
                self.log("info", "块标记 " + redact.frame_line(
                    "D2H", seq, payload, redact.CTL) + " ⇒ 回 OK")
                self.reply(OK)
                continue
            buf += payload
            self.log("info", "数据 %dB（累计 %d/%s）"
                     % (len(payload), len(buf),
                        ("0x%x" % declared) if declared else "?"))
            # 官方 8 MiB 读：每个 2 MiB 数据块后主机回一次 OK，设备再发
            # OK@0x0 块标记，主机再回一次 OK，然后才发下一块。少一次 ACK
            # 设备就停在首块之后。收满 declared 后再等设备 END。
            self.reply(OK)
            if declared and len(buf) >= declared:
                # 申报长度的全部数据已收满，但回合尾 END 还没到。
                # 这是「设备状态未知」的第一种触发条件：先记账，若随后真的
                # 收到 END，_run_round 的 END 分支会把这条记录清掉。
                self.last_round_gap = "数据收满但无 CMD:END"
                self.log("info", "数据收满（已回片 ACK，等设备 END）")
                break
        if path and buf:
            with open(path, "wb") as fh:
                fh.write(bytes(buf))
        return len(buf)

    # ---- 注册点 -----------------------------------------------------

    def request_input(self, kind, info):
        """运行途中向宿主索取输入（如签名应答）：阻塞到回应或超时/取消。

        机制在内核，时机由载荷决定；内核不知道也不关心什么时候该要。
        返回值是宿主给的原文，载荷负责原样提交。
        """
        return self.input.request(kind, info)

    def require(self, kind):
        data = self.input.resolve(kind)
        if isinstance(data, str):
            with open(data, "rb") as fh:
                data = fh.read()
        if not data:
            raise KernelError("二层文件为空: kind=%r" % (kind,))
        self.log("info", redact.asset_identity(kind, data))
        return data

    def enter_loop(self):
        self.loop_entered = True
        self.log("info", "载荷已声明 enter_loop（声明级，注册未验证）")
