# -*- coding: utf-8 -*-
"""存储供数：唯一的供数状态机。

回合结构（只描述结构与长度，不含任何时间判据）：

    [上层已发长度帧 OK@0x<总长>]
    READY      设备 3B `OK\\0`            —— 每条命令一次
    REPEAT 每块：
        TOKEN      主机 7B `OK@0x0\\0`
        READY      设备 3B `OK\\0`
        DATA       主机 头(12) + 载荷
        ACK        设备 7B `OK@0x0\\0`

数据帧不发零长包：残留在管道里的零长包可能被设备下一次控制帧读吞成
"0 字节完成"，使真正的主机令牌错位到数据帧头的位置。

本模块只做这一种形状，不提供「先发数据再等回执」的旁路。
"""

from . import redact
from .const import OK, OK0

__all__ = ["download", "download_stream"]

# 视为终止的设备帧：出现即不再供数，并把该帧退回队列交给命令层裁决。
_TERMINAL_PREFIXES = (b"ERR",)
_TERMINAL_NEEDLES = (b"CMD:END",)


def _is_terminal(payload):
    """终止帧：设备真发来的 END XML，或以 ERR 开头的短帧。

    设备日志会把 XML 原文嵌进日志行，日志行里出现 "CMD:END" 并不等于终止；
    因此只认以 XML 开头（或短 ERR 帧）的载荷。
    """
    if payload.startswith(_TERMINAL_PREFIXES):
        return True
    head = payload.lstrip()
    return head.startswith(b"<?xml") and any(n in payload for n in _TERMINAL_NEEDLES)


def _wait(channel, want, budget_ms, step):
    """在预算内等一帧指定载荷。

    返回 "ok" / "timeout" / "terminal"。
    非目标帧（日志、心跳、别的 OK@）记日志后丢弃；终止帧退回队列，
    使命令层仍能看到 END。
    """
    end = channel.clock.now() + max(0, int(budget_ms)) / 1000.0
    seen = []
    while channel.clock.now() < end:
        frames = channel.poll(0.02, 5) or []
        for flag, payload in frames:
            if payload == want:
                return "ok"
            if _is_terminal(payload):
                channel.unread([(flag, payload)])
                return "terminal"
            seen.append(redact.frame_line(
                "D2H", len(seen) + 1, payload, redact.classify_inbound(payload)))
    channel.log("    供数[%s]: %.1fs 内未等到 %r；期间收到 %s"
                % (step, budget_ms / 1000.0, want, "; ".join(seen[:4])))
    return "timeout"


def download(channel, data, chunk, *, budget_ms, token=OK0, ready=OK,
             ack=OK0):
    """按上面的状态机供数（内存字节），返回实际供出的字节数。

    未完成即返回已供字节数，由调用方判定失败；本函数不重试、不跳过步骤。
    """
    if chunk <= 0:
        chunk = max(1, len(data))
    state = {"off": 0}

    def read_piece(n):
        off = state["off"]
        piece = data[off:off + n]
        state["off"] = off + len(piece)
        return piece

    return _run(channel, read_piece, len(data), chunk, budget_ms,
                token, ready, ack)


def download_stream(channel, stream, total, chunk, *, budget_ms, token=OK0,
                    ready=OK, ack=OK0):
    """按同一状态机从文件对象供数；每块现读，不把整幅镜像读进内存。"""
    if chunk <= 0:
        chunk = max(1, total)
    return _run(channel, lambda n: stream.read(n), total, chunk, budget_ms,
                token, ready, ack)


def _run(channel, read_piece, total, chunk, budget_ms, token, ready, ack):
    # 长度帧之后必须等到设备的 3B OK，才允许发第一枚令牌。
    if _wait(channel, ready, budget_ms, "长度帧回执") != "ok":
        return 0

    sent = 0
    index = 0
    total_blocks = (total + max(1, chunk) - 1) // max(1, chunk)
    while sent < total:
        # 先读块、再发令牌：读盘（尤其 System Volume 上的镜像）不能落在
        # 「令牌已确认 → 设备等数据」这段窗口里。
        piece = read_piece(chunk)
        if not piece:
            return sent

        channel.send(token, role=redact.CTL)
        if _wait(channel, ready, budget_ms, "令牌回执") != "ok":
            return sent

        # 资产帧只记长度与块序号；令牌与收讫由 Channel 以符号形式记录。
        channel.send(piece, role=redact.ASSET, label="image",
                     index=index, total=total_blocks)
        index += 1
        if _wait(channel, ack, budget_ms, "数据收讫") != "ok":
            return sent
        sent += len(piece)
    return sent
