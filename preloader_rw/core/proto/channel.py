# -*- coding: utf-8 -*-
"""帧通道：命令通道的唯一 OUT 写入口。

四条实机验证过的不变量，改动前必须重跑发射判据：
  (a) 只有 send 写 OUT；读路径（poll / _read_once）不写。
  (b) OUT 超时绝不重发。bulk OUT 超时不等于对方未收到，迟到的 URB 仍会
      落地，重发即制造重复帧（实机 234824：连续 OK → CMD XML invalid →
      ERR!UNSUPPORTED → halt）。
  (c) 写 OUT 之前先服务一次 IN（小帧）。设备心跳/日志与命令共用一条 IN，
      主机在 OUT 上死等时那条 IN 拿不到完成，其轮询会清掉 OUT 完成位
      （实机 234243）。服务期间收到的帧压入队列，交由 poll 取走，不丢。
  (d) 帧头与载荷分两次传输，中间留节拍。该形态是成功轮验证过的形态。

节拍数值不写在本文件，全部由构造参数传入。
"""

from collections import deque
import threading

from ..ports.device import Endpoint
from . import redact
from .const import FLAG_CMD, OK, OK0
from .frame import encode_header, parse


class Channel(object):
    """一帧 = 头 + 节拍 + 载荷；读到的帧先进队列再交给 poll。"""

    def __init__(self, device, clock, *, beat, chunk=0, read_size=4096,
                 read_ms=20, xfer_timeout_ms=3000, service_s=0.03,
                 drain_s=0.1, small_frame=8192, slice_ms=30,
                 service_gap_s=0.005, log=None):
        self.device = device
        self.clock = clock
        self.beat = beat
        self.chunk = chunk
        self.read_size = read_size
        self.read_ms = read_ms
        self.xfer_timeout_ms = xfer_timeout_ms
        self.service_s = service_s
        self.drain_s = drain_s
        self.small_frame = small_frame
        # 大帧写切片：每片最多 slice_ms，片间服务一次 IN。见 _write_out。
        self.slice_ms = slice_ms
        self.service_gap_s = service_gap_s
        self.log = log or (lambda m: None)

        self.quiet = False
        self.oks = 0
        # 日志用帧序号：发送按逻辑帧（头 + 载荷）计一次，接收按帧计一次。
        self._tx_seq = 0
        self._rx_frames = 0

        self._buf = b""
        self._q = deque()
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._reader = None
        self._reader_stop = threading.Event()
        self._reader_error = None
        # 设备帧序号：只要它变化，就说明设备还活着（心跳/日志也算）。
        self._rx_seq = 0

    # ---- 常驻 IN 读线程 ---------------------------------------------

    def start_reader(self):
        """常驻读 IN：官方主机的 IN 端点始终有读在途，我们照此对齐。

        等待时才读 IN 会让端点出现无读窗口；设备的心跳 IN 与数据 OUT 共用
        QMU 完成位（F289），窗口内完成不了的心跳会被其轮询线程清掉，
        连带把 OUT 的完成位带走。
        """
        if self._reader is not None:
            return self
        self._reader_stop.clear()
        self._reader = threading.Thread(target=self._reader_loop,
                                        name="mtkpr-in", daemon=True)
        self._reader.start()
        return self

    def stop_reader(self):
        self._reader_stop.set()
        reader = self._reader
        self._reader = None
        if reader is not None:
            reader.join(timeout=1.0)

    def _reader_loop(self):
        try:
            while not self._reader_stop.is_set():
                try:
                    data = self.device.read(Endpoint.IN, self.read_size, self.read_ms)
                except Exception as exc:
                    self._reader_error = exc
                    self.log("    IN 读线程退出: %r" % (exc,))
                    return
                if not data:
                    continue
                self._ingest(data)
        finally:
            # 读线程退出后必须把 _reader 清掉：否则 poll 仍走队列等待分支，
            # 而队列再也没人喂，表现为“永远等不到帧”。
            with self._cond:
                self._reader = None
                self._cond.notify_all()

    # ---- 读 ---------------------------------------------------------

    def _ingest(self, data):
        """拆帧并入队。半帧留在缓冲里等下一次读。"""
        if redact.RAW_MODE:
            # 拆帧之前先落原始字节：帧头里没有、解析器会吃掉的那部分
            # （同步字错位、半帧、长度字段被写坏）只能在这里看到。
            shown = data if len(data) <= 256 else data[:256]
            tail = "" if len(shown) == len(data) else " …(%d B)" % len(data)
            self.log("    IN-RAW n=%d hex=%s%s" % (len(data), shown.hex(), tail))
        with self._cond:
            self._buf += data
            frames, used = parse(self._buf)
            self._buf = self._buf[used:]
            for flag, payload in frames:
                self._q.append((flag, payload))
            if frames:
                self._rx_seq += 1
                self._cond.notify_all()
        return frames

    def _account(self, flag, payload):
        self._rx_frames += 1
        if payload == OK:
            self.oks += 1
            return
        if redact.RAW_MODE:
            # 一次性诊断开关：设备方向帧原样输出，不做分类、脱敏与空行抑制。
            # 大帧（2 MiB 数据）只保留前 512 B：完整载荷进日志会把 GUI 单行
            # 撑到兆级，日志控件直接卡死，对协议诊断也没有价值。
            shown = payload if len(payload) <= 512 else payload[:512]
            tail = "" if len(shown) == len(payload) else " …(%d B)" % len(payload)
            self.log("    D2H #%d len=%d raw=%r%s"
                     % (self._rx_frames, len(payload), shown, tail))
            return
        role = redact.classify_inbound(payload)
        line = redact.frame_line("D2H", self._rx_frames, payload, role)
        if line is not None:
            self.log("    " + line)

    def _read_once(self, seconds, read_ms=None):
        """在给定窗口内读一次 IN。

        写等待期间调用：让设备侧挂起的日志/心跳传输先完成，读到就返回；
        读到的帧经 _ingest 入队，不丢。窗口内没有数据则静默返回。
        """
        if self._reader is not None:
            # 常驻读线程已在取 IN，本函数只让出一小段时间，避免两个线程
            # 同时在同一端点上提交读。
            if seconds and seconds > 0:
                with self._cond:
                    self._cond.wait(min(seconds, 0.02))
            return
        end = self.clock.now() + seconds
        wait = self.read_ms if read_ms is None else read_ms
        while self.clock.now() < end:
            data = self.device.read(Endpoint.IN, self.read_size, wait)
            if not data:
                continue
            self._ingest(data)
            return

    def poll(self, seconds, read_ms=None, limit=None):
        """收帧：先取队列；无读线程时才直接读 IN。

        载荷以 OK 开头即提前返回（收讫/令牌），其余帧收满窗口后返回；
        队列中未取走的帧保留给下一次 poll。
        limit=1 时最多返回一帧，其余留在队列里——上层只用一个队列，
        不允许另建私有缓存，否则供数与命令循环会互相吞帧。
        """
        if self._reader is not None:
            return self._poll_queue(seconds, limit)

        out = []
        while self._q:
            flag, payload = self._q.popleft()
            self._account(flag, payload)
            out.append((flag, payload))
            if limit is not None and len(out) >= limit:
                return out
            if payload.startswith(b"OK"):
                return out

        end = self.clock.now() + seconds
        while self.clock.now() < end:
            data = self.device.read(Endpoint.IN, self.read_size,
                                    self.read_ms if read_ms is None else read_ms)
            if not data:
                continue
            self._ingest(data)
            while self._q:
                flag, payload = self._q.popleft()
                self._account(flag, payload)
                out.append((flag, payload))
                if limit is not None and len(out) >= limit:
                    return out
                if payload.startswith(b"OK"):
                    return out
        return out

    def _poll_queue(self, seconds, limit=None):
        """有常驻读线程时的收帧：只从队列取，不在本线程读设备。"""
        out = []
        end = self.clock.now() + max(0.0, seconds)
        done = False
        with self._cond:
            while not done:
                while self._q:
                    out.append(self._q.popleft())
                    if limit is not None and len(out) >= limit:
                        done = True
                        break
                    if out[-1][1].startswith(b"OK"):
                        done = True
                        break
                if done:
                    break
                remain = end - self.clock.now()
                if remain <= 0:
                    break
                self._cond.wait(min(remain, 0.02))
        for flag, payload in out:
            self._account(flag, payload)
        return out

    def unread(self, frames):
        """把已取出的帧按原序放回队首。供等待期间读到的帧复用。"""
        with self._cond:
            for item in reversed(list(frames)):
                self._q.appendleft(item)
            self._cond.notify_all()

    def drop_stale_acks(self):
        """丢弃队列里遗留的纯收讫帧（3B OK / 7B OK@0x0）。

        命令边界用：上一条命令结束时残留的收讫帧若不丢弃，会被下一条命令的
        供数等待当成「本命令的长度帧回执/令牌回执/数据收讫」，使主机在设备
        尚未确认新命令时就发令牌与数据。
        """
        with self._cond:
            kept = deque()
            while self._q:
                flag, payload = self._q.popleft()
                if payload in (OK, OK0):
                    continue
                kept.append((flag, payload))
            self._q = kept
            # 半帧缓冲同样属于上一命令：留着会与下一命令首帧拼成伪帧。
            self._buf = b""

    def wait_any(self, seconds):
        return self.poll(seconds)

    # ---- 写 ---------------------------------------------------------

    def _write_out(self, data, label="frame", service=None):
        """单笔 OUT：小帧先服务 IN；一次性写到底，失败不重发。

        service 为 None 时取通道默认窗口；显式给 0 表示本帧不插写前服务窗
        （接收器驱动的段流形态：设备侧有专用接收循环，不靠这条窗保证）。

        失败时先把 IN 收一遍：设备可能已用 END 给出裁决，读到的帧进队列
        交给上层判定，随后原样抛出异常。

        唯一的重发例外是端点被挂起（PIPE）：STALL 表示设备**拒收**了这一帧，
        字节没有落地，此时清一次 halt 再发同一帧是补发而不是重发。其余失败
        （超时、IO）一律不重发——迟到的 URB 仍会落地，重发即制造重复帧。
        """
        _service = self.service_s if service is None else service
        # 常驻读线程在跑时 IN 已被持续取走，写前服务窗只是空等（实测每帧
        # 多 20–40 ms，比官方主机慢一档）。无读线程时才需要这条窗。
        if _service and len(data) <= self.small_frame and self._reader is None:
            self._read_once(_service)
        try:
            return self._write_data(data)
        except Exception as exc:
            if "LIBUSB_ERROR_PIPE" in str(exc):
                self.log("    H2D %s 命中 PIPE：清 halt 后原帧重发一次" % label)
                if self.clear_stall():
                    try:
                        return self.device.write(Endpoint.OUT, data,
                                                 self.xfer_timeout_ms)
                    except Exception as exc2:
                        exc = exc2
            self.log("    H2D %s 写失败 (%d B): %r" % (label, len(data), exc))
            # 失败窗口里设备可能已经发了裁决帧（ERR / END / 错误日志）。
            # 此前的 _read_once 只把字节塞进队列、不打印，等于把设备的原话
            # 丢弃；这里用 poll 读并逐帧打印，让失败原因可见。
            try:
                self.poll(self.drain_s, self.read_ms)
            except Exception:
                pass
            raise

    def _write_data(self, data):
        """单笔 OUT：大帧一次发完，不切片。

        设备帧层按帧头声明长度记账，不看单笔传输实际收了多少字节。切片
        产生的任意传输边界都可能被设备当成该帧结束，它按整帧回执，剩余
        字节再无人接收。

        小帧是单包、原子，超时可以安全重试；重试只为等待设备腾出接收缓冲，
        上限 = max(3×预算, 10 秒)。大帧超时即失败，绝不续写。
        """
        budget_ms = self.xfer_timeout_ms
        if len(data) > self.small_frame:
            return self.device.write(Endpoint.OUT, data, budget_ms)

        deadline = self.clock.now() + max(3.0 * budget_ms / 1000.0, 10.0)
        while True:
            try:
                return self.device.write(Endpoint.OUT, data, budget_ms)
            except Exception as exc:
                if "LIBUSB_ERROR_TIMEOUT" not in str(exc):
                    raise
                if self.clock.now() >= deadline:
                    raise
                # 常驻读线程在跑时，IN 已由它持续取走：这里再读一次就是
                # 【两个消费者抢同一端点】，会把本该进队列的帧读掉，
                # 设备侧就等不到 IN 完成回调（0x40068164 由 0x40026CE0 置位），
                # 于是它在 0x4002638C–0x4002639C 关中断自旋、永不返回，
                # 表现为「发送上传数据时卡死、连心跳都停」。
                # 只有无读线程时才走这条手动服务窗。
                if self._reader is None:
                    self._read_once(self.service_gap_s, read_ms=1)

    def clear_stall(self):
        """清 OUT 端点的 halt；返回是否清成功。仅用于 PIPE（端点在 STALL）。"""
        clear = getattr(self.device, "clear_halt", None)
        if clear is None:
            self.log("    H2D clear_halt 不可用")
            return False
        try:
            clear(Endpoint.OUT)
            return True
        except Exception as exc:
            self.log("    H2D clear_halt 失败: %r" % (exc,))
            return False

    def send(self, payload, flag=FLAG_CMD, service=None, beat=None,
             role=redact.UNKNOWN, label="", index=None, total=None):
        """发一帧：头 → 节拍 → 载荷（或分块载荷）。

        帧头的长度字段恒等于本帧真实载荷长度——通道不再接受任何
        「申报总长」覆盖，避免令牌帧被写成数据帧长度（帧序错位的来源）。

        零长包不在本函数里发，供数状态机也不发：设备侧帧层按 12 B 头里的
        申报长度取字节，零长包会在块与块之间制造一次「0 字节完成」。

        service / beat 为 None 时取通道默认；显式给 0 表示本帧既不插写前
        IN 服务窗、也不在头/载荷之间留节拍。用于设备侧由接收循环驱动、
        无需命令对话保护的段流（DA2 下载），使发送速率回到链路速率。

        role / label / index / total 只影响日志，不影响线上字节：
        asset 帧只记长度与块序号，任何等级都不记录载荷内容。
        """
        _service = self.service_s if service is None else service
        _beat = self.beat if beat is None else beat

        self._write_out(encode_header(flag, len(payload) & 0xFFFFFFFF),
                        "head", service=_service)
        if _beat:
            self.clock.sleep(_beat)

        if self.chunk and len(payload) > self.chunk:
            off = 0
            while off < len(payload):
                piece = payload[off:off + self.chunk]
                self._write_out(piece, "payload@0x%X" % off, service=_service)
                off += len(piece)
        else:
            self._write_out(payload, "payload", service=_service)

        self._tx_seq += 1
        if (not self.quiet
                and (role != redact.ASSET
                     or redact.should_log_asset(index, total))):
            line = redact.frame_line(
                "H2D", self._tx_seq, payload, role, label, index, total)
            if line is not None:
                self.log("    " + line)

    # 旧实现的名字，迁移期保留同一语义。
    send_split = send


__all__ = ["Channel"]
