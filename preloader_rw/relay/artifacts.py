# -*- coding: utf-8 -*-
"""壳读入的装配结果：路线、引导镜像、二层文件字节。"""

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ArtifactBundle:
    route: str
    da1: bytes
    da2: bytes
    credentials: Mapping
    required_kinds: tuple
    # 装好的命令表（预检期验过一遍，内核直接用它，不再重读文件）。
    table: object = None
    table_path: str = ""

    def credential(self, kind):
        if kind not in self.required_kinds:
            raise IOError("路线请求了未声明的二层文件: %r" % (kind,))
        data = self.credentials.get(kind, b"")
        if not data:
            raise IOError("二层文件为空: %r" % (kind,))
        return data


def _read(path):
    with open(os.fspath(path), "rb") as handle:
        return handle.read()
