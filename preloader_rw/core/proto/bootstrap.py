# -*- coding: utf-8 -*-
"""preloader 阶段：EP0 配置、握手、探针。

全部动作走 Device 端口，不出现物理端点号与句柄；超时由参数给。
"""

from ..ports.device import Endpoint

HS_WAKE = 0xA0
HS = bytes([0xA0, 0x0A, 0x50, 0x05])
PROBES = ((0xFD, 4), (0xFC, 8), (0xE7, 38), (0xFE, 1), (0xD8, 6),
          (0xFE, 1), (0xFD, 4), (0xFC, 8), (0xE7, 38), (0xD8, 6),
          (0xFD, 4), (0xFC, 8), (0xD8, 6),
          (0xD7, 0))
DRAIN_READ_MS = 100
DRAIN_LIMIT = 64
CTRL_TIMEOUT_MS = 1000


def ep0(device, clock, *, drain_read_ms=DRAIN_READ_MS, ctrl_timeout_ms=CTRL_TIMEOUT_MS,
        read_ms=100):
    """EP0 请求序列：接口/描述符配置，随后取空端点。

    BOS 描述符交换后设备会突发一段日志/心跳。旧代码在这里等固定 1.35 s
    静默窗；实测该窗的作用只是把端点清干净（不清会污染随后握手的回显
    校验），不是等设备思考，故改为「取空为止」——空读一次即说明设备已无
    待取数据。这一项是主机自插的时间，与设备速度无关。
    """
    device.control(0xA1, 0x21, 0, 0, 7, ctrl_timeout_ms)
    device.control(0x21, 0x22, 0, 0, 0, ctrl_timeout_ms)
    device.control(0x80, 0x06, 0x0F00, 0, 5, ctrl_timeout_ms)
    device.control(0x80, 0x06, 0x0F00, 0, 22, ctrl_timeout_ms)

    while device.read(Endpoint.IN, DRAIN_LIMIT, drain_read_ms):
        pass

    device.control(0xA1, 0x21, 0, 0, 7, ctrl_timeout_ms)
    device.control(0xA1, 0x21, 0, 0, 7, ctrl_timeout_ms)
    device.control(0x21, 0x23, 0, 0, 0, ctrl_timeout_ms)
    device.control(0x21, 0x22, 2, 0, 0, ctrl_timeout_ms)
    device.control(0x21, 0x22, 3, 0, 0, ctrl_timeout_ms)


def handshake(device, clock, *, write_timeout_ms=3000, read_timeout_ms=60, log=None):
    """preloader 握手：唤醒字节 + 四字节序列，逐字节校验反码回显。"""
    log = log or (lambda m: None)
    device.write(Endpoint.OUT, bytes([HS_WAKE]), write_timeout_ms)
    device.read(Endpoint.IN, 1, read_timeout_ms)

    for value in HS:
        device.write(Endpoint.OUT, bytes([value]), write_timeout_ms)
        echo = device.read(Endpoint.IN, 1, read_timeout_ms)
        expected = bytes([value ^ 0xFF])
        verdict = "OK" if echo == expected else ("(空)" if not echo else "不符")
        log("    OUT %02X -> %s" % (value, verdict))
        if echo != expected:
            return False
    return True


def _read_some(device, clock, count, *, max_idle=3.0, read_ms=200):
    """读满 count 字节；连续 max_idle 秒无数据即返回已收部分。"""
    buf = b""
    last = clock.now()
    while len(buf) < count:
        data = device.read(Endpoint.IN, count - len(buf), read_ms)
        if data:
            buf += data
            last = clock.now()
        elif clock.now() - last > max_idle:
            break
    return buf


def probe(device, clock, cmd, payload_len, *, log=None, write_timeout_ms=3000,
          read_ms=200):
    """单条探针：发命令字节，校验回显，再读声明长度的载荷。"""
    log = log or (lambda m: None)
    device.write(Endpoint.OUT, bytes([cmd]), write_timeout_ms)
    if cmd == 0xFE:
        data = _read_some(device, clock, 1, max_idle=1.0, read_ms=read_ms)
        log("  fe -> len=%d" % len(data))
        return data

    echo = _read_some(device, clock, 1, max_idle=1.0, read_ms=read_ms)
    if echo != bytes([cmd]):
        raise IOError("probe %02X 回显不符（实收 %dB）" % (cmd, len(echo)))
    payload = _read_some(device, clock, payload_len, read_ms=read_ms) if payload_len else b""
    log("  %02X -> len=%d" % (cmd, len(payload)))
    return payload


def probes(device, clock, *, log=None, write_timeout_ms=3000, read_ms=200):
    """按固定序列跑探针；同类命令只保留首次结果。"""
    out = {}
    for cmd, payload_len in PROBES:
        payload = probe(device, clock, cmd, payload_len, log=log,
                        write_timeout_ms=write_timeout_ms, read_ms=read_ms)
        if cmd in (0xFD, 0xFC, 0xE7, 0xD8):
            out.setdefault(cmd, payload)
    return out


__all__ = [
    "HS_WAKE",
    "HS",
    "PROBES",
    "DRAIN_READ_MS",
    "CTRL_TIMEOUT_MS",
    "ep0",
    "handshake",
    "probe",
    "probes",
]
