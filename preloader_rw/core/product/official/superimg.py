# -*- coding: utf-8 -*-
"""把 payload 目录里的逻辑分区镜像合成一个可写的 super.img。

格式按 Android liblp（metadata 10.2）：geometry 主/备各一份，metadata
主/备 × A/B 两槽；四张表 = partitions / extents / groups / block_devices。
不依赖 lpmake，输出为 raw 镜像，可直接作为物理分区 super 的写入源。

约束：分区名带槽后缀（system_a 等）；只写目标槽，另一槽不在镜像内。
"""

import hashlib
import os
import shutil
import struct

__all__ = ["LOGICAL_BASES", "plan_super", "build_super"]

LP_SECTOR = 512
LP_RESERVED = 0x1000
LP_GEOMETRY_SIZE = 0x1000
LP_GEOMETRY_MAGIC = 0x616C4467
LP_HEADER_MAGIC = 0x414C5030
LP_MAJOR = 10
LP_MINOR = 2
LP_HEADER_SIZE = 256
LP_META_MAX = 0x10000
LP_META_SLOTS = 2
LP_BLOCK = 0x1000
LP_ALIGN = 0x100000
LP_GROUP = "main"
LP_DEVICE = "super"

# payload 目录里属于 super 的逻辑分区；必填项缺一即拒绝合并。
LOGICAL_BASES = ("system", "system_ext", "product", "vendor",
                 "odm_dlkm", "vendor_dlkm", "system_dlkm")
REQUIRED_BASES = ("system", "system_ext", "product", "vendor",
                  "odm_dlkm", "vendor_dlkm")


def _align(value, align=LP_ALIGN):
    return (int(value) + align - 1) // align * align


def _image_for(root, base, slot):
    for name in ("%s_%s.img" % (base, slot), "%s.img" % base):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            return path
    return None


def _metadata_size():
    return LP_RESERVED + (LP_GEOMETRY_SIZE + LP_META_MAX * LP_META_SLOTS) * 2


def plan_super(root, device_size, slot="a"):
    """只做布局计算，不写文件。返回可供 build_super 使用的计划。"""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise ValueError("镜像根目录不存在: %s" % (root,))
    slot = str(slot).strip().lower()
    if slot not in ("a", "b"):
        raise ValueError("槽位只能是 a 或 b，收到 %r" % (slot,))
    device_size = int(device_size)
    if device_size <= 0:
        raise ValueError("super 分区尺寸非法: %r" % (device_size,))

    found = {}
    missing = []
    for base in LOGICAL_BASES:
        path = _image_for(root, base, slot)
        if path is None:
            missing.append(base)
        else:
            found[base] = path
    missing_required = [base for base in REQUIRED_BASES if base not in found]
    if missing_required:
        lines = ["还有 %d 个镜像没有被准备：" % (len(missing_required),)]
        for index, base in enumerate(missing_required, 1):
            lines.append("%d) %s.img（或 %s_%s.img）"
                         % (index, base, base, slot))
        lines.append("")
        lines.append("无法合并。请把上述文件放进：%s" % (root,))
        raise ValueError("\n".join(lines))

    data_start = _align(_metadata_size())
    offset = data_start
    items = []
    for base in LOGICAL_BASES:
        path = found.get(base)
        if path is None:
            continue
        size = os.path.getsize(path)
        if size <= 0:
            raise ValueError("镜像为空: %s" % (path,))
        aligned = _align(size)
        items.append({
            "name": "%s_%s" % (base, slot),
            "path": path,
            "size": size,
            "aligned": aligned,
            "offset": offset,
            "sectors": aligned // LP_SECTOR,
        })
        offset += aligned
    data_end = offset
    if data_end > device_size:
        raise ValueError(
            "镜像合计 %d B 超过 super 分区 %d B（差 %d B）"
            % (data_end, device_size, data_end - device_size))

    return {
        "root": root,
        "slot": slot,
        "device_size": device_size,
        "metadata_size": _metadata_size(),
        "data_start": data_start,
        "data_end": data_end,
        "items": items,
        "missing": [b for b in missing if b not in REQUIRED_BASES],
    }


def _geometry():
    body = struct.pack("<II32sIII", LP_GEOMETRY_MAGIC, 52, b"\x00" * 32,
                       LP_META_MAX, LP_META_SLOTS, LP_BLOCK)
    checksum = hashlib.sha256(body).digest()
    return body[:8] + checksum + body[40:]


def _metadata(plan):
    items = plan["items"]
    parts = b"".join(
        struct.pack("<36sIIII", item["name"].encode("ascii"),
                    1, index, 1, 0)
        for index, item in enumerate(items))
    exts = b"".join(
        struct.pack("<QIQI", item["sectors"], 0,
                    item["offset"] // LP_SECTOR, 0)
        for item in items)
    groups = struct.pack("<36sIQ", LP_GROUP.encode("ascii"), 0,
                         plan["device_size"])
    devices = struct.pack("<QIIQ36sI", plan["data_start"] // LP_SECTOR,
                          LP_ALIGN, 0, plan["device_size"],
                          LP_DEVICE.encode("ascii"), 0)

    off_parts = 0
    off_exts = off_parts + len(parts)
    off_groups = off_exts + len(exts)
    off_devices = off_groups + len(groups)
    tables = parts + exts + groups + devices

    header = struct.pack("<IHHI32sI32s", LP_HEADER_MAGIC, LP_MAJOR, LP_MINOR,
                         LP_HEADER_SIZE, b"\x00" * 32, len(tables),
                         b"\x00" * 32)
    header += struct.pack("<III", off_parts, len(items), 52)
    header += struct.pack("<III", off_exts, len(items), 24)
    header += struct.pack("<III", off_groups, 1, 48)
    header += struct.pack("<III", off_devices, 1, 64)
    header += struct.pack("<I", 0) + b"\x00" * 124
    if len(header) != LP_HEADER_SIZE:
        raise AssertionError("LP header 长度错误: %d" % (len(header),))
    # 先落 tables 校验和，再算 header 校验和（后者覆盖前者）。
    header = header[:48] + hashlib.sha256(tables).digest() + header[80:]
    header = header[:12] + hashlib.sha256(header).digest() + header[44:]
    return header + tables


def _write_metadata(handle, plan):
    geometry = _geometry()
    blob = _metadata(plan).ljust(LP_META_MAX, b"\x00")
    handle.seek(0)
    handle.write(b"\x00" * LP_RESERVED)
    handle.seek(LP_RESERVED)
    handle.write(geometry)
    handle.seek(LP_RESERVED + LP_GEOMETRY_SIZE)
    handle.write(geometry)
    meta_start = LP_RESERVED + LP_GEOMETRY_SIZE * 2
    for index in range(LP_META_SLOTS):
        handle.seek(meta_start + index * LP_META_MAX)
        handle.write(blob)
    backup_start = meta_start + LP_META_MAX * LP_META_SLOTS
    for index in range(LP_META_SLOTS):
        handle.seek(backup_start + index * LP_META_MAX)
        handle.write(blob)


def build_super(root, device_size, out_path, slot="a", progress=None,
                chunk=4 << 20):
    """合成 super.img；progress(done, total, text) 每块回调一次。"""
    plan = plan_super(root, device_size, slot)
    total = sum(item["size"] for item in plan["items"])
    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    free = shutil.disk_usage(out_dir).free
    if free < plan["data_end"] + (64 << 20):
        raise ValueError("磁盘空间不足：需要 %d B，可用 %d B"
                         % (plan["data_end"], free))

    done = 0
    with open(out_path, "wb") as handle:
        _write_metadata(handle, plan)
        for item in plan["items"]:
            if progress is not None:
                progress(done, total, "打包 %s" % (item["name"],))
            handle.seek(item["offset"])
            with open(item["path"], "rb") as source:
                while True:
                    block = source.read(chunk)
                    if not block:
                        break
                    handle.write(block)
                    done += len(block)
                    if progress is not None:
                        progress(done, total, "打包 %s" % (item["name"],))
            pad = (-item["size"]) % LP_SECTOR
            if pad:
                handle.write(b"\x00" * pad)
        handle.truncate(plan["data_end"])
    return {
        "path": os.path.abspath(out_path),
        "plan": plan,
        "total": total,
    }
