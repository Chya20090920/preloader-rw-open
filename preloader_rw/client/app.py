"""GUI 装配根：把壳桥、状态镜像与五个页面接起来。

受众: 代理(GUI 端)
视角: 对读者称「你」；正文不出现「我」
禁入: 元指令 · 过程注解 · 自注括号 · 对第三方的话

页面不直接碰子进程；唯一通信边界是 `client\\bridge.py`。

外壳分三段：左栏（身份与导航）、顶栏（当前页名）、底栏（运行目录与版本）。
三段都压到最小高度——控制台的行数由数据决定，不由外壳决定。
"""

import argparse
import os
import sys

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QStackedWidget, QVBoxLayout, QWidget)

from . import theme as T
from .bridge import RelayBridge
from .icons import draw_icon
from .pages import FlashPage, LogsPage, ResourcesPage, RunsPage
from .state import RelayState
from .widgets import NavItem, typeface

from preloader_rw import REPO_ROOT

ROOT = REPO_ROOT
RUNS = os.path.join(ROOT, "runs")

NAV = [
    ("box", "资源"),
    ("bolt", "刷写"),
    ("terminal", "日志"),
    ("clock", "运行记录"),
]


def _logo_pixmap(size: int = 18) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw_icon(p, "chip", QRectF(0, 0, size, size), T.INK, 1.4)
    p.end()
    return pm


class Sidebar(QFrame):
    navigate = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(T.SIDEBAR_W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 10)
        lay.setSpacing(0)

        brand = QHBoxLayout()
        brand.setContentsMargins(14, 0, 14, 0)
        brand.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(_logo_pixmap(15))
        logo.setFixedSize(15, 15)
        brand.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
        name = QLabel("FW CONSOLE")
        name.setObjectName("BrandName")
        name.setFont(typeface(12, QFont.Weight.DemiBold, tracking=0.9))
        brand.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)
        brand.addStretch(1)
        lay.addLayout(brand)
        lay.addSpacing(14)

        self.items: list[NavItem] = []
        for i, (icon, text) in enumerate(NAV):
            it = NavItem(icon, text)
            it.clicked.connect(lambda _=False, i=i: self.navigate.emit(i))
            lay.addWidget(it)
            self.items.append(it)

        lay.addStretch(1)

        dev = QFrame()
        dev.setObjectName("SideDevice")
        dl = QVBoxLayout(dev)
        dl.setContentsMargins(9, 6, 9, 7)
        dl.setSpacing(2)
        did = QLabel("USB 设备")
        did.setObjectName("SideDeviceId")
        dl.addWidget(did)
        self.state_label = QLabel("壳未启动")
        self.state_label.setObjectName("SideDeviceState")
        dl.addWidget(self.state_label)
        holder = QHBoxLayout()
        holder.setContentsMargins(10, 0, 10, 0)
        holder.addWidget(dev)
        lay.addLayout(holder)


class MainWindow(QMainWindow):
    def __init__(self, page: int = 2):
        super().__init__()
        self.setWindowTitle("固件刷写控制台")
        self.setWindowIcon(QIcon(_logo_pixmap(48)))
        self.resize(1280, 820)
        self.setMinimumSize(1024, 640)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        self.sidebar = Sidebar()
        h.addWidget(self.sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)

        topbar = QFrame()
        topbar.setObjectName("TopBar")
        topbar.setFixedHeight(T.TOPBAR_H)
        tl = QHBoxLayout(topbar)
        tl.setContentsMargins(14, 0, 14, 0)
        tl.setSpacing(0)
        self.title = QLabel()
        self.title.setObjectName("PageTitle")
        tl.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)
        tl.addStretch(1)
        right.addWidget(topbar)

        self.stack = QStackedWidget()
        self.resources = ResourcesPage()
        self.flash = FlashPage(root=ROOT)
        self.logs = LogsPage()
        self.runs = RunsPage(runs_root=RUNS)
        for w in (self.resources, self.flash, self.logs, self.runs):
            self.stack.addWidget(w)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(14, 12, 14, 12)
        hl.setSpacing(0)
        hl.addWidget(self.stack)
        right.addWidget(holder, 1)

        bar = QFrame()
        bar.setObjectName("StatusBar")
        bar.setFixedHeight(T.STATUS_H)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 0, 14, 0)
        bl.setSpacing(8)
        self.path_label = QLabel("运行目录: —")
        self.path_label.setObjectName("StatusText")
        bl.addWidget(self.path_label)
        bl.addStretch(1)
        ver = QLabel("v0.1.0 · PySide6")
        ver.setObjectName("StatusVer")
        bl.addWidget(ver)
        right.addWidget(bar)

        h.addLayout(right, 1)

        # ---- 通信边界与状态镜像 ------------------------------------
        self.state = RelayState(self)
        # 装配由壳从 PANE_* 环境变量读取；GUI 不传配置、不做配置选择。
        self.bridge = RelayBridge(argv=["-m", "preloader_rw.relay", "--json"], parent=self)
        self.bridge.event_received.connect(self.state.apply_event)
        self.bridge.response_ready.connect(self.state.apply_response)
        self.bridge.diagnostics.connect(
            lambda text: self.logs.append("dim", "shell", text))
        self.bridge.down.connect(self._on_down)
        # 等壳真的回话，而不是等 QProcess 创建出进程
        self.bridge.ready.connect(self._on_shell_up)
        self._fire_on_up = False
        self.state.state_changed.connect(self._on_state)
        self.state.log_appended.connect(self.logs.on_log)
        self.state.snapshot_changed.connect(self.resources.on_snapshot)
        self.state.snapshot_changed.connect(self._on_snapshot)
        self.resources.log_line.connect(self.logs.append)
        self.flash.bind(self.state, self.bridge)
        self.flash.log_line.connect(self.logs.append)
        # 设备需要签名应答时弹输入窗，提交/取消回给后端进程。
        self.bridge.event_received.connect(self._maybe_host_input)
        self._input_dlg = None
        # DA 镜像与载荷选择：资源页 ↔ 刷写页 ↔ 会话文件（下次启动记住）。
        self.resources.da_changed.connect(self._on_da_changed)
        self.resources.payload_changed.connect(self._on_payload_changed)
        self.resources.table_changed.connect(self._on_table_changed)
        session = self._load_session()
        if session:
            self.resources.set_da_paths(session.get("da1", ""),
                                        session.get("da2", ""))
            self.flash.set_da(session.get("da1", ""), session.get("da2", ""))
            self.resources.set_payload_path(session.get("payload", ""))
            self.flash.set_payload(session.get("payload", ""))
            self.resources.set_table_path(session.get("table", ""))
            self.flash.set_table(session.get("table", ""))

        self.sidebar.navigate.connect(self.go)
        self.runs.refresh()
        for i in range(len(NAV)):
            self.sidebar.items[i].setChecked(i == page)
        self.go(page)

    # ---- 壳会话 -----------------------------------------------------

    def start_shell(self, fire=False):
        self._fire_on_up = fire
        self.bridge.start()

    def closeEvent(self, event):
        self.bridge.stop()
        super().closeEvent(event)

    # ---- 回调 -------------------------------------------------------

    def _on_shell_up(self):
        self.logs.append("info", "shell", "壳进程就绪")
        self.sidebar.state_label.setText("壳已启动 · 空闲")
        self.bridge.info()
        if self._fire_on_up:
            self.bridge.fire()

    def _on_snapshot(self, snap):
        run_dir = str(snap.get("run_dir") or "")
        self.path_label.setText("运行目录: " + (run_dir or "—"))
        self.path_label.setToolTip(run_dir)

    def _on_state(self, state, reason):
        label = state if state else "—"
        if state == "dead":
            label = "会话已终止 · 可重新发射"
        elif state == "failed":
            label = "失败 · 可重新发射"
        elif state == "unknown":
            # 设备状态未知：明确标出来，避免读作「已就绪」。
            label = "状态未知 · 需探针确认"
        self.sidebar.state_label.setText(
            ("设备 · " + label) if state and state != "boot" else "壳已启动 · 空闲")
        if state == "gated":
            self.bridge.info()

    # ---- DA 镜像会话记忆 -------------------------------------------------

    @staticmethod
    def _session_path():
        return os.path.join(ROOT, ".gui_session.json")

    def _load_session(self):
        try:
            import json
            with open(self._session_path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {}

    def _on_da_changed(self, da1, da2):
        self.flash.set_da(da1, da2)
        self._save_session(da1=da1, da2=da2)

    def _on_payload_changed(self, payload):
        self.flash.set_payload(payload)
        self._save_session(payload=payload)

    def _on_table_changed(self, table):
        self.flash.set_table(table)
        self._save_session(table=table)

    def _save_session(self, **updates):
        try:
            import json
            data = self._load_session()
            data.update(updates)
            with open(self._session_path(), "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
        except OSError as exc:
            self.logs.append("warn", "gui", "会话配置写入失败: %s" % (exc,))

    def _maybe_host_input(self, evt):
        """host_input 事件 → 弹签名应答窗；同一时刻只弹一个。"""
        if not isinstance(evt, dict) or evt.get("event") != "host_input":
            return
        if self._input_dlg is not None:
            return
        data = evt.get("data") or {}
        req_id = int(data.get("id") or 0)
        self.logs.append("info", "shell",
                         "宿主输入请求: %s（%.0f s 内应答）"
                         % (data.get("input", ""), float(data.get("timeout_s") or 0)))
        from .pages import HostInputDialog
        dlg = HostInputDialog(str(data.get("input", "")),
                              str(data.get("info", "")),
                              float(data.get("timeout_s") or 300.0), self)
        self._input_dlg = dlg
        dlg.submitted.connect(
            lambda payload, rid=req_id: self.bridge.send(
                "provide", {"id": rid, "hex": payload.hex()}))
        dlg.cancelled.connect(
            lambda rid=req_id: self.bridge.send("provide_cancel", {"id": rid}))
        dlg.finished.connect(lambda *_: setattr(self, "_input_dlg", None))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_down(self, message):
        self.logs.append("err", "shell", message)
        self.sidebar.state_label.setText("壳未运行")

    def go(self, i: int):
        self.stack.setCurrentIndex(i)
        for j, it in enumerate(self.sidebar.items):
            it.setChecked(j == i)
        _icon, title = NAV[i]
        self.title.setText(title)
        if i == 3:
            self.runs.refresh()


def _dark_titlebar(w: QWidget):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(w.winId())
        value = ctypes.c_int(1)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="client")
    ap.add_argument("--page", type=int, default=1, help="0 资源 1 刷写 2 日志 3 运行记录")
    ap.add_argument("--shot", default="", help="截图到指定 PNG 后退出")
    ap.add_argument("--shell", action="store_true", help="截图前先拉起壳（不认证）")
    ap.add_argument("--fire", action="store_true", help="截图前先请求认证并建立会话")
    ap.add_argument("--size", default="1280x820")
    args, _ = ap.parse_known_args(argv)

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    f = QFont("Microsoft YaHei UI")
    f.setPointSize(9)
    app.setFont(f)
    app.setStyleSheet(T.stylesheet())

    w, h = (int(x) for x in args.size.lower().split("x"))
    win = MainWindow(page=max(0, min(3, args.page)))
    win.resize(w, h)
    win.show()
    _dark_titlebar(win)

    if not args.shot:
        win.start_shell(fire=False)

    if args.shot:
        def snap():
            app.processEvents()
            win.grab().save(args.shot, "PNG")
            win.bridge.stop()
            app.quit()
        if args.shell or args.fire:
            win.start_shell(fire=args.fire)
        QTimer.singleShot(4200 if (args.shell or args.fire) else 900, snap)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
