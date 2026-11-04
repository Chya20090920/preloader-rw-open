# -*- coding: utf-8 -*-
"""分区表：唯一权威来源是设备自身的 GPT（READ-FLASH 直读）。

运行期路径：READ-FLASH 读存储 LUN 前段 → from_gpt() 校验两处 CRC32 →
PartitionTableHolder。不以 READ-PARTITION-TABLE 的 XML 为权威：设备侧该
命令的 16 KB 输出缓冲不可靠（偏移 1108 起 0xC00 被 target_file 转义缓冲
覆盖），41 条条目在上传前即丢失。

from_device() 保留给 XML 交叉校验；本模块不内置任何静态分区数据。
分表的取用一律经 PartitionTableHolder：读命令产出后写入，写命令读它。
"""

import struct

from ...proto import tables, xml

__all__ = ["TABLE_TARGET", "GPT_OFFSET", "GPT_LENGTH", "GPT_TARGET",
           "PartitionTableHolder", "damage_span", "from_device", "from_gpt",
           "load_into"]

# 分区表的接收区。
TABLE_TARGET = "MEM://0x20590000:0x02000000"

# ---- GPT 直读（READ-FLASH）----------------------------------------
# 存储 LUN 前 64 KB：保护 MBR + GPT 头 + 条目数组（覆盖到 512 项）。
GPT_OFFSET = 0
GPT_LENGTH = 0x10000
GPT_TARGET = "gpt"
# UFS 逻辑块可以是 512 或 4096；由 GPT 头位置与头 CRC 共同判定。
GPT_SECTORS = (512, 4096)
GPT_ENTRY_MIN = 128


def _u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def _u64(data, off):
    return struct.unpack_from("<Q", data, off)[0]


def _gpt_header(data):
    """定位并校验 GPT 头；返回 (header_off, sector_size, header_size)。"""
    for sector in GPT_SECTORS:
        header_off = sector
        if len(data) < header_off + 0x5C:
            continue
        if data[header_off:header_off + 8] != b"EFI PART":
            continue
        header_size = _u32(data, header_off + 12)
        if header_size < 0x5C or header_size > sector:
            continue
        header = bytearray(data[header_off:header_off + header_size])
        stored = _u32(header, 16)
        header[16:20] = b"\x00\x00\x00\x00"
        if tables.crc32(bytes(header)) != stored:
            continue
        return header_off, sector, header_size
    raise ValueError("未找到可校验的 GPT 头（尝试扇区 512/4096）")


def from_gpt(data):
    """解析 READ-FLASH 读回的存储前段，返回运行期分区表。

    扇区大小按 GPT 头实际位置判定（512 或 4096）；GPT 头 CRC32 与条目数组
    CRC32 任一不符即抛 ValueError，不用损坏数据构造表。设备侧 READ-FLASH
    走存储通道，不经过 READ-PARTITION-TABLE 的 16 KB 堆缓冲。
    """
    data = bytes(data)
    header_off, sector, header_size = _gpt_header(data)
    entry_lba = _u64(data, header_off + 72)
    entry_count = _u32(data, header_off + 80)
    entry_size = _u32(data, header_off + 84)
    stored_array_crc = _u32(data, header_off + 88)
    if (entry_lba == 0 or entry_count == 0 or entry_count > 4096
            or entry_size < GPT_ENTRY_MIN or entry_size % 8):
        raise ValueError("GPT 条目参数非法: lba=%d count=%d size=%d"
                         % (entry_lba, entry_count, entry_size))
    array_off = entry_lba * sector
    array_end = array_off + entry_count * entry_size
    if array_end > len(data):
        raise ValueError("GPT 条目数组超出读回范围：需 %d B，收到 %d B"
                         % (array_end, len(data)))
    array = data[array_off:array_end]
    if tables.crc32(array) != stored_array_crc:
        raise ValueError("GPT 条目数组 CRC32 不符（存储 0x%08x）"
                         % (stored_array_crc,))
    rows = []
    for index in range(entry_count):
        entry = array[index * entry_size:(index + 1) * entry_size]
        if entry[:16] == b"\x00" * 16:
            continue
        first = _u64(entry, 32)
        last = _u64(entry, 40)
        name = entry[56:128].decode("utf-16le", "ignore").split("\x00")[0]
        if not name or first == 0 or last < first:
            raise ValueError("GPT 条目 %d 非法: name=%r first=%d last=%d"
                             % (index, name, first, last))
        rows.append((name, first * sector, (last - first + 1) * sector))
    if not rows:
        raise ValueError("GPT 无已用条目")
    return tables.PartitionTable(rows)


def damage_span(data):
    """设备上传的分区表被非 XML 字节打断的区间；无打断返回 None。

    实机（全部 43 轮一致）：偏移 1108 起 3072 B 被替换成 target_file 字符串
    加全零，表在该区间内的分区条目整段丢失。返回 (start, length)。
    """
    start = data.find(b"\x00")
    if start < 0 or start == len(data) - 1:
        return None
    end = start
    while end < len(data) and (data[end] == 0 or data[end] == 0x20):
        end += 1
    if end - start < 16:
        return None
    return start, end - start


def from_device(data):
    """把设备上传的 XML 解析成运行期权威表。"""
    rows = xml.parse_partition_table(data)
    if not rows:
        raise ValueError("设备分区表解析为空（未识别 <partition_table>）")
    return tables.PartitionTable(rows)


class PartitionTableHolder(object):
    """运行期分表的唯一持有者：读命令写入，写命令取用。

    装配与取表由此解耦——注册表在表存在之前就能装好；写命令在表未就绪时
    按既有规则拒绝服务，不靠装配顺序来保证。
    """

    def __init__(self, table=None):
        self._table = None
        if table is not None:
            self.set(table)

    @property
    def ready(self):
        return self._table is not None

    def get(self):
        return self._table

    def set(self, table):
        if not isinstance(table, tables.PartitionTable):
            raise TypeError("holder 只接受 PartitionTable，收到 %r"
                            % (type(table).__name__,))
        self._table = table
        return table


def load_into(holder, path):
    """读命令落盘后调用：解析文件并写入 holder。"""
    with open(path, "rb") as handle:
        return holder.set(from_device(handle.read()))
