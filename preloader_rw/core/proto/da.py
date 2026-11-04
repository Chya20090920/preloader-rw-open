# -*- coding: utf-8 -*-
"""DA1 / DA2 镜像上传。

DA1 在帧协议之前，走裸传输：3 个大端传输头字 + 原始分块，块长为 512 整数倍
时补 ZLP。这一阶段直接使用 Device 端口，因为此时还没有 Channel。

DA2 在帧协议之内，必须经 Channel.send 发送，不得直接写 OUT。
"""

import struct

from ..ports.device import Endpoint
from . import redact
from .const import OK0

DA1_FIRST_WORD = 0x02000000
DA1_FLAGS_WORD = 0x00000100
DA1_BLOCK = 65536
# DA2 分块大小。设备在 DOWNLOAD-FILE(2nd-DA) 里未申报 packet_length（A1：
# golden 234243 i=61 packet_length=null），即分块是主机选择，不是设备约束。
DA2_PIECE = 4096


def da1_header(length):
    """DA1 传输头三字：首字、镜像长度、标志，均为大端。"""
    return (struct.pack(">I", DA1_FIRST_WORD),
            struct.pack(">I", length & 0xFFFFFFFF),
            struct.pack(">I", DA1_FLAGS_WORD))


def verify_header(words, length):
    """传输头自证：与长度推导出的三字逐一比对，不符即抛。"""
    expected = da1_header(length)
    actual = tuple(words)
    if actual != expected:
        index = next((i for i, (a, e) in enumerate(zip(actual, expected))
                      if a != e), -1)
        raise IOError("传输头自证失败: 第 %d 字不符" % (index + 1))
    return True


def send_header(device, clock, length, *, log=None, write_timeout_ms=3000,
                read_timeout_ms=300, tail_timeout_s=2.0, tail_read_ms=200):
    """发三个传输头字，逐字等回显；然后收两个字节的头尾。"""
    log = log or (lambda m: None)
    for number, word in enumerate(da1_header(length), 1):
        device.write(Endpoint.OUT, word, write_timeout_ms)
        echo = device.read(Endpoint.IN, 4, read_timeout_ms)
        if echo != word:
            raise IOError("传输头第 %d 字回显不符（期望 %dB，实收 %dB）"
                          % (number, len(word), len(echo)))
        log("    头字 %d OK len=%d" % (number, len(word)))

    tail = b""
    end = clock.now() + tail_timeout_s
    while len(tail) < 2 and clock.now() < end:
        data = device.read(Endpoint.IN, 2 - len(tail), tail_read_ms)
        if data:
            tail += data
    log("    头尾 len=%d" % len(tail))
    return tail


def upload_da1(device, clock, raw, *, block=DA1_BLOCK, timeout_ms=3000,
               zlp_timeout_ms=500, log=None):
    """DA1 原始分块上传。块长为 512 整数倍时补 ZLP。"""
    log = log or (lambda m: None)
    blocks = [raw[i:i + block] for i in range(0, len(raw), block)]
    if b"".join(blocks) != raw:
        raise ValueError("DA1 分块自证失败")

    log("  第一段 DA: %d 块 %s" % (len(blocks), [len(b) for b in blocks]))
    start = clock.now()
    for index, piece in enumerate(blocks):
        device.write(Endpoint.OUT, piece, timeout_ms)
        if len(piece) % 512 == 0:
            device.write_zlp(Endpoint.OUT, zlp_timeout_ms)
        if redact.should_log_asset(index, len(blocks)):
            log("    " + redact.frame_line(
                "H2D", index + 1, piece, redact.ASSET, "da1",
                index, len(blocks)))
    log("    完成 %.3fs" % (clock.now() - start))
    return len(raw)


def wait_ready(device, clock, *, timeout_s=3.0, read_ms=200, size=64, log=None):
    """等 DA1 的 4 字节就绪回执（B0 CC …）。返回已收字节。"""
    log = log or (lambda m: None)
    buf = b""
    start = clock.now()
    while clock.now() - start < timeout_s:
        data = device.read(Endpoint.IN, size, read_ms)
        if data:
            buf += data
            log("    [%.2fs] IN len=%d 就绪=%s"
                % (clock.now() - start, len(data),
                   "是" if data[:2] == b"\xb0\xcc" else "否"))
        if len(buf) >= 4:
            break
    return buf


def finish(device, clock, *, write_timeout_ms=3000, read_timeout_ms=500, log=None):
    """DA1 收尾：D5 回显，随后 02 回显。返回 D5 是否回显成功。"""
    log = log or (lambda m: None)
    device.write(Endpoint.OUT, b"\xD5", write_timeout_ms)
    echo = device.read(Endpoint.IN, 1, read_timeout_ms)
    log("  d5 回显 len=%d 匹配=%s" % (len(echo), "是" if echo == b"\xD5" else "否"))

    device.write(Endpoint.OUT, struct.pack("<I", 2), write_timeout_ms)
    first = device.read(Endpoint.IN, 4, read_timeout_ms)
    second = device.read(Endpoint.IN, 2, read_timeout_ms)
    log("  02 回显 len=%d+%d" % (len(first), len(second)))
    return echo == b"\xD5"


def upload_da2(channel, clock, raw, *, piece_size=DA2_PIECE,
               serve_s=0.005, serve_read_ms=5, log=None):
    """DA2 帧化上传：按链路速率发「令牌 + 数据」，不插等待。

    设备侧是自驱接收循环：读控制帧（须以 "OK" 开头）
    → 写 3B OK → 读一帧数据 → 写 3B OK，直到收满申报总长。所以主机只需把
    「令牌 + 数据」紧邻送下去；每笔 OUT 前做一次**不等待**的 IN 服务，把
    设备那 3B OK 取走——否则设备会阻塞在自己的 IN 写上，连带停住读循环。

    这里不插服务窗、不留头/载荷节拍：那是命令对话形态的保护，接收循环
    形态不需要，插上只是把 356 KiB 的流拖成 26 s。
    """
    log = log or (lambda m: None)
    pieces = [raw[i:i + piece_size] for i in range(0, len(raw), piece_size)]
    if b"".join(pieces) != raw:
        raise ValueError("DA2 分块自证失败")

    total = len(pieces)
    log("  第二段 DA: %d 段 (%d x %dB + 尾 %dB)"
        % (total, total - 1, piece_size, len(pieces[-1]) if pieces else 0))
    start = clock.now()
    for index, piece in enumerate(pieces):
        channel.poll(serve_s, serve_read_ms)
        channel.send(OK0, service=0, beat=0, role=redact.CTL)
        channel.poll(serve_s, serve_read_ms)
        channel.send(piece, service=0, beat=0, role=redact.ASSET,
                     label="da2", index=index, total=total)
    log("    完成: %d 段 %.1fs" % (total, clock.now() - start))
    return total


__all__ = [
    "DA1_FIRST_WORD",
    "DA1_FLAGS_WORD",
    "DA1_BLOCK",
    "DA2_PIECE",
    "da1_header",
    "verify_header",
    "send_header",
    "upload_da1",
    "wait_ready",
    "finish",
    "upload_da2",
]
