# -*- coding: utf-8 -*-
"""命令许可闸：两条命令之间的 CMD:START 握手。

设备在每条命令结束后发 CMD:START，等主机 3 B OK；在此之前发出的下一条
命令帧会被它当成 START 的结果，判 0xc0010006 并 halt（实机 14:33:09：
命令帧与 D2H CMD:START 同一毫秒发出，12 s 后设备回
`REVEIVING Start CMD Result ERROR(0xc0010006). halt.`）。

闸挂在会话上，不挂在循环实例上：命令循环按批次新建，许可却跨批次存在
（分区表走一个 `kernel.loop()`，设备信息三条走另一个）。
"""


class StartGate(object):
    """armed：上一条命令已收 END，下一条命令帧必须等 START。
    ready ：设备已发 START 且主机已应答，许可已到手。
    """

    __slots__ = ("armed", "ready")

    def __init__(self):
        self.armed = False
        self.ready = False

    def arm(self):
        """命令结束（收到 END）：重置为「等 START」。"""
        self.armed = True
        self.ready = False

    def granted(self):
        """收到并应答了 START：许可到手。"""
        self.ready = True

    def consume(self):
        """命令帧即将发出：许可被本条命令用掉。"""
        self.armed = False
        self.ready = False


__all__ = ["StartGate"]
