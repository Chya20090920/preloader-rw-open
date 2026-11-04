# -*- coding: utf-8 -*-
"""命令循环骨架。

每轮：等待一个设备事件 -> 上行交 dispatcher -> 若有活跃命令则 advance。
循环不认命令名、分区名与地址；这些只以数据形式存在于注册表中。

事件到命令的时序：
- BUSY 由 SessionLease 保证，同一时刻最多一条活跃命令。
- 命令结束（done）后 BUSY -> GATED，循环继续等设备事件。
- 回合没有明确结果（无 CMD:END / 空 result）时 BUSY -> STALE：设备状态未知，
  租约拒绝新命令（reason="device_unknown"），只放行外壳显式发起的那一次探针。
- DEV_GONE 把会话推进到 DEAD，循环退出。
"""

from . import acks
from .gate import StartGate
from .inbound import EventKind
from . import timing
from .events import SessionState
from .failure import Failure
from .lease import LeaseError
from .provider import Request
from .tickets import CommandResult, CommandTicket
from .waits import Wait, WaitKind, Waiter

import threading
import time

# 回合尾「明确结果」的唯一判据：设备发了 CMD:END 且 result 非空。
# result==OK 才是成功；其余非空值（ERR / Security deny / Server is not
# authenticated …）是设备明确表态，会话仍然可信——只有「没有结果」才未知。
OK_RESULT = "OK"


def is_decided_result(text):
    """result 文本是否构成一次明确裁决（非空）。"""
    return bool((text or "").strip())


class CommandLoop(object):
    def __init__(self, kernel, dispatcher, lease, ack, waiter=None, on_response=None,
                 stop_when_done=False, start_gate=None):
        """ack 为通用收讫帧字节，由内核层注入（协议常量不进 engine）。

        start_gate 由会话持有并跨循环实例传入：命令许可跨批次存在，不能
        跟着本次循环的生死走（见 engine.gate）。
        """
        self._kernel = kernel
        self._dispatcher = dispatcher
        self._lease = lease
        self._waiter = waiter or Waiter(kernel)
        self._on_response = on_response
        self._active = None
        self._rounds = 0
        self._stopped = False
        self._issued = False
        # 当前命令的帧是否已发出；窗口归属判定的唯一依据（见 _awaiting_flush）。
        self._frame_sent = False
        self._failure = None
        self._last_error = ""
        self._stop_when_done = stop_when_done
        self._ack = ack
        # 当前命令的完成票据；与 _active 同生命周期，一起收尾。
        self._ticket = None
        # 一次性循环用：待执行命令按序激活，同一时刻只有一条在跑。
        self._pending = []
        # 设备在每条命令结束后发 START；START 是“可以发下一条命令”的许可。
        # 由会话持有，跨循环实例延续。
        self._gate = start_gate if start_gate is not None else StartGate()
        # 允许壳从另一个线程提交意图；锁只保护一次转移，不覆盖整个等待。
        self._lock = threading.RLock()
        # 阶段截止时间：设备持续发 LOG/心跳也不能把 END 预算无限续命。
        self._clock = getattr(kernel, "clock", None)
        self._deadline_instance = None
        self._deadline_stage = ""
        self._deadline_at = 0.0

    # ---- 只读视图 ----

    @property
    def lease(self):
        return self._lease

    @property
    def active(self):
        return self._active

    @property
    def rounds(self):
        return self._rounds

    @property
    def stopped(self):
        return self._stopped

    @property
    def failure(self):
        return self._failure

    @property
    def last_error(self):
        return self._last_error

    # ---- 下行 ----

    def submit(self, name, args=None):
        """按 name 取 CommandSpec，占用会话并生成实例；返回完成票据。

        可从壳的线程调用；与循环线程共用一把锁。BUSY 立即抛出、不排队
        ——拒绝时不产生票据，调用方据 `LeaseError.reason` 分类。
        """
        with self._lock:
            ticket = CommandTicket(name)
            self._submit_locked(name, args, ticket)
            return ticket

    def _submit_locked(self, name, args=None, ticket=None):
        if self._active is not None:
            raise LeaseError(
                "BUSY: 正在执行 %r，拒绝第二条命令 %r；状态保持 BUSY"
                % (self._active.spec.name, name),
                reason="busy",
                state=self._lease.state,
            )
        spec = self._dispatcher.command(name)
        # 探针命令 = 规格里显式声明「允许在 STALE 下执行」的唯一通道
        # （见 product 层 GET-DA-INFO 的 requires；是数据，不是名字判断）。
        probe = SessionState.STALE in spec.requires
        if self._lease.state is SessionState.STALE and not probe:
            # 设备状态未知：拒绝任何新命令，理由 device_unknown。
            # 必须在 requires 检查之前判，否则会被归为 state_required，
            # 调用方就分不清「设备死了」与「命令用错了状态」。
            raise LeaseError(
                "STALE: 设备状态未知（%s），拒绝命令 %r；须先发一次指定探针"
                "并收到 CMD:END result=OK"
                % (self._lease.stale_cause or "device_unknown", name),
                reason="device_unknown",
                state=self._lease.state,
            )
        if self._lease.state not in spec.requires:
            raise LeaseError(
                "命令 %r 需要会话状态 %s，当前 %s"
                % (name, sorted(s.value for s in spec.requires), self._lease.state.value),
                reason="state_required",
                state=self._lease.state,
            )
        self._lease.begin_command(name, probe=probe)
        try:
            instance = spec.build(args)
        except Exception:
            # 组帧失败没有碰过设备：探针失败仍留在 STALE，普通命令回 GATED。
            self._lease.end_command(
                target=SessionState.STALE if probe else None)
            raise
        self._active = instance
        self._ticket = ticket
        self._issued = True
        # 命令帧不在这里发：设备句柄的写者只能有一个——循环线程。
        # 从提交线程直接 send 会与循环线程正在进行的 wait_event（IN）并发，
        # 同一设备句柄两个方向的传输互相踩，帧序被打破。
        self._frame_sent = False

    def queue(self, items):
        """按序执行的一批命令；每次只激活一条，前一条结束才发下一条。"""
        with self._lock:
            self._pending.extend(items or ())

    def stop(self):
        self._stopped = True

    # ---- 循环 ----

    def run(self, max_rounds=None):
        """跑命令循环；返回结束时租约状态。max_rounds 仅用于离线自检。

        传输层异常（bulk OUT 超时、设备掉线等）意味着设备侧状态未知：
        本会话不能再发命令，必须落到 DEAD 并结算票据，而不是让异常逃出
        循环线程后把租约留在 BUSY。
        """
        try:
            while not self._stopped:
                # 先补发待发的命令帧（只由本线程发），再去等设备事件。
                # 等待在锁外进行：submit 允许在等待期间从壳线程落下，本次等待
                # 返回的事件可能早于“命令帧发出”，归属判定见 _awaiting_flush。
                with self._lock:
                    self._activate_pending()
                    self._flush_issue()
                    wait = self._deadline_wait(self._next_wait())
                event = self._waiter.wait(wait)
                if event.kind is EventKind.DEV_GONE:
                    self._enter_dead()
                    break
                with self._lock:
                    stale = self._awaiting_flush()
                    self._route_upstream(event, deliver=not stale)
                    if not stale:
                        self._advance(event)
                    self._rounds += 1
                    if self._stop_when_done and self._issued and self._active is None:
                        self._stopped = True
                    if max_rounds is not None and self._rounds >= max_rounds:
                        self._stopped = True
        except Exception as exc:
            detail = "命令循环异常终止: %r" % (exc,)
            self._last_error = detail
            self._kernel.log("err", detail)
            self._enter_dead(detail, failure=Failure.HOST_TIMEOUT)
        return self._lease.state

    # ---- 内部 ----

    def _activate_pending(self):
        """从队列取一条命令激活；调用方须持锁。"""
        if self._active is not None or not self._pending:
            return
        if self._lease.state is SessionState.STALE:
            # 设备状态未知：队列里的命令一条都不能发（租约也会拒绝）。
            # 这里静默保留队列，绝不让 LeaseError 在循环线程里抛出去——
            # 那会把「状态未知」误判成「循环异常」并落到 DEAD。
            return
        name, args = self._pending.pop(0)
        self._submit_locked(name, args, CommandTicket(name))

    def _flush_issue(self):
        """把已提交但还没发出的命令帧发出去；只有循环线程会调本方法。

        设备在命令之间会发 CMD:START 并等主机 3 B OK。若命令帧抢在应答之前
        发出，设备会把它当成 START 的结果并 halt（实测
        `REVEIVING Start CMD Result ERROR(0xc0010006)`）。所以先过许可闸。
        """
        instance = self._active
        if instance is None or self._frame_sent:
            return
        if self._gate.armed and not self._gate.ready:
            self._wait_start_permit()
        self._gate.consume()
        payload = getattr(instance, "payload", None)
        if payload:
            # 命令边界：先把上一命令遗留的纯收讫帧丢掉，再发本命令首帧。
            drop = getattr(self._kernel, "drop_pending", None)
            if drop is not None:
                drop()
            self._kernel.send(payload)
        self._frame_sent = True

    def _wait_start_permit(self):
        """等设备的 CMD:START 并应答；超预算仍放行（让传输层给出真实错误）。"""
        budget_ms = 200
        rounds = max(1, int(timing.MAX_WAIT_MS / budget_ms))
        for _ in range(rounds):
            event = self._kernel.wait_event(budget_ms)
            if event.kind is EventKind.DEV_GONE:
                return False
            if event.kind is EventKind.TIMEOUT:
                continue
            reply = acks.generic_reply(event.kind.value, event.raw, self._ack)
            if reply:
                self._kernel.send(reply)
            if event.kind is EventKind.START:
                self._gate.granted()
                return True
        self._kernel.log("warn", "等设备 START 许可超时（%d ms），按已许可继续"
                         % timing.MAX_WAIT_MS)
        return False

    def _next_wait(self):
        if self._active is not None:
            wait = self._active.next_wait()
            if wait is not None:
                return wait
        return Wait(WaitKind.FRAME, timing.DIALOG_MS, serve_in=True)

    def _deadline_wait(self, wait):
        """阶段预算按绝对截止时间计算。

        设备在等 END 期间仍会发 LOG/心跳；若每次 LOG 都重置预算，END 永远
        等不到也不会超时。这里从进入该阶段起记一次截止时间，后续等待只用
        剩余预算；耗尽即返回零预算等待，让上层收到 TIMEOUT。
        """
        instance = self._active
        if instance is None:
            self._deadline_instance = None
            self._deadline_stage = ""
            return wait
        if instance is not self._deadline_instance:
            self._deadline_instance = instance
            self._deadline_stage = ""
        stage = instance.stage.name
        # 只对 END 阶段做绝对截止：数据/写入阶段允许心跳续命，
        # 但 END 是命令收尾，不能被心跳无限拖住。
        if stage != "end":
            self._deadline_stage = ""
            return wait
        now = self._now()
        if stage != self._deadline_stage:
            self._deadline_stage = stage
            self._deadline_at = now + max(0, wait.budget_ms) / 1000.0
            return wait
        remain_ms = int((self._deadline_at - now) * 1000.0)
        if remain_ms <= 0:
            return Wait(wait.kind, 0, serve_in=False)
        return Wait(wait.kind, min(wait.budget_ms, remain_ms), wait.serve_in)

    def _now(self):
        if self._clock is not None:
            return self._clock.now()
        return time.monotonic()

    def _awaiting_flush(self):
        """命令已武装、但命令帧还没发出——即提交落在上一次等待中间。

        命令的事件窗口从**它的帧发出之后**开始（`_frame_sent` 由
        `_flush_issue` 置位）。submit 允许落在等待中间，此时本次等待返回的
        那一帧 / 那次超时都早于命令帧发出，属于空闲期；把它喂给刚武装的
        命令，等于把空闲等待的 TIMEOUT 当成命令自己的超时——命令一帧未发，
        已被判 host_timeout（实机 runs\\20261019_095030：misc 与 boot_b 两次
        失败，raw.log 同区间零 H2D，轮次 1373/1374 相差一轮）。

        无活跃命令时不进入此态：此时上行仍按原样派发（该轮 Provider 路由
        与旧行为一致）。
        """
        return self._active is not None and not self._frame_sent

    def _enter_dead(self, detail="设备从总线消失", failure=None):
        if self._lease.state in (SessionState.ABSENT, SessionState.DEAD):
            self._stopped = True
            self.fail_pending(SessionState.DEAD, detail, failure)
            return
        self._lease.mark_dead()
        self._stopped = True
        self.fail_pending(SessionState.DEAD, detail, failure)

    def fail_pending(self, state=SessionState.DEAD, detail="命令循环退出",
                     failure=None):
        """把未结算的票据一律结算——绝不让调用方永远等下去。

        会话死掉、循环被停、循环线程异常，都必须走这里收尾。
        failure 显式给出时不按 state 推导（例如主机写超时导致会话不可继续）。
        """
        with self._lock:
            self._active = None
            ticket = self._ticket
            self._ticket = None
        if ticket is None:
            return
        if failure is None:
            failure = (Failure.DEV_GONE if state is SessionState.DEAD
                       else Failure.HOST_TIMEOUT)
        ticket.resolve(CommandResult(name=ticket.name, ok=False, state=state,
                                     failure=failure, rounds=self._rounds,
                                     detail=detail))

    def _route_upstream(self, event, deliver=True):
        if event.kind in (EventKind.TIMEOUT, EventKind.DEV_GONE):
            return None
        request = Request.from_event(event)
        # 跨轮通用收讫由会话层统一应答（唯一规则见 acks），当轮收讫仍归 Provider。
        reply = acks.generic_reply(request.kind.value, request.raw, self._ack)
        if reply:
            self._kernel.send(reply)
        if event.kind is EventKind.START:
            # 设备发 START 即已交出下一条命令的许可；应答完成后许可到手。
            self._gate.granted()
        if event.kind is EventKind.END and (self._active is None or not deliver):
            # 设备发完 END 会阻塞读 64 B 的 END-RESP（分发器 0x40008AC8 之后
            # 的 64 字节读）。迟到的 END 已无命令认领它，但设备仍在等这一读；
            # 不应答，设备就永远停在 END 与下一次 START 之间（实机 15:13：
            # UFS 上传后命令判超时，会话随即卡在 GATED）。
            self._kernel.send(self._ack)
        if not deliver:
            # 事件早于命令帧发出：不交 Provider——刚武装的 Provider 会认领
            # 不属于它的请求（END / EXISTS / DOWNLOAD-FILE 都按“当前命令”匹配）。
            return None
        provider = self._dispatcher.route(request)
        if provider is None:
            return None
        response = self._dispatcher.serve(provider, request)
        if response is not None:
            self._execute_response(response)
            if self._on_response is not None:
                self._on_response(request, response)
        return response

    def _execute_response(self, response):
        """按 Response 的字段发帧 / 供数 / 收文件。引擎不解释字节内容。"""
        if response.failure is not None:
            self._failure = response.failure
        if response.reply:
            self._kernel.send(response.reply)
        if response.decl_reply:
            self._kernel.send(response.decl_reply)
        if response.source_path:
            # 流式供数：镜像按块从磁盘读，不整幅进内存。
            self._kernel.supply_file(response.source_path, response.total,
                                     chunk=response.chunk)
        elif response.payload:
            if response.decl_len is not None:
                self._kernel.supply(response.payload, response.decl_len,
                                    chunk=response.chunk)
            else:
                self._kernel.send(response.payload)
        if response.recv_path:
            self._kernel.recv_file(response.recv_path)

    def _advance(self, event):
        if self._active is None:
            return
        self._active.advance(event)
        if self._active.done:
            instance = self._active
            self._active = None
            ended = event.kind is EventKind.END
            self._settle(instance, ended=ended,
                         result=(event.result if ended else "") or "")

    def _settle(self, instance, ended=False, result=""):
        """命令结束：锁的状态迁移 + 票据结算。锁与票据必须一起收尾。

        ended 表示设备已发 END：它随后会发 CMD:START 等下一条命令的许可，
        下一条命令帧必须先应答那条 START。

        终态裁决只看一件事——**回合有没有明确结果**：
          · 收到 CMD:END 且 result 非空 → 设备表态了，会话可信：回 GATED；
          · 否则（没 END / END 无 result）→ 设备状态未知：落 STALE，
            租约从此拒绝新命令（reason="device_unknown"）。
        探针命令另加一条：result=OK 才能把 STALE 解除（回 GATED）。
        """
        is_probe = SessionState.STALE in getattr(instance.spec, "requires", ())
        target, reason = self._verdict(is_probe, ended, result)
        state = self._lease.state
        if state is SessionState.BUSY:
            if target is SessionState.GATED:
                state = self._lease.end_command()
            else:
                state = self._lease.enter_stale(reason)
                self._kernel.log("err", "设备状态未知（%s）→ STALE：%s"
                                 % (instance.spec.name, reason))
        ticket = self._ticket
        self._ticket = None
        if ended:
            self._gate.arm()
        if ticket is None:
            return
        failure = instance.failure
        ticket.resolve(CommandResult(
            name=instance.spec.name, ok=failure is None, state=state,
            failure=failure, rounds=self._rounds,
            detail=getattr(instance, "result_text", "") or reason or ""))
        # 没收到 END 就结束的命令（超时 / 设备错误）意味着设备侧状态未知：
        # 队列里的下一条命令不得再发进去——那会把等待预算耗在已经停摆的
        # 设备上（实机 14:44：UFS 超时后继续发 DA-INFO，白等 3 s 并被丢弃）。
        if failure is None and state is not SessionState.STALE:
            self._activate_pending()

    def _verdict(self, is_probe, ended, result):
        """回合终态裁决：返回 (目标状态, 原因串)。

        STALE 的两个触发条件（缺一不可的否定形式）：
          ① 未收到 CMD:END（含 TIMEOUT / 设备中途静默 / 数据收满但无 END）；
          ② 收到了 CMD:END 但 result 为空——没表态同样算未知。
        探针在 STALE 下执行：只有 result=OK 才允许解除。
        """
        text = (result or "").strip()
        if not ended:
            detail = getattr(self._kernel, "last_round_gap", "") or ""
            return (SessionState.STALE,
                    "回合未收到 CMD:END%s" % (("：" + detail) if detail else ""))
        if not is_decided_result(text):
            return (SessionState.STALE, "回合尾 CMD:END 缺 result")
        if is_probe and text.upper() != OK_RESULT:
            return (SessionState.STALE, "探针回 result=%r" % (text,))
        return (SessionState.GATED, "")
