# -*- coding: utf-8 -*-
"""时间预算：所有毫秒常量只在这里定义。

约定：
- 对话预算（等设备下一帧）与单笔传输预算是两回事，不得互相替代。
- poll 窗口只表示"本轮最多等多久再来一批"，不是攒批延时。
- 其他模块不得自带时间数字；新增预算一律先加到本文件与 TIMEOUT_PROFILES。
"""

# 命令循环节拍（原 BEAT = 0.041 s）
TICK_MS = 41

# 一轮收帧窗口（原 DIALOG_POLL_S = 0.05 s）
POLL_WINDOW_MS = 50

# 单次读超时（原 DIALOG_READ_MS）
DIALOG_READ_MS = 20

# 一条命令的对话预算：等设备下一帧（原 HOST_TIMEOUT = 1.0 s）
DIALOG_MS = 1000

# 收讫帧预算
ACK_MS = 1000

# 全局等待上限：START/dialog 等仍受此值约束。
# upload 档按用户当轮判定要求单独放到 10 s（官方 2 MiB 块 ≈ 2.77 s）。
MAX_WAIT_MS = 3000

# 命令循环首个帧的预算（设备进入命令态）
DEV_INIT_MS = MAX_WAIT_MS

# 刷写占有时段的无帧预算
WRITE_SILENCE_MS = MAX_WAIT_MS

# 一次上传的总预算：10 s（用户当轮判定预算）
UPLOAD_TOTAL_MS = 10000

# 单笔 bulk 传输预算
TRANSFER_MS = MAX_WAIT_MS

# 单次读超时下限
READ_MS = 250

# 普通静默判据
SILENCE_MS = DIALOG_MS

# 设备心跳周期（实机实测 1000.4 ms，取自 runs\20261021_071110 的 17 个间期）
HEARTBEAT_MS = 1000

# 活性预算：连续 3 个心跳周期【一帧都没有】才判「会话已死」。
# 旧实现把「等下一帧的对话预算」（DIALOG_MS=1000）直接当活性判据，余量只有
# ~3 ms——丢一拍心跳、或某一帧晚到 3 ms，就会把活着的设备判成死。
LIVENESS_MS = 3 * HEARTBEAT_MS

# 引导阶段预算
BOOTSTRAP_MS = DEV_INIT_MS

# 超时档：CommandSpec.timeout_profile 只能取这里的键
TIMEOUT_PROFILES = {
    "dialog": DIALOG_MS,
    "ack": ACK_MS,
    "transfer": TRANSFER_MS,
    "write": WRITE_SILENCE_MS,
    "upload": UPLOAD_TOTAL_MS,
    "boot": BOOTSTRAP_MS,
}

DEFAULT_TIMEOUT_PROFILE = "dialog"


def profile_ms(name):
    """按档名取毫秒预算；未知档直接报错，不允许静默回退。"""
    try:
        return TIMEOUT_PROFILES[name]
    except KeyError:
        raise KeyError("未知超时档: %r" % (name,))
