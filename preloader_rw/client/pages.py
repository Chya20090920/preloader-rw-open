"""GUI 各页：只显示壳真实上报的状态，不内置任何演示数据。

受众: 代理(GUI 端)
视角: 对读者称「你」；正文不出现「我」
禁入: 元指令 · 过程注解 · 自注括号 · 对第三方的话

数据来源只有三个：
    · `state` / `health` / `log` / `command` 事件（壳推送）；
    · `info` 快照（壳应答，含 resources / partitions / run_dir）；
    · `runs\\` 目录（本机文件系统）。
读不到就显示「—」，不得填演示值。
"""

import os
import tempfile
import time

from PySide6.QtCore import Qt, QProcess, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QSizePolicy,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from . import theme as T
from .widgets import Card, Divider, KeyValue, Pill, Readout, ReadoutBar

DASH = "—"

# 壳状态 → (中文, 明度)。黑白表里状态不是色相，是亮度：空闲 < 进行中 < 就绪，
# 失败是唯一的例外，它是全表唯一的彩色。
STATE_TEXT = {
    "boot": ("空闲", T.DIM),
    "listening": ("侦听中", T.MUTE),
    "fb_probing": ("探测 fastboot", T.MUTE),
    "fb_ready": ("健康已采集", T.MUTE),
    "kernel_wait": ("启动中", T.MUTE),
    "bootstrapping": ("引导中", T.MUTE),
    "gated": ("已就绪", T.INK),
    "busy": ("命令执行中", T.MUTE),
    # 设备状态未知：不是就绪态（读写按钮必须保持不可用，只有探针/重发可用）。
    "unknown": ("状态未知 · 需探针确认", T.ALERT),
    "dead": ("会话已终止 · 需重新发射", T.ALERT),
    "failed": ("失败", T.ALERT),
}

# 链路六个灯与壳状态的推进刻度（1..6）
GATE_KEYS = ("device", "link", "res", "cred", "auth", "session")
LIT_BY_STATE = {
    "boot": 0, "listening": 1, "fb_probing": 1, "fb_ready": 2,
    "kernel_wait": 3, "bootstrapping": 4, "gated": 6, "busy": 6,
    # 状态未知：会话/授权灯不亮（4 = 走到引导，认证与命令面都不算数）
    "unknown": 4,
}


def size_text(n):
    n = int(n or 0)
    if n >= 1 << 30:
        return "%.1f GB" % (n / float(1 << 30))
    if n >= 1 << 20:
        return "%.1f MB" % (n / float(1 << 20))
    if n >= 1 << 10:
        return "%.1f KB" % (n / float(1 << 10))
    return "%d B" % n


def _clear_body(card, keep=1):
    """清掉卡片正文（保留标题行），供按事件重建。"""
    layout = card.body()
    while layout.count() > keep:
        item = layout.takeAt(layout.count() - 1)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


def _sep() -> QFrame:
    """组间分隔：1px 竖线，只到这里高。"""
    line = QFrame()
    line.setObjectName("Sep")
    line.setFixedSize(1, 22)
    return line


def _label(text, obj="Value"):
    lab = QLabel(text)
    lab.setObjectName(obj)
    return lab


def _alert(parent, title: str, text: str):
    """没有图标的对话框。

    黑白表里只有一个色相，也就没有彩色信息图标的位置：语义由标题和正文
    承担，"无法合并" 四个字比一个黄色三角说得更清楚。
    """
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.NoIcon)
    box.setText(text)
    box.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
    return box.exec()


class _ElideLabel(QLabel):
    """等宽路径标签：宽度不够时中间省略（…），不换行不裁字。"""

    def __init__(self, text, font, parent=None):
        super().__init__(parent)
        self._full = text
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self._apply()

    def set_full_text(self, text):
        self._full = text
        self._apply()

    def resizeEvent(self, event):
        self._apply()
        super().resizeEvent(event)

    def _apply(self):
        fm = self.fontMetrics()
        width = max(40, self.width() - 4)
        self.setText(fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle,
                                   width))


class ResourcesPage(QWidget):
    da_changed = Signal(str, str)          # DA1 路径, DA2 路径（"" = 未选）
    payload_changed = Signal(str)          # 载荷路径（"" = 用壳默认）
    table_changed = Signal(str)            # 命令表路径（"" = 用仓库自带表）

    log_line = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.cards = {}
        self._snapshot = {}
        self._merge_root = ""
        self._merge_worker = None
        self._super_size = 0
        self._current_slot = ""
        # 单卡四行：DA1 / 2nd-DA / 载荷 / 命令表，同一行格式、同一列位、同一按钮尺寸。
        self.cards = {"targets": Card("分析对象")}
        root.addWidget(self.cards["targets"])
        self._paths = {"da1": "", "da2": "", "payload": "", "table": ""}
        self._cfg = {"da1": "", "da2": "", "payload": "", "table": ""}
        self._refresh_target_rows()

        merge = Card("一键合并物理区")
        row = QHBoxLayout()
        row.setSpacing(8)
        self._merge_pick = QPushButton("选择根目录")
        self._merge_pick.setObjectName("PathPicker")
        self._merge_pick.setFixedHeight(T.BTN_H)
        self._merge_pick.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Fixed)
        self._merge_pick.clicked.connect(self._pick_merge_root)
        row.addWidget(self._merge_pick, 1)
        self._merge_start = QPushButton("开始合并")
        self._merge_start.setObjectName("Primary")
        self._merge_start.setMinimumWidth(T.BTN_W)
        self._merge_start.setFixedHeight(T.BTN_H)
        self._merge_start.clicked.connect(self._start_merge)
        row.addWidget(self._merge_start)
        merge.body().addLayout(row)
        root.addWidget(merge)
        root.addStretch(1)

    # ---- 分析对象（DA1 / 2nd-DA / 载荷，单卡三行） -----------------------------

    def _refresh_target_rows(self):
        """重建四行：标签(56px) | 路径(等宽省略,自适应) | 选择…(64) | 清除(64)。

        行高 28px；三段列宽与按钮列位四行完全一致（固定像素，不靠内容
        自适应）；无状态徽章——路径本身表达状态：选了就有，没选就是空。
        """
        card = self.cards["targets"]
        _clear_body(card)
        mono = QFont(T.FONT_MONO)
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        for role, title in (("da1", "DA1"), ("da2", "2nd-DA"),
                            ("payload", "载荷"), ("table", "命令表")):
            card.body().addWidget(self._target_row(role, title, mono))

    def _target_row(self, role, title, mono):
        path = self._paths[role] or self._cfg.get(role) or ""
        w = QWidget()
        w.setFixedHeight(28)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        name = QLabel(title)
        name.setStyleSheet(f"font-size:{T.FS_MD}; font-weight:600;")
        name.setFixedWidth(56)
        lay.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)
        lab = _ElideLabel(path or "未选择", mono)
        lab.setStyleSheet(f"color:{T.MUTE}; font-size:{T.FS_SM};")
        lay.addWidget(lab, 1, Qt.AlignmentFlag.AlignVCenter)
        pick = QPushButton("选择…")
        pick.setObjectName("GhostSmall")
        pick.setFixedSize(T.BTN_W, T.BTN_H)
        pick.setCursor(Qt.CursorShape.PointingHandCursor)
        pick.clicked.connect(lambda _=False, r=role: self._pick_target(r))
        lay.addWidget(pick, 0, Qt.AlignmentFlag.AlignVCenter)
        clear = QPushButton("清除")
        clear.setObjectName("GhostSmall")
        clear.setFixedSize(T.BTN_W, T.BTN_H)
        clear.clicked.connect(lambda _=False, r=role: self.set_target(r, ""))
        lay.addWidget(clear, 0, Qt.AlignmentFlag.AlignVCenter)
        return w

    def _pick_target(self, role):
        current = self._paths[role]
        start = os.path.dirname(current) if current else os.path.expanduser("~")
        if role == "payload":
            filt = "载荷 (*.py *.pyc *.dll *.pyd *.so);;所有文件 (*)"
        elif role == "table":
            filt = "命令表 (*.json);;所有文件 (*)"
        else:
            filt = "镜像 (*.bin *.img);;所有文件 (*)"
        path, _f = QFileDialog.getOpenFileName(
            self, "选择 %s" % role.upper(), start, filt)
        if path:
            self.set_target(role, path)

    def set_target(self, role, path):
        """选定/清除一行；路径写入会话并通知刷写页。"""
        if role not in self._paths:
            return
        self._paths[role] = (path or "").strip()
        self._refresh_target_rows()
        if role == "payload":
            self.payload_changed.emit(self._paths["payload"])
        elif role == "table":
            self.table_changed.emit(self._paths["table"])
        else:
            self.da_changed.emit(self._paths["da1"], self._paths["da2"])

    # 会话恢复用：不回发信号（由 app 统一分发给刷写页）。
    def set_da_paths(self, da1, da2):
        self._paths["da1"] = (da1 or "").strip()
        self._paths["da2"] = (da2 or "").strip()
        self._refresh_target_rows()

    def set_payload_path(self, path):
        self._paths["payload"] = (path or "").strip()
        self._refresh_target_rows()

    def set_table_path(self, path):
        self._paths["table"] = (path or "").strip()
        self._refresh_target_rows()
    def _pick_merge_root(self):
        start = self._merge_root or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "选择 payload 根目录", start)
        if not path:
            return
        self._merge_root = path
        if self._merge_pick is not None:
            self._merge_pick.setText("根目录: " + os.path.basename(path))
            self._merge_pick.setToolTip(path)

    def _start_merge(self):
        if self._merge_worker is not None and self._merge_worker.isRunning():
            return
        if not self._merge_root or not os.path.isdir(self._merge_root):
            _alert(self, "无法合并", "先选择一个存在的根目录")
            return
        if not self._super_size:
            _alert(self, "无法合并",
                   "未取得设备 super 分区尺寸；先认证并建立会话")
            return
        slot = self._current_slot
        if slot not in ("a", "b"):
            _alert(self, "无法合并",
                   "未取得设备当前槽位；先认证并建立会话")
            return
        try:
            from preloader_rw.core.product.official.superimg import plan_super
            plan_super(self._merge_root, self._super_size, slot=slot)
        except ValueError as exc:
            _alert(self, "无法合并", str(exc))
            return
        out_path, _filter = QFileDialog.getSaveFileName(
            self, "保存合并后的 super.img",
            os.path.join(self._merge_root, "super.img"), "镜像 (*.img)")
        if not out_path:
            return
        self._merge_start.setEnabled(False)
        self._merge_pick.setEnabled(False)
        self._merge_start.setText("合并中…")
        self.log_line.emit("info", "merge",
                           "合并 super：源 %s → %s（槽位 %s）"
                           % (self._merge_root, out_path, slot))
        worker = _MergeWorker(self._merge_root, self._super_size, out_path,
                              slot, self)
        worker.progress.connect(self._on_merge_progress)
        worker.merged.connect(self._on_merge_done)
        self._merge_worker = worker
        worker.start()

    def _on_merge_progress(self, percent, _text):
        self._merge_start.setText("合并中 %d%%" % percent)

    def _on_merge_done(self, ok, message, path):
        self._merge_start.setEnabled(True)
        self._merge_pick.setEnabled(True)
        self._merge_start.setText("开始合并")
        if ok:
            self.log_line.emit("ok", "merge", message)
            _alert(self, "合并完成",
                   "已生成：\n%s\n\n回到刷写面板，为 super 行选择该文件。" % path)
        else:
            self.log_line.emit("err", "merge", "合并失败: " + message)
            _alert(self, "合并失败", message)

    @staticmethod
    def _rows_for(resources, match):
        return [r for r in resources if match(str(r.get("role", "")))]

    def on_snapshot(self, snap):
        self._snapshot = snap
        size = 0
        for row in snap.get("partitions") or []:
            if str(row.get("name") or "") == "super":
                size = int(row.get("size") or 0)
                break
        self._super_size = size
        slot = str((snap.get("health") or {}).get("current-slot") or "").lower()
        self._current_slot = slot if slot in ("a", "b") else ""
        resources = list(snap.get("resources") or [])
        # 壳配置里的路径作为回落显示（用户没在本机选过时）。
        for row in self._rows_for(resources,
                                  lambda r: r in ("da1", "da2", "route", "table")):
            role = str(row.get("role", ""))
            key = "payload" if role == "route" else role
            if key in self._cfg and key != "table":
                self._cfg[key] = str(row.get("path") or "")
            elif key == "table" and not self._paths["table"]:
                self._cfg["table"] = str(row.get("path") or "")
        self._refresh_target_rows()

class PartitionTable(QTableWidget):
    """行来源 = 壳上报的设备分区表 + 本机镜像目录。"""

    selection_changed = Signal()
    pick_requested = Signal(int)          # 行号：请求为这一行选镜像文件
    erase_requested = Signal(int)         # 行号：请求擦除该分区

    def __init__(self, rows=None, parent=None):
        self._rows = []
        super().__init__(0, 7, parent)
        self._checks = []
        self.setHorizontalHeaderLabels(["", "分区", "容量", "偏移", "镜像", "状态", ""])
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hh = self.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for c, mode, width in ((0, QHeaderView.ResizeMode.Fixed, 38),
                               (1, QHeaderView.ResizeMode.Fixed, 116),
                               (2, QHeaderView.ResizeMode.Fixed, 80),
                               (3, QHeaderView.ResizeMode.Fixed, 92),
                               (4, QHeaderView.ResizeMode.Stretch, None),
                               (5, QHeaderView.ResizeMode.Fixed, 76),
                               (6, QHeaderView.ResizeMode.Fixed, 132)):
            hh.setSectionResizeMode(c, mode)
            if width is not None:
                hh.resizeSection(c, width)
        self.set_rows(rows or [])

    def set_rows(self, rows):
        self.setRowCount(0)
        self._checks = []
        self._rows = list(rows)
        self.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            self.setRowHeight(r, 24)
            cb = QCheckBox()
            cb.setChecked(bool(row.get("image_ok")))
            # 勾选与「是否有镜像」解耦：读取不依赖镜像映射。
            cb.setEnabled(True)
            cb.stateChanged.connect(lambda *_: self.selection_changed.emit())
            self._checks.append(cb)
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(10, 0, 0, 0)
            hl.addWidget(cb)
            self.setCellWidget(r, 0, holder)
            self._fill_row(r)
            btn = QPushButton("选择")
            btn.setObjectName("GhostSmall")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, idx=r: self.pick_requested.emit(idx))
            b_erase = QPushButton("擦除")
            b_erase.setObjectName("GhostSmall")
            b_erase.setCursor(Qt.CursorShape.PointingHandCursor)
            b_erase.setToolTip("擦除此分区（会先弹出确认）")
            b_erase.clicked.connect(
                lambda _=False, idx=r: self.erase_requested.emit(idx))
            wrap = QWidget()
            wl = QHBoxLayout(wrap)
            wl.setContentsMargins(4, 0, 4, 0)
            wl.setSpacing(6)
            wl.addWidget(btn)
            wl.addWidget(b_erase)
            self.setCellWidget(r, 6, wrap)

    def _fill_row(self, r):
        row = self._rows[r]
        name = str(row.get("name", ""))
        values = (name, size_text(row.get("size", 0)),
                  "0x%X" % int(row.get("start", 0) or 0),
                  str(row.get("image", "")))
        for c, text in ((1, values[0]), (2, values[1]), (3, values[2]), (4, values[3])):
            item = QTableWidgetItem(text)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if c in (1, 3, 4):
                item.setFont(QFont("Cascadia Mono", 8))
            if not row.get("image_ok") and c in (1, 4):
                item.setForeground(QColor(T.MUTE))
            self.setItem(r, c, item)
        self._set_status(r, row)

    def _set_status(self, r, row):
        if not row.get("image_ok"):
            state, tone = "缺镜像", T.DIM
        else:
            state, tone = "已就绪", T.INK
        item = QTableWidgetItem(state)
        item.setForeground(QColor(tone))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.setItem(r, 5, item)

    def row_at(self, r):
        return self._rows[r] if 0 <= r < len(self._rows) else None

    def set_image(self, r, path):
        """现场为某行指定镜像文件；勾选框随之放开并自动勾上。"""
        row = self.row_at(r)
        if row is None:
            return
        row["image"] = path
        row["image_ok"] = bool(path) and os.path.isfile(path)
        self._fill_row(r)
        cb = self._checks[r]
        cb.blockSignals(True)
        cb.setEnabled(True)
        cb.setChecked(row["image_ok"])
        cb.blockSignals(False)
        self.selection_changed.emit()

    def set_status(self, name, text, tone):
        for r, row in enumerate(self._rows):
            if row.get("name") == name:
                item = QTableWidgetItem(text)
                item.setForeground(QColor(tone))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.setItem(r, 5, item)

    def set_all(self, on):
        for cb in self._checks:
            if cb.isEnabled():
                cb.blockSignals(True)
                cb.setChecked(on)
                cb.blockSignals(False)
        self.selection_changed.emit()

    def selected(self):
        return [row for row, cb in zip(self._rows, self._checks) if cb.isChecked()]

    def summary_text(self):
        sel = self.selected()
        total = sum(int(r.get("size", 0) or 0) for r in sel)
        return "已选 %d / %d 个分区 · 计划 %s" % (len(sel), len(self._rows),
                                                size_text(total))


class _ProgressDialog(QDialog):
    """执行进度：原来是页面底部一条常驻状态栏，改成点「开始」才出现。

    非模态——壳的命令事件还要能进来刷新它；关掉它不打断执行。
    """

    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("执行")
        self.setModal(False)
        self.setFixedSize(420, 148)
        self._done = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        self.status = QLabel("准备中")
        self.status.setObjectName("DlgStatus")
        lay.addWidget(self.status)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.bar = QProgressBar()
        self.bar.setObjectName("Progress")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(6)
        row.addWidget(self.bar, 1)
        self.pct = QLabel("0%")
        self.pct.setObjectName("DlgPct")
        self.pct.setFixedWidth(42)
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight
                              | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.pct)
        lay.addLayout(row)

        self.detail = QLabel("")
        self.detail.setObjectName("DlgDetail")
        self.detail.setWordWrap(False)
        lay.addWidget(self.detail)
        lay.addStretch(1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn = QPushButton("取消")
        self.btn.setObjectName("Ghost")
        self.btn.setFixedSize(72, 24)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self._on_clicked)
        btns.addWidget(self.btn)
        lay.addLayout(btns)

    def _on_clicked(self):
        if self._done:
            self.close()
        else:
            self.cancel_requested.emit()

    def begin(self, text: str):
        self._done = False
        self.btn.setText("取消")
        self.status.setText(text)
        self.status.setStyleSheet("")
        self.detail.setText("")
        self.pct.setText("0%")
        self.bar.setValue(0)
        self.show()
        self.raise_()
        self.activateWindow()

    def set_status(self, text: str, tone: str = T.MUTE):
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{tone};")

    def set_detail(self, text: str):
        self.detail.setText(text)

    def set_progress(self, pct: int, total: int = 0, done: int = 0):
        self.bar.setValue(max(0, min(1000, pct * 10)))
        self.pct.setText("%d%%" % pct)
        if total:
            self.detail.setText("%d / %d" % (done, total))

    def finish(self, text: str, ok: bool, total: int = 0):
        self._done = True
        self.btn.setText("关闭")
        self.set_status(text, T.INK if ok else T.ALERT)
        if ok:
            self.bar.setValue(1000)
            self.pct.setText("100%")


class HostInputDialog(QDialog):
    """签名应答输入窗：文件选择或文本粘贴，提交或取消。

    提交 = 提供签名应答；取消/关闭 = 中止当前操作。
    """

    submitted = Signal(bytes)
    cancelled = Signal()

    def __init__(self, kind: str, info: str, timeout_s: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("需要服务器签名应答")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._path = ""
        self._done = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        head = QLabel("设备请求签名应答。")
        head.setObjectName("DlgStatus")
        lay.addWidget(head)
        hint = QLabel("请选择签名文件，或直接粘贴签名文本。等待上限 %.0f 秒。"
                      % timeout_s)
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._pick = QPushButton("选择签名文件…")
        self._pick.setObjectName("PathPicker")
        self._pick.setFixedHeight(26)
        self._pick.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pick.clicked.connect(self._pick_file)
        row.addWidget(self._pick, 1)
        lay.addLayout(row)

        self.text = QPlainTextEdit()
        self.text.setObjectName("LogView")
        self.text.setPlaceholderText("或在此粘贴签名文本（按原文提交）")
        self.text.setFixedHeight(96)
        lay.addWidget(self.text)

        self.error = QLabel("")
        self.error.setStyleSheet(f"color:{T.ALERT};")
        self.error.setWordWrap(True)
        lay.addWidget(self.error)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("Ghost")
        self.btn_cancel.setFixedSize(72, 24)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_ok = QPushButton("提交")
        self.btn_ok.setObjectName("Primary")
        self.btn_ok.setFixedSize(72, 24)
        self.btn_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ok.clicked.connect(self._on_submit)
        btns.addWidget(self.btn_cancel)
        btns.addSpacing(6)
        btns.addWidget(self.btn_ok)
        lay.addLayout(btns)

    def _pick_file(self):
        path, _f = QFileDialog.getOpenFileName(
            self, "选择签名文件", os.path.expanduser("~"),
            "签名文件 (*.bin *.sig *.txt *.auth);;所有文件 (*)")
        if path:
            self._path = path
            self._pick.setText(os.path.basename(path))
            self._pick.setToolTip(path)

    def _on_submit(self):
        data = b""
        if self._path:
            try:
                with open(self._path, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                self.error.setText("读取文件失败: %s" % (exc,))
                return
        else:
            data = self.text.toPlainText().strip().encode("utf-8")
        if not data:
            self.error.setText("没有可提交的内容：选择文件或粘贴文本")
            return
        self._done = True
        self.submitted.emit(data)
        self.close()

    def _on_cancel(self):
        self._done = True
        self.cancelled.emit()
        self.close()

    def closeEvent(self, event):
        if not self._done:
            self._done = True
            self.cancelled.emit()
        super().closeEvent(event)


class FlashPage(QWidget):
    """刷写页：三段竖排——链路 / 分区表 / 执行条。

    不分成左右两栏。左表右栏的原型在窄窗口下会把表格压成一条，右栏又
    留不下；竖向三段让唯一会增长的东西（分区表）吃掉剩余高度，其余两段
    按内容取高，窗口怎么拉都不塌。
    """

    log_line = Signal(str, str, str)

    def __init__(self, root="", parent=None):
        super().__init__(parent)
        self._root = root
        self._bridge = None
        self._snapshot = {}
        self._table = ""
        self._state = "boot"
        self._da1 = ""
        self._da2 = ""
        self._payload = ""
        self._running = False
        self._mode = ""          # "write" / "read"
        self._queue = []
        # 串行提交时同时只有一条在飞，记着它的分区名：
        # command 事件带的是命令名（WRITE-PARTITION），不是分区名。
        self._inflight = ""
        self._total = 0
        self._done = 0
        self._failed = 0
        self._dlg = None
        self._driver_proc = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 整页只有一块面板，内部用 1px 横线分段。三段各自带背景和边框时，
        # 每一段都是一层面包，叠起来就是汉堡；一条线分段时，它是一张表。
        card = QFrame()
        card.setObjectName("Card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        # ---- 段一：链路 ----
        rail = QFrame()
        rail.setObjectName("Rail")
        rail.setFixedHeight(36)
        rl = QHBoxLayout(rail)
        rl.setContentsMargins(10, 0, 8, 0)
        rl.setSpacing(5)
        self._dots = {}
        labels = {"device": "设备", "link": "连接", "res": "资源",
                  "cred": "签名", "auth": "认证", "session": "会话"}
        for i, key in enumerate(GATE_KEYS):
            if i:
                sep = QLabel("›")
                sep.setObjectName("RailSep")
                rl.addWidget(sep)
            dot = QLabel("●")
            dot.setObjectName("RailDot")
            dot.setFixedWidth(8)
            rl.addWidget(dot)
            txt = QLabel(labels[key])
            txt.setObjectName("RailText")
            rl.addWidget(txt)
            self._dots[key] = dot
        rl.addStretch(1)
        self.detect_label = QLabel("设备: 枚举中…")
        self.detect_label.setObjectName("Hint")
        rl.addWidget(self.detect_label)
        rl.addSpacing(6)
        self.btn_driver = QPushButton("安装驱动")
        self.btn_driver.setObjectName("Ghost")
        self.btn_driver.setFixedSize(64, 24)
        self.btn_driver.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_driver.setVisible(False)
        self.btn_driver.clicked.connect(self.install_driver)
        rl.addWidget(self.btn_driver)
        rl.addSpacing(8)
        self.btn_fire = QPushButton("认证并建立会话")
        self.btn_fire.setObjectName("Primary")
        self.btn_fire.setFixedHeight(24)
        self.btn_fire.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fire.clicked.connect(self.start_session)
        rl.addWidget(self.btn_fire)
        cv.addWidget(rail)

        # ---- 段二：标题 + 分区表 ----
        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(10, 8, 10, 8)
        bv.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(7)
        mark = QLabel()
        mark.setObjectName("CardMark")
        mark.setFixedSize(2, 9)
        head.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        ttl = QLabel("分区")
        ttl.setObjectName("CardTitle")
        head.addWidget(ttl, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self.count = QLabel("0 个分区")
        self.count.setObjectName("Hint")
        head.addWidget(self.count)
        bv.addLayout(head)

        # 空态与表格互斥：留一个隐藏的表格占着 stretch，标题会被顶到中间。
        self.empty = QLabel("未取得设备分区表 —— 先在右上角认证并建立会话")
        self.empty.setObjectName("Hint")
        empty_page = QWidget()
        el = QVBoxLayout(empty_page)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(0)
        el.addStretch(1)
        el.addWidget(self.empty, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addStretch(1)

        self.table = PartitionTable([], self)
        self.table.selection_changed.connect(self._on_selection)
        self.table.pick_requested.connect(self.pick_image)
        self.table.erase_requested.connect(self.confirm_erase)

        self._mid = QStackedWidget()
        self._mid.addWidget(empty_page)
        self._mid.addWidget(self.table)
        self._mid.setCurrentWidget(empty_page)
        bv.addWidget(self._mid, 1)
        cv.addWidget(body, 1)

        # ---- 段三：页脚（映射 / 勾选 / 动作）----
        foot = QFrame()
        foot.setObjectName("Foot")
        foot.setFixedHeight(36)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(10, 0, 8, 0)
        fl.setSpacing(6)

        # 分区表由壳在引导后读 GPT 并随 info 快照下发，本页不设读表按钮。

        b_all = QPushButton("全选")
        b_all.setObjectName("GhostSmall")
        b_all.setFixedSize(44, 24)
        b_all.clicked.connect(lambda: self.table.set_all(True))
        b_none = QPushButton("清空")
        b_none.setObjectName("GhostSmall")
        b_none.setFixedSize(44, 24)
        b_none.clicked.connect(lambda: self.table.set_all(False))
        fl.addWidget(b_all)
        fl.addWidget(b_none)
        fl.addStretch(1)
        fl.addSpacing(20)

        # 动作跟着表格走：它们作用于「勾选的行」，不属于页面底部。
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("Ghost")
        self.btn_cancel.setFixedSize(56, 24)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.cancel_flash)
        self.btn_read = QPushButton("开始读取")
        self.btn_read.setObjectName("Ghost")
        self.btn_read.setFixedSize(78, 24)
        self.btn_read.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_read.clicked.connect(self.start_read)
        self.btn_start = QPushButton("开始刷写")
        self.btn_start.setObjectName("Primary")
        self.btn_start.setFixedSize(78, 24)
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.clicked.connect(self.start_flash)
        fl.addWidget(self.btn_cancel)
        fl.addSpacing(8)
        fl.addWidget(self.btn_read)
        fl.addSpacing(6)
        fl.addWidget(self.btn_start)
        cv.addWidget(foot)

        lay.addWidget(card)

        # 初始内容用仓库自带的参考分区表；设备分区表随快照到达后替换。
        offline = self._load_reference_partitions()
        if offline:
            self._apply_rows(offline)
            self._mid.setCurrentWidget(self.table)
            self.count.setText("%d 个分区" % len(self.table._rows))

        # 设备枚举轮询：只在无会话时跑——会话期间设备被内核占用是正常态。
        self._detect_timer = QTimer(self)
        self._detect_timer.setInterval(3000)
        self._detect_timer.timeout.connect(self._poll_detect)
        self._detect_timer.start()

        self._refresh_buttons()
        self._paint_gate(0)

    # ---- 绑定 -------------------------------------------------------

    def _load_reference_partitions(self):
        """读仓库里的参考分区表（data/partitions.json）。

        返回 [{"name","start","size"}]；文件缺失或损坏就返回空——不伪造。
        """
        path = os.path.join(self._root, "data", "partitions.json")
        try:
            import json
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            return [{"name": str(p["name"]),
                     "start": int(p["start_byte"]),
                     "size": int(p["size_byte"])}
                    for p in doc.get("partitions") or []]
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def bind(self, state, bridge):
        self._bridge = bridge
        state.state_changed.connect(self.on_state)
        state.snapshot_changed.connect(self.on_snapshot)
        state.command_finished.connect(self.on_command)
        state.log_appended.connect(self._on_log)
        QTimer.singleShot(0, self._poll_detect)

    # ---- 数据 -------------------------------------------------------

    def on_state(self, state, reason):
        self._state = state
        self._paint_gate(LIT_BY_STATE.get(state, 0), failed=state in ("failed", "dead"))
        self._refresh_buttons()
        if self._running:
            return
        if state == "gated":
            self._set_status("就绪 · 未开始", T.DIM)
        elif state in ("failed", "dead"):
            self._set_status("会话已终止", T.ALERT)

    def on_snapshot(self, snap):
        """分区表与镜像映射都取自壳的 info 快照——GUI 不做配置选择。

        快照没带回分区数据时不动现有行：启动时的空快照不该把页面初始的
        参考分区表抹掉；有数据才替换。
        """
        self._snapshot = snap
        parts = list(snap.get("partitions") or [])
        if parts:
            self._apply_rows(parts, snap.get("images"))
        has_rows = bool(self.table._rows)
        self._mid.setCurrentWidget(self.table if has_rows else self._mid.widget(0))
        self.count.setText("%d 个分区 · 已选 %d"
                           % (len(self.table._rows), len(self.table.selected())))
        self._queue, self._inflight, self._running, self._mode = [], "", False, ""
        self._refresh_buttons()
        self._on_selection()

    def _apply_rows(self, parts, images=None):
        images = dict(images or self._snapshot.get("images") or {})
        rows = []
        for part in parts:
            name = str(part.get("name", ""))
            # 只认装配单给出的映射；没映射就显式标“—”，不编造不存在的路径。
            path = str(images.get(name) or "")
            if path and not os.path.isabs(path):
                path = os.path.join(self._root, path)
            rows.append({"name": name, "start": part.get("start", 0),
                         "size": part.get("size", 0),
                         "image": path or DASH,
                         "image_ok": bool(path) and os.path.isfile(path)})
        self.table.set_rows(rows)
        # 填完必须切页：否则数据进了表，界面还停在「未取得分区表」那句提示上。
        self._mid.setCurrentWidget(self.table if rows else self._mid.widget(0))
        self.count.setText("%d 个分区 · 已选 %d"
                           % (len(rows), len(self.table.selected())))

    def _on_selection(self):
        sel = self.table.selected()
        total = sum(int(r.get("size", 0) or 0) for r in sel)
        self.count.setText("%d 个分区 · 已选 %d · %s"
                           % (len(self.table._rows), len(sel), size_text(total)))

    def confirm_erase(self, row_index):
        """按分区名擦除；先弹确认，再提交意图。"""
        row = self.table.row_at(row_index)
        if row is None:
            return
        name = str(row.get("name", ""))
        if not name:
            return
        if self._bridge is None:
            self.log_line.emit("warn", "flash", "壳进程未启动")
            return
        box = QMessageBox(self)
        box.setWindowTitle("确认擦除")
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setText("擦除分区 %s？" % name)
        box.setInformativeText("该分区内容将被清除，且不可恢复。")
        yes = box.addButton("擦除", QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        if box.clickedButton() is not yes:
            return
        self._inflight = name
        self.table.set_status(name, "擦除中", T.MUTE)
        self.log_line.emit("info", "flash", "提交擦除: %s" % name)

        def ack(res):
            if res.get("ok"):
                return
            self._inflight = ""
            self.table.set_status(name, "被拒", T.ALERT)
            self.log_line.emit(
                "err", "flash", "%s 擦除被拒: %s %s"
                % (name, res.get("code", ""), res.get("detail", "")))

        try:
            self._bridge.erase(name, ack)
        except Exception as exc:
            self._inflight = ""
            self.table.set_status(name, "提交失败", T.ALERT)
            self.log_line.emit("err", "flash", "擦除提交失败: %r" % (exc,))

    def pick_image(self, row_index):
        """现场选镜像文件：不必预先在装配单里配映射。"""
        from PySide6.QtWidgets import QFileDialog
        row = self.table.row_at(row_index)
        if row is None:
            return
        current = str(row.get("image") or "")
        start = os.path.dirname(current) if os.path.isabs(current) else self._root
        path, _ = QFileDialog.getOpenFileName(
            self, "为该分区选择镜像文件", start or self._root,
            "镜像文件 (*.img *.bin);;全部文件 (*)")
        if not path:
            return
        self.table.set_image(row_index, os.path.normpath(path))
        self.log_line.emit("info", "flash", "已选镜像 %s -> %s"
                           % (row.get("name", ""), path))
        self._on_selection()

    def on_command(self, data):
        name = str(data.get("name", ""))
        ok = bool(data.get("ok"))
        detail = str(data.get("detail") or "")
        rounds = data.get("rounds") or 0
        # 事件带的是命令名，分区名是本页自己记的（串行，一条在飞）。
        partition = self._inflight or name
        self._inflight = ""
        self._done += 1

        self.table.set_status(partition, "成功" if ok else "失败",
                              T.INK if ok else T.ALERT)
        summary = ("%s · %s（%s）· %d 轮 %s"
                   % ("OK" if ok else "失败", partition, name, rounds, detail)).strip()
        self._set_status(("完成 · " if ok else "失败 · ")
                         + "%d/%d · %s" % (self._done, self._total, partition),
                         T.INK if ok else T.ALERT)
        if self._dlg is not None:
            self._dlg.set_detail(summary)

        self.log_line.emit("ok" if ok else "err", "command", summary)
        if not ok:
            self._failed += 1
        self._paint_progress()
        # 逐条串行：上一条结算后再提交下一条，通道一次只跑一条。
        if self._queue:
            self._submit_next()
        else:
            self._finish_run()

    def _ensure_dlg(self) -> "_ProgressDialog":
        if self._dlg is None:
            self._dlg = _ProgressDialog(self)
            self._dlg.cancel_requested.connect(self.cancel_flash)
        return self._dlg

    def _set_status(self, text: str, tone: str = T.MUTE):
        if self._dlg is not None:
            self._dlg.set_status(text, tone)

    def _paint_progress(self):
        if self._dlg is None:
            return
        total = self._total or 1
        pct = min(100, int(round(100.0 * self._done / total)))
        self._dlg.set_progress(pct, self._total, self._done)

    # ---- 动作 -------------------------------------------------------

    def set_da(self, da1, da2):
        """资源页选定的 DA 镜像；缺任一时认证按钮保持禁用并写明缺哪个。"""
        self._da1 = (da1 or "").strip()
        self._da2 = (da2 or "").strip()
        self._refresh_buttons()

    def set_payload(self, path):
        """资源页选定的载荷；空 = 壳默认载荷。"""
        self._payload = (path or "").strip()

    def set_table(self, path):
        """资源页选定的命令表；空 = 仓库自带默认表。"""
        self._table = (path or "").strip()

    def start_session(self):
        if not self._bridge_ready():
            return
        if not (self._da1 and self._da2):
            self.log_line.emit("warn", "session",
                               "缺 DA 镜像：先在资源页选择 DA1 与 2nd-DA")
            return
        self.log_line.emit("info", "session", "请求认证并建立会话")
        args = {"da1": self._da1, "da2": self._da2}
        if self._payload:
            args["payload"] = self._payload
        if self._table:
            args["table"] = self._table
        self._bridge.fire(self._on_fire_ack, args)

    def _bridge_ready(self) -> bool:
        """壳没在跑就直接说，不要让点击变成一条没有下文的日志。"""
        if self._bridge is None or not self._bridge.alive:
            self.log_line.emit("err", "session",
                               "壳未运行——先看日志页的预检错误")
            return False
        return True

    def _on_fire_ack(self, res):
        ok = bool(res.get("ok"))
        self.log_line.emit(
            "info" if ok else "err", "session",
            "认证请求: %s %s" % (res.get("code", ""), res.get("detail", "")))
        if not ok:
            self._set_status("认证未启动", T.MUTE)

    # ---- 设备枚举与驱动 ----------------------------------------------

    def _poll_detect(self):
        if self._bridge is None or not self._bridge.alive:
            self._paint_detect({"state": "bridge_down"})
            return
        if self._state not in ("boot", "listening", "failed", "dead"):
            # 会话期间设备被内核占用：那时的"打不开"不代表连接问题，不刷新。
            return
        self._bridge.detect(self._on_detect)

    def _on_detect(self, res):
        if not res.get("ok"):
            self._paint_detect({"state": "error",
                                "detail": str(res.get("detail", ""))})
            return
        detail = res.get("detail")
        self._paint_detect(detail if isinstance(detail, dict) else {})

    def _paint_detect(self, d):
        state = d.get("state", "")
        show_driver = False
        if state == "none":
            text = "设备: 未插入"
        elif state == "no_driver":
            text = "设备: 已插入 · 驱动未接管"
            show_driver = True
        elif state == "busy":
            text = "设备: 已插入 · 被占用或无权限"
        elif state == "openable":
            text = "设备: 可打开 %04x:%04x" % (d.get("vid", 0), d.get("pid", 0))
            if not d.get("edl"):
                text += "（非 EDL 模式）"
        elif state == "bridge_down":
            text = "设备: 壳未运行"
        else:
            text = "设备: 枚举失败 %s" % d.get("detail", "")
        self.detect_label.setText(text)
        self.btn_driver.setVisible(show_driver)

    def install_driver(self):
        script = os.path.join(self._root, "preloader_rw", "backends",
                              "winusb", "driver", "install.ps1")
        if not os.path.isfile(script):
            self.log_line.emit("err", "driver", "缺少驱动安装脚本: %s" % script)
            return
        # pnputil 必须管理员权限：经 UAC 提权运行，过程输出在管理员窗口里。
        self.log_line.emit("info", "driver",
                           "请求管理员权限安装 WinUSB 驱动（%s）…" % script)
        self.btn_driver.setEnabled(False)
        cmd = ("$p = Start-Process powershell -Verb RunAs -Wait -PassThru "
               "-ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass',"
               "'-File','%s'); exit $p.ExitCode" % script)
        proc = QProcess(self)
        self._driver_proc = proc
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.finished.connect(self._on_driver_done)
        proc.start("powershell", ["-NoProfile", "-Command", cmd])

    def _on_driver_done(self, code, _status):
        proc = self._driver_proc
        out = ""
        if proc is not None:
            out = bytes(proc.readAll()).decode("utf-8", "replace").strip()
            proc.deleteLater()
            self._driver_proc = None
        self.btn_driver.setEnabled(True)
        if code == 0:
            self.log_line.emit("info", "driver",
                               "驱动安装完成——重新插拔设备后生效")
        elif "canceled" in out.lower() or "cancelled" in out.lower():
            self.log_line.emit("warn", "driver", "已取消管理员授权，未安装")
        else:
            tail = out.splitlines()[-1] if out else "无输出"
            self.log_line.emit("err", "driver",
                               "驱动安装失败（退出码 %s）: %s" % (code, tail))
        self._poll_detect()

    def _begin_run(self, rows, mode, verb):
        self._running = True
        self._mode = mode
        self._failed = 0
        self._queue = [dict(r) for r in rows]
        self._inflight = ""
        self._total = len(rows)
        self._done = 0
        self._ensure_dlg().begin("%s中 · 0/%d" % (verb, len(rows)))
        self.log_line.emit("info", "flash",
                           "排队 %d 个分区，逐条串行%s" % (len(rows), verb))
        self._refresh_buttons()
        self._submit_next()

    def start_flash(self):
        if self._running or not self._bridge_ready():
            return
        rows = [r for r in self.table.selected() if r.get("image_ok")]
        if not rows:
            self.log_line.emit("warn", "flash", "没有可刷写分区（检查装配单镜像映射）")
            return
        self._begin_run(rows, "write", "刷写")

    def start_read(self):
        """读取已勾选分区：每次点击只问一次输出目录，分区文件名自动派生。"""
        if self._running or not self._bridge_ready():
            return
        rows = self.table.selected()
        if not rows:
            self.log_line.emit("warn", "flash", "没有选中分区")
            return
        out_dir = QFileDialog.getExistingDirectory(
            self, "选择读取输出目录", self._root)
        if not out_dir:
            self.log_line.emit("warn", "flash", "已取消读取（未提交任何命令）")
            return
        out_dir = os.path.normpath(out_dir)
        planned = [dict(row, path=os.path.join(out_dir, "%s.img" % row["name"]))
                   for row in rows]
        self._begin_run(planned, "read", "读取")

    def _submit_next(self):
        if not self._queue:
            self._finish_run()
            return
        row = self._queue.pop(0)
        name = row["name"]
        self._inflight = name
        self.table.set_status(name, "提交中", T.MUTE)

        def ack(res, partition=name):
            ok = bool(res.get("ok"))
            # 写/读意图受理即 RUNNING，终局由 command 事件给出；受理失败才在此结算。
            if ok:
                return
            self._inflight = ""
            self._done += 1
            self.table.set_status(partition, "被拒", T.ALERT)
            self._failed += 1
            self.log_line.emit(
                "err", "flash", "%s 被拒: %s %s"
                % (partition, res.get("code", ""), res.get("detail", "")))
            self._paint_progress()
            if self._queue:
                self._submit_next()
            else:
                self._finish_run()

        try:
            if self._mode == "read":
                self._bridge.read(name, row["path"], ack)
            else:
                self._bridge.write(name, row["image"], ack)
        except Exception as exc:
            self._queue, self._inflight, self._running, self._mode = [], "", False, ""
            self.table.set_status(name, "提交失败", T.ALERT)
            self.log_line.emit("err", "flash", "提交失败: %r" % (exc,))
            self._refresh_buttons()

    def _finish_run(self):
        self._running = False
        done_text = "读取完成" if self._mode == "read" else "完成"
        self._mode = ""
        if self._failed:
            text = "有失败 · %d/%d" % (self._done - self._failed, self._total)
            self._set_status(text, T.ALERT)
            self._dlg.finish(text, ok=False, total=self._total)
        else:
            text = "%s · %d 个分区" % (done_text, self._total)
            self._set_status(text, T.INK)
            self._dlg.finish(text, ok=True, total=self._total)
        self._refresh_buttons()

    def cancel_flash(self):
        self._queue, self._inflight = [], ""
        self._running = False
        self._mode = ""
        self._set_status("已停止排队", T.MUTE)
        self.log_line.emit("warn", "flash", "已停止后续提交（当前命令仍会跑完）")
        self._refresh_buttons()

    # ---- 内部 -------------------------------------------------------

    def _on_log(self, level, source, text, ts):
        if source in ("flash", "session", "command"):
            self.log_line.emit(level, source, text)

    def _paint_gate(self, lit, failed=False):
        for i, key in enumerate(GATE_KEYS):
            if failed:
                color = T.ALERT
            elif i < lit:
                color = T.INK
            elif i == lit:
                color = T.MUTE
            else:
                color = T.DIM
            self._dots[key].setStyleSheet(f"color:{color}; font-size:8px;")

    def _refresh_buttons(self):
        gated = self._state == "gated"
        busy = self._state in ("listening", "fb_probing", "fb_ready",
                               "kernel_wait", "bootstrapping")
        allowed = self._state in ("boot", "dead", "failed", "unknown")
        # 缺 DA 镜像时禁用认证按钮，文案写明缺哪个。
        missing_da = []
        if not self._da1:
            missing_da.append("DA1")
        if not self._da2:
            missing_da.append("2nd-DA")
        self.btn_fire.setEnabled(allowed and not missing_da)
        if missing_da and allowed:
            self.btn_fire.setText("先在资源页选择 %s 镜像" % " 与 ".join(missing_da))
        elif gated:
            self.btn_fire.setText("会话已就绪")
        elif self._state == "unknown":
            self.btn_fire.setText("状态未知 · 可重新认证")
        elif busy:
            self.btn_fire.setText("认证中…")
        elif self._state == "failed":
            self.btn_fire.setText("重新认证")
        else:
            self.btn_fire.setText("认证并建立会话")
        self.btn_read.setEnabled(gated and not self._running)
        self.btn_start.setEnabled(gated and not self._running)
        self.btn_cancel.setEnabled(self._running)


class LogsPage(QWidget):
    LEVELS = (("全部", None), ("信息", "info"), ("警告", "warn"), ("错误", "err"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []
        self._pending = []
        self._level = None
        self._query = ""
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(16)
        self._flush_timer.timeout.connect(self._flush)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._chips = []
        for i, (name, level) in enumerate(self.LEVELS):
            b = QPushButton(name)
            b.setObjectName("GhostSmall")
            b.setCheckable(True)
            b.setFixedHeight(24)
            b.setChecked(i == 0)
            b.clicked.connect(lambda _=False, lv=level: self._set_level(lv))
            bar.addWidget(b)
            self._chips.append(b)
        bar.addSpacing(8)
        search = QLineEdit()
        search.setObjectName("Search")
        search.setPlaceholderText("过滤关键字：分区 / 地址 / 帧")
        search.setFixedWidth(240)
        search.setFixedHeight(24)
        search.textChanged.connect(self._set_query)
        bar.addWidget(search)
        bar.addStretch(1)
        exp = QPushButton("导出")
        exp.setObjectName("GhostSmall")
        exp.setFixedHeight(24)
        exp.clicked.connect(self._export)
        clear = QPushButton("清空")
        clear.setObjectName("GhostSmall")
        clear.setFixedHeight(24)
        clear.clicked.connect(self.clear)
        bar.addWidget(exp)
        bar.addWidget(clear)
        root.addLayout(bar)

        self.view = QPlainTextEdit()
        self.view.setObjectName("LogView")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(4000)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self.view, 1)

    def _set_level(self, level):
        self._level = level
        for b, (_, lv) in zip(self._chips, self.LEVELS):
            b.setChecked(lv == level)
        self._rebuild()

    def _set_query(self, text):
        self._query = text.strip().lower()
        self._rebuild()

    def clear(self):
        self._pending = []
        self._flush_timer.stop()
        self._entries.clear()
        self.view.clear()

    def _matches(self, level, source, text):
        if self._level and level != self._level:
            return False
        if self._query and self._query not in (source + " " + text).lower():
            return False
        return True

    def append(self, level, source, text):
        self.on_log(level, source, text, time.strftime("%H:%M:%S"))

    def on_log(self, level, source, text, ts):
        self._append(level, source, text, ts)

    def _append(self, level, source, text, ts):
        self._entries.append((level, source, text, ts))
        # 条目只留最近 20000 条：视图只留 4000 块，历史列表同样不能无限长。
        if len(self._entries) > 20000:
            del self._entries[:len(self._entries) - 20000]
        if not self._matches(level, source, text):
            return
        # 大分区读的日志是一阵一阵来的；逐条 appendHtml + 滚动会让 GUI 线程
        # 在整段突发里没有空闲。先入队，由 16ms 定时器一次刷完。
        self._pending.append((level, source, text, ts))
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush(self):
        if not self._pending:
            return
        batch, self._pending = self._pending, []
        bar = self.view.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 4
        self.view.setUpdatesEnabled(False)
        try:
            for level, source, text, ts in batch:
                self.view.appendHtml(self._fmt(level, source, text, ts))
        finally:
            self.view.setUpdatesEnabled(True)
        if follow:
            bar.setValue(bar.maximum())

    @staticmethod
    def _fmt(level, source, text, ts):
        from html import escape
        color = {"ok": T.INK, "warn": T.INK, "err": T.ALERT,
                 "info": T.INK_2, "dim": T.DIM}.get(level, T.INK_2)
        tag = level.upper().ljust(4).replace(" ", "&nbsp;")
        # 日志正文可能含 XML / 尖括号（raw= 模式尤其如此）；不转义会被
        # appendHtml 当标签解析，整行内容直接消失。超长行再兜一层：单行
        # 兆级文本会让 QPlainTextEdit 的排版直接卡死。
        text = str(text)
        if len(text) > 4000:
            text = text[:4000] + "…(%d 字符)" % len(text)
        return (f'<span style="color:{T.DIM}">{ts}</span>'
                f'&nbsp;&nbsp;<span style="color:{color}">{tag}</span>'
                f'&nbsp;&nbsp;<span style="color:{T.DIM}">{escape(str(source))}</span>'
                f'&nbsp;&nbsp;<span style="color:{color}">{escape(text)}</span>')

    def _rebuild(self):
        self._pending = []
        self._flush_timer.stop()
        self.view.clear()
        self.view.setUpdatesEnabled(False)
        try:
            for level, source, text, ts in self._entries:
                if self._matches(level, source, text):
                    self.view.appendHtml(self._fmt(level, source, text, ts))
        finally:
            self.view.setUpdatesEnabled(True)

    def _export(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", "run.log",
                                              "日志 (*.log);;文本 (*.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            for level, source, text, ts in self._entries:
                fh.write(f"{ts}  {level.upper():<4}  {source}  {text}\n")


def _verdict_of(run_dir):
    path = os.path.join(run_dir, "verdict.txt")
    if not os.path.isfile(path):
        return "", ""
    verdict = state = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("verdict="):
                    verdict = line.split("=", 1)[1]
                elif line.startswith("state="):
                    state = line.split("=", 1)[1]
    except OSError:
        return "", ""
    return verdict, state


class RunsPage(QWidget):
    def __init__(self, runs_root="", parent=None):
        super().__init__(parent)
        self._root = runs_root
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.tiles = {
            "total": Readout("总轮次", "0", runs_root or DASH),
            "scored": Readout("有判值", "0", "verdict.txt 在位", T.INK),
            "unscored": Readout("无判值", "0", "缺 verdict.txt", T.MUTE),
            "latest": Readout("最新轮次", DASH, ""),
        }
        bar = ReadoutBar()
        for tile in self.tiles.values():
            bar.add(tile)
        root.addWidget(bar)

        card = Card("运行记录")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["轮次", "判值", "状态", "产物", "结束"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setFixedHeight(24)
        for c, w in ((0, 168), (1, 120), (2, 120), (3, 60)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            hh.resizeSection(c, w)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        card.body().addWidget(self.table)
        root.addWidget(card, 1)

    def refresh(self, limit=40):
        if not self._root or not os.path.isdir(self._root):
            self.tiles["total"].set_value("0", T.MUTE, self._root or DASH)
            return []
        # 轮次目录名形如 YYYYMMDD_HHMMSS（可带后缀）；其余同层目录不算轮次。
        names = sorted((n for n in os.listdir(self._root)
                        if len(n) >= 9 and n[:8].isdigit() and n[8] == "_"
                        and os.path.isdir(os.path.join(self._root, n))),
                       reverse=True)
        total = len(names)
        scored = 0
        rows = []
        for name in names:
            run_dir = os.path.join(self._root, name)
            verdict, state = _verdict_of(run_dir)
            if verdict:
                scored += 1
            try:
                files = sum(1 for n in os.listdir(run_dir)
                            if os.path.isfile(os.path.join(run_dir, n)))
                mtime = time.strftime("%m-%d %H:%M",
                                      time.localtime(os.path.getmtime(run_dir)))
            except OSError:
                files, mtime = 0, DASH
            rows.append((name, verdict or DASH, state or DASH,
                         str(files), mtime, bool(verdict)))
        self.tiles["total"].set_value(str(total), T.INK)
        self.tiles["scored"].set_value(str(scored), T.INK)
        self.tiles["unscored"].set_value(str(total - scored), T.MUTE)
        if names:
            self.tiles["latest"].set_value(names[0], T.INK)
        shown = rows[:limit]
        self.table.setRowCount(len(shown))
        for r, (name, verdict, state, files, mtime, ok) in enumerate(shown):
            self.table.setRowHeight(r, 24)
            for c, text in enumerate((name, verdict, state, files, mtime)):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if c in (0, 4):
                    item.setFont(QFont("Cascadia Mono", 8))
                if c == 1:
                    item.setForeground(QColor(T.INK if ok else T.MUTE))
                self.table.setItem(r, c, item)
        return rows
