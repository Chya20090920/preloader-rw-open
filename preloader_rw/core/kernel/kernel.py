# -*- coding: utf-8 -*-
"""内核总装：两段式。

第一阶段 = 传输层 + 路线载荷。内核只做机械动作：打开设备、EP0/握手/探针、
DA1 上传、建帧通道与会话，然后执行载荷给的路线（DA1 前置命令、BOOT-TO、
2nd-DA 供数、注册）。内核自身不决定序列、不主动发任何命令。
第二阶段 = 载荷退出后，由壳经命令循环投递意图；内核只做循环体。

**内核认识哪些命令来自命令表**（`KernelConfig.table`，默认
`preloader_rw/profiles/mtk_official.json`）：命令名、拼帧方式、回合内的
`<info>` / `<key>`、注册探针、传输符号都在表里。本文件与 session 里不出现
任何具体命令名——换一份 DA 固件换的是表与载荷，不是这里。
"""

from dataclasses import dataclass, field
from typing import Optional, Sequence

import threading

from ..engine.events import SessionState
from ..engine.lease import SessionLease
from ..proto import bootstrap as boot
from ..proto import da
from ..proto import redact
from ..proto.channel import Channel
from ..proto.const import OK
from . import commands as command_book
from .clock import SystemClock
from .register import RegisterPhase
from .session import KernelSession, KernelError


@dataclass
class KernelConfig:
    da1: bytes
    da2: bytes
    pids: Sequence[int] = field(default_factory=lambda: (0x2000,))
    beat: float = 0.041
    chunk: int = 0x200000
    read_ms: int = 20
    xfer_timeout_ms: int = 10000
    upload_timeout_s: float = 10.0
    attach: bool = False
    # 命令表：内核认识哪些命令、帧怎么拼、探针发哪条。None = 仓库自带默认表。
    # 换一份 DA 固件 = 换一张表（外加载荷），内核代码不动。
    table: object = None
    # 2nd-DA 下载参数：由载荷调用 serve_da2 时使用，内核不在引导阶段发它。
    # None = 取表里 boot 段的默认值。
    boot_at: Optional[int] = None
    boot_jmp: Optional[int] = None
    da2_mem_addr: Optional[int] = None


class Kernel(object):
    def __init__(self, backend, input_provider, config, clock=None, log=None):
        self.backend = backend
        self.input = input_provider
        self.config = config
        self.clock = clock or SystemClock()
        self.log = log or (lambda level, text: None)
        # 表在内核里只有一份：会话、注册裁决、探针共用同一张。
        self.table = config.table if config.table is not None \
            else command_book.default_table()

        self.device = None
        self.info = None
        self.channel = None
        self.session = None
        self.lease = SessionLease()
        self.registered = False
        self._runner = None
        self._thread = None
        self._loop_error = None

    # ---- 引导 -------------------------------------------------------

    @property
    def state(self):
        return self.lease.state

    def _log_line(self, text):
        self.log("info", text)

    def wait_device(self, timeout_s=0.0, on_waiting=None):
        """等目标设备出现。on_waiting 在第一次 find 之前调用一次。

        壳用 on_waiting 触发 fastboot → EDL 切换：先让内核进入等待，再切。
        """
        if on_waiting is not None:
            on_waiting()
        deadline = None if timeout_s <= 0 else self.clock.now() + timeout_s
        started = self.clock.now()
        last_log = started
        last_err = ""
        while True:
            dev, info, err = self.backend.find(self.config.pids)
            if dev is not None:
                self.log("ok", "设备已出现（等待 %.2fs）" % (self.clock.now() - started))
                return dev, info
            last_err = err or "no device"
            now = self.clock.now()
            if now - last_log >= 5.0:
                try:
                    seen = ["%04x:%04x" % (d.vid, d.pid) for d in self.backend.list()]
                except Exception as exc:
                    seen = ["<枚举失败 %r>" % (exc,)]
                self.log("info", "等待设备 %s 已 %.1fs；当前枚举: %s（%s）"
                         % (list(self.config.pids), now - started,
                            ", ".join(seen) or "(空)", last_err))
                last_log = now
            if deadline is not None and now >= deadline:
                raise KernelError("等待设备超时: %s" % last_err)
            self.clock.sleep(0.05)

    def bootstrap(self, device_timeout_s=0.0, on_waiting=None):
        """铺传输层：EP0 → 握手 → 探针 → DA1 上传 → 建帧通道与会话。

        只做一次。本方法不发任何命令：DA1 前置命令、BOOT-TO、2nd-DA 供数
        全部由载付在 `run_route` 里经原语决定。
        """
        if self.lease.state is not SessionState.ABSENT:
            raise KernelError("引导只能做一次，当前 %s" % (self.lease.state.value,))

        dev, info = self.wait_device(device_timeout_s, on_waiting)
        self.device = dev
        self.info = info
        self.lease.mark_present()
        self.lease.acquire()
        dev.claim(0)
        dev.claim(1)
        self.lease.enter_bootstrapping()
        self.log("info", self.table.summary())

        if not self.config.attach:
            self.log("info", "阶段: EP0")
            boot.ep0(dev, self.clock)
            self.log("info", "阶段: 握手")
            if not boot.handshake(dev, self.clock, log=self._log_line):
                raise KernelError("preloader 握手失败")
            self.log("info", "阶段: 探针")
            boot.probes(dev, self.clock, log=self._log_line)
            self.log("info", "阶段: DA1 上传")
            self._push_da1()
        else:
            self.log("info", "附加模式：跳过 EP0/DA1/DA2")

        self.channel = Channel(
            dev, self.clock,
            beat=self.config.beat,
            chunk=self.config.chunk,
            read_ms=self.config.read_ms,
            xfer_timeout_ms=self.config.xfer_timeout_ms,
            log=self._log_line,
        )
        boot = self.table.boot
        boot_at = boot.at if self.config.boot_at is None else self.config.boot_at
        boot_jmp = boot.jmp if self.config.boot_jmp is None else self.config.boot_jmp
        da2_mem = (boot.da2_mem if self.config.da2_mem_addr is None
                   else self.config.da2_mem_addr)
        self.session = KernelSession(
            self.channel, self.clock, self.input,
            log=self.log,
            supply_chunk=self.config.chunk,
            read_ms=self.config.read_ms,
            upload_timeout_s=self.config.upload_timeout_s,
            attached=self.config.attach,
            da2=self.config.da2,
            boot_at=boot_at,
            boot_jmp=boot_jmp,
            da2_mem_addr=da2_mem,
            table=self.table,
        )
        # DA1/2nd-DA 上传阶段由 da.py 直接读 IN；常驻读线程必须在上传结束后
        # 再起，否则两条读会抢同一端点的回显字节。非附加模式下由
        # session.serve_da2() 在供数完成后拉起；附加模式没有上传段，此处直接起。
        if self.config.attach:
            self.channel.start_reader()
        return self

    def _push_da1(self):
        raw = self.config.da1
        if not raw:
            raise KernelError("DA1 为空")
        self.log("info", redact.asset_identity("da1", raw))
        da.send_header(self.device, self.clock, len(raw), log=self._log_line)
        da.upload_da1(self.device, self.clock, raw, log=self._log_line)
        status = da.wait_ready(self.device, self.clock, log=self._log_line)
        if status[:2] != b"\xb0\xcc":
            raise KernelError("DA1 未获就绪回执（len=%d）" % len(status))
        if not da.finish(self.device, self.clock, log=self._log_line):
            raise KernelError("DA1 收尾回显失败")

    # ---- 第一阶段：载付驱动 -----------------------------------------

    def run_route(self, route_path):
        """执行路线载荷（第一阶段总入口），返回时租约已进入 GATED。

        载荷拿到的是 KernelSession 的原语面：命令序列、节奏、走到哪一步都由
        载荷决定。**注册判据不在这里、也不在载荷手里**：RegisterPhase 在载荷
        返回后用固定探针（GET-DA-INFO / GET-HW-INFO 至少一条回 END OK）裁决，
        载荷自述的 `enter_loop()` 只作日志。
        """
        if self.session is None:
            raise KernelError("先 bootstrap")
        phase = RegisterPhase(self.session, self.lease, log=self.log,
                              table=self.table)
        try:
            phase.run(route_path)
        except KernelError:
            raise
        except Exception as exc:
            self.log("err", "路线阶段异常: %r" % (exc,))
            raise
        self.registered = True
        return self

    # ---- 命令循环 ---------------------------------------------------

    def loop(self, dispatcher, commands=None, on_response=None, max_rounds=None,
             stop_when_done=False):
        from ..engine.loop import CommandLoop

        if not self.registered:
            raise KernelError("会话未注册，拒绝进入命令循环")
        runner = CommandLoop(self.session, dispatcher, self.lease,
                             on_response=on_response,
                             stop_when_done=stop_when_done, ack=OK,
                             start_gate=self.session.start_gate)
        runner.queue(commands or ())
        try:
            return runner.run(max_rounds=max_rounds)
        finally:
            runner.fail_pending(runner.lease.state, "命令循环已停止")

    def start_loop(self, dispatcher, on_response=None, on_exit=None):
        """在后台线程跑命令循环；之后可用 submit 从其他线程投递意图。

        on_exit(error, state) 在循环线程收尾后调用一次：壳据此把会话落到
        DEAD，而不是继续显示「已就绪」。
        """
        from ..engine.loop import CommandLoop

        if not self.registered:
            raise KernelError("会话未注册，拒绝进入命令循环")
        if self._runner is not None:
            raise KernelError("命令循环已在运行")
        self._runner = CommandLoop(self.session, dispatcher, self.lease,
                                   on_response=on_response, ack=OK,
                                   start_gate=self.session.start_gate)
        self._loop_error = None

        def _run():
            runner = self._runner
            error = None
            try:
                runner.run()
            except Exception as exc:  # 循环线程异常必须被壳看见
                error = exc
                self._loop_error = exc
            finally:
                # 循环无论怎样收尾，未结算的票据都要结算，否则调用方永远等。
                runner.fail_pending(runner.lease.state, "命令循环已停止")
                if on_exit is not None:
                    try:
                        on_exit(error or runner.last_error or None,
                                runner.lease.state)
                    except Exception:
                        pass

        self._thread = threading.Thread(target=_run, name="mtkpr-loop", daemon=True)
        self._thread.start()
        return self

    def submit(self, name, args=None):
        """投递一条命令，返回完成票据（CommandTicket）。

        只做投递与拒绝分类；完成回执由票据给出（裁决 / 失败分类 / 终态）。
        BUSY 与状态不符一律抛 LeaseError，不产生票据。
        """
        if self._runner is None:
            raise KernelError("命令循环未启动")
        if self._loop_error is not None:
            raise KernelError("命令循环已异常退出: %r" % (self._loop_error,))
        return self._runner.submit(name, args)

    def run_command(self, name, args=None):
        """同步面：投递一条命令并等它结算，返回 CommandResult。

        不设时间预算——会话死掉也会结算票据，调用方不会挂住。
        """
        ticket = self.submit(name, args)
        return ticket.wait()

    # ---- STALE 的显式解除 -------------------------------------------

    def probe_alive(self, name="GET-DA-INFO", target_file=None, save_path=""):
        """STALE 的唯一解除通道：外壳显式发起的一次指定探针。

        语义（与 engine.lease 一致）：
          · 只有设备状态未知（STALE）时才需要走这里；
          · 探针命令必须是规格里显式声明 `requires` 含 STALE 的那条
            （product 层的 GET-DA-INFO），因此它能穿过租约的 device_unknown 拒绝；
          · **只有**该回合收到 `CMD:END result=OK` 才算确认存活——那时循环的
            `_settle` 已经把租约从 STALE 解回 GATED；其余结果留在 STALE。

        返回 True 表示已确认存活（租约回到 GATED）。
        """
        if self._runner is None:
            raise KernelError("命令循环未启动")
        if self._loop_error is not None:
            raise KernelError("命令循环已异常退出: %r" % (self._loop_error,))
        if self.lease.state is not SessionState.STALE:
            # 不是 STALE 就不需要（也不允许）走这条通道：普通命令本来就能发。
            return self.lease.state is SessionState.GATED
        detail = self.lease.stale_cause or "device_unknown"
        # 探针目标取当前表的配置：换表就是换命令面，不能还拿默认表的目标。
        target = target_file or self.table.probe_target()
        if not target:
            self.log("err", "命令表 %s 没有配探针目标，无法确认设备存活"
                     % (self.table.name,))
            return False
        self.log("info", "设备状态未知（%s）→ 发探针 %s 确认存活" % (detail, name))
        args = {"target_file": target}
        if save_path:
            args["save_path"] = save_path
        try:
            result = self.run_command(name, args)
        except Exception as exc:
            self.log("err", "探针 %s 未能发出: %r" % (name, exc))
            return False
        alive = bool(result.ok) and self.lease.state is SessionState.GATED
        self.log("ok" if alive else "err",
                 "探针 %s 裁决: ok=%r state=%s%s"
                 % (name, result.ok, getattr(result.state, "value", result.state),
                    (" detail=%r" % (result.detail,)) if result.detail else ""))
        return alive

    def stop_loop(self):
        if self._runner is not None:
            self._runner.stop()
            self._runner.fail_pending(self._runner.lease.state, "命令循环被停止")
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._runner = None
        self._thread = None

    # ---- 收尾 -------------------------------------------------------

    def close(self):
        if self.device is None:
            return
        if self.channel is not None:
            try:
                self.channel.stop_reader()
            except Exception:
                pass
        try:
            self.device.release(1)
            self.device.release(0)
        except Exception:
            pass
        try:
            self.device.close()
        finally:
            self.device = None
            self.channel = None
