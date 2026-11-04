# -*- coding: utf-8 -*-
"""fastboot 阶段设备信息读取：只报读得到的，读不到的一律不报。

部分 bootloader 对 ``getvar <任意键>`` 一律回一个 ``unknown`` 占位——
只有下列 10 个键返回真实值，其余返回空或占位。因此本件**不设候选池**：
只逐个读这 10 个键，读到值才收；读不到就丢，绝不把 ``unknown`` 当作
数据上报。

批量 ``getvar all`` 在这类 bootloader 上可能不返回，故只用单键。
"""

from __future__ import annotations

# 可读的 10 个键；顺序即界面展示顺序。
HEALTH_KEYS = (
    "serialno",
    "product",
    "version",
    "version-bootloader",
    "current-slot",
    "slot-count",
    "unlocked",
    "secure",
    "max-download-size",
    "off-mode-charge",
)

# bootloader 用它表示“这个键我不认”。
PLACEHOLDER = "unknown"


def probe(cli) -> dict:
    """逐个读 HEALTH_KEYS；只收真实值，空值与占位一律丢弃。"""
    values = {}
    for key in HEALTH_KEYS:
        try:
            got = cli.getvar(key)
        except Exception:
            continue
        value = got.get(key)
        if not value or value == PLACEHOLDER:
            continue
        values[key] = value
    if values:
        values["source"] = "fastboot"
    return values
