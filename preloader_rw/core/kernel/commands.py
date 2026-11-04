# -*- coding: utf-8 -*-
"""命令构造器注册表，以及默认命令表的门面。

本文件只做两件事：

  1. **拼帧**——把参数 dict 变成设备认的 XML 帧（`BUILDERS` 里的那些函数）；
  2. **给出默认表**——`preloader_rw/profiles/mtk_official.json` 装出来的
     `CommandTable`，内核各处经本模块取它，保持旧调用形态不变。

命令清单、参数路径、回合内可能的 `<info>` / `<key>`、注册探针，全部在
JSON 里（见 `core/kernel/table.py` 的说明）。**本文件不再列出任何命令的
登记信息**——加一条命令、换一份 DA，改的是表，不是这里。

带条件元素或嵌套 XML 的命令（`SET-BOOT-MODE`、`DEBUG:UFS`、
`WRITE-PARTITION` 的 `pre_erase`）只能用具名构造器；扁平命令可以在表里
直接写 `{"template": …}`，写表的人不必碰 Python。
"""

import os

from ..proto import da2_commands
from ..proto import xml
from . import table as table_mod
from .table import (Command, PARTITION_TAG, SHAPE_COMMAND, SHAPE_DOWNLOAD,
                    SHAPE_DUPLEX, SHAPE_UPLOAD, TableError)

__all__ = [
    "SHAPE_COMMAND",
    "SHAPE_UPLOAD",
    "SHAPE_DOWNLOAD",
    "SHAPE_DUPLEX",
    "PARTITION_TAG",
    "Command",
    "TableError",
    "BUILDERS",
    "TABLE",
    "default_table",
    "load_table",
    "get",
    "names",
    "shape",
    "frame",
    "uncovered",
    "BOOK",
]

_PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_TABLE = os.path.join(_PKG, "profiles", "mtk_official.json")


# ---- 帧构造（全部委托 proto.xml，本文件不写 XML 字面量） ---------------

def _need(args, key):
    if key not in args:
        raise ValueError("缺少命令参数: %s" % (key,))
    return args[key]


def _boot_to(args):
    return xml.boot_to(_need(args, "at_address"),
                       _need(args, "jmp_address"),
                       _need(args, "source_file"))


def _host_supported(args):
    return xml.host_supported()


def _notify_init_hw(args):
    return xml.notify_init_hw()


def _can_higher_speed(args):
    return xml.can_higher_speed(_need(args, "target_file"))


def _security_get_fw_info(args):
    return xml.security_get_fw_info(_need(args, "target_file"))


def _security_set_flash_policy(args):
    return xml.security_set_flash_policy(_need(args, "source_file"))


def _get_hw_info(args):
    return xml.get_hw_info(_need(args, "target_file"))


def _get_ufs_info(args):
    return xml.get_ufs_info(_need(args, "target_file"))


def _get_da_info(args):
    return xml.get_da_info(_need(args, "target_file"))


def _read_partition_table(args):
    return xml.read_partition_table(_need(args, "target_file"))


def _read_partition(args):
    return xml.read_partition(_need(args, "partition"),
                              _need(args, "target_file"))


def _read_flash(args):
    return xml.read_flash(_need(args, "partition"),
                          args.get("offset", 0),
                          _need(args, "length"),
                          _need(args, "target_file"))


def _write_partition(args):
    return xml.write_partition(_need(args, "partition"),
                               _need(args, "source_file"),
                               args.get("pre_erase"))


def _write_flash(args):
    return xml.write_flash(_need(args, "partition"),
                           _need(args, "offset"),
                           _need(args, "source_file"))


def _read_efuse(args):
    return xml.read_efuse(_need(args, "target_file"))


def _write_efuse(args):
    return xml.write_efuse(_need(args, "source_file"))


def _debug_ufs(args):
    return xml.debug_ufs(
        _need(args, "function"),
        source_file=args.get("source_file"),
        switch=args.get("switch"),
        write_turbo_size=args.get("write_turbo/size"),
        write_turbo_reduction=args.get("write_turbo/reduction"),
        hpb_ctrl_mode=args.get("hpb/ctrl_mode"),
        hpb_pinned_region_count=args.get("hpb/pinned_region_count"),
        hpb_pinned_region_start_index=args.get("hpb/pinned_region_start_index"),
        hpb_size=args.get("hpb/size"),
        split_last_lua_size=args.get("split_last_lua/size"),
        split_last_lua_type=args.get("split_last_lua/type"),
    )


def _emmc_control(args):
    return xml.emmc_control(_need(args, "function"),
                            _need(args, "target_file"))


def _reboot(args):
    return xml.reboot(_need(args, "action"))


def _flash_all(args):
    return xml.flash_all(_need(args, "path_separator"),
                         _need(args, "source_file"))


def _erase_flash(args):
    return xml.erase_flash(_need(args, "partition"),
                           _need(args, "offset"),
                           _need(args, "length"))


def _flash_update(args):
    return xml.flash_update(_need(args, "path_separator"),
                            _need(args, "source_file"),
                            _need(args, "backup_folder"))


def _write_partitions(args):
    return xml.write_partitions(_need(args, "source_file"))


def _set_boot_mode(args):
    return xml.set_boot_mode(_need(args, "mode"),
                             args.get("connect_type"),
                             args.get("mobile_log"),
                             args.get("adb"))


def _set_rsc(args):
    return xml.set_rsc(_need(args, "key"), _need(args, "source_file"))


def _write_private_cert(args):
    return xml.write_private_cert(_need(args, "source_file"))


def _get_downloaded_image_feedback(args):
    return xml.get_downloaded_image_feedback(_need(args, "target_file"))


def _security_get_auth_token(args):
    return xml.security_get_auth_token(_need(args, "target_file"))


def _security_write_auth(args):
    return xml.security_write_auth(_need(args, "partition"),
                                   _need(args, "offset"),
                                   _need(args, "source_file"))


def _security_set_allinone_signature(args):
    return xml.security_set_allinone_signature(_need(args, "source_file"))


def _erase_partition(args):
    return xml.erase_partition(_need(args, "partition"))


def _set_runtime_parameter(args):
    return xml.set_runtime_parameter(
        checksum_level=args.get("checksum_level", "NONE"),
        battery_exist=args.get("battery_exist", "AUTO-DETECT"),
        da_log_level=args.get("da_log_level", "INFO"),
        log_channel=args.get("log_channel", "USB"),
        system_os=args.get("system_os", "WINDOWS"),
    )


def _set_host_info(args):
    return xml.set_host_info(args.get("timestamp"))


def _get_sys_property(args):
    return xml.get_sys_property(_need(args, "key"),
                                _need(args, "target_file"))


# ---- 构造器注册表 -----------------------------------------------------
#
# 表里的 `"build": "<名字>"` 就是查这张表。新增一种帧形状 = 在这里加一个
# 函数，然后在表里引用它；内核其余部分不需要知道。

BUILDERS = {
    "boot_to": _boot_to,
    "host_supported": _host_supported,
    "notify_init_hw": _notify_init_hw,
    "can_higher_speed": _can_higher_speed,
    "security_get_fw_info": _security_get_fw_info,
    "security_set_flash_policy": _security_set_flash_policy,
    "get_hw_info": _get_hw_info,
    "get_ufs_info": _get_ufs_info,
    "get_da_info": _get_da_info,
    "read_partition_table": _read_partition_table,
    "set_runtime_parameter": _set_runtime_parameter,
    "set_host_info": _set_host_info,
    "get_sys_property": _get_sys_property,
    "write_partition": _write_partition,
    "write_flash": _write_flash,
    "write_efuse": _write_efuse,
    "read_efuse": _read_efuse,
    "debug_ufs": _debug_ufs,
    "emmc_control": _emmc_control,
    "reboot": _reboot,
    "flash_all": _flash_all,
    "erase_flash": _erase_flash,
    "flash_update": _flash_update,
    "write_partitions": _write_partitions,
    "set_boot_mode": _set_boot_mode,
    "set_rsc": _set_rsc,
    "write_private_cert": _write_private_cert,
    "get_downloaded_image_feedback": _get_downloaded_image_feedback,
    "security_get_auth_token": _security_get_auth_token,
    "security_write_auth": _security_write_auth,
    "security_set_allinone_signature": _security_set_allinone_signature,
    "erase_partition": _erase_partition,
    "read_partition": _read_partition,
    "read_flash": _read_flash,
}


# ---- 默认表门面 -------------------------------------------------------

def load_table(path):
    """装一张外部表。异常统一是 TableError（消息可直接给用户看）。"""
    return table_mod.load(path, BUILDERS)


TABLE = load_table(DEFAULT_TABLE)
BOOK = TABLE.commands


def default_table():
    """内置表。内核没被显式给表时用这一张。"""
    return TABLE


def get(name):
    return TABLE.get(name)


def names(phase=None):
    return TABLE.names(phase)


def shape(name, override=None):
    return TABLE.shape(name, override)


def frame(name, args=None, args_xml=None):
    return TABLE.frame(name, args, args_xml)


def uncovered():
    """镜像内已命中、但本表未登记的名字（应恒为空，供离线判据用）。"""
    return TABLE.uncovered(da2_commands.names())
