# -*- coding: utf-8 -*-
"""离线检查一张命令表。

    python scripts\\check_table.py                    仓库自带默认表
    python scripts\\check_table.py my_table.json      自己的表
    python scripts\\check_table.py my_table.json --frames   连每条命令的帧一起打印

不碰设备、不开 USB，只回答三个问题：

  1. 表装得起来吗（JSON 语法、schema、必填字段、构造器是否存在）；
  2. 每条命令拼得出帧吗（参数清单与模板/构造器是否对得上）；
  3. 探针落在表里登记过的命令上吗，有没有目标地址。

退出码 0 = 通过，1 = 有问题（消息可直接给写表的人看）。
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from preloader_rw.core.kernel import commands as command_book   # noqa: E402
from preloader_rw.core.kernel.table import TableError           # noqa: E402


# 给每条命令造一组能过类型检查的样参：地址/偏移/长度给整数，其余给字符串。
_STR = {
    "partition": "boot", "key": "DA.SLA", "function": "FLIP",
    "action": "COLD-RESET", "mode": "FASTBOOT", "path_separator": "\\",
    "backup_folder": "C:\\bk", "timestamp": "20260101T000000",
    "checksum_level": "NONE", "battery_exist": "AUTO-DETECT",
    "da_log_level": "INFO", "log_channel": "UART", "system_os": "WINDOWS",
    "pre_erase": "YES", "connect_type": "USB", "mobile_log": "1", "adb": "0",
    "switch": "ON", "write_turbo/size": "0x100", "write_turbo/reduction": "0x10",
    "hpb/ctrl_mode": "1", "hpb/pinned_region_count": "2",
    "hpb/pinned_region_start_index": "3", "hpb/size": "0x200",
    "split_last_lua/size": "0x300", "split_last_lua/type": "1",
}


def sample_args(spec):
    args = {}
    for name in spec.args_names:
        if name in _STR:
            args[name] = _STR[name]
        elif name.endswith("address"):
            args[name] = 0x40000000
        elif name.endswith("offset"):
            args[name] = 0x1000
        elif name.endswith("length"):
            args[name] = 0x20000
        elif name == "source_file":
            args[name] = "C:/imgs/boot.img"
        elif name == "target_file":
            args[name] = "MEM://0x100000:0x1000"
        elif name == "info":
            args[name] = "20260101T000000"
        else:
            args[name] = "X"
    return args


def main(argv=None):
    ap = argparse.ArgumentParser(prog="check_table", description="离线检查命令表")
    ap.add_argument("path", nargs="?", default="",
                    help="表路径；省略则检查仓库自带默认表")
    ap.add_argument("--frames", action="store_true", help="打印每条命令的帧")
    ns = ap.parse_args(argv)

    try:
        table = (command_book.load_table(ns.path) if ns.path
                 else command_book.default_table())
    except TableError as exc:
        sys.stderr.write("命令表不成立: %s\n" % (exc,))
        return 1

    print(table.summary())
    print("传输符号: start=%r end=%r download=%r upload=%r filesys=%r"
          % (table.transport.start, table.transport.end,
             table.transport.download, table.transport.upload,
             table.transport.filesys))
    print("探针: %s" % ("; ".join("%s→%s" % (p.command, p.target)
                                 for p in table.probes) or "(未配置)"))
    print("命令 %d 条（pre %d / post %d）"
          % (len(table), len(table.names("pre")), len(table.names("post"))))

    failures = []
    for name in table.names():
        spec = table.get(name)
        try:
            frame = table.frame(name, sample_args(spec))
        except Exception as exc:
            failures.append("%s: %r" % (name, exc))
            continue
        if not frame.startswith(b"<?xml") or not frame.endswith(b"\x00"):
            failures.append("%s: 帧不以 <?xml 开头或没有 NUL 收尾" % (name,))
            continue
        if ns.frames:
            print("  %-38s %s" % (name, frame[:96].decode("utf-8", "replace")))

    if failures:
        sys.stderr.write("拼帧失败 %d 条:\n" % len(failures))
        for line in failures:
            sys.stderr.write("  - %s\n" % line)
        return 1

    if not table.probes:
        sys.stderr.write("警告: 表里没有 probe 段；路线内若没有命令走完，注册无法裁决\n")
    print("通过: %d 条命令全部拼帧成功" % len(table))
    return 0


if __name__ == "__main__":
    sys.exit(main())
