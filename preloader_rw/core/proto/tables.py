# -*- coding: utf-8 -*-
"""GPT 分区表运算。

本模块只做运算，不含任何静态分区数据；表由调用方（product 层）提供。
表项形态：(name, base, size)，按表内顺序排列。
"""

import zlib


def _fields(entry):
    return entry[0], entry[1], entry[2]


def at(table, target):
    """按名查分区。返回 (base, size)；未命中返回 (None, None)。"""
    for entry in table:
        part_name, base, size = _fields(entry)
        if part_name == target:
            return base, size
    return None, None


def spans(table, offset, length):
    """区间 [offset, offset+length) 覆盖到的分区片段。

    返回 [(name, start, span_len), ...]，按表内顺序；只列真实交集。
    """
    out = []
    end = offset + length
    for entry in table:
        name, base, size = _fields(entry)
        part_end = base + size
        if offset < part_end and end > base:
            start = max(offset, base)
            out.append((name, start, min(end, part_end) - start))
    return out


def segments(base, size, seg_size):
    """把 [base, base+size) 按 seg_size 切段。返回 [(start, len), ...]。"""
    if seg_size <= 0:
        raise ValueError("段长必须为正: %r" % (seg_size,))
    out = []
    off = 0
    while off < size:
        out.append((base + off, min(seg_size, size - off)))
        off += seg_size
    return out


def crc32(data, initial=0):
    """GPT 头/表项使用的 CRC32。返回无符号 32 位。"""
    return zlib.crc32(data, initial) & 0xFFFFFFFF


class PartitionTable(object):
    """运行期分区表：只由设备上传的权威数据构造。

    不提供任何内置表；空表构造即报错，避免"没读到表也敢写"。
    """

    __slots__ = ("entries", "_by_name")

    def __init__(self, entries):
        rows = []
        seen = {}
        for name, base, size in entries:
            name = str(name)
            base = int(base)
            size = int(size)
            if not name or base < 0 or size <= 0:
                raise ValueError("非法分区项: %r" % ((name, base, size),))
            value = (base, size)
            if name in seen:
                if seen[name] != value:
                    raise ValueError("分区 %r 重复且值不一致: %r != %r"
                                     % (name, value, seen[name]))
                continue
            seen[name] = value
            rows.append((name, base, size))
        if not rows:
            raise ValueError("分区表为空")
        self.entries = tuple(rows)
        self._by_name = seen

    def __len__(self):
        return len(self.entries)

    def at(self, name):
        return self._by_name.get(name, (None, None))

    def capacity(self, name):
        return self.at(name)[1]

    def spans(self, offset, length):
        return spans(self.entries, offset, length)

    def names(self):
        return tuple(self._by_name)


__all__ = ["at", "spans", "segments", "crc32", "PartitionTable"]
