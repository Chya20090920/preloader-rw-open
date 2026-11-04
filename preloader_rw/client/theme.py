# -*- coding: utf-8 -*-
"""调色与排版：单色工程控制台。

三条约束，来自需求本身：
    · 简约黑白 —— 全表只有一个非中性色，且只用于失败；
    · 紧凑     —— 内边距、行高、字号按 2px 栅格压到最小可读；
    · 专业工具 —— 1px 实线分格，无阴影、无渐变、无大圆角，数值一律等宽。

层级只由明度承担，不由颜色承担：

    底 → 面      VOID  <  SURF  <  CARD  <  CARD_2
    弱 → 强      GHOST <  DIM   <  MUTE  <  INK
    状态         空闲 DIM · 进行中 MUTE · 就绪 INK · 失败 ALERT

明度阶梯是四阶，正好够放四种状态；这也是本表不用状态色的原因——
多一个色相，就多一个可以说谎的地方。
"""

# ---- 面 ------------------------------------------------------------------
VOID = "#0B0C0D"      # 应用底、日志底
SURF = "#121415"      # 侧栏、顶栏、底栏、表头
CARD = "#181B1E"      # 卡片、输入框、下拉
CARD_2 = "#23272B"    # 选中行、按下态、卡内次级块

# ---- 线 ------------------------------------------------------------------
LINE = "#333A40"      # 主分格线
LINE_2 = "#21262A"    # 次级线（表格行线、卡内分隔）

# ---- 字 ------------------------------------------------------------------
INK = "#EAEBE9"       # 主文字、就绪、成功
INK_2 = "#C6CAC8"     # 正文（日志、长文本）
MUTE = "#8B9199"      # 次级文字、进行中
DIM = "#666D75"       # 三级文字、标签、占位
GHOST = "#3E444B"     # 禁用、极弱提示

# ---- 唯一的彩色 ----------------------------------------------------------
ALERT = "#D9443F"     # 失败、错误。除它之外全表无色相。

# ---- 字体 ----------------------------------------------------------------
F_UI = '"Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif'
F_MONO = '"Cascadia Mono", "Consolas", "SFMono-Regular", monospace'
FONT_UI = F_UI          # 兼容旧名
FONT_MONO = F_MONO      # 兼容旧名
FONT_NUM = F_MONO       # 数值没有独立字体族：等宽即读数

UI_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Cascadia Mono"

# ---- 字号（紧凑）---------------------------------------------------------
FS_XS = "10px"    # 标签、表头、状态栏
FS_SM = "11px"    # 次级正文、表格、日志
FS_MD = "12px"    # 正文
FS_BASE = "12px"
FS_LG = "13px"    # 页标题、设备名
FS_XL = "15px"    # 读数

# ---- 半径（近直角）-------------------------------------------------------
R_SM = "0px"
R_MD = "0px"
R_LG = "0px"

# ---- 尺寸（装配用，不进 QSS）---------------------------------------------
SIDEBAR_W = 172
TOPBAR_H = 40
STATUS_H = 24
NAV_H = 30

# 按钮统一刻度：全页按钮同一高度、动作按钮同一宽度，同列自然对齐。
BTN_H = 22
BTN_W = 64


QSS = """
QWidget {
    color: $INK;
    font-family: $F_UI;
    font-size: $FS_MD;
}
QMainWindow, QWidget#Root { background: $VOID; }

/* ---- 侧栏 ---- */
QFrame#Sidebar { background: $SURF; border-right: 1px solid $LINE; }
QLabel#BrandName { color: $INK; font-size: $FS_MD; font-weight: 600; }
QFrame#SideDevice { background: $CARD; border: 1px solid $LINE; border-radius: $R_SM; }
QLabel#SideDeviceId { color: $DIM; font-size: $FS_XS; }
QLabel#SideDeviceState { color: $MUTE; font-size: $FS_XS; }

/* ---- 顶栏 / 底栏 ---- */
QFrame#TopBar { background: $SURF; border-bottom: 1px solid $LINE; }
QLabel#PageTitle { color: $INK; font-size: $FS_LG; font-weight: 600; }
QFrame#StatusBar { background: $SURF; border-top: 1px solid $LINE; }
QLabel#StatusText { color: $DIM; font-size: $FS_XS; font-family: $F_MONO; }
QLabel#StatusVer { color: $GHOST; font-size: $FS_XS; }

/* ---- 卡片 ---- */
QFrame#Card { background: $CARD; border: 1px solid $LINE; border-radius: $R_SM; }
QFrame#CardRule { background: $LINE_2; border: none; }
QLabel#CardTitle { color: $DIM; font-size: $FS_XS; }
QLabel#CardMark { background: $MUTE; }
QLabel#Hint { color: $DIM; font-size: $FS_SM; }
QLabel#Value { color: $INK; font-size: $FS_MD; }
QLabel#ValueMono { color: $INK; font-family: $F_MONO; font-size: $FS_SM; }
QLabel#KeyText { color: $DIM; font-size: $FS_SM; }

/* ---- 读数条 ---- */
QFrame#ReadoutBar { background: $CARD; border: 1px solid $LINE; border-radius: $R_SM; }
QFrame#Readout { background: transparent; border: none; }
QFrame#ReadoutSep { background: $LINE_2; border: none; }
QLabel#ReadoutLabel { color: $DIM; font-size: $FS_XS; }
QLabel#ReadoutSub { color: $DIM; font-size: $FS_XS; }
QLabel#BigNum { color: $INK; font-family: $F_MONO; font-size: 22px; font-weight: 600; }
QLabel#BigNumUnit { color: $DIM; font-size: $FS_SM; }

/* ---- 刷写页：链路条与执行条 ---- */
QFrame#Rail { background: transparent; border: none;
              border-bottom: 1px solid $LINE; }
QFrame#Foot { background: transparent; border: none; }
QLabel#RailDot { color: $GHOST; }
QLabel#RailSep { color: $GHOST; font-size: $FS_XS; }
QLabel#RailText { color: $DIM; font-size: $FS_XS; }
QFrame#Deck { background: transparent; border: none;
              border-top: 1px solid $LINE; }
QLabel#DlgStatus { color: $INK; font-size: $FS_MD; }
QLabel#DlgPct { color: $INK; font-family: $F_MONO; font-size: $FS_MD; font-weight: 600; }
QDialog QProgressBar#Progress { background: $CARD; }
QLabel#DlgDetail { color: $DIM; font-size: $FS_SM; }
QFrame#Sep { background: $LINE; border: none; }

/* ---- 按钮 ---- */
QPushButton#Primary {
    background: $INK; color: $VOID; border: 1px solid $INK;
    border-radius: $R_SM; padding: 0 14px; font-weight: 600;
}
QPushButton#Primary:hover { background: #FFFFFF; border-color: #FFFFFF; }
QPushButton#Primary:pressed { background: $INK_2; border-color: $INK_2; }
QPushButton#Primary:disabled {
    background: $SURF; color: $GHOST; border: 1px solid $LINE;
}

QPushButton#Ghost {
    background: transparent; color: $MUTE; border: 1px solid $LINE;
    border-radius: $R_SM; padding: 0 12px;
}
QPushButton#Ghost:hover { color: $INK; border-color: $GHOST; background: $CARD_2; }
QPushButton#Ghost:pressed { background: $CARD; }
QPushButton#Ghost:disabled { color: $GHOST; border-color: $LINE_2; }

QPushButton#PathPicker {
    background: $CARD; color: $MUTE; border: 1px solid $LINE;
    border-radius: $R_SM; padding: 0 9px; text-align: left;
    font-size: $FS_SM; font-family: $F_MONO;
}
QPushButton#PathPicker:hover { color: $INK; border-color: $GHOST; }

QPushButton#GhostSmall {
    background: transparent; color: $MUTE; border: 1px solid $LINE;
    border-radius: $R_SM; padding: 0 9px; font-size: $FS_SM;
}
QPushButton#GhostSmall:hover { color: $INK; border-color: $GHOST; }
QPushButton#GhostSmall:pressed { background: $CARD_2; }
QPushButton#GhostSmall:checked {
    background: $CARD_2; color: $INK; border-color: $INK;
}

/* ---- 表格 ---- */
QTableWidget {
    background: transparent; border: none;
    gridline-color: transparent; outline: 0;
    selection-background-color: $CARD_2;
    font-size: $FS_SM;
}
QTableWidget::item { padding-left: 8px; border-bottom: 1px solid $LINE_2; }
QTableWidget::item:selected { background: $CARD_2; color: $INK; }
QHeaderView { background: $SURF; }
QHeaderView::section {
    background: $SURF; color: $DIM; border: none;
    border-right: 1px solid $LINE_2; border-bottom: 1px solid $LINE;
    padding: 0 8px; font-size: $FS_XS; font-weight: 600;
}
QTableWidget QTableCornerButton::section { background: $SURF; border: none; }

/* ---- 表单 ---- */
QLineEdit#Search {
    background: $CARD; border: 1px solid $LINE; border-radius: $R_SM;
    padding: 0 8px; color: $INK; font-size: $FS_SM;
    selection-background-color: $CARD_2;
}
QLineEdit#Search:focus { border-color: $GHOST; }
QComboBox {
    background: $CARD; color: $INK; border: 1px solid $LINE;
    border-radius: $R_SM; padding: 0 8px; font-size: $FS_SM;
}
QComboBox:hover { border-color: $GHOST; }
QComboBox:focus { border-color: $MUTE; }
QComboBox::drop-down { border: none; width: 16px; }
QComboBox QAbstractItemView {
    background: $CARD; color: $INK; border: 1px solid $LINE;
    outline: 0; selection-background-color: $CARD_2; selection-color: $INK;
}
QCheckBox { color: $INK; spacing: 8px; font-size: $FS_SM; }
QCheckBox::indicator {
    width: 13px; height: 13px;
    border: 1px solid $GHOST; border-radius: 2px; background: $CARD;
}
QCheckBox::indicator:hover { border-color: $MUTE; }
QCheckBox::indicator:checked { background: $INK; border-color: $INK; }
QCheckBox::indicator:disabled { background: $SURF; border-color: $LINE; }
QCheckBox::indicator:checked:disabled { background: $GHOST; border-color: $GHOST; }

/* ---- 日志 ---- */
QPlainTextEdit#LogView {
    background: $VOID; border: 1px solid $LINE; border-radius: $R_SM;
    color: $INK_2; font-family: $F_MONO; font-size: $FS_SM;
    selection-background-color: $CARD_2; padding: 3px 8px;
}

/* ---- 滚动条 ---- */
QScrollBar:vertical { background: transparent; width: 9px; margin: 0; }
QScrollBar::handle:vertical {
    background: $GHOST; border-radius: 0; min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: $MUTE; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 9px; margin: 0; }
QScrollBar::handle:horizontal {
    background: $GHOST; border-radius: 0; min-width: 28px;
}
QScrollBar::handle:horizontal:hover { background: $MUTE; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }

/* ---- 进度 ---- */
QProgressBar#Progress {
    background: $SURF; border: none; border-radius: 0;
    text-align: center; color: transparent;
}
QProgressBar#Progress::chunk { background: $INK; border-radius: 0; }

/* ---- 对话框 ---- */
QDialog { background: $SURF; }
QDialog QLabel { color: $INK; }
QDialog QListWidget {
    background: $CARD; border: 1px solid $LINE; border-radius: $R_SM;
    color: $INK; outline: 0;
}
QDialog QListWidget::item { color: $INK; padding: 6px 8px; border: none; }
QDialog QListWidget::item:hover { background: $CARD_2; }
QDialog QListWidget::item:selected { background: $CARD_2; color: $INK; }
QDialogButtonBox QPushButton {
    background: transparent; color: $INK; border: 1px solid $LINE;
    border-radius: $R_SM; padding: 0 18px; min-width: 68px; height: 24px;
}
QDialogButtonBox QPushButton:hover { background: $CARD_2; border-color: $GHOST; }
QDialogButtonBox QPushButton:default {
    background: $INK; color: $VOID; border-color: $INK; font-weight: 600;
}
QDialogButtonBox QPushButton:default:hover { background: #FFFFFF; }

QMessageBox { background: $SURF; }
QMessageBox QLabel { color: $INK; font-size: $FS_MD; }
QMessageBox QPushButton {
    background: transparent; color: $INK; border: 1px solid $LINE;
    border-radius: $R_SM; padding: 0 18px; min-width: 70px; min-height: 24px;
}
QMessageBox QPushButton:hover { background: $CARD_2; border-color: $GHOST; }

QToolTip {
    background: $CARD; color: $INK; border: 1px solid $LINE;
    padding: 4px 6px; font-size: $FS_SM;
}

QMenu { background: $CARD; color: $INK; border: 1px solid $LINE; }
QMenu::item { padding: 5px 20px 5px 14px; }
QMenu::item:selected { background: $CARD_2; }
"""


def stylesheet() -> str:
    mapping = {
        "$VOID": VOID, "$SURF": SURF, "$CARD": CARD, "$CARD_2": CARD_2,
        "$LINE": LINE, "$LINE_2": LINE_2,
        "$INK": INK, "$INK_2": INK_2, "$MUTE": MUTE, "$DIM": DIM, "$GHOST": GHOST,
        "$ALERT": ALERT,
        "$F_UI": F_UI, "$F_MONO": F_MONO,
        "$FS_XS": FS_XS, "$FS_SM": FS_SM, "$FS_MD": FS_MD,
        "$FS_BASE": FS_BASE, "$FS_LG": FS_LG, "$FS_XL": FS_XL,
        "$R_SM": R_SM, "$R_MD": R_MD, "$R_LG": R_LG,
    }
    out = QSS
    for key, value in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        out = out.replace(key, value)
    return out
