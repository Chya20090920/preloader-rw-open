# -*- coding: utf-8 -*-
"""启动前预检：任何一项不通过都不创建内核、不打开设备。"""

import os
from dataclasses import dataclass

from ..core.kernel import commands as command_book
from ..core.kernel.table import TableError
from .artifacts import ArtifactBundle

_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(_PKG, "backends", "fastboot")
VENDOR_FILES = ("fastboot.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")
ROUTE_SUFFIXES = (".py", ".pyc", ".dll", ".pyd", ".so")
TABLE_SUFFIXES = (".json",)


@dataclass(frozen=True)
class PreflightOk:
    bundle: ArtifactBundle


@dataclass(frozen=True)
class PreflightFail:
    code: str
    detail: str


def _nonempty_file(path):
    return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0


def _resolve_table(config):
    """装命令表；返回 (表, 失败)。空路径 = 仓库自带默认表。"""
    path = (config.table or "").strip()
    if not path:
        return command_book.default_table(), None
    if not _nonempty_file(path):
        return None, PreflightFail("MISSING_TABLE", path)
    if os.path.splitext(path)[1].lower() not in TABLE_SUFFIXES:
        return None, PreflightFail(
            "BAD_TABLE", "命令表后缀必须是 .json: %s"
            % os.path.splitext(path)[1])
    try:
        return command_book.load_table(path), None
    except TableError as exc:
        return None, PreflightFail("BAD_TABLE", str(exc))


def run(config, vendor=VENDOR):
    """执行预检。返回 PreflightOk 或 PreflightFail。"""
    if not _nonempty_file(config.route):
        return PreflightFail("MISSING_ROUTE", "route")
    if os.path.splitext(config.route)[1].lower() not in ROUTE_SUFFIXES:
        return PreflightFail("BAD_ROUTE", "route 后缀不支持: %s"
                             % os.path.splitext(config.route)[1].lower())

    table, table_fail = _resolve_table(config)
    if table_fail is not None:
        return table_fail

    if not config.attach:
        for label in ("da1", "da2"):
            path = getattr(config, label)
            if not _nonempty_file(path):
                return PreflightFail("MISSING_ARTIFACT", label)

    for kind in config.required_kinds:
        if kind not in config.credentials:
            return PreflightFail("MISSING_CREDENTIAL", kind)
        path = config.credentials[kind]
        if not _nonempty_file(path):
            return PreflightFail("EMPTY_CREDENTIAL", kind)

    if config.fastboot_enabled:
        for name in VENDOR_FILES:
            path = os.path.join(vendor, name)
            if not _nonempty_file(path):
                return PreflightFail("VENDOR_INCOMPLETE", name)

    credentials = {}
    for kind in config.required_kinds:
        with open(config.credentials[kind], "rb") as handle:
            credentials[kind] = handle.read()

    def _read(path):
        if not path:
            return b""
        with open(path, "rb") as handle:
            return handle.read()

    bundle = ArtifactBundle(
        route=config.route,
        da1=_read(config.da1),
        da2=_read(config.da2),
        credentials=credentials,
        required_kinds=tuple(config.required_kinds),
        table=table,
        table_path=(config.table or ""),
    )
    return PreflightOk(bundle)
