# -*- coding: utf-8 -*-
"""GUI 与壳的唯一通信边界：起 `python -m preloader_rw.relay --json` 子进程，跑 JSONL。

受众: 代理(GUI 端)
视角: 对读者称「你」；本件不出现「我」
禁入: 元指令 · 过程注解 · 自注括号 · 对第三方的话

不变量：
    · GUI 只在本件里碰子进程；其余 UI 代码只认信号与 `send()`。
    · 请求必带 id；响应按 id 交回发起者回调。
    · 壳的 stderr 只作诊断原样转发，不参与协议。
    · 命令完成只由 `command` 事件宣告；GUI 不轮询壳内部状态。
"""

import os
import sys

from PySide6.QtCore import QObject, QProcess, Signal

from . import protocol as P


# Windows: 强制子进程不带控制台窗口。Qt 默认走 CreateProcess 而不设该标志，
# 结果每起一个壳进程就闪一个黑窗口。
CREATE_NO_WINDOW = 0x08000000


def _hide_console(proc):
    try:
        def modify(args):
            args.flags |= CREATE_NO_WINDOW
        proc.setCreateProcessArgumentsModifier(modify)
    except Exception:
        pass


class RelayBridge(QObject):
    started = Signal()                       # 子进程已就绪
    ready = Signal()                         # 收到壳的第一条上行消息
    event_received = Signal(object)          # evt envelope
    response_ready = Signal(int, object)     # id, res envelope
    diagnostics = Signal(str)                # 壳 stderr 行
    down = Signal(str)                       # 进程退出/错误

    def __init__(self, argv=None, cwd=None, python=None, parent=None):
        super().__init__(parent)
        self._argv = list(argv) if argv else ["-m", "preloader_rw.relay", "--json"]
        if argv is None and getattr(sys, "frozen", False):
            # 冻结包里没有 `-m`：让同一个 exe 以 --relay 把自己拉起来。
            self._argv = ["--relay", "--json"]
        from preloader_rw import REPO_ROOT
        self._cwd = cwd or REPO_ROOT
        self._python = python or sys.executable
        self._proc = None
        self._buf = b""
        self._pending = {}
        self._next_id = 0
        self._ready = False

    # ---- 生命周期 ---------------------------------------------------

    def start(self):
        if self._proc is not None:
            return
        self._ready = False
        proc = QProcess(self)
        proc.setProgram(self._python)
        proc.setArguments(self._argv)
        proc.setWorkingDirectory(self._cwd)
        _hide_console(proc)
        proc.started.connect(self.started.emit)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        proc.start()

    def stop(self):
        proc = self._proc
        if proc is None:
            return
        proc.closeWriteChannel()
        if not proc.waitForFinished(1500):
            proc.terminate()
            if not proc.waitForFinished(1500):
                proc.kill()
                proc.waitForFinished(1000)
        self._proc = None

    @property
    def alive(self):
        return (self._proc is not None
                and self._proc.state() != QProcess.ProcessState.NotRunning)

    # ---- 写点 -------------------------------------------------------

    def send(self, intent, args=None, on_response=None):
        """发一条意图；返回本请求的 id。"""
        if not self.alive:
            raise RuntimeError("壳进程未运行")
        self._next_id += 1
        rid = self._next_id
        if on_response is not None:
            self._pending[rid] = on_response
        line = P.dumps(P.req_msg(rid, intent, args)) + "\n"
        self._proc.write(line.encode("utf-8"))
        return rid

    def fire(self, on_response=None, args=None):
        return self.send(P.INTENTS[0], args, on_response)

    def probe(self, on_response=None):
        """只读读取设备信息；不需要认证。"""
        return self.send("probe", None, on_response)

    def detect(self, on_response=None):
        """枚举一次 USB 连接状态（未插入/未绑驱动/被占用/可打开）。"""
        return self.send("detect", None, on_response)

    def info(self, on_response=None):
        return self.send("info", None, on_response)

    def health(self, on_response=None):
        return self.send("health", None, on_response)

    def read(self, partition, path, on_response=None):
        return self.send("read", {"partition": partition, "path": path},
                         on_response)

    def write(self, partition, image, on_response=None):
        return self.send("write", {"partition": partition, "image": image},
                         on_response)

    def erase(self, partition, on_response=None):
        return self.send("erase", {"partition": partition}, on_response)

    # ---- 读点 -------------------------------------------------------

    def _on_stdout(self):
        data = bytes(self._proc.readAllStandardOutput())
        if not data:
            return
        self._buf += data
        if b"\n" not in self._buf:
            return
        # 一次 split 取全部完整行：逐行 split(b"\n", 1) 会把剩余缓冲反复
        # 复制，块数一多就是 O(n²)，GUI 线程会在大分区读的日志突发里卡死。
        parts = self._buf.split(b"\n")
        self._buf = parts.pop()
        for line in parts:
            text = line.strip().decode("utf-8", "replace").strip()
            if text:
                self._dispatch(text)

    def _dispatch(self, text):
        if not self._ready:
            self._ready = True
            self.ready.emit()
        try:
            obj = P.loads(text)
        except ValueError:
            self.diagnostics.emit("壳输出非法行: " + text)
            return
        kind = obj.get("kind")
        if kind == P.KIND_EVT:
            self.event_received.emit(obj)
        elif kind == P.KIND_RES:
            rid = obj.get("id")
            callback = self._pending.pop(rid, None)
            self.response_ready.emit(rid, obj)
            if callback is not None:
                try:
                    callback(obj)
                except Exception as exc:
                    self.diagnostics.emit("响应回调异常: %r" % (exc,))

    def _on_stderr(self):
        data = bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")
        for line in data.splitlines():
            if line.strip():
                self.diagnostics.emit(line.rstrip())

    def _on_finished(self, code, _status):
        self.down.emit("壳进程退出: code=%s" % (code,))

    def _on_error(self, _error):
        self.down.emit("壳进程启动失败（%s）" % (self._python,))
