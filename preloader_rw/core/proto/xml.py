# -*- coding: utf-8 -*-
"""命令 XML 的构造与字段解析。

全工程唯一使用正则的位置：设备回帧的字段提取集中在本文件，其余模块只
调用这里的函数，不再各自编译正则。

构造侧只做字符串拼装，不按命令名分支；命令种类由调用方选择函数。
"""

import re
import time

from .const import EXISTS_NOT, OK, OK0

HEAD = b'<?xml version="1.0" encoding="utf-8"?><da><version>1.0</version>'
NUL = b"\x00"

_HOST_CAPABILITY = (b"CMD:DOWNLOAD-FILE^1@CMD:DOWNLOAD-FILE^1.1@CMD:DOWNLOAD-FILE^2@"
                    b"CMD:FILE-SYS-OPERATION^1@CMD:PROGRESS-REPORT^1@CMD:UPLOAD-FILE^1@")


def _b(value):
    return value if isinstance(value, bytes) else str(value).encode("utf-8")


def command(body):
    """HEAD + body + NUL。body 须自带结束标签。"""
    return HEAD + _b(body) + NUL


def mem_source(addr, length):
    """设备内存源/目标串：MEM://0x<addr>:0x<length>。"""
    return "MEM://0x%x:0x%x" % (addr, length)


# ---- 命令构造 -------------------------------------------------------

def ram_test(function="FLIP", start_address=0, length=0):
    return command(b'<command>CMD:RAM-TEST</command><arg><function>%s</function>'
                   b'<start_address>0x%x</start_address><length>0x%x</length></arg></da>'
                   % (_b(function), start_address, length))


def boot_to(at_address, jmp_address, source_file):
    return command(b'<command>CMD:BOOT-TO</command><arg>'
                   b'<at_address>0x%08X</at_address>'
                   b'<jmp_address>0x%08X</jmp_address> '
                   b'<source_file>%s</source_file></arg></da>'
                   % (at_address, jmp_address, _b(source_file)))


def host_supported(capability=_HOST_CAPABILITY):
    return command(b'<command>CMD:HOST-SUPPORTED-COMMANDS</command><arg>'
                   b'<host_capability>' + _b(capability) +
                   b'</host_capability></arg></da>')


def notify_init_hw():
    return command(b'<command>CMD:NOTIFY-INIT-HW</command><arg></arg></da>')


def can_higher_speed(target_file):
    return command(b'<command>CMD:CAN-HIGHER-USB-SPEED</command><arg>'
                   b'<target_file>%s</target_file></arg></da>' % _b(target_file))


def security_get_fw_info(target_file):
    return command(b'<command>CMD:SECURITY-GET-DEV-FW-INFO</command><arg>'
                   b'<target_file>%s</target_file></arg></da>' % _b(target_file))


def security_set_flash_policy(source_file):
    return command(b'<command>CMD:SECURITY-SET-FLASH-POLICY</command><arg>'
                   b'<source_file>%s</source_file></arg></da>' % _b(source_file))


def get_hw_info(target_file):
    return command(b'<command>CMD:GET-HW-INFO</command><arg>'
                   b'<target_file>%s</target_file></arg></da>' % _b(target_file))


def get_ufs_info(target_file):
    return command(b'<command>CMD:GET-UFS-INFO</command><arg>'
                   b'<target_file>%s</target_file></arg></da>' % _b(target_file))


def get_da_info(target_file):
    return command(b'<command>CMD:GET-DA-INFO</command><arg>'
                   b'<target_file>%s</target_file></arg></da>' % _b(target_file))


def read_partition_table(target_file):
    return command(b'<command>CMD:READ-PARTITION-TABLE</command><arg>'
                   b'<target_file>%s</target_file></arg></da>' % _b(target_file))


def set_runtime_parameter(checksum_level="NONE", battery_exist="AUTO-DETECT",
                          da_log_level="INFO", log_channel="USB",
                          system_os="WINDOWS"):
    return command(b'<command>CMD:SET-RUNTIME-PARAMETER</command><arg>'
                   b'<checksum_level>%s</checksum_level>'
                   b'<battery_exist>%s</battery_exist>'
                   b'<da_log_level>%s</da_log_level>'
                   b'<log_channel>%s</log_channel>'
                   b'<system_os>%s</system_os></arg></da>'
                   % (_b(checksum_level), _b(battery_exist), _b(da_log_level),
                      _b(log_channel), _b(system_os)))


def set_host_info(timestamp=None):
    if timestamp is None:
        timestamp = time.strftime("%Y%m%dT%H%M%S")
    return command(b'<command>CMD:SET-HOST-INFO</command><arg><info>%s</info></arg></da>'
                   % _b(timestamp))


def get_sys_property(key, target_file):
    return command(b'<command>CMD:GET-SYS-PROPERTY</command><arg>'
                   b'<key>%s</key><target_file>%s</target_file></arg></da>'
                   % (_b(key), _b(target_file)))


def write_partition(partition, source_file, pre_erase=None):
    """分区写：偏移与长度由设备按自身分区表解析。

    `da/arg/pre_erase`（镜像 0x40001cd0 读它）只有值为 `YES` 时设备才先擦除；
    不传则该元素不出现，设备走不擦分支。
    """
    tail = (b'<pre_erase>%s</pre_erase>' % _b(pre_erase)) if pre_erase else b""
    return command(b'<command>CMD:WRITE-PARTITION</command><arg>'
                   b'<partition>%s</partition>%s<source_file>%s</source_file>'
                   b'</arg></da>'
                   % (_b(partition), tail, _b(source_file)))


def write_efuse(source_file):
    """eFuse 写：设备只取 da/arg/source_file，后续走 DOWNLOAD-FILE(info=efuse)。"""
    return command(b'<command>CMD:WRITE-EFUSE</command><arg>'
                   b'<source_file>%s</source_file></arg></da>'
                   % (_b(source_file),))


def read_efuse(target_file):
    return command(b'<command>CMD:READ-EFUSE</command><arg>'
                   b'<target_file>%s</target_file></arg></da>'
                   % (_b(target_file),))


def _opt(pairs):
    """按顺序拼可选元素；值为 None 的元素不出现。"""
    return b"".join(b"<%s>%s</%s>" % (_b(tag), _b(value), _b(tag))
                    for tag, value in pairs if value is not None)


def debug_ufs(function, source_file=None, switch=None,
              write_turbo_size=None, write_turbo_reduction=None,
              hpb_ctrl_mode=None, hpb_pinned_region_count=None,
              hpb_pinned_region_start_index=None, hpb_size=None,
              split_last_lua_size=None, split_last_lua_type=None):
    """CMSIS 调试命令：只取 function（UPDATE-FIRMWARE / SETTING）与可选子项。

    参数路径与镜像 0x40006be8 的解析路径逐条对应；写固件时走
    DOWNLOAD-FILE(info=FW)。
    """
    write_turbo = _opt((("size", write_turbo_size),
                        ("reduction", write_turbo_reduction)))
    hpb = _opt((("ctrl_mode", hpb_ctrl_mode),
                ("pinned_region_count", hpb_pinned_region_count),
                ("pinned_region_start_index", hpb_pinned_region_start_index),
                ("size", hpb_size)))
    split = _opt((("size", split_last_lua_size),
                  ("type", split_last_lua_type)))
    body = _opt((("function", function),))
    if source_file is not None:
        body += _opt((("source_file", source_file),))
    if switch is not None:
        body += _opt((("switch", switch),))
    if write_turbo:
        body += b"<write_turbo>" + write_turbo + b"</write_turbo>"
    if hpb:
        body += b"<hpb>" + hpb + b"</hpb>"
    if split:
        body += b"<split_last_lua>" + split + b"</split_last_lua>"
    return command(b'<command>CMD:DEBUG:UFS</command><arg>' + body + b'</arg></da>')


def emmc_control(function, target_file):
    """eMMC 控制：function 决定设备上传 LIFE-CYCLE-STATUS 还是 RPMB-KEY-STATUS。"""
    return command(b'<command>CMD:EMMC-CONTROL</command><arg>'
                   b'<function>%s</function><target_file>%s</target_file>'
                   b'</arg></da>' % (_b(function), _b(target_file)))


def reboot(action):
    """action ∈ IMMEDIATE / WARM-RESET / COLD-RESET / DISCONNECT（镜像 0x400056d0）。"""
    return command(b'<command>CMD:REBOOT</command><arg>'
                   b'<action>%s</action></arg></da>' % (_b(action),))


def flash_all(path_separator, source_file):
    """整机写入：先取散列文件（DOWNLOAD-FILE info=scatter-file），再逐分区写。"""
    return command(b'<command>CMD:FLASH-ALL</command><arg>'
                   b'<path_separator>%s</path_separator>'
                   b'<source_file>%s</source_file></arg></da>'
                   % (_b(path_separator), _b(source_file)))


def erase_flash(partition, offset, length):
    """按分区名 + 偏移 + 长度擦除；长度可为 MAX（镜像 0x400035fc 识别）。"""
    return command(b'<command>CMD:ERASE-FLASH</command><arg>'
                   b'<partition>%s</partition><offset>0x%x</offset>'
                   b'<length>0x%x</length></arg></da>'
                   % (_b(partition), offset, length))


def flash_update(path_separator, source_file, backup_folder):
    """差分升级：散列文件 → 逐分区备份上传 → 记录文件上传 → payload 下载。"""
    return command(b'<command>CMD:FLASH-UPDATE</command><arg>'
                   b'<path_separator>%s</path_separator>'
                   b'<source_file>%s</source_file>'
                   b'<backup_folder>%s</backup_folder></arg></da>'
                   % (_b(path_separator), _b(source_file), _b(backup_folder)))


def write_partitions(source_file):
    """多分区写：只取散列文件路径，其余由设备解析。"""
    return command(b'<command>CMD:WRITE-PARTITIONS</command><arg>'
                   b'<source_file>%s</source_file></arg></da>'
                   % (_b(source_file),))


def set_boot_mode(mode, connect_type=None, mobile_log=None, adb=None):
    """mode ∈ META / FASTBOOT / ANDROID-TEST-MODE；其余三项只在给值时出现。"""
    extra = _opt((("connect_type", connect_type),
                  ("mobile_log", mobile_log),
                  ("adb", adb)))
    return command(b'<command>CMD:SET-BOOT-MODE</command><arg>'
                   b'<mode>%s</mode>%s</arg></da>' % (_b(mode), extra))


def set_rsc(key, source_file):
    """RSC 写入：key + 文件路径；文件走 DOWNLOAD-FILE(info=RSC file)。"""
    return command(b'<command>CMD:SET-RSC</command><arg>'
                   b'<key>%s</key><source_file>%s</source_file></arg></da>'
                   % (_b(key), _b(source_file)))


def write_private_cert(source_file):
    return command(b'<command>CMD:WRITE-PRIVATE-CERT</command><arg>'
                   b'<source_file>%s</source_file></arg></da>'
                   % (_b(source_file),))


def get_downloaded_image_feedback(target_file):
    return command(b'<command>CMD:GET-DOWNLOADED-IMAGE-FEEDBACK</command><arg>'
                   b'<target_file>%s</target_file></arg></da>'
                   % (_b(target_file),))


def security_get_auth_token(target_file):
    return command(b'<command>CMD:SECURITY-GET-AUTH-TOKEN</command><arg>'
                   b'<target_file>%s</target_file></arg></da>'
                   % (_b(target_file),))


def security_get_dev_fw_info(target_file):
    return command(b'<command>CMD:SECURITY-GET-DEV-FW-INFO</command><arg>'
                   b'<target_file>%s</target_file></arg></da>'
                   % (_b(target_file),))


def security_write_auth(partition, offset, source_file):
    return command(b'<command>CMD:SECURITY-WRITE-AUTH</command><arg>'
                   b'<partition>%s</partition><offset>0x%x</offset>'
                   b'<source_file>%s</source_file></arg></da>'
                   % (_b(partition), offset, _b(source_file)))


def security_set_allinone_signature(source_file):
    return command(b'<command>CMD:SECURITY-SET-ALLINONE-SIGNATURE</command><arg>'
                   b'<source_file>%s</source_file></arg></da>'
                   % (_b(source_file),))


def write_flash(partition, offset, source_file):
    """原始偏移写：分区 + offset + 文件路径。

    长度不由主机申报；设备先发 `FILE-SYS-OPERATION key=FILE-SIZE`，
    主机答总长后再走 DOWNLOAD-FILE。
    """
    return command(b'<command>CMD:WRITE-FLASH</command><arg>'
                   b'<partition>%s</partition><offset>0x%x</offset>'
                   b'<source_file>%s</source_file></arg></da>'
                   % (_b(partition), offset, _b(source_file)))


def erase_partition(partition):
    """按分区名擦除：起止由设备按自身分区表解析。"""
    return command(b'<command>CMD:ERASE-PARTITION</command><arg>'
                   b'<partition>%s</partition></arg></da>' % (_b(partition),))


def read_partition(partition, target_file):
    """整分区回读：起止由设备按自身分区表解析，主机只给分区名。"""
    return command(b'<command>CMD:READ-PARTITION</command><arg>'
                   b'<partition>%s</partition><target_file>%s</target_file>'
                   b'</arg></da>' % (_b(partition), _b(target_file)))


def read_flash(partition, offset, length, target_file):
    return command(b'<command>CMD:READ-FLASH</command><arg>'
                   b'<partition>%s</partition><offset>0x%x</offset>'
                   b'<length>0x%x</length><target_file>%s</target_file>'
                   b'</arg></da>'
                   % (_b(partition), offset, length, _b(target_file)))


def size_reply(size):
    """DOWNLOAD-FILE 的总长应答：OK@0x<十六进制>NUL，不补零。"""
    return b"OK@0x%x\x00" % size


# ---- 字段解析 -------------------------------------------------------

_FIELD_RE = {}


def _field_re(name):
    pattern = _FIELD_RE.get(name)
    if pattern is None:
        tag = name.encode("ascii")
        pattern = re.compile(rb"<" + tag + rb">([^<]*)</" + tag + rb">")
        _FIELD_RE[name] = pattern
    return pattern


def field(payload, name):
    """取 <name>…</name> 的文本；缺失返回空串。"""
    match = _field_re(name).search(payload)
    return match.group(1).decode("ascii", "ignore") if match else ""


def info(payload):
    return field(payload, "info")


def source_file(payload):
    return field(payload, "source_file")


def target_file(payload):
    return field(payload, "target_file")


def key(payload):
    return field(payload, "key")


def file_path(payload):
    return field(payload, "file_path")


def result(payload):
    return field(payload, "result")


def packet_length(payload):
    """设备申报的片长；缺失或非法返回 0。"""
    text = field(payload, "packet_length").strip()
    if not text:
        return 0
    try:
        value = int(text, 16)
    except ValueError:
        return 0
    return value if value > 0 else 0


def is_command_frame(payload):
    """命令帧判定：以 <?xml 开头，且不是日志里的内嵌 XML。

    设备日志与命令帧同 flag，且日志正文会原样嵌入 XML；日志行以换行
    与 @Protocol: 开头，必须排除，否则会把日志当成 END/START。
    """
    return payload[:5] == b"<?xml" and b"\r\n@Protocol:" not in payload


_PT_RE = re.compile(
    rb"<pt>\s*<name>([^<]+)</name>\s*<start>([^<]+)</start>\s*"
    rb"<size>([^<]+)</size>\s*</pt>",
    re.S,
)


def parse_partition_table(payload):
    """解析设备上传的 <partition_table>。

    设备上传的格式：
        <?xml ...?><partition_table version="1.0">
          <pt><name>misc</name><start>0x8000</start><size>0x80000</size></pt>
          ...
        </partition_table>

    返回 ((name, start, size), ...)；格式不认识时返回空元组。
    """
    out = []
    for match in _PT_RE.finditer(payload):
        name = match.group(1).decode("ascii", "ignore").strip()
        try:
            start = int(match.group(2).strip(), 0)
            size = int(match.group(3).strip(), 0)
        except ValueError:
            continue
        if name:
            out.append((name, start, size))
    return tuple(out)


__all__ = [
    "HEAD",
    "NUL",
    "OK",
    "OK0",
    "EXISTS_NOT",
    "command",
    "mem_source",
    "ram_test",
    "boot_to",
    "host_supported",
    "notify_init_hw",
    "can_higher_speed",
    "security_get_fw_info",
    "security_set_flash_policy",
    "get_hw_info",
    "get_ufs_info",
    "get_da_info",
    "read_partition_table",
    "set_runtime_parameter",
    "set_host_info",
    "get_sys_property",
    "write_partition",
    "write_efuse",
    "read_efuse",
    "debug_ufs",
    "emmc_control",
    "reboot",
    "flash_all",
    "erase_flash",
    "flash_update",
    "write_partitions",
    "set_boot_mode",
    "set_rsc",
    "write_private_cert",
    "get_downloaded_image_feedback",
    "security_get_auth_token",
    "security_get_dev_fw_info",
    "security_write_auth",
    "security_set_allinone_signature",
    "write_flash",
    "erase_partition",
    "read_flash",
    "read_partition",
    "size_reply",
    "field",
    "info",
    "source_file",
    "target_file",
    "key",
    "file_path",
    "result",
    "packet_length",
    "is_command_frame",
    "parse_partition_table",
]
