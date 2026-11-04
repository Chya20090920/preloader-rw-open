# -*- coding: utf-8 -*-
"""通用收讫应答：唯一实现。

原则（旧 src/session.py L195-204 与 L437 的合并语义）：

  · 当轮收讫归“当轮处理者”：回合尾、设备下载请求、设备上传请求、文件系统
    查询四类事件的应答帧由该轮的载荷/Provider 决定，不在本模块；
  · 跨轮通用帧由会话层统一应答：回合开始、含命令节点的 XML 通知帧、
    以及带内容的收讫帧（形式为“OK@…”或“OK!…”）；
  · 纯 3B OK（b"OK\0"）是上传状态机内部的握手：上传由内核上传状态机
    应答，轮外出现即忽略（旧 L195-198：上传上下文为空时不回）。多回一条会
    把回合边界推错位，设备随即判下一轮命令 UNSUPPORTED 并停机。

收讫帧字节由调用方注入（ack 参数）：engine 只承载规则，不认识协议常量，
帧形态仍归属 proto/kernel 层。
"""

# 需要会话层统一应答的通用事件（值形态，两个事件枚举共用）。
_UNIVERSAL = ("start", "notice")


def generic_reply(kind_value, raw, ack):
    """返回该上行事件需要的通用收讫帧；不需要则 None。

    kind_value 取 EventKind / RequestKind 的 `.value`，两边同源同名。
    ack 为通用收讫帧字节（如 b"OK\\0"），由内核层注入。
    """
    if kind_value == "ok":
        return None if raw == ack else ack
    if kind_value in _UNIVERSAL:
        return ack
    return None


__all__ = ["generic_reply"]
