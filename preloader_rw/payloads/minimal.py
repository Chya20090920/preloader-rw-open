# -*- coding: utf-8 -*-
"""最小示例载荷：证明「换一份载荷，内核一行不改」。

载荷契约（见 `preloader_rw/core/kernel/api.py` 的 KernelAPI）：
  · 入口必须是 `route_main(api)`；
  · 可以调用内核原语（send / wait_event / reply / command / supply /
    serve_da2 / recv_file / require / log / sleep）；
  · 结束必须调 `api.enter_loop()` 声明命令面已就绪，否则注册失败；
  · 认证需要的二层文件经 `api.require("<kind>")` 取（本例不用）。

这份载荷不做认证、不发任何命令：只等一个 CMD:START 应答后移交。
它不能解锁受限命令（写分区会失败），但装配、预检与注册全部成立——
这就是「用户可替换载荷」的最小形态。
"""

from preloader_rw.core.engine import acks, timing
from preloader_rw.core.engine.inbound import EventKind
from preloader_rw.core.proto import xml


def route_main(api):
    deadline_ms = timing.DEV_INIT_MS
    while True:
        event = api.wait_event(deadline_ms)
        kind = event.kind
        if kind is EventKind.START:
            api.reply(xml.OK)
            break
        if kind is EventKind.TIMEOUT:
            raise IOError("等 START 失败：设备无帧")
        if kind is EventKind.DEV_GONE:
            raise IOError("设备消失")
        deadline_ms = timing.DIALOG_MS
        reply = acks.generic_reply(kind.value, getattr(event, "raw", b""), xml.OK)
        if reply and kind in (EventKind.END, EventKind.START, EventKind.NOTICE):
            api.reply(reply)
    api.enter_loop()
