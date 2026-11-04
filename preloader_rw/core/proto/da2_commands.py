# -*- coding: utf-8 -*-
"""DA2 镜像内的命令名清单（设备端受限命令的镜像）。

本文件是“设备有哪些命令”的唯一事实来源；内核命令表（kernel/commands.py）
从这里取名字，product 层不得再维护第二份副本。

提取方式（2026-10-19，本机 Python 3 标准库，只读 DA2 镜像）：

    import re
    path = r"<2nd-DA 镜像路径>"          # 由 PANE_DA2 提供的同一份镜像
    data = open(path, "rb").read()
    names = re.findall(rb"CMD:[A-Za-z0-9_\\-]+", data)

命中结果：
  · 原始命中 37 条，按首次出现去重后仍为 37 条（无重复）。
  · 其中 7 条是传输 / 框架命令：START、END、UNKNOWN、DOWNLOAD-FILE、
    UPLOAD-FILE、PROGRESS-REPORT、FILE-SYS-OPERATION。
  · 其余 30 条为受限应用命令，即 RESTRICTED_COMMANDS。

名字校正：
  · 字面量是 `CMD:DEBUG:UFS`；上面的正则只吃到第一个冒号后的 `DEBUG`。
    以设备实际登记的名字为准，本清单记为 `CMD:DEBUG:UFS`。

未命中说明（盲区，不臆造）：
  · BOOT-TO、HOST-SUPPORTED-COMMANDS、CAN-HIGHER-USB-SPEED、
    SET-RUNTIME-PARAMETER 在 DA2 镜像中查不到 `CMD:` 字面量（偏移 = -1）。
    它们由 DA2 之外的阶段（DA1 / 引导）处理，内核命令表把它们标为 pre。
"""

__all__ = [
    "EXTRACTED_COMMANDS",
    "TRANSPORT_COMMANDS",
    "RESTRICTED_COMMANDS",
    "PRE_REGISTRATION_COMMANDS",
    "names",
    "has",
]

# 镜像内按首次出现顺序的 37 条原始命中（含 CMD: 前缀）。
EXTRACTED_COMMANDS = (
    "CMD:WRITE-EFUSE",
    "CMD:READ-EFUSE",
    "CMD:GET-HW-INFO",
    "CMD:GET-UFS-INFO",
    "CMD:READ-PARTITION-TABLE",
    "CMD:DEBUG:UFS",
    "CMD:EMMC-CONTROL",
    "CMD:REBOOT",
    "CMD:WRITE-PARTITION",
    "CMD:WRITE-FLASH",
    "CMD:READ-PARTITION",
    "CMD:READ-FLASH",
    "CMD:FLASH-ALL",
    "CMD:ERASE-PARTITION",
    "CMD:ERASE-FLASH",
    "CMD:FLASH-UPDATE",
    "CMD:WRITE-PARTITIONS",
    "CMD:SET-BOOT-MODE",
    "CMD:SET-RSC",
    "CMD:WRITE-PRIVATE-CERT",
    "CMD:GET-DA-INFO",
    "CMD:SET-HOST-INFO",
    "CMD:GET-DOWNLOADED-IMAGE-FEEDBACK",
    "CMD:SECURITY-GET-AUTH-TOKEN",
    "CMD:SECURITY-WRITE-AUTH",
    "CMD:NOTIFY-INIT-HW",
    "CMD:SECURITY-SET-ALLINONE-SIGNATURE",
    "CMD:GET-SYS-PROPERTY",
    "CMD:SECURITY-GET-DEV-FW-INFO",
    "CMD:SECURITY-SET-FLASH-POLICY",
    "CMD:START",
    "CMD:END",
    "CMD:UNKNOWN",
    "CMD:DOWNLOAD-FILE",
    "CMD:UPLOAD-FILE",
    "CMD:PROGRESS-REPORT",
    "CMD:FILE-SYS-OPERATION",
)

# 传输 / 框架命令：负责帧流转与文件搬运，不属于“受限应用命令”。
TRANSPORT_COMMANDS = (
    "CMD:START",
    "CMD:END",
    "CMD:UNKNOWN",
    "CMD:DOWNLOAD-FILE",
    "CMD:UPLOAD-FILE",
    "CMD:PROGRESS-REPORT",
    "CMD:FILE-SYS-OPERATION",
)

# 30 条受限应用命令（镜像内实际命中，保持首次出现顺序）。
RESTRICTED_COMMANDS = tuple(
    item for item in EXTRACTED_COMMANDS if item not in TRANSPORT_COMMANDS
)

# 注册之前由 DA1 / 引导阶段处理的命令（镜像内无 CMD: 字面量，见模块头）。
PRE_REGISTRATION_COMMANDS = (
    "CMD:BOOT-TO",
    "CMD:HOST-SUPPORTED-COMMANDS",
    "CMD:CAN-HIGHER-USB-SPEED",
    "CMD:SET-RUNTIME-PARAMETER",
)


def names():
    """返回去掉 CMD: 前缀的受限命令名，便于与命令表键名对齐。"""
    return tuple(item[len("CMD:"):] for item in RESTRICTED_COMMANDS)


def has(name):
    """命令名是否在受限清单内；带不带 CMD: 前缀都接受。"""
    if name in RESTRICTED_COMMANDS:
        return True
    return ("CMD:" + name) in RESTRICTED_COMMANDS
