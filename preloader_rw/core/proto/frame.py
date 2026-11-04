# -*- coding: utf-8 -*-
"""12 字节帧头编解码与帧流解析。

帧 = 同步字(4) + flag(4，小端) + 载荷长度(4，小端) + 载荷。
flag=2（设备日志）的载荷**自带 4 字节元数据前缀**（如 `\x01\x00\x00\x00`），
该前缀属于载荷、不属于帧头：
    efeeeefe 02000000 19000000 | 01000000 + 21 B 文本（申报 25 = 4 + 21）
按 16 B 头解析会把下一条帧的同步字吃进本帧载荷，帧流随即失步。

解析只消费完整帧；半帧留在调用方的缓冲里，不产出、不吞字节。
"""

import struct

from .const import FLAG_CMD, FRAME_SYNC, MAX_PAYLOAD

HEADER_SIZE = 12


def encode_header(flag, length):
    """编码 12 字节帧头。长度按小端写入。"""
    return FRAME_SYNC + struct.pack("<II", flag, length & 0xFFFFFFFF)


def decode_header(buf):
    """解出 (flag, length)；同步字不符或不足 12 字节返回 None。"""
    if len(buf) < HEADER_SIZE or buf[:4] != FRAME_SYNC:
        return None
    flag, length = struct.unpack("<II", buf[4:HEADER_SIZE])
    return flag, length


def build(payload, flag=FLAG_CMD):
    """把载荷封装成一帧。长度字段取载荷实长。"""
    return encode_header(flag, len(payload)) + payload


def parse(buf):
    """从字节流中解析完整帧。

    返回 (frames, used)：
      frames = [(flag, payload), ...]
      used   = 已消费字节数；未消费部分含半帧或垃圾，由调用方保留。
    声明长度超过 MAX_PAYLOAD 时停止解析，避免按坏长度分配内存。
    帧头恒为 12 B；flag=2 的 4 字节日志元数据在载荷内部，由上层剥除。
    """
    frames = []
    i = 0
    while True:
        j = buf.find(FRAME_SYNC, i)
        if j < 0 or j + HEADER_SIZE > len(buf):
            break
        flag, length = struct.unpack("<II", buf[j + 4:j + HEADER_SIZE])
        if length > MAX_PAYLOAD or j + HEADER_SIZE + length > len(buf):
            break
        frames.append((flag, buf[j + HEADER_SIZE:j + HEADER_SIZE + length]))
        i = j + HEADER_SIZE + length
    return frames, i


def text(payload):
    """可打印化：不可打印字节显示为点。仅用于日志。"""
    return "".join(chr(c) if 0x20 <= c < 0x7F else "." for c in payload)


__all__ = [
    "HEADER_SIZE",
    "encode_header",
    "decode_header",
    "build",
    "parse",
    "text",
]
