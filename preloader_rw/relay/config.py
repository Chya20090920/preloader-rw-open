# -*- coding: utf-8 -*-
"""中继壳配置：所有输入必须显式给出，没有默认路径。"""

from dataclasses import dataclass, field
from typing import Mapping


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RelayConfig:
    """壳的输入装配单。缺项即 ConfigError，不落回默认值。"""

    route: str
    # 命令表：内核认识哪些命令、帧怎么拼、探针发哪条。
    # 空 = 仓库自带的那张（preloader_rw/profiles/mtk_official.json）。
    table: str = ""
    credentials: Mapping[str, str] = field(default_factory=dict)
    required_kinds: tuple = ()
    da1: str = ""
    da2: str = ""
    attach: bool = False
    fastboot_enabled: bool = True
    fastboot_timeout_s: float = 2.0
    device_timeout_s: float = 3.0
    beat: float = 0.041
    poll_s: float = 0.5
    images: Mapping[str, str] = field(default_factory=dict)
    backend: str = "libusb"

    def __post_init__(self):
        if not self.route:
            raise ConfigError("route 不得为空")
        # 缺 da1/da2 不在此抛错：那是预检的事（MISSING_ARTIFACT），
        # --json 模式下壳先存活、快照如实反映未配置，fire 时才硬性拦截。
        kinds = tuple(self.required_kinds)
        if any(not isinstance(k, str) or not k for k in kinds):
            raise ConfigError("required_kinds 含非法 kind: %r" % (kinds,))
        if len(set(kinds)) != len(kinds):
            raise ConfigError("required_kinds 有重复项: %r" % (kinds,))
        keys = set(self.credentials)
        undeclared = keys - set(kinds)
        if undeclared:
            raise ConfigError("credentials 含未声明的 kind: %r" % sorted(undeclared))
        object.__setattr__(self, "required_kinds", kinds)
