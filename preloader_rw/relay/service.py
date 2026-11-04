# -*- coding: utf-8 -*-
"""中继壳：装配输入、把设备送进内核 GATED，然后长期持有会话。

它不构造协议帧、不决定命令字节；只做设备生命周期与意图翻译。
"""

import dataclasses
import os
import threading
import time

from . import preflight as preflight_mod
from .config import RelayConfig, ConfigError
from .events import (CommandEvent, Fire, HealthReport, HostInput, IntentResult,
                     LogEvent, StateEvent)
from .inputs import HostInputCancelled, HostInputTimeout, RuntimeInput
from .states import RelayMachine, RelayState, TransitionError
from preloader_rw.core.proto.redact import mask_serial

# 宿主取消运行时索取的队列哨兵。
_INPUT_CANCEL = object()


class RelayError(RuntimeError):
    pass


def _is_file(path):
    return bool(path) and os.path.isfile(path)


# 批量读 = 逐块 READ-FLASH。一条命令的 <length> 是 0x800000 时，设备内部
# 仍按 2 MiB 块（packet_length 0x200000）上传；0x10000 只用于 GPT/元数据，
# 大分区读用 0x800000。
READ_CHUNK = 0x800000


class RelayService(object):
    def __init__(self, config, kernel_factory, backend_factory,
                 fastboot_cli=None, watcher=None, clock=None, emit=None,
                 table_holder=None, registry_factory=None, runs_root=None,
                 run_dir=None):
        if not isinstance(config, RelayConfig):
            raise ConfigError("config 必须是 RelayConfig")
        self.config = config
        self._kernel_factory = kernel_factory
        self._backend_factory = backend_factory
        self._clock = clock or _SystemClock()
        self._emit_raw = emit or (lambda event: None)
        self._table_holder = table_holder
        self._registry_factory = registry_factory
        self._runs_root = runs_root
        self._run_dir = run_dir

        self.bundle = None
        self.kernel = None
        self.health = HealthReport(source="none", values={})
        self.events = []
        self.machine = RelayMachine(emit=self._on_state)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._last_error = ""
        self._subscribers = []
        self._device_watch = None
        self._input_seq = 0
        self._input_queues = {}

        self._fastboot = fastboot_cli
        self._watcher = watcher

    # ---- 依赖延迟加载 -------------------------------------------------

    def _cli(self):
        if self._fastboot is None:
            from .fastboot.cli import FastbootCli
            self._fastboot = FastbootCli()
        return self._fastboot

    def _fb_watcher(self):
        if self._watcher is None:
            from .fastboot.watcher import FastbootWatcher
            self._watcher = FastbootWatcher(self._cli(), interval=self.config.poll_s)
        return self._watcher

    def _partitions(self):
        from preloader_rw.core.product.official import partitions
        return partitions

    def _holder(self):
        """运行期分区表持有者：读命令写入，写命令取用。"""
        if self._table_holder is None:
            self._table_holder = self._partitions().PartitionTableHolder()
        return self._table_holder

    @property
    def table(self):
        """设备上传的分区表；未取得时为 None。"""
        if self._table_holder is None:
            return None
        return self._table_holder.get()

    def _build_registry(self):
        if self._registry_factory is not None:
            return self._registry_factory
        from preloader_rw.core.product.registry import build_registry
        return build_registry

    # ---- 事件 -------------------------------------------------------

    def subscribe(self, callback):
        """注册事件订阅者（GUI 管道）。订阅者异常不得影响壳运行。"""
        self._subscribers.append(callback)

    def _emit(self, event):
        if hasattr(event, "ts"):
            event = dataclasses.replace(event, ts=time.time())
        self.events.append(event)
        self._emit_raw(event)
        for callback in list(self._subscribers):
            try:
                callback(event)
            except Exception:
                pass

    def emit_kernel_log(self, level, text):
        """把内核边界日志并进事件流（等待期即存活信号）。"""
        self._emit(LogEvent(level, text, source="kernel"))

    def _on_state(self, raw):
        _tag, state, reason = raw
        self._emit(StateEvent(state=state, reason=reason))

    def _set(self, target, reason=""):
        with self._lock:
            return self.machine.enter(target, reason)

    # ---- 预检 -------------------------------------------------------

    def preflight(self):
        result = preflight_mod.run(self.config)
        if isinstance(result, preflight_mod.PreflightFail):
            self._last_error = "%s: %s" % (result.code, result.detail)
            self._emit(LogEvent("err", "preflight 失败 -> " + self._last_error))
            return result
        self.bundle = result.bundle
        self._emit(LogEvent("ok", "preflight 通过"))
        return result

    # ---- 生命周期 ---------------------------------------------------

    def run_once(self, fastboot_present=None):
        """同步跑一次完整生命周期；返回结束状态。测试与 CLI 用。"""
        if self.bundle is None:
            result = self.preflight()
            if isinstance(result, preflight_mod.PreflightFail):
                self._last_error = result.code
                self._set(RelayState.FAILED, result.code)
                return self.machine.state

        self._set(RelayState.LISTENING, "start")
        # 上一次会话可能已死：先把旧内核（含设备句柄）收掉，重新发射才能重开设备。
        self._close_kernel()
        try:
            present = self._detect_fastboot(fastboot_present)
            if present:
                self._health_round()
            else:
                self._set(RelayState.KERNEL_WAIT, "无 fastboot")
                self._boot(on_waiting=None)
        except Exception as exc:
            self._last_error = repr(exc)
            self._emit(LogEvent("err", "会话失败: %r" % (exc,)))
            if self.machine.state is not RelayState.FAILED:
                self._set(RelayState.FAILED, str(exc))
        return self.machine.state

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            raise RelayError("壳已在运行")
        self._stop.clear()
        self._thread = threading.Thread(target=self.run_once, name="relay", daemon=True)
        self._thread.start()
        return self

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    def stop(self):
        self._stop.set()
        self._close_kernel()
        if self.machine.state not in (RelayState.FAILED, RelayState.DEAD):
            try:
                self._set(RelayState.DEAD, "stop")
            except Exception:
                pass

    def _close_kernel(self):
        """停循环并释放设备句柄；重复调用安全。"""
        kernel = self.kernel
        self.kernel = None
        if kernel is None:
            return
        try:
            kernel.stop_loop()
        except Exception:
            pass
        try:
            kernel.close()
        except Exception:
            pass

    # ---- 两条路径 ---------------------------------------------------

    def _detect_fastboot(self, forced):
        if forced is not None:
            return bool(forced)
        if not self.config.fastboot_enabled:
            return False
        watcher = self._fb_watcher()
        return bool(watcher.wait(self.config.fastboot_timeout_s))

    def _health_round(self):
        from .fastboot.edl import enter_edl
        from .fastboot.health import probe

        self._set(RelayState.FB_PROBING, "fastboot 在位")
        cli = self._cli()
        values = probe(cli)
        self.health = HealthReport(source="fastboot", values=values)
        self._emit(self.health)
        self._set(RelayState.FB_READY, "健康采集完成")
        self._set(RelayState.KERNEL_WAIT, "等内核就绪后切 EDL")
        self._boot(on_waiting=lambda: self._trigger_edl(enter_edl, cli))

    def _trigger_edl(self, enter_edl, cli):
        """发出 EDL 切换后立即返回，轮询由 wait_device 同步开始。

        fastboot oem reboot-edl 是同步子进程（最长等 3 s）。若在轮询前等它
        退出，设备的 preloader 窗口会被整个错过。这里把命令放到独立线程，
        主线程立刻进入设备枚举。
        """
        self._emit(LogEvent("info", "内核已就绪，触发 fastboot oem reboot-edl"))

        def run():
            try:
                enter_edl(cli)
                self._emit(LogEvent("info", "reboot-edl 已发出"))
            except Exception as exc:
                self._emit(LogEvent("err", "reboot-edl 失败: %r" % (exc,)))

        threading.Thread(target=run, name="relay-edl", daemon=True).start()

    def _boot(self, on_waiting):
        backend = self._backend_factory()
        runtime_input = RuntimeInput(self.bundle)
        runtime_input.on_request = self._request_host_input
        kernel = self._kernel_factory(self.bundle, backend, runtime_input)
        self.kernel = kernel
        self._set(RelayState.BOOTSTRAPPING, "第一阶段：载付驱动")
        self._emit(LogEvent("info", "引导开始"))
        kernel.bootstrap(device_timeout_s=self.config.device_timeout_s,
                         on_waiting=on_waiting)
        self._emit(LogEvent("ok", "传输层就绪（DA1 已上传）"))
        self._emit(LogEvent("info", "载付开始推进第一阶段"))
        # 陈旧 GPT 不得冒充本轮产物：先清，再让载荷自己决定要不要重读。
        try:
            os.remove(os.path.join("downloads", "gpt.bin"))
        except OSError:
            pass
        kernel.run_route(self.bundle.route)
        self._emit(LogEvent("ok", "第一阶段完成：命令循环已移交"))

        # 分区表由载荷在工作流里读好（默认载荷：READ-FLASH 直读 GPT 落盘
        # downloads/gpt.bin）。壳不决定读不读、怎么读，只解析落盘文件：
        # 文件在就解析进 holder；不在就保持空表，写意图会以 NO_TABLE 拒绝。
        holder = self._holder()
        partitions = self._partitions()
        gpt_path = os.path.join("downloads", "gpt.bin")
        if os.path.isfile(gpt_path):
            with open(gpt_path, "rb") as handle:
                table = partitions.from_gpt(handle.read())
            holder.set(table)
            self._emit(LogEvent("ok", "设备 GPT: %d 条（CRC 校验通过）" % len(table)))
        else:
            self._emit(LogEvent("warn", "载荷未产出 GPT（downloads/gpt.bin）："
                                        "分区读写将不可用"))
        registry = self._build_registry()(holder)

        # 分区表阶段若把会话弄死（设备 halt / 传输异常），不能宣布“就绪”。
        # 同理，若会话落到 STALE（回合没有明确结果），设备状态未知：也不许
        # 宣布就绪——那正是「设备已死、壳认为就绪」的入口。
        state = getattr(kernel.lease, "state", None)
        state_value = getattr(state, "value", "")
        if state_value == "dead":
            raise IOError("设备信息读取期间会话终止（%s）" % self._last_error)
        if state_value and state_value not in ("gated", "busy"):
            raise IOError("设备信息读取期间会话状态未知（%s），拒绝宣布就绪"
                          % state_value)

        self._emit(LogEvent("info", "启动命令循环"))
        kernel.start_loop(registry, on_exit=self._on_loop_exit)
        try:
            self._set(RelayState.GATED, "命令循环就绪")
        except TransitionError:
            # 循环在置 GATED 之前就已终止（回调已把状态落到 DEAD）。
            return
        self._emit(LogEvent("ok", "已就绪，可接受读写意图"))

    def _on_loop_exit(self, error, state):
        """命令循环线程收尾：会话不再可用，状态必须离开 GATED。"""
        detail = ("命令循环异常终止: %r" % (error,)) if error \
            else "命令循环已退出: %s" % getattr(state, "value", state)
        self._last_error = detail
        self._emit(LogEvent("err", detail))
        try:
            if self.machine.state not in (RelayState.DEAD, RelayState.FAILED):
                self._set(RelayState.DEAD, detail)
        except TransitionError:
            pass

    def _runs_dir(self):
        if self._run_dir is None:
            from preloader_rw.core.engine.runlog import new_run_dir
            self._run_dir = str(new_run_dir(self._runs_root))
        return self._run_dir

    # ---- 运行时索取（载荷 → 壳 → 宿主） ------------------------------

    def _request_host_input(self, kind, info):
        """载荷在运行途中索取输入：发事件并阻塞等宿主应答。

        三种结局分明：拿到数据（返回 bytes）、超时（HostInputTimeout）、
        取消（HostInputCancelled）。没有第四种——「用户没给」永远不可能
        被当成「设备拒绝」。
        """
        import queue as _queue

        timeout_s = float(os.environ.get("PANE_INPUT_TIMEOUT_S", "300"))
        with self._lock:
            self._input_seq += 1
            req_id = self._input_seq
            channel = _queue.Queue()
            self._input_queues[req_id] = channel
        self._emit(HostInput(kind=kind, info=info,
                             req_id=req_id, timeout_s=timeout_s))
        try:
            item = channel.get(timeout=timeout_s)
        except _queue.Empty:
            raise HostInputTimeout(
                "宿主在 %.0f s 内未提供 %s（超时）" % (timeout_s, kind))
        finally:
            with self._lock:
                self._input_queues.pop(req_id, None)
        if item is _INPUT_CANCEL:
            raise HostInputCancelled("宿主取消了 %s 输入" % (kind,))
        return item

    # ---- 意图 -------------------------------------------------------

    def detect_device(self):
        """枚举一次 USB 连接状态：临时后端，试开即关，不影响会话。"""
        try:
            backend = self._backend_factory()
        except Exception as exc:
            return IntentResult(False, "DETECT", "后端不可用: %s" % (exc,))
        try:
            detect = getattr(backend, "detect", None)
            if detect is None:
                return IntentResult(False, "DETECT", "当前后端不支持枚举")
            return IntentResult(True, "OK", detect())
        except Exception as exc:
            return IntentResult(False, "DETECT", str(exc))
        finally:
            try:
                backend.close()
            except Exception:
                pass

    def fire(self, intent=None):
        """触发一次完整生命周期；不阻塞调用方（引导在独立线程里跑）。

        UNKNOWN（设备状态未知）也允许重开：它会关掉旧内核、重建传输层，
        比在未知设备上继续发命令安全。

        intent 可携带 da1/da2 镜像路径；携带即校验文件存在并重排配置、
        强制重新预检。缺 DA 时预检在 fire 同步执行并直接回 CONFIG 错误
        ——可读、可修正；壳不因启动缺配置而退出。
        """
        da1 = str(getattr(intent, "da1", "") or "").strip()
        da2 = str(getattr(intent, "da2", "") or "").strip()
        payload = str(getattr(intent, "payload", "") or "").strip()
        table = str(getattr(intent, "table", "") or "").strip()
        if payload:
            if not os.path.isfile(payload) or os.path.getsize(payload) == 0:
                return IntentResult(
                    False, "CONFIG",
                    "载荷文件不存在或为空: %s" % payload)
            if os.path.splitext(payload)[1].lower() not in preflight_mod.ROUTE_SUFFIXES:
                return IntentResult(
                    False, "CONFIG",
                    "载荷后缀不支持: %s" % os.path.splitext(payload)[1])
        if table:
            if not os.path.isfile(table) or os.path.getsize(table) == 0:
                return IntentResult(
                    False, "CONFIG",
                    "命令表不存在或为空: %s" % table)
            if os.path.splitext(table)[1].lower() not in preflight_mod.TABLE_SUFFIXES:
                return IntentResult(
                    False, "CONFIG",
                    "命令表后缀必须是 .json: %s" % os.path.splitext(table)[1])
        if da1 or da2 or payload or table:
            import dataclasses
            for label, path in (("da1", da1), ("da2", da2)):
                if path and not (os.path.isfile(path) and os.path.getsize(path) > 0):
                    return IntentResult(
                        False, "CONFIG",
                        "%s 镜像不存在或为空: %s（在资源页选择，或配置 PANE_DA1/PANE_DA2）"
                        % (label.upper(), path))
            self.config = dataclasses.replace(
                self.config,
                da1=da1 or self.config.da1, da2=da2 or self.config.da2,
                route=payload or self.config.route,
                table=table or self.config.table)
            self.bundle = None
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return IntentResult(False, "ALREADY", "会话已在运行")
            if self.machine.state not in (RelayState.BOOT, RelayState.DEAD,
                                          RelayState.FAILED,
                                          RelayState.UNKNOWN):
                return IntentResult(False, "ALREADY",
                                    "当前状态 %s" % self.machine.state.value)
            if self.bundle is None:
                # 缺配置的错误在 fire 应答里直接给出，而不是让线程跑出去再失败。
                result = self.preflight()
                if isinstance(result, preflight_mod.PreflightFail):
                    return IntentResult(
                        False, "CONFIG",
                        "%s（%s；在资源页选择 DA 镜像，或配置 PANE_DA1/PANE_DA2）"
                        % (result.code, result.detail))
            try:
                self.start()
            except Exception as exc:
                return IntentResult(False, "FAILED", str(exc))
            return IntentResult(True, "FIRED", "")

    def recover(self):
        """设备状态未知时的显式探针：确认存活才把壳解回 GATED。

        这是 UNKNOWN 回到 GATED 的唯一路径（内核侧对应
        `Kernel.probe_alive` → 租约 `confirm_alive`）。判据只有一条：
        探针回合尾收到 `CMD:END result=OK`。
        """
        with self._lock:
            kernel = self.kernel
            if kernel is None:
                return IntentResult(False, "NO_SESSION", "没有活动会话")
            try:
                state = getattr(kernel.lease, "state", None)
                state_value = getattr(state, "value", "")
            except Exception:
                return IntentResult(False, "NO_SESSION", "会话状态不可读")
            if state_value == "gated":
                with self._lock:
                    if self.machine.state is not RelayState.GATED:
                        self._set(RelayState.GATED, "探针：会话本来就可发命令")
                return IntentResult(True, "OK", "会话已就绪")
            if state_value != "stale":
                return IntentResult(False, "NOT_STALE",
                                    "当前会话状态 %s，无需探针" % (state_value or "?"))
            self._emit(LogEvent("info", "设备状态未知 → 发探针 GET-DA-INFO 确认存活"))
            try:
                alive = kernel.probe_alive()
            except Exception as exc:
                self._emit(LogEvent("err", "探针未能发出: %r" % (exc,)))
                return IntentResult(False, "PROBE_FAILED", repr(exc))
            if not alive:
                self._emit(LogEvent("err", "探针未确认存活，仍是 UNKNOWN"))
                return IntentResult(False, "PROBE_FAILED", "探针未收到 CMD:END result=OK")
            try:
                self._set(RelayState.GATED, "探针确认设备存活")
            except Exception as exc:
                return IntentResult(False, "STATE", repr(exc))
            self._emit(LogEvent("ok", "探针确认设备存活，恢复就绪"))
            return IntentResult(True, "OK", "探针确认设备存活")

    def _read_device_info(self):
        """读一次完整设备信息并推 health 事件；失败返回 False。"""
        from .fastboot.health import probe as health_probe
        try:
            values = health_probe(self._cli())
        except Exception as exc:
            self._emit(LogEvent("err", "读取设备信息失败: %r" % (exc,)))
            return False
        with self._lock:
            self.health = HealthReport(source="fastboot", values=values)
        self._emit(self.health)
        self._emit(LogEvent("ok", "设备信息: 产品 %s · 序列号 %s · 槽位 %s · 已解锁 %s"
                            % (values.get("product"), mask_serial(values.get("serialno")),
                               values.get("current-slot"), values.get("unlocked"))))
        return True

    def probe_device(self):
        """只读读取设备信息：fastboot 在位判定 + 逐键 getvar。

        不切 EDL、不引导、不占用传输端点；任何状态下都可调用。
        """
        if self._thread is not None and self._thread.is_alive():
            return IntentResult(True, "RUNNING", self.health.values)
        try:
            present = self._detect_fastboot(None)
        except Exception as exc:
            return IntentResult(False, "PROBE_FAILED", repr(exc))
        if not present:
            with self._lock:
                self.health = HealthReport(source="none", values={})
            self._emit(self.health)
            self._emit(LogEvent("warn", "未检测到 fastboot 设备"))
            return IntentResult(True, "NO_DEVICE", "未检测到 fastboot 设备")
        if not self._read_device_info():
            return IntentResult(False, "PROBE_FAILED", "读取设备信息失败")
        return IntentResult(True, "OK", self.health.values)

    def start_device_watch(self, interval_s=3.0):
        """只读插拔轮询：先启动工具、后插设备也能识别；拔掉也会被反映。"""
        if self._device_watch is not None and self._device_watch.is_alive():
            return
        self._device_watch = threading.Thread(
            target=self._device_watch_loop, args=(float(interval_s),),
            name="relay-device-watch", daemon=True)
        self._device_watch.start()

    def _device_watch_loop(self, interval_s):
        """轮询只在「尚无会话」时进行（BOOT / DEAD）。

        一旦 fire 过，设备就交给内核独占；此时再起 `fastboot.exe` 会去抢设备、
        足以打断正在进行的 bulk 传输。所以 GATED / BUSY / 过渡态一律停轮询。
        """
        last = None
        while not self._stop.is_set():
            idle_state = self.machine.state in (RelayState.BOOT, RelayState.DEAD)
            busy = self._thread is not None and self._thread.is_alive()
            if idle_state and not busy:
                try:
                    present = bool(self._cli().present(
                        timeout=max(1.0, min(interval_s, 3.0))))
                except Exception:
                    present = False
                if present != last:
                    last = present
                    if present:
                        self._emit(LogEvent("ok", "检测到 fastboot 设备（插拔轮询）"))
                        self._read_device_info()
                    else:
                        with self._lock:
                            self.health = HealthReport(source="none", values={})
                        self._emit(self.health)
                        self._emit(LogEvent("warn", "fastboot 设备已离开"))
            time.sleep(interval_s)

    def submit(self, intent):
        from . import events as E
        if isinstance(intent, Fire):
            return self.fire(intent)
        if isinstance(intent, E.Detect):
            return self.detect_device()
        if isinstance(intent, E.ProvideInput):
            channel = self._input_queues.get(intent.req_id)
            if channel is None:
                return IntentResult(False, "NO_REQUEST",
                                    "没有等待中的索取（id=%d）" % intent.req_id)
            channel.put(bytes(intent.data))
            return IntentResult(True, "OK", "")
        if isinstance(intent, E.CancelInput):
            channel = self._input_queues.get(intent.req_id)
            if channel is None:
                return IntentResult(False, "NO_REQUEST",
                                    "没有等待中的索取（id=%d）" % intent.req_id)
            channel.put(_INPUT_CANCEL)
            return IntentResult(True, "OK", "")
        if isinstance(intent, (E.ReadInfo, E.ProbeHealth, E.ProbeDevice)):
            # 只读查询不依赖会话状态：GUI 启动时就要拿到快照与设备信息。
            with self._lock:
                if isinstance(intent, E.ProbeDevice):
                    # 会话处于 UNKNOWN 时，「探测设备」应走内核探针（唯一能把
                    # 状态解回 GATED 的动作），而不是 fastboot getvar。
                    if self.machine.state is RelayState.UNKNOWN and self.kernel is not None:
                        return self.recover()
                    return self.probe_device()
                return self._dispatch(intent)
        with self._lock:
            if self.machine.state is not RelayState.GATED:
                return IntentResult(False, "NOT_READY",
                                    "当前状态 %s" % self.machine.state.value)
            try:
                return self._dispatch(intent)
            except Exception as exc:
                reason = getattr(exc, "reason", "")
                code = "BUSY" if reason == "busy" else "FAILED"
                return IntentResult(False, code, str(exc))

    def _dispatch(self, intent):
        from . import events as E
        from preloader_rw.core.product.official.write import WriteRequest

        if isinstance(intent, E.WritePartition):
            if self.table is None:
                return IntentResult(False, "NO_TABLE", "未加载设备分区表")
            base, _size = self.table.at(intent.name)
            if base is None:
                return IntentResult(False, "NO_PART", intent.name)
            if not os.path.isfile(intent.image):
                return IntentResult(False, "NO_IMAGE", intent.image)
            ticket = self.kernel.submit("WRITE-PARTITION",
                                        WriteRequest(intent.name, intent.image))
            self._watch(ticket)
            return IntentResult(True, "RUNNING", "已提交写命令")
        if isinstance(intent, E.ErasePartition):
            if self.table is None:
                return IntentResult(False, "NO_TABLE", "未加载设备分区表")
            base, _size = self.table.at(intent.name)
            if base is None:
                return IntentResult(False, "NO_PART", intent.name)
            ticket = self.kernel.submit("ERASE-PARTITION",
                                        {"partition": intent.name})
            self._watch(ticket)
            return IntentResult(True, "RUNNING", "已提交擦除命令")
        if isinstance(intent, E.ReadPartition):
            if self.table is None:
                return IntentResult(False, "NO_TABLE", "未加载设备分区表")
            base, size = self.table.at(intent.name)
            if base is None:
                return IntentResult(False, "NO_PART", intent.name)
            if not intent.path:
                return IntentResult(False, "NO_PATH", "未给出输出路径")
            # 批量读 = 逐块 READ-FLASH；每块都是与官方序列同形的标准回合。
            # 不在 READ-PARTITION 上赌「整分区一次上传」的非官方形态。
            threading.Thread(
                target=self._read_partition,
                args=(intent.name, base, size, intent.path),
                name="relay-read", daemon=True).start()
            return IntentResult(True, "RUNNING", "已提交读命令")
        if isinstance(intent, E.ReadInfo):
            return IntentResult(True, "OK", self.snapshot())
        if isinstance(intent, E.ProbeHealth):
            return IntentResult(True, "OK", self.health.values)
        if isinstance(intent, E.Cancel):
            return IntentResult(False, "NOT_RUNNING", "MVP 不支持取消正在执行的命令")
        return IntentResult(False, "BAD_INTENT", repr(intent))

    def _watch(self, ticket):
        """命令完成回执：票据结算后推事件，并把终态回写壳状态机。

        壳不轮询内核内部状态；完成、失败、会话死掉三者的唯一终局都由票据
        给出。票据必然结算（循环收尾时也结算），所以这里不会挂住。
        """

        def _wait():
            result = ticket.wait()
            self._emit(CommandEvent(
                name=result.name,
                ok=result.ok,
                state=result.state.value,
                failure=result.failure.value if result.failure else "",
                rounds=result.rounds,
                detail=result.detail or "",
            ))
            if result.state.value == "dead":
                try:
                    self._set(RelayState.DEAD, "设备消失")
                except Exception:
                    pass
            elif result.state.value == "stale":
                # 设备状态未知：壳必须离开 GATED，否则它会继续接受读写意图，
                # 而内核那边每一条都会被 device_unknown 拒掉（等于空等）。
                try:
                    if self.machine.state not in (RelayState.DEAD,
                                                  RelayState.FAILED):
                        self._set(RelayState.UNKNOWN,
                                  "回合无明确结果：%s" % (result.detail or "缺 CMD:END"))
                except Exception:
                    pass

        threading.Thread(target=_wait, name="relay-command", daemon=True).start()

    def _read_partition(self, name, base, size, path):
        """整分区回读：逐块 READ-FLASH，每块一个标准上传回合，最后拼成一个文件。

        不使用 READ-PARTITION 的整分区上传：官方主机的序列是每 0x10000
        一条 READ-FLASH。逐块命令让传输层只处理一种形状，不需要为
        「多块上传」单独写状态机。
        """
        from preloader_rw.core.product.official.commands import STORAGE_LABEL

        part_dir = os.path.join(self._runs_dir(), "_read_%s" % name)
        parts = []
        rounds = 0
        total = 0
        try:
            os.makedirs(part_dir, exist_ok=True)
            self._emit(LogEvent("info", "读取 %s: %d B，按 0x%X 分块"
                                % (name, size, READ_CHUNK)))
            for offset in range(0, size, READ_CHUNK):
                length = min(READ_CHUNK, size - offset)
                part = os.path.join(part_dir, "%08X.bin" % offset)
                result = self.kernel.run_command("READ-FLASH", {
                    "partition": STORAGE_LABEL,
                    "offset": base + offset,
                    "length": length,
                    "target_file": path,
                    "save_path": part,
                })
                rounds += result.rounds
                if not result.ok:
                    raise IOError(
                        "块 0x%X 读取失败: %s %s"
                        % (offset,
                           result.failure.value if result.failure else "",
                           result.detail or ""))
                parts.append(part)
                total += length
            with open(path, "wb") as out:
                for part in parts:
                    with open(part, "rb") as fh:
                        out.write(fh.read())
            for part in parts:
                try:
                    os.remove(part)
                except OSError:
                    pass
            try:
                os.rmdir(part_dir)
            except OSError:
                pass
            self._emit(CommandEvent(
                name="READ-PARTITION", ok=True,
                state=self.machine.state.value, rounds=rounds,
                detail="读取 %s %d B" % (name, total)))
        except Exception as exc:
            state = self.machine.state.value
            dead = False
            stale = False
            try:
                dead = self.kernel.lease.state.value == "dead"
                stale = self.kernel.lease.state.value == "stale"
            except Exception:
                pass
            self._emit(CommandEvent(
                name="READ-PARTITION", ok=False, state=state,
                failure="READ_FAILED", rounds=rounds,
                detail="%s: %r" % (name, exc)))
            if dead and state != "dead":
                try:
                    self._set(RelayState.DEAD, "设备消失")
                except Exception:
                    pass
            elif stale and not dead and self.machine.state not in (
                    RelayState.DEAD, RelayState.FAILED):
                try:
                    self._set(RelayState.UNKNOWN, "读回合无明确结果")
                except Exception:
                    pass

    def snapshot(self):
        """GUI 开机取的一份完整态；不含事件流（事件流走 evt 通道）。"""
        rows = []
        if self.table is not None:
            rows = [{"name": name, "start": base, "size": size}
                    for name, base, size in self.table.entries]
        cfg = self.config
        resources = [{"role": "route", "path": cfg.route, "ok": _is_file(cfg.route)},
                     {"role": "da1", "path": cfg.da1, "ok": _is_file(cfg.da1)},
                     {"role": "da2", "path": cfg.da2, "ok": _is_file(cfg.da2)}]
        # 命令表：没选就是仓库自带那张，路径栏显示它的名字而不是空白。
        table_label = cfg.table
        if not table_label:
            try:
                from preloader_rw.core.kernel import commands as _commands
                table_label = "内置: %s" % _commands.default_table().name
            except Exception:
                table_label = "内置默认表"
        resources.append({"role": "table", "path": table_label,
                          "ok": (not cfg.table) or _is_file(cfg.table)})
        for kind, path in sorted((cfg.credentials or {}).items()):
            resources.append({"role": "credential:" + kind, "path": path,
                              "ok": _is_file(path)})
        return {
            "state": self.machine.state.value,
            "health": self.health.values,
            "error": self._last_error,
            "run_dir": self._run_dir or "",
            "resources": resources,
            "images": dict(cfg.images or {}),
            "backend": cfg.backend,
            "partition_count": len(rows),
            "partitions": rows,
        }


class _SystemClock(object):
    def now(self):
        return time.monotonic()

    def sleep(self, seconds):
        if seconds > 0:
            time.sleep(seconds)
