# -*- coding: utf-8 -*-
"""自绘控件：单色、直角、紧凑。

三条原则，写在每一个 paintEvent 里：

    · 不用阴影和渐变 —— 它们靠模拟光照表达层级；这里层级靠明度，
      加一层阴影等于在同一句话上盖两个语调；
    · 不用圆角胶囊 —— 胶囊把两块信息装进一个软容器；读数是硬边界；
    · 状态只在明度轴上移动，唯一的例外是失败（ALERT）。

读数（Readout）是本表唯一由自绘结构承担的信息块：一条 2px 的明度轨
加三行字。四个读数并排时共用外框，形成一块仪表条，而不是四张卡片。
"""

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from . import theme as T
from .icons import draw_icon

DASH = "—"


def typeface(size: int, weight: QFont.Weight = QFont.Weight.Normal,
          mono: bool = False, tracking: float = 0.0) -> QFont:
    f = QFont(T.MONO_FAMILY if mono else T.UI_FAMILY)
    f.setPixelSize(size)
    f.setWeight(weight)
    if tracking:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, tracking)
    return f


class Card(QFrame):
    """硬边界面板：1px 线，近直角，标题是一段带竖向标记的微字。"""

    def __init__(self, title: str | None = None, pad: int = 12, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(pad, pad - 2, pad, pad)
        self._v.setSpacing(8)
        self._title = None
        if title:
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(7)
            mark = QLabel()
            mark.setObjectName("CardMark")
            mark.setFixedSize(2, 9)
            head.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
            lab = QLabel(title)
            lab.setObjectName("CardTitle")
            head.addWidget(lab, 0, Qt.AlignmentFlag.AlignVCenter)
            head.addStretch(1)
            self._v.addLayout(head)
            self._title = lab

    def body(self) -> QVBoxLayout:
        return self._v

    def add(self, w):
        self._v.addWidget(w)
        return w


class Divider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CardRule")
        self.setFixedHeight(1)


class Pill(QWidget):
    """状态徽章：1px 直角框 + 4px 实心方块 + 微字。"""

    def __init__(self, text: str, color: str = T.MUTE, parent=None):
        super().__init__(parent)
        self._text = text
        self._color = QColor(color)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_text(self, text: str, color: str | None = None):
        self._text = text
        if color:
            self._color = QColor(color)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(typeface(10))
        return QSize(fm.horizontalAdvance(self._text) + 30, 19)

    def paintEvent(self, _):
        p = QPainter(self)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor(T.LINE), 1))
        p.setBrush(QColor(T.CARD_2))
        p.drawRect(r)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawRect(QRectF(8, r.center().y() - 2, 4, 4))

        p.setFont(typeface(10))
        p.setPen(self._color)
        p.drawText(QRectF(19, 0, self.width() - 26, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self._text)


class KeyValue(QWidget):
    def __init__(self, key: str, value: str, mono: bool = False, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        k = QLabel(key)
        k.setObjectName("KeyText")
        k.setFixedWidth(62)
        self._value = QLabel(value)
        self._value.setObjectName("ValueMono" if mono else "Value")
        self._value.setWordWrap(True)
        lay.addWidget(k, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self._value, 1)

    def set_value(self, value: str):
        self._value.setText(value)


class Readout(QFrame):
    """一个读数：明度轨 + 标签 + 值 + 备注。四块并排即为仪表条。"""

    def __init__(self, label: str, value: str = DASH, sub: str = "",
                 tone: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Readout")
        self._tone = tone or T.MUTE

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._rail = QFrame()
        self._rail.setFixedWidth(2)
        outer.addWidget(self._rail)

        box = QVBoxLayout()
        box.setContentsMargins(11, 7, 12, 8)
        box.setSpacing(2)

        self._label = QLabel(label)
        self._label.setObjectName("ReadoutLabel")
        self._value = QLabel(value)
        self._sub = QLabel(sub)
        self._sub.setObjectName("ReadoutSub")

        box.addWidget(self._label)
        box.addWidget(self._value)
        box.addWidget(self._sub)
        outer.addLayout(box, 1)

        self._apply_tone(self._tone)

    def _apply_tone(self, tone: str):
        # 一个读数里只有值是数据，其余都在说明这份数据：值必须最重。
        # 字号写进控件样式表——应用级 QSS 的 font-size 会盖掉 setFont()。
        self._tone = tone
        self._rail.setStyleSheet(f"background:{tone};")
        self._value.setStyleSheet(
            f"color:{tone}; font-family:{T.F_MONO};"
            f" font-size:{T.FS_XL}; font-weight:600;")

    def set_value(self, value: str, tone: str | None = None, sub: str | None = None):
        self._value.setText(str(value))
        if tone is not None:
            self._apply_tone(tone)
        if sub is not None:
            self._sub.setText(sub)


class ReadoutBar(QFrame):
    """把若干 Readout 并成一整块：共用外框，内部用 1px 线分格。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ReadoutBar")
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._n = 0

    def add(self, readout: QWidget):
        if self._n:
            sep = QFrame()
            sep.setObjectName("ReadoutSep")
            sep.setFixedWidth(1)
            self._lay.addWidget(sep)
        self._lay.addWidget(readout, 1)
        self._n += 1
        return readout


# 旧名保留：页面里按 StatTile 构造的调用点不必逐个改名。
StatTile = Readout


class NavItem(QAbstractButton):
    """导航行：整行反白 + 左侧 2px 明度轨。不用胶囊。"""

    def __init__(self, icon: str, text: str, parent=None):
        super().__init__(parent)
        self._icon = icon
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(T.NAV_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        active = self.isChecked()
        hover = self.underMouse()
        r = QRectF(0, 0, self.width(), self.height())

        if active:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.CARD_2))
            p.drawRect(r)
            p.setBrush(QColor(T.INK))
            p.drawRect(QRectF(0, 0, 2, self.height()))
        elif hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.CARD))
            p.drawRect(r)

        color = T.INK if active else (T.INK_2 if hover else T.MUTE)
        draw_icon(p, self._icon, QRectF(14, (self.height() - 14) / 2, 14, 14),
                  color, 1.3)

        p.setFont(typeface(12, QFont.Weight.Medium if active else QFont.Weight.Normal,
                        tracking=0.3))
        p.setPen(QColor(color))
        p.drawText(QRectF(38, 0, self.width() - 46, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self.text())


class Segmented(QWidget):
    changed = Signal(int)

    def __init__(self, options: list[str], current: int = 0, parent=None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget#Segmented {{ background:{T.CARD}; border:1px solid {T.LINE};"
            f" border-radius:{T.R_SM}; }}"
            f"QPushButton {{ background:transparent; border:none;"
            f" padding:0 14px; color:{T.MUTE}; font-size:{T.FS_SM}; }}"
            f"QPushButton:hover {{ color:{T.INK}; }}"
            f"QPushButton:checked {{ background:{T.CARD_2}; color:{T.INK};"
            f" font-weight:600; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        self._buttons: list[QPushButton] = []
        for i, opt in enumerate(options):
            b = QPushButton(opt)
            b.setCheckable(True)
            b.setFixedHeight(24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=i: self.set_current(i))
            lay.addWidget(b)
            self._buttons.append(b)
        self._current = -1
        self.set_current(current)

    def set_current(self, i: int):
        if i == self._current:
            return
        self._current = i
        for j, b in enumerate(self._buttons):
            b.setChecked(j == i)
        self.changed.emit(i)

    def current(self) -> int:
        return self._current


class ChainRail(QWidget):
    """阶段轨：等距刻度，完成段为实心方块。"""

    def __init__(self, stages: list[str], parent=None):
        super().__init__(parent)
        self.stages = stages
        self._done = 0
        self._active = -1
        self._pulse = 0.0
        self._pulse_dir = 1
        self.setMinimumHeight(52)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_pulse)

    def _on_pulse(self):
        self._pulse += 0.07 * self._pulse_dir
        if self._pulse >= 1.0:
            self._pulse, self._pulse_dir = 1.0, -1
        elif self._pulse <= 0.0:
            self._pulse, self._pulse_dir = 0.0, 1
        self.update()

    def set_state(self, done: int, active: int):
        self._done, self._active = done, active
        if active >= 0:
            self._timer.start(40)
        else:
            self._timer.stop()
            self._pulse = 0.0
        self.update()

    def reset(self):
        self._done, self._active = 0, -1
        self._timer.stop()
        self._pulse = 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        n = len(self.stages)
        left, right = 10.0, self.width() - 10.0
        y = 14.0
        step = (right - left) / (n - 1) if n > 1 else 0

        p.setPen(QPen(QColor(T.LINE), 1.0))
        p.drawLine(int(left), int(y), int(right), int(y))

        if self._done > 0:
            end = left + step * min(self._done, n - 1)
            p.setPen(QPen(QColor(T.MUTE), 1.4))
            p.drawLine(int(left), int(y), int(end), int(y))

        for i, label in enumerate(self.stages):
            x = left + step * i
            if i < self._done:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(T.INK))
                p.drawRect(QRectF(x - 3.5, y - 3.5, 7, 7))
                color = T.MUTE
            elif i == self._active:
                alpha = int(90 + 120 * self._pulse)
                halo = QColor(T.INK)
                halo.setAlpha(alpha)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(halo)
                p.drawRect(QRectF(x - 3.5, y - 3.5, 7, 7))
                color = T.INK
            else:
                p.setPen(QPen(QColor(T.LINE), 1.0))
                p.setBrush(QColor(T.SURF))
                p.drawRect(QRectF(x - 3, y - 3, 6, 6))
                color = T.DIM

            p.setFont(typeface(10, tracking=0.3))
            p.setPen(QColor(color))
            p.drawText(QRectF(x - 40, y + 9, 80, 16),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       label)


class MiniChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[float] = []
        self.setMinimumHeight(24)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def push(self, value: float):
        self._data.append(value)
        if len(self._data) > 60:
            self._data.pop(0)
        self.update()

    def clear(self):
        self._data.clear()
        self.update()

    def paintEvent(self, _):
        if len(self._data) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        pad = 1
        max_v = max(self._data) or 1.0

        path = QPainterPath()
        for i, v in enumerate(self._data):
            x = pad + i * (w - 2 * pad) / (len(self._data) - 1)
            y = h - pad - (v / max_v) * (h - 2 * pad)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        p.setPen(QPen(QColor(T.MUTE), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
